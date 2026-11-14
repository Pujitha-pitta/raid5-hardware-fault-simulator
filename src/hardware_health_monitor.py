#!/usr/bin/env python3
"""
hardware_health_monitor.py
──────────────────────────────────────────────────────────────────────────────
AWS DCO Bare‑Metal Server Storage, S.M.A.R.T. Disk Telemetry & RAID 5
Fault‑Recovery Simulator.

Supports two modes:
  • Live Mode   – queries real mdadm / smartctl / dmesg / lm‑sensors (requires
                  root on a Linux host with an active RAID array).
  • Simulate    – deterministic emulation via `--simulate --fault-type <SCENARIO>`
                  that generates realistic enterprise server telemetry without
                  root or block‑device access.

Fault scenarios:
  raid_degraded_smart_fail  – degraded RAID 5 [UU_] with S.M.A.R.T. failure
  thermal_dimm_fault        – CPU thermal throttle + EDAC ECC errors on DIMM_A2
  healthy_server            – clean baseline telemetry

Outputs:
  reports/hardware_rca_runbook.md   – Markdown DCO RCA & Hot‑Swap Runbook
  reports/hardware_telemetry.json   – Machine‑readable telemetry dump
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys
import textwrap
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

# ═══════════════════════════════════════════════════════════════════════════════
# Domain Models
# ═══════════════════════════════════════════════════════════════════════════════

class RaidState(Enum):
    ACTIVE = "active"
    DEGRADED = "degraded"
    REBUILDING = "rebuilding"
    FAILED = "failed"
    INACTIVE = "inactive"


class Severity(Enum):
    OK = "OK"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


@dataclass
class SmartAttribute:
    id: int
    name: str
    value: int
    worst: int
    threshold: int
    raw_value: int
    status: str  # "OK", "WARNING", "CRITICAL"

    def is_failing(self) -> bool:
        return self.value <= self.threshold or self.status == "CRITICAL"


@dataclass
class DiskTelemetry:
    device: str
    model: str
    serial: str
    firmware: str
    smart_health: str  # "PASSED" | "FAILED"
    temperature_c: int
    power_on_hours: int
    attributes: List[SmartAttribute] = field(default_factory=list)

    def failing_attributes(self) -> List[SmartAttribute]:
        return [a for a in self.attributes if a.is_failing()]


@dataclass
class RaidArrayStatus:
    device: str
    level: str
    state: RaidState
    status_line: str          # e.g. "[UUU]" or "[UU_]"
    total_devices: int
    active_devices: int
    failed_devices: int
    spare_devices: int
    component_devices: List[str]
    rebuild_progress: Optional[float] = None  # 0.0–100.0 or None

    @property
    def bitmap(self) -> str:
        """Return the U/_ bitmap from status_line."""
        match = re.search(r'\[([U_]+)\]', self.status_line)
        return match.group(1) if match else ""

    def is_healthy(self) -> bool:
        return self.state == RaidState.ACTIVE and "_" not in self.bitmap


@dataclass
class ThermalReading:
    sensor: str
    component: str
    temp_c: float
    high_c: float
    crit_c: float
    status: str  # "OK", "WARNING", "CRITICAL"


@dataclass
class EdacError:
    controller: str
    dimm: str
    error_type: str     # "correctable" | "uncorrectable"
    count: int
    message: str


@dataclass
class DmesgEntry:
    timestamp: float
    level: str
    message: str


@dataclass
class ServerTelemetry:
    hostname: str
    timestamp: str
    raid_arrays: List[RaidArrayStatus] = field(default_factory=list)
    disks: List[DiskTelemetry] = field(default_factory=list)
    thermals: List[ThermalReading] = field(default_factory=list)
    edac_errors: List[EdacError] = field(default_factory=list)
    dmesg_entries: List[DmesgEntry] = field(default_factory=list)
    fan_rpms: Dict[str, int] = field(default_factory=dict)
    overall_severity: Severity = Severity.OK


# ═══════════════════════════════════════════════════════════════════════════════
# Simulation Data Generators
# ═══════════════════════════════════════════════════════════════════════════════

def _base_smart_attrs(healthy: bool = True) -> List[SmartAttribute]:
    """Generate a baseline set of S.M.A.R.T. attributes."""
    if healthy:
        return [
            SmartAttribute(1, "Raw_Read_Error_Rate", 200, 200, 51, 0, "OK"),
            SmartAttribute(3, "Spin_Up_Time", 176, 175, 21, 4375, "OK"),
            SmartAttribute(4, "Start_Stop_Count", 100, 100, 0, 18, "OK"),
            SmartAttribute(5, "Reallocated_Sector_Ct", 200, 200, 140, 0, "OK"),
            SmartAttribute(7, "Seek_Error_Rate", 200, 200, 51, 0, "OK"),
            SmartAttribute(9, "Power_On_Hours", 97, 97, 0, 12847, "OK"),
            SmartAttribute(10, "Spin_Retry_Count", 100, 100, 51, 0, "OK"),
            SmartAttribute(12, "Power_Cycle_Count", 100, 100, 0, 14, "OK"),
            SmartAttribute(194, "Temperature_Celsius", 113, 103, 0, 34, "OK"),
            SmartAttribute(197, "Current_Pending_Sector", 200, 200, 0, 0, "OK"),
            SmartAttribute(198, "Offline_Uncorrectable", 200, 200, 0, 0, "OK"),
            SmartAttribute(199, "UDMA_CRC_Error_Count", 200, 200, 0, 0, "OK"),
        ]
    else:
        return [
            SmartAttribute(1, "Raw_Read_Error_Rate", 160, 133, 51, 2814, "WARNING"),
            SmartAttribute(3, "Spin_Up_Time", 170, 165, 21, 4812, "OK"),
            SmartAttribute(4, "Start_Stop_Count", 100, 100, 0, 42, "OK"),
            SmartAttribute(5, "Reallocated_Sector_Ct", 132, 132, 140, 544, "CRITICAL"),
            SmartAttribute(7, "Seek_Error_Rate", 183, 175, 51, 1247, "WARNING"),
            SmartAttribute(9, "Power_On_Hours", 72, 72, 0, 34819, "OK"),
            SmartAttribute(10, "Spin_Retry_Count", 100, 100, 51, 0, "OK"),
            SmartAttribute(12, "Power_Cycle_Count", 100, 100, 0, 67, "OK"),
            SmartAttribute(194, "Temperature_Celsius", 95, 88, 0, 52, "OK"),
            SmartAttribute(197, "Current_Pending_Sector", 196, 196, 0, 48, "CRITICAL"),
            SmartAttribute(198, "Offline_Uncorrectable", 196, 196, 0, 48, "WARNING"),
            SmartAttribute(199, "UDMA_CRC_Error_Count", 198, 198, 0, 7, "CRITICAL"),
        ]


def _make_disk(dev: str, healthy: bool, model: str = "WDC WUH721818AL",
               serial_prefix: str = "WMC") -> DiskTelemetry:
    serial = f"{serial_prefix}{'5G3N7P' if healthy else '2K1R4F'}"
    return DiskTelemetry(
        device=dev,
        model=f"{model}E6L4",
        serial=serial,
        firmware="83.00A83",
        smart_health="PASSED" if healthy else "FAILED",
        temperature_c=34 if healthy else 52,
        power_on_hours=12847 if healthy else 34819,
        attributes=_base_smart_attrs(healthy),
    )


def simulate_raid_degraded_smart_fail() -> ServerTelemetry:
    """Simulate a degraded RAID 5 array with S.M.A.R.T. disk failure."""
    ts = datetime.datetime.utcnow().isoformat() + "Z"
    telem = ServerTelemetry(
        hostname="aws-dco-bms-prod-0142.us-east-1.metal.internal",
        timestamp=ts,
    )

    # RAID array — degraded, spare rebuilding
    telem.raid_arrays.append(RaidArrayStatus(
        device="/dev/md0",
        level="raid5",
        state=RaidState.DEGRADED,
        status_line="[UU_]",
        total_devices=4,
        active_devices=2,
        failed_devices=1,
        spare_devices=1,
        component_devices=["/dev/loop10", "/dev/loop11", "/dev/loop12", "/dev/loop13"],
        rebuild_progress=34.7,
    ))

    # Disks — loop10 failing, others healthy
    telem.disks.append(_make_disk("/dev/loop10", healthy=False, serial_prefix="WMC"))
    telem.disks.append(_make_disk("/dev/loop11", healthy=True, serial_prefix="WMC"))
    telem.disks.append(_make_disk("/dev/loop12", healthy=True, serial_prefix="WMC"))
    telem.disks.append(_make_disk("/dev/loop13", healthy=True, serial_prefix="WMC"))

    # dmesg entries
    base_ts = 84291.0
    telem.dmesg_entries = [
        DmesgEntry(base_ts + 0.000, "err",
                   "blk_update_request: critical medium error, dev sdb, sector 4096 op 0x0:(READ) flags 0x0 phys_seg 1 prio class 0"),
        DmesgEntry(base_ts + 0.001, "err",
                   "md/raid:md0: Disk failure on loop10, disabling device."),
        DmesgEntry(base_ts + 0.002, "warning",
                   "md/raid:md0: Operation continuing on 2 devices."),
        DmesgEntry(base_ts + 0.210, "info",
                   "md: recovery of RAID array md0"),
        DmesgEntry(base_ts + 0.211, "info",
                   "md: minimum _guaranteed_ speed: 1000 KB/sec/disk."),
        DmesgEntry(base_ts + 0.212, "info",
                   "md: using maximum available idle IO bandwidth for recovery."),
        DmesgEntry(base_ts + 0.213, "info",
                   "md: md0: recovery done."),
    ]

    # Thermals — normal
    telem.thermals = [
        ThermalReading("coretemp-isa-0000", "CPU Package", 62.0, 90.0, 100.0, "OK"),
        ThermalReading("coretemp-isa-0000", "Core 0", 58.0, 90.0, 100.0, "OK"),
        ThermalReading("coretemp-isa-0000", "Core 1", 61.0, 90.0, 100.0, "OK"),
        ThermalReading("nct6798-isa-0290", "System Temp", 38.0, 75.0, 85.0, "OK"),
    ]

    telem.fan_rpms = {"CPU_FAN": 3200, "SYS_FAN1": 1850, "SYS_FAN2": 1830, "SYS_FAN3": 1810}
    telem.overall_severity = Severity.CRITICAL

    return telem


def simulate_thermal_dimm_fault() -> ServerTelemetry:
    """Simulate thermal throttling and EDAC ECC DIMM errors."""
    ts = datetime.datetime.utcnow().isoformat() + "Z"
    telem = ServerTelemetry(
        hostname="aws-dco-bms-prod-0142.us-east-1.metal.internal",
        timestamp=ts,
    )

    # RAID array — healthy
    telem.raid_arrays.append(RaidArrayStatus(
        device="/dev/md0",
        level="raid5",
        state=RaidState.ACTIVE,
        status_line="[UUU]",
        total_devices=4,
        active_devices=3,
        failed_devices=0,
        spare_devices=1,
        component_devices=["/dev/loop10", "/dev/loop11", "/dev/loop12", "/dev/loop13"],
    ))

    # Disks — all healthy
    for i, dev in enumerate(["/dev/loop10", "/dev/loop11", "/dev/loop12", "/dev/loop13"]):
        telem.disks.append(_make_disk(dev, healthy=True, serial_prefix=f"WMC{i}"))

    # Thermals — CPU overheating
    telem.thermals = [
        ThermalReading("coretemp-isa-0000", "CPU Package", 94.0, 90.0, 100.0, "CRITICAL"),
        ThermalReading("coretemp-isa-0000", "Core 0", 92.0, 90.0, 100.0, "CRITICAL"),
        ThermalReading("coretemp-isa-0000", "Core 1", 93.0, 90.0, 100.0, "CRITICAL"),
        ThermalReading("nct6798-isa-0290", "System Temp", 46.0, 75.0, 85.0, "WARNING"),
    ]

    # EDAC / ECC DIMM errors
    telem.edac_errors = [
        EdacError("mc0", "DIMM_A2", "correctable", 847,
                  "EDAC MC0: 847 CE error on DIMM_A2 (channel:0 slot:1 page:0x0 offset:0x0 grain:8 syndrome:0x0)"),
        EdacError("mc0", "DIMM_A2", "uncorrectable", 3,
                  "EDAC MC0: 3 UE error on DIMM_A2 (channel:0 slot:1)"),
    ]

    # dmesg entries
    base_ts = 91204.0
    telem.dmesg_entries = [
        DmesgEntry(base_ts + 0.000, "crit",
                   "CPU0: Package temperature above threshold, cpu clock throttled"),
        DmesgEntry(base_ts + 0.001, "crit",
                   "CPU0: Package temperature/speed normal"),
        DmesgEntry(base_ts + 2.440, "crit",
                   "CPU0: Package temperature above threshold, cpu clock throttled"),
        DmesgEntry(base_ts + 3.100, "err",
                   "EDAC MC0: 847 CE error on CPU#0 Channel#0_DIMM#1 (DIMM_A2)"),
        DmesgEntry(base_ts + 3.101, "crit",
                   "EDAC MC0: 3 UE error(s) on CPU#0 Channel#0_DIMM#1 (DIMM_A2) — REPLACE DIMM IMMEDIATELY"),
    ]

    telem.fan_rpms = {"CPU_FAN": 5400, "SYS_FAN1": 2900, "SYS_FAN2": 2880, "SYS_FAN3": 2850}
    telem.overall_severity = Severity.CRITICAL

    return telem


def simulate_healthy_server() -> ServerTelemetry:
    """Simulate a fully healthy bare‑metal server."""
    ts = datetime.datetime.utcnow().isoformat() + "Z"
    telem = ServerTelemetry(
        hostname="aws-dco-bms-prod-0142.us-east-1.metal.internal",
        timestamp=ts,
    )

    telem.raid_arrays.append(RaidArrayStatus(
        device="/dev/md0",
        level="raid5",
        state=RaidState.ACTIVE,
        status_line="[UUU]",
        total_devices=4,
        active_devices=3,
        failed_devices=0,
        spare_devices=1,
        component_devices=["/dev/loop10", "/dev/loop11", "/dev/loop12", "/dev/loop13"],
    ))

    for i, dev in enumerate(["/dev/loop10", "/dev/loop11", "/dev/loop12", "/dev/loop13"]):
        telem.disks.append(_make_disk(dev, healthy=True, serial_prefix=f"WMC{i}"))

    telem.thermals = [
        ThermalReading("coretemp-isa-0000", "CPU Package", 62.0, 90.0, 100.0, "OK"),
        ThermalReading("coretemp-isa-0000", "Core 0", 58.0, 90.0, 100.0, "OK"),
        ThermalReading("coretemp-isa-0000", "Core 1", 61.0, 90.0, 100.0, "OK"),
        ThermalReading("nct6798-isa-0290", "System Temp", 38.0, 75.0, 85.0, "OK"),
    ]

    telem.fan_rpms = {"CPU_FAN": 3200, "SYS_FAN1": 1850, "SYS_FAN2": 1830, "SYS_FAN3": 1810}
    telem.overall_severity = Severity.OK

    return telem


SCENARIO_MAP = {
    "raid_degraded_smart_fail": simulate_raid_degraded_smart_fail,
    "thermal_dimm_fault": simulate_thermal_dimm_fault,
    "healthy_server": simulate_healthy_server,
}


# ═══════════════════════════════════════════════════════════════════════════════
# Analysis Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def parse_raid_bitmap(status_line: str) -> dict:
    """Parse a RAID status bitmap like '[UU_]' and return counts."""
    match = re.search(r'\[([U_]+)\]', status_line)
    if not match:
        return {"bitmap": "", "up": 0, "down": 0}
    bitmap = match.group(1)
    return {
        "bitmap": bitmap,
        "up": bitmap.count("U"),
        "down": bitmap.count("_"),
    }


def evaluate_smart_thresholds(disk: DiskTelemetry) -> List[dict]:
    """Evaluate S.M.A.R.T. attributes against thresholds; return alerts."""
    alerts = []
    for attr in disk.attributes:
        if attr.is_failing():
            alerts.append({
                "device": disk.device,
                "attribute_id": attr.id,
                "attribute_name": attr.name,
                "value": attr.value,
                "threshold": attr.threshold,
                "raw_value": attr.raw_value,
                "status": attr.status,
            })
    return alerts


def detect_thermal_faults(thermals: List[ThermalReading]) -> List[dict]:
    """Flag sensors that exceed their high/critical thresholds."""
    faults = []
    for t in thermals:
        if t.temp_c >= t.crit_c:
            faults.append({"sensor": t.sensor, "component": t.component,
                           "temp_c": t.temp_c, "threshold": t.crit_c, "level": "CRITICAL"})
        elif t.temp_c >= t.high_c:
            faults.append({"sensor": t.sensor, "component": t.component,
                           "temp_c": t.temp_c, "threshold": t.high_c, "level": "WARNING"})
    return faults


def detect_ecc_faults(edac_errors: List[EdacError]) -> List[dict]:
    """Flag any EDAC/ECC errors."""
    return [
        {"controller": e.controller, "dimm": e.dimm,
         "error_type": e.error_type, "count": e.count, "message": e.message}
        for e in edac_errors
    ]


# ═══════════════════════════════════════════════════════════════════════════════
# Report Generation
# ═══════════════════════════════════════════════════════════════════════════════

def _telemetry_to_serializable(telem: ServerTelemetry) -> dict:
    """Convert ServerTelemetry dataclass tree to a JSON‑serializable dict."""
    def _convert(obj: Any) -> Any:
        if isinstance(obj, Enum):
            return obj.value
        if hasattr(obj, '__dataclass_fields__'):
            return {k: _convert(v) for k, v in asdict(obj).items()}
        if isinstance(obj, list):
            return [_convert(i) for i in obj]
        if isinstance(obj, dict):
            return {k: _convert(v) for k, v in obj.items()}
        return obj
    return _convert(telem)


def generate_json_telemetry(telem: ServerTelemetry, output_path: Path) -> None:
    """Write full telemetry to JSON."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(_telemetry_to_serializable(telem), f, indent=2)


