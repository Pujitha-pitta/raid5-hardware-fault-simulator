# RAID 5 Hardware Fault & Storage Telemetry Simulator

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](#license)
[![Python 3.8+](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://www.python.org/)
[![Platform: Linux](https://img.shields.io/badge/platform-Linux%20%7C%20Any%20(sim)-lightgrey.svg)](#)

> A **Bare-Metal Server Storage, S.M.A.R.T. Disk Telemetry & RAID 5 Fault-Recovery Simulator** for enterprise systems engineering. Supports both **live Linux kernel-mode** orchestration and a **deterministic simulation mode** that runs anywhere — no root, no block devices required.

---

## Table of Contents

- [Architecture Overview](#architecture-overview)
- [RAID 5 Concepts](#raid-5-concepts)
- [S.M.A.R.T. Failure Indicators](#smart-failure-indicators)
- [Kernel Log (dmesg) Analysis](#kernel-log-dmesg-analysis)
- [Directory Structure](#directory-structure)
- [Quick Start](#quick-start)
- [Live Kernel Mode](#live-kernel-mode)
- [Simulation Mode](#simulation-mode)
- [Running Tests](#running-tests)
- [Generated Reports](#generated-reports)
- [License](#license)

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                   RAID 5 Hardware Fault Simulator                   │
├────────────────────────────┬────────────────────────────────────────┤
│   Live Kernel Mode (Bash)  │      Simulation Mode (Python)         │
│                            │                                        │
│  loopback devices          │  Deterministic telemetry generators    │
│  /dev/loop10–/dev/loop13   │  ├── RAID state emulation             │
│  mdadm RAID 5 (/dev/md0)  │  ├── S.M.A.R.T. attribute synthesis   │
│  dmesg kernel log capture  │  ├── dmesg log simulation             │
│  /proc/mdstat monitoring   │  ├── lm-sensors thermal data          │
│  Hot-swap recovery         │  └── EDAC ECC memory errors           │
│                            │                                        │
│  Requires: Linux, root     │  Requires: Python 3.8+ only           │
├────────────────────────────┴────────────────────────────────────────┤
│                        Output Artifacts                             │
│  ├── reports/hardware_rca_runbook.md   (Markdown RCA & Runbook)    │
│  └── reports/hardware_telemetry.json   (Machine-readable JSON)     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## RAID 5 Concepts

### Striping with Distributed Parity

RAID 5 distributes data **and** parity blocks across all member disks. Unlike RAID 4 (which dedicates one disk to parity), RAID 5 distributes parity evenly, eliminating the parity-disk bottleneck.

```
Stripe 0:  │ D0  │ D1  │ P0  │      ← parity on disk 2
Stripe 1:  │ D2  │ P1  │ D3  │      ← parity on disk 1
Stripe 2:  │ P2  │ D4  │ D5  │      ← parity on disk 0
```

- **Minimum disks:** 3
- **Usable capacity:** (N − 1) × disk size
- **Fault tolerance:** Survives 1 disk failure; data is reconstructed from the remaining data + parity blocks via XOR.

### Hot-Spare Rebuild Mechanics

A **hot spare** is an idle disk pre-assigned to the array. When mdadm detects a failed member, it automatically activates the spare and begins a **rebuild** (also called *resync* or *recovery*):

1. Kernel marks the failed disk as `(F)` → array enters **degraded** state `[UU_]`.
2. mdadm activates the spare → begins parity-driven reconstruction onto the spare.
3. Rebuild progress is visible in `/proc/mdstat` (e.g., `recovery = 34.7%`).
4. Once complete, the array returns to **active** `[UUU]`.
5. Systems engineer performs physical hot-swap of the failed drive and re-adds it as the new spare.

---

## S.M.A.R.T. Failure Indicators

Self-Monitoring, Analysis and Reporting Technology (S.M.A.R.T.) provides early warning of drive failures. The three most critical pre-failure attributes monitored by this simulator:

| ID  | Attribute                | What It Means | Alert When |
|----:|--------------------------|---------------|------------|
|   5 | `Reallocated_Sector_Ct`  | Sectors remapped to spare area due to defects | `value ≤ threshold` or `raw > 0` increasing |
| 197 | `Current_Pending_Sector` | Sectors awaiting reallocation on next write | `raw > 0` |
| 199 | `UDMA_CRC_Error_Count`   | Interface communication errors (cable/backplane) | `raw > 0` increasing |

Additional monitoring:
- **ID 1 `Raw_Read_Error_Rate`** — media read retries.
- **ID 7 `Seek_Error_Rate`** — head positioning failures.
- **ID 194 `Temperature_Celsius`** — drive operating temperature.

---

## Kernel Log (dmesg) Analysis

When a disk fails, the Linux kernel logs critical events:

```
[84291.000] err: blk_update_request: critical medium error, dev sdb, sector 4096
[84291.001] err: md/raid:md0: Disk failure on loop10, disabling device.
[84291.002] warning: md/raid:md0: Operation continuing on 2 devices.
[84291.210] info: md: recovery of RAID array md0
```

Key patterns to watch in `dmesg`:
- `blk_update_request: critical medium error` — unrecoverable read error from the block layer.
- `md/raid:md0: Disk failure` — mdadm has marked a component device as faulty.
- `md: recovery of RAID array` — rebuild has been initiated.
- `CPU0: Package temperature above threshold` — thermal throttling engaged.
- `EDAC MC0: CE/UE error` — memory ECC correctable/uncorrectable error.

---

## Directory Structure

```
raid5-hardware-fault-simulator/
├── README.md                           # This file
├── scripts/
│   └── raid5_lab_orchestrator.sh       # Live kernel-mode RAID 5 lab
├── src/
│   └── hardware_health_monitor.py      # Python CLI simulator
├── tests/
│   └── test_hardware_monitor.py        # Unit test suite
└── reports/                            # Generated output (gitignored)
    ├── hardware_rca_runbook.md         # RCA & Hot-Swap Runbook
    └── hardware_telemetry.json         # Full telemetry dump
```

---

## Quick Start

```bash
# Clone the repository
git clone https://github.com/Pujitha-pitta/raid5-hardware-fault-simulator.git
cd raid5-hardware-fault-simulator

# Run the simulator (no root, no Linux required)
python3 src/hardware_health_monitor.py --simulate --fault-type raid_degraded_smart_fail

# Run all unit tests
python3 -m unittest discover -s tests -v

# View the generated reports
cat reports/hardware_rca_runbook.md
cat reports/hardware_telemetry.json
```

---

## Live Kernel Mode

> **⚠️ Requires:** Linux host with `mdadm`, `losetup`, and root privileges.

The Bash orchestrator creates a real RAID 5 array using loopback block devices:

```bash
# Full lab run (creates 4 × 100MB virtual disks, RAID 5, fault injection, recovery)
sudo bash scripts/raid5_lab_orchestrator.sh

# Clean up all resources (stop array, detach loops, delete images)
sudo bash scripts/raid5_lab_orchestrator.sh --cleanup
```

### What It Does

| Phase | Action |
|------:|--------|
| 1 | Creates 4 × 100 MB disk images (`disk0.img` – `disk3.img`) attached to `/dev/loop10`–`/dev/loop13` |
| 2 | Assembles RAID 5 array `/dev/md0` with 3 active devices + 1 hot spare |
| 3 | Injects fault: `mdadm --manage /dev/md0 --fail /dev/loop10` |
| 4 | Hot-swap recovery: remove → zero-superblock → recreate image → re-add |
| 5 | Monitors rebuild to completion |

---

## Simulation Mode

The Python simulator emulates all hardware telemetry deterministically:

```bash
# Degraded RAID 5 with S.M.A.R.T. disk failure
python3 src/hardware_health_monitor.py \
    --simulate \
    --fault-type raid_degraded_smart_fail \
    --output-dir reports/

# CPU thermal throttling + DIMM ECC errors
python3 src/hardware_health_monitor.py \
    --simulate \
    --fault-type thermal_dimm_fault

# Healthy baseline (all systems nominal)
python3 src/hardware_health_monitor.py \
    --simulate \
    --fault-type healthy_server
```

### Available Fault Scenarios

| Scenario | Description |
|----------|-------------|
| `raid_degraded_smart_fail` | RAID 5 in `[UU_]` degraded state, S.M.A.R.T. failure on disk 0 (IDs 5, 197, 199 critical), `dmesg` showing `blk_update_request: critical medium error`, hot spare rebuilding at 34.7% |
| `thermal_dimm_fault` | CPU Package at 94.0°C (thermal throttling), EDAC reporting 847 CE + 3 UE on DIMM_A2, healthy RAID array |
| `healthy_server` | Clean `[UUU]` array, all S.M.A.R.T. attributes normal, nominal temperatures and fan speeds |

### CLI Options

| Flag | Default | Description |
|------|---------|-------------|
| `--simulate` | *(required)* | Enable deterministic simulation mode |
| `--fault-type` | `healthy_server` | Fault scenario to simulate |
| `--array` | `/dev/md0` | RAID array device path |
| `--disk` | `None` | Specific disk to inspect |
| `--output-dir` | `reports/` | Output directory for generated reports |

---

## Running Tests

```bash
# Discover and run all tests
python3 -m unittest discover -s tests -v

# Run a specific test class
python3 -m unittest tests.test_hardware_monitor.TestSmartThresholdAlerting -v
```

### Test Coverage

| Test Class | What It Validates |
|------------|-------------------|
| `TestRaidBitmapParsing` | RAID status parsing: `[UUU]`, `[UU_]`, `[U__]`, `[___]`, `[UUUU]` |
| `TestSmartThresholdAlerting` | S.M.A.R.T. attribute threshold evaluation and alerting |
| `TestThermalFaultDetection` | CPU/system thermal threshold detection |
| `TestEccFaultDetection` | EDAC/ECC correctable and uncorrectable error detection |
| `TestSimulationScenarios` | End-to-end validation of all three fault scenarios |
| `TestRcaRunbookGeneration` | RCA runbook content, Markdown structure, and file creation |
| `TestJsonTelemetryOutput` | JSON output validity, enum serialization, all scenarios |
| `TestCliInterface` | Argument parsing, return codes, output file creation |
| `TestRaidArrayStatus` | Model properties: `bitmap`, `is_healthy()` |

---

## Generated Reports

### Hardware RCA & Hot-Swap Runbook (`hardware_rca_runbook.md`)

A comprehensive Markdown document containing:

1. **RAID Array Status** — simulated `/proc/mdstat` and `mdadm --detail` output
2. **S.M.A.R.T. Disk Health** — per-disk attribute tables with threshold alerts
3. **Kernel Log Analysis** — simulated `dmesg` entries
4. **Thermal, Fan & ECC Status** — `lm-sensors` readings and EDAC error reports
5. **Root Cause Analysis Summary** — automated diagnosis
6. **Hot-Swap Runbook** — step-by-step checklist including:
   - ESD safety checks
   - Drive bay locator LED verification
   - Physical replacement procedure
   - RAID rebuild monitoring
   - Post-swap S.M.A.R.T. self-test
   - DIMM/CPU thermal remediation (when applicable)

### Hardware Telemetry JSON (`hardware_telemetry.json`)

Machine-readable dump of all telemetry data for integration with monitoring systems (Prometheus, Datadog, Grafana, etc.).

---

## License

MIT License — see [LICENSE](LICENSE) for details.