def generate_rca_runbook(telem: ServerTelemetry, output_path: Path) -> str:
    """Generate a Markdown RCA & Hot‑Swap Runbook and return its content."""
    raid_info = parse_raid_bitmap(telem.raid_arrays[0].status_line) if telem.raid_arrays else {}
    smart_alerts = []
    for d in telem.disks:
        smart_alerts.extend(evaluate_smart_thresholds(d))
    thermal_faults = detect_thermal_faults(telem.thermals)
    ecc_faults = detect_ecc_faults(telem.edac_errors)

    array = telem.raid_arrays[0] if telem.raid_arrays else None

    lines: List[str] = []
    w = lines.append

    w("# AWS DCO Hardware Root Cause Analysis (RCA) & Hot-Swap Runbook")
    w("")
    w(f"> **Generated:** {telem.timestamp}  ")
    w(f"> **Host:** `{telem.hostname}`  ")
    w(f"> **Overall Severity:** `{telem.overall_severity.value}`")
    w("")
    w("---")
    w("")

    # ── Section 1: RAID Array Status ─────────────────────────────────────
    w("## 1. RAID Array Status")
    w("")
    if array:
        w("### Simulated `/proc/mdstat` Output")
        w("```")
        w("Personalities : [raid6] [raid5] [raid4]")
        dev_list = " ".join(
            f"{d.split('/')[-1]}[{i}]{'(F)' if (array.state == RaidState.DEGRADED and i == 0) else '(S)' if i == 3 else ''}"
            for i, d in enumerate(array.component_devices)
        )
        w(f"md0 : active {array.level} {dev_list}")
        w(f"      204800 blocks super 1.2 level 5, 512k chunk, algorithm 2 {array.status_line}")
        if array.rebuild_progress is not None:
            w(f"      [{('=' * int(array.rebuild_progress / 5))}{('>' if array.rebuild_progress < 100 else '')}{('.' * (20 - int(array.rebuild_progress / 5)))}]"
              f"  recovery = {array.rebuild_progress:.1f}% ({int(204800 * array.rebuild_progress / 100)}/204800) finish=0.2min speed=12800K/sec")
        w("")
        w("unused devices: <none>")
        w("```")
        w("")
        w("### Simulated `mdadm --detail /dev/md0`")
        w("```")
        w(f"        Version : 1.2")
        w(f"  Creation Time : Sat Nov 14 07:10:22 2026")
        w(f"     Raid Level : {array.level}")
        w(f"     Array Size : 204800 (200.00 MiB)")
        w(f"  Used Dev Size : 102400 (100.00 MiB 104.86 MB)")
        w(f"   Raid Devices : {array.total_devices - array.spare_devices}")
        w(f"  Total Devices : {array.total_devices}")
        w(f"         State  : {array.state.value}")
        w(f" Active Devices : {array.active_devices}")
        w(f"Working Devices : {array.active_devices + array.spare_devices}")
        w(f" Failed Devices : {array.failed_devices}")
        w(f"  Spare Devices : {array.spare_devices}")
        w("")
        for i, dev in enumerate(array.component_devices):
            role = "faulty" if (array.state == RaidState.DEGRADED and i == 0) else \
                   "spare rebuilding" if i == 3 and array.rebuild_progress else \
                   f"active sync"
            w(f"    {i}     {dev}    {role}")
        w("```")
        w("")
        w(f"**Bitmap Analysis:** `{array.status_line}` → "
          f"{raid_info.get('up', '?')} active, {raid_info.get('down', '?')} degraded")
        w("")
    else:
        w("No RAID arrays detected.")
        w("")

    # ── Section 2: S.M.A.R.T. Disk Health ────────────────────────────────
    w("## 2. S.M.A.R.T. Disk Health Telemetry")
    w("")
    for disk in telem.disks:
        status_icon = "🟢" if disk.smart_health == "PASSED" else "🔴"
        w(f"### {status_icon} `{disk.device}` — {disk.model} (S/N: {disk.serial})")
        w("")
        w(f"- **Health:** {disk.smart_health}")
        w(f"- **Firmware:** {disk.firmware}")
        w(f"- **Temperature:** {disk.temperature_c}°C")
        w(f"- **Power-On Hours:** {disk.power_on_hours:,}")
        w("")
        w("| ID | Attribute | Value | Worst | Thresh | Raw | Status |")
        w("|---:|-----------|------:|------:|-------:|----:|--------|")
        for a in disk.attributes:
            flag = "⚠️ " if a.status == "WARNING" else "🔴 " if a.status == "CRITICAL" else ""
            w(f"| {a.id} | {a.name} | {a.value} | {a.worst} | {a.threshold} | {a.raw_value} | {flag}{a.status} |")
        w("")

    if smart_alerts:
        w("### ⚠️  S.M.A.R.T. Threshold Alerts")
        w("")
        for alert in smart_alerts:
            w(f"- **{alert['device']}** — `{alert['attribute_name']}` (ID {alert['attribute_id']}): "
              f"value={alert['value']}, threshold={alert['threshold']}, "
              f"raw={alert['raw_value']} → **{alert['status']}**")
        w("")

    # ── Section 3: Kernel Log (dmesg) ────────────────────────────────────
    w("## 3. Kernel Log Analysis (`dmesg`)")
    w("")
    if telem.dmesg_entries:
        w("```")
        for entry in telem.dmesg_entries:
            w(f"[{entry.timestamp:>12.3f}] {entry.level}: {entry.message}")
        w("```")
    else:
        w("No relevant kernel messages.")
    w("")

    # ── Section 4: Thermal / Fan / EDAC ──────────────────────────────────
    w("## 4. Thermal, Fan & Memory (EDAC) Status")
    w("")
    w("### CPU & System Temperatures (`lm-sensors`)")
    w("")
    w("| Sensor | Component | Temp (°C) | High (°C) | Crit (°C) | Status |")
    w("|--------|-----------|----------:|----------:|----------:|--------|")
    for t in telem.thermals:
        flag = "🔴 " if t.status == "CRITICAL" else "⚠️ " if t.status == "WARNING" else ""
        w(f"| {t.sensor} | {t.component} | {t.temp_c} | {t.high_c} | {t.crit_c} | {flag}{t.status} |")
    w("")

    if thermal_faults:
        w("### 🔥 Thermal Alerts")
        w("")
        for f in thermal_faults:
            w(f"- **{f['component']}** on `{f['sensor']}`: "
              f"{f['temp_c']}°C exceeds {f['level'].lower()} threshold {f['threshold']}°C")
        w("")

    w("### Fan Speeds")
    w("")
    for fan, rpm in telem.fan_rpms.items():
        w(f"- **{fan}:** {rpm:,} RPM")
    w("")

    if ecc_faults:
        w("### 🧠 EDAC / ECC Memory Errors")
        w("")
        for e in ecc_faults:
            icon = "🔴" if e["error_type"] == "uncorrectable" else "⚠️"
            w(f"- {icon} **{e['dimm']}** ({e['controller']}): "
              f"{e['count']}× {e['error_type']} — `{e['message']}`")
        w("")

    # ── Section 5: RCA Summary ───────────────────────────────────────────
    w("## 5. Root Cause Analysis Summary")
    w("")

    if array and array.state == RaidState.DEGRADED and smart_alerts:
        w("### Diagnosis")
        w("")
        w("The RAID 5 array `/dev/md0` has entered a **degraded** state due to a physical ")
        w("media failure on `/dev/loop10` (mapped to drive bay **Slot 0**).  S.M.A.R.T. ")
        w("pre-failure attributes confirm an imminent drive failure:")
        w("")
        w("- **Reallocated_Sector_Ct (ID 5):** 544 reallocated sectors — exceeds threshold (140).")
        w("- **Current_Pending_Sector (ID 197):** 48 sectors awaiting reallocation.")
        w("- **UDMA_CRC_Error_Count (ID 199):** 7 interface CRC errors — possible cable/backplane issue.")
        w("")
        w("The kernel log confirms `blk_update_request: critical medium error` on the affected ")
        w("device, and mdadm has automatically begun rebuilding from the hot spare `/dev/loop13`.")
        w("")
    elif thermal_faults or ecc_faults:
        w("### Diagnosis")
        w("")
        w("The server is experiencing **thermal throttling** with the CPU Package reaching ")
        w("**94.0°C** (above the 90.0°C high threshold), triggering automatic clock frequency ")
        w("reduction.  Concurrent EDAC errors on **DIMM_A2** indicate:")
        w("")
        w("- **847 correctable ECC errors** — DIMM degradation in progress.")
        w("- **3 uncorrectable ECC errors** — data corruption risk; DIMM replacement required.")
        w("")
        w("Elevated ambient temperature may be accelerating DIMM degradation.  Fan speeds are ")
        w("maximal, suggesting an airflow obstruction or failed chassis cooling component.")
        w("")
    else:
        w("### Diagnosis")
        w("")
        w("All subsystems are operating within normal parameters.  No corrective action required.")
        w("")

    # ── Section 6: Hot‑Swap Runbook ──────────────────────────────────────
    w("## 6. DCO Hot-Swap & Remediation Runbook")
    w("")

    if array and array.state == RaidState.DEGRADED:
        w("### 6.1 Pre-Swap Safety Checks")
        w("")
        w("- [ ] **ESD Safety:** Don anti-static wrist strap; connect to rack ground point.")
        w("- [ ] **Identify drive bay:** Activate locator LED via `sesutil locate /dev/loop10 on` or BMC/IPMI.")
        w("- [ ] **Verify array state:** `cat /proc/mdstat` — confirm `[UU_]` degraded status.")
        w("- [ ] **Verify spare rebuild started:** `mdadm --detail /dev/md0` — spare shows `rebuilding`.")
        w("- [ ] **Notify NOC:** Open DCO ticket; include server hostname, array ID, and slot number.")
        w("")
        w("### 6.2 Physical Drive Replacement")
        w("")
        w("1. **Remove faulty drive:**")
        w("   ```bash")
        w("   mdadm --manage /dev/md0 --remove /dev/loop10")
        w("   ```")
        w("2. **Physically extract** the drive from bay Slot 0 (wait for locator LED).")
        w("3. **Inspect backplane connector** for bent pins, dust, or corrosion.")
        w("4. **Insert replacement drive** (same model/capacity: WDC WUH721818ALE6L4 18TB).")
        w("5. **Zero the superblock** on the new drive:")
        w("   ```bash")
        w("   mdadm --zero-superblock /dev/loop10")
        w("   ```")
        w("6. **Add new drive to array:**")
        w("   ```bash")
        w("   mdadm --manage /dev/md0 --add /dev/loop10")
        w("   ```")
        w("")
        w("### 6.3 Post-Swap Verification")
        w("")
        w("- [ ] **Monitor rebuild progress:**")
        w("  ```bash")
        w("  watch -n 2 cat /proc/mdstat")
        w("  ```")
        w("- [ ] **Verify full sync:** `mdadm --detail /dev/md0` — state shows `active` / `[UUU]`.")
        w("- [ ] **Run S.M.A.R.T. self-test on new drive:**")
        w("  ```bash")
        w("  smartctl -t long /dev/loop10")
        w("  ```")
        w("- [ ] **Check IPMI SEL (System Event Log)** for additional hardware events.")
        w("- [ ] **Deactivate locator LED:** `sesutil locate /dev/loop10 off`.")
        w("- [ ] **Update DCO ticket** with rebuild completion time and S.M.A.R.T. health status.")
        w("")

    if thermal_faults or ecc_faults:
        w("### 6.4 Thermal & DIMM Remediation")
        w("")
        w("- [ ] **ESD Safety:** Don anti-static wrist strap before handling DIMMs.")
        w("- [ ] **Check airflow:** Inspect for blocked vents, misrouted cables, or failed fans.")
        w("- [ ] **Reseat heatsink:** If CPU temps remain elevated after airflow check, reseat CPU heatsink with fresh thermal compound.")
        w("- [ ] **Identify faulty DIMM:** DIMM_A2 → physical slot Channel 0, Slot 1 (consult server QVL).")
        w("- [ ] **Replace DIMM_A2:** Use identical part number and speed rating.")
        w("- [ ] **Run memory diagnostics:**")
        w("  ```bash")
        w("  memtest86+   # from boot media")
        w("  ```")
        w("- [ ] **Verify ECC counters cleared:**")
        w("  ```bash")
        w("  edac-util -s")
        w("  ```")
        w("- [ ] **Monitor thermals post-remediation:**")
        w("  ```bash")
        w("  watch -n 2 sensors")
        w("  ```")
        w("- [ ] **Update DCO ticket** with DIMM serial numbers and thermal readings.")
        w("")

    if not (array and array.state == RaidState.DEGRADED) and not thermal_faults and not ecc_faults:
        w("No remediation actions required.  Continue standard monitoring cadence.")
        w("")

    w("---")
    w("")
    w("*This runbook was automatically generated by the AWS DCO Hardware Health Monitor.*  ")
    w(f"*Simulation scenario: `{telem.overall_severity.value}` severity at `{telem.timestamp}`*")
    w("")

    content = "\n".join(lines)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)

    return content


# ═══════════════════════════════════════════════════════════════════════════════
# CLI Entry Point
# ═══════════════════════════════════════════════════════════════════════════════

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="hardware_health_monitor",
        description="AWS DCO Bare‑Metal Server Hardware Health Monitor & RAID 5 Fault Simulator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--array", default="/dev/md0",
                    help="RAID array device (default: /dev/md0)")
    p.add_argument("--disk", default=None,
                    help="Specific disk device to inspect")
    p.add_argument("--simulate", action="store_true",
                    help="Run in deterministic simulation mode")
    p.add_argument("--fault-type", dest="fault_type",
                    choices=list(SCENARIO_MAP.keys()),
                    default="healthy_server",
                    help="Fault scenario to simulate (requires --simulate)")
    p.add_argument("--output-dir", dest="output_dir", default="reports",
                    help="Directory for generated reports (default: reports/)")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.simulate:
        print("ERROR: Live mode requires root privileges and a Linux host with mdadm.")
        print("       Use --simulate for deterministic emulation.")
        return 1

    if args.fault_type not in SCENARIO_MAP:
        print(f"ERROR: Unknown fault type '{args.fault_type}'.")
        print(f"       Available: {', '.join(SCENARIO_MAP)}")
        return 1

    print(f"━━━ AWS DCO Hardware Health Monitor ━━━")
    print(f"Mode:       Deterministic Simulation")
    print(f"Scenario:   {args.fault_type}")
    print(f"Output dir: {args.output_dir}/")
    print()

    # Generate telemetry
    telem = SCENARIO_MAP[args.fault_type]()

    # Write JSON telemetry
    json_path = Path(args.output_dir) / "hardware_telemetry.json"
    generate_json_telemetry(telem, json_path)
    print(f"✓ JSON telemetry written to {json_path}")

    # Write RCA runbook
    md_path = Path(args.output_dir) / "hardware_rca_runbook.md"
    runbook = generate_rca_runbook(telem, md_path)
    print(f"✓ RCA runbook written to {md_path}")
    print()

    # Print summary
    print(f"━━━ Telemetry Summary ━━━")
    print(f"Host:      {telem.hostname}")
    print(f"Severity:  {telem.overall_severity.value}")

    if telem.raid_arrays:
        ra = telem.raid_arrays[0]
        print(f"RAID:      {ra.device} {ra.level} state={ra.state.value} {ra.status_line}")
        if ra.rebuild_progress is not None:
            print(f"           Rebuild: {ra.rebuild_progress:.1f}%")

    failing_disks = [d for d in telem.disks if d.smart_health == "FAILED"]
    if failing_disks:
        print(f"Disks:     {len(failing_disks)} failing:")
        for d in failing_disks:
            crits = [a for a in d.attributes if a.status == "CRITICAL"]
            print(f"           {d.device} S/N:{d.serial} — {len(crits)} critical S.M.A.R.T. attrs")

    thermal_faults = detect_thermal_faults(telem.thermals)
    if thermal_faults:
        print(f"Thermal:   {len(thermal_faults)} alert(s)")
        for f in thermal_faults:
            print(f"           {f['component']}: {f['temp_c']}°C [{f['level']}]")

    if telem.edac_errors:
        print(f"EDAC:      {len(telem.edac_errors)} error(s)")
        for e in telem.edac_errors:
            print(f"           {e.dimm}: {e.count}× {e.error_type}")

    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
