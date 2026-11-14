#!/usr/bin/env python3
"""
test_hardware_monitor.py
──────────────────────────────────────────────────────────────────────────────
Unit test suite for the AWS DCO Hardware Health Monitor.

Validates:
  • RAID state bitmap parsing ([UUU] vs [UU_])
  • S.M.A.R.T. threshold alerting
  • Thermal / ECC fault detection
  • Simulation scenario integrity
  • RCA runbook generation
  • CLI argument parsing
  • JSON telemetry output
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

# Ensure src/ is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from hardware_health_monitor import (
    DiskTelemetry,
    DmesgEntry,
    EdacError,
    RaidArrayStatus,
    RaidState,
    Severity,
    ServerTelemetry,
    SmartAttribute,
    ThermalReading,
    build_parser,
    detect_ecc_faults,
    detect_thermal_faults,
    evaluate_smart_thresholds,
    generate_json_telemetry,
    generate_rca_runbook,
    main,
    parse_raid_bitmap,
    simulate_healthy_server,
    simulate_raid_degraded_smart_fail,
    simulate_thermal_dimm_fault,
)


class TestRaidBitmapParsing(unittest.TestCase):
    """Test parse_raid_bitmap for various RAID status strings."""

    def test_healthy_three_disk(self):
        result = parse_raid_bitmap("[UUU]")
        self.assertEqual(result["bitmap"], "UUU")
        self.assertEqual(result["up"], 3)
        self.assertEqual(result["down"], 0)

    def test_degraded_one_disk(self):
        result = parse_raid_bitmap("[UU_]")
        self.assertEqual(result["bitmap"], "UU_")
        self.assertEqual(result["up"], 2)
        self.assertEqual(result["down"], 1)

    def test_degraded_two_disks(self):
        result = parse_raid_bitmap("[U__]")
        self.assertEqual(result["bitmap"], "U__")
        self.assertEqual(result["up"], 1)
        self.assertEqual(result["down"], 2)

    def test_fully_failed(self):
        result = parse_raid_bitmap("[___]")
        self.assertEqual(result["bitmap"], "___")
        self.assertEqual(result["up"], 0)
        self.assertEqual(result["down"], 3)

    def test_embedded_in_text(self):
        line = "204800 blocks super 1.2 level 5, 512k chunk, algorithm 2 [UU_]"
        result = parse_raid_bitmap(line)
        self.assertEqual(result["up"], 2)
        self.assertEqual(result["down"], 1)

    def test_no_bitmap(self):
        result = parse_raid_bitmap("no bracket content here")
        self.assertEqual(result["bitmap"], "")
        self.assertEqual(result["up"], 0)
        self.assertEqual(result["down"], 0)

    def test_four_disk_raid6(self):
        result = parse_raid_bitmap("[UUUU]")
        self.assertEqual(result["up"], 4)
        self.assertEqual(result["down"], 0)


class TestSmartThresholdAlerting(unittest.TestCase):
    """Test S.M.A.R.T. attribute threshold evaluation."""

    def _make_disk(self, attrs):
        return DiskTelemetry(
            device="/dev/sda", model="Test", serial="T001",
            firmware="1.0", smart_health="PASSED",
            temperature_c=35, power_on_hours=1000,
            attributes=attrs,
        )

    def test_healthy_disk_no_alerts(self):
        attrs = [
            SmartAttribute(5, "Reallocated_Sector_Ct", 200, 200, 140, 0, "OK"),
            SmartAttribute(197, "Current_Pending_Sector", 200, 200, 0, 0, "OK"),
        ]
        disk = self._make_disk(attrs)
        alerts = evaluate_smart_thresholds(disk)
        self.assertEqual(len(alerts), 0)

    def test_critical_reallocated_sectors(self):
        attrs = [
            SmartAttribute(5, "Reallocated_Sector_Ct", 132, 132, 140, 544, "CRITICAL"),
        ]
        disk = self._make_disk(attrs)
        alerts = evaluate_smart_thresholds(disk)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["attribute_id"], 5)
        self.assertEqual(alerts[0]["status"], "CRITICAL")

    def test_value_at_threshold_boundary(self):
        """Value == threshold should trigger (is_failing returns True when <=)."""
        attrs = [
            SmartAttribute(5, "Reallocated_Sector_Ct", 140, 140, 140, 10, "WARNING"),
        ]
        disk = self._make_disk(attrs)
        alerts = evaluate_smart_thresholds(disk)
        self.assertEqual(len(alerts), 1)

    def test_multiple_failing_attributes(self):
        attrs = [
            SmartAttribute(5, "Reallocated_Sector_Ct", 132, 132, 140, 544, "CRITICAL"),
            SmartAttribute(197, "Current_Pending_Sector", 196, 196, 0, 48, "CRITICAL"),
            SmartAttribute(199, "UDMA_CRC_Error_Count", 198, 198, 0, 7, "CRITICAL"),
        ]
        disk = self._make_disk(attrs)
        alerts = evaluate_smart_thresholds(disk)
        self.assertEqual(len(alerts), 3)

    def test_failing_attributes_method(self):
        attrs = [
            SmartAttribute(5, "Reallocated_Sector_Ct", 132, 132, 140, 544, "CRITICAL"),
            SmartAttribute(9, "Power_On_Hours", 97, 97, 0, 12847, "OK"),
        ]
        disk = self._make_disk(attrs)
        failing = disk.failing_attributes()
        self.assertEqual(len(failing), 1)
        self.assertEqual(failing[0].id, 5)


class TestThermalFaultDetection(unittest.TestCase):
    """Test thermal threshold detection."""

    def test_normal_temperatures(self):
        readings = [
            ThermalReading("coretemp", "CPU Package", 62.0, 90.0, 100.0, "OK"),
            ThermalReading("coretemp", "Core 0", 58.0, 90.0, 100.0, "OK"),
        ]
        faults = detect_thermal_faults(readings)
        self.assertEqual(len(faults), 0)

    def test_warning_temperature(self):
        readings = [
            ThermalReading("coretemp", "CPU Package", 92.0, 90.0, 100.0, "WARNING"),
        ]
        faults = detect_thermal_faults(readings)
        self.assertEqual(len(faults), 1)
        self.assertEqual(faults[0]["level"], "WARNING")

    def test_critical_temperature(self):
        readings = [
            ThermalReading("coretemp", "CPU Package", 101.0, 90.0, 100.0, "CRITICAL"),
        ]
        faults = detect_thermal_faults(readings)
        self.assertEqual(len(faults), 1)
        self.assertEqual(faults[0]["level"], "CRITICAL")

    def test_exactly_at_high_threshold(self):
        readings = [
            ThermalReading("coretemp", "CPU Package", 90.0, 90.0, 100.0, "WARNING"),
        ]
        faults = detect_thermal_faults(readings)
        self.assertEqual(len(faults), 1)

    def test_mixed_temperatures(self):
        readings = [
            ThermalReading("coretemp", "CPU Package", 94.0, 90.0, 100.0, "CRITICAL"),
            ThermalReading("coretemp", "Core 0", 58.0, 90.0, 100.0, "OK"),
            ThermalReading("nct6798", "System Temp", 46.0, 75.0, 85.0, "WARNING"),
        ]
        faults = detect_thermal_faults(readings)
        self.assertEqual(len(faults), 1)  # Only CPU Package is above threshold


class TestEccFaultDetection(unittest.TestCase):
    """Test EDAC/ECC error detection."""

    def test_no_errors(self):
        faults = detect_ecc_faults([])
        self.assertEqual(len(faults), 0)

    def test_correctable_error(self):
        errors = [
            EdacError("mc0", "DIMM_A2", "correctable", 847, "CE error on DIMM_A2"),
        ]
        faults = detect_ecc_faults(errors)
        self.assertEqual(len(faults), 1)
        self.assertEqual(faults[0]["error_type"], "correctable")
        self.assertEqual(faults[0]["count"], 847)

    def test_uncorrectable_error(self):
        errors = [
            EdacError("mc0", "DIMM_A2", "uncorrectable", 3, "UE error on DIMM_A2"),
        ]
        faults = detect_ecc_faults(errors)
        self.assertEqual(len(faults), 1)
        self.assertEqual(faults[0]["error_type"], "uncorrectable")

    def test_multiple_errors(self):
        errors = [
            EdacError("mc0", "DIMM_A2", "correctable", 847, "CE"),
            EdacError("mc0", "DIMM_A2", "uncorrectable", 3, "UE"),
        ]
        faults = detect_ecc_faults(errors)
        self.assertEqual(len(faults), 2)


class TestSimulationScenarios(unittest.TestCase):
    """Validate each simulation scenario produces correct data."""

    def test_raid_degraded_smart_fail(self):
        telem = simulate_raid_degraded_smart_fail()

        self.assertEqual(telem.overall_severity, Severity.CRITICAL)
        self.assertEqual(len(telem.raid_arrays), 1)

        ra = telem.raid_arrays[0]
        self.assertEqual(ra.state, RaidState.DEGRADED)
        self.assertEqual(ra.status_line, "[UU_]")
        self.assertEqual(ra.failed_devices, 1)
        self.assertIsNotNone(ra.rebuild_progress)
        self.assertFalse(ra.is_healthy())

        bitmap = parse_raid_bitmap(ra.status_line)
        self.assertEqual(bitmap["up"], 2)
        self.assertEqual(bitmap["down"], 1)

        # Check failing disk
        failing_disk = telem.disks[0]
        self.assertEqual(failing_disk.smart_health, "FAILED")
        failing_attrs = failing_disk.failing_attributes()
        self.assertGreater(len(failing_attrs), 0)

        # Check critical S.M.A.R.T. attribute IDs
        critical_ids = {a.id for a in failing_attrs if a.status == "CRITICAL"}
        self.assertIn(5, critical_ids)     # Reallocated_Sector_Ct
        self.assertIn(197, critical_ids)   # Current_Pending_Sector
        self.assertIn(199, critical_ids)   # UDMA_CRC_Error_Count

        # Check dmesg has medium error
        dmesg_text = " ".join(e.message for e in telem.dmesg_entries)
        self.assertIn("critical medium error", dmesg_text)
        self.assertIn("blk_update_request", dmesg_text)

    def test_thermal_dimm_fault(self):
        telem = simulate_thermal_dimm_fault()

        self.assertEqual(telem.overall_severity, Severity.CRITICAL)

        # RAID should be healthy
        ra = telem.raid_arrays[0]
        self.assertEqual(ra.state, RaidState.ACTIVE)
        self.assertTrue(ra.is_healthy())

        # Thermals should show CPU overheating
        thermal_faults = detect_thermal_faults(telem.thermals)
        self.assertGreater(len(thermal_faults), 0)
        cpu_fault = next(f for f in thermal_faults if "CPU Package" in f["component"])
        self.assertEqual(cpu_fault["temp_c"], 94.0)

        # EDAC errors should be present
        self.assertGreater(len(telem.edac_errors), 0)
        dimm_errors = [e for e in telem.edac_errors if e.dimm == "DIMM_A2"]
        self.assertGreater(len(dimm_errors), 0)

        # Should have both correctable and uncorrectable
        error_types = {e.error_type for e in telem.edac_errors}
        self.assertIn("correctable", error_types)
        self.assertIn("uncorrectable", error_types)

    def test_healthy_server(self):
        telem = simulate_healthy_server()

        self.assertEqual(telem.overall_severity, Severity.OK)

        ra = telem.raid_arrays[0]
        self.assertEqual(ra.state, RaidState.ACTIVE)
        self.assertEqual(ra.status_line, "[UUU]")
        self.assertTrue(ra.is_healthy())
        self.assertEqual(ra.failed_devices, 0)

        # All disks should be healthy
        for disk in telem.disks:
            self.assertEqual(disk.smart_health, "PASSED")
            self.assertEqual(len(disk.failing_attributes()), 0)

        # No thermal faults
        thermal_faults = detect_thermal_faults(telem.thermals)
        self.assertEqual(len(thermal_faults), 0)

        # No ECC errors
        self.assertEqual(len(telem.edac_errors), 0)


class TestRcaRunbookGeneration(unittest.TestCase):
    """Test that RCA runbook generation works for all scenarios."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def test_degraded_runbook_content(self):
        telem = simulate_raid_degraded_smart_fail()
        path = Path(self.tmpdir) / "runbook.md"
        content = generate_rca_runbook(telem, path)

        self.assertTrue(path.exists())
        self.assertIn("Root Cause Analysis", content)
        self.assertIn("[UU_]", content)
        self.assertIn("Reallocated_Sector_Ct", content)
        self.assertIn("Hot-Swap", content)
        self.assertIn("ESD Safety", content)
        self.assertIn("locator LED", content)
        self.assertIn("mdadm --manage", content)
        self.assertIn("blk_update_request", content)
        self.assertIn("critical medium error", content)

    def test_thermal_runbook_content(self):
        telem = simulate_thermal_dimm_fault()
        path = Path(self.tmpdir) / "runbook.md"
        content = generate_rca_runbook(telem, path)

        self.assertTrue(path.exists())
        self.assertIn("94.0°C", content)
        self.assertIn("DIMM_A2", content)
        self.assertIn("correctable", content.lower())
        self.assertIn("uncorrectable", content.lower())
        self.assertIn("ESD Safety", content)
        self.assertIn("memtest86", content)

    def test_healthy_runbook_content(self):
        telem = simulate_healthy_server()
        path = Path(self.tmpdir) / "runbook.md"
        content = generate_rca_runbook(telem, path)

        self.assertTrue(path.exists())
        self.assertIn("[UUU]", content)
        self.assertIn("No corrective action required", content)

    def test_runbook_is_valid_markdown(self):
        telem = simulate_raid_degraded_smart_fail()
        path = Path(self.tmpdir) / "runbook.md"
        content = generate_rca_runbook(telem, path)

        # Check for proper Markdown heading structure
        self.assertTrue(content.startswith("# "))
        self.assertIn("## ", content)
        # Check for code blocks
        self.assertIn("```", content)
        # Check for table formatting
        self.assertIn("|", content)


class TestJsonTelemetryOutput(unittest.TestCase):
    """Test JSON telemetry output."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def test_json_output_valid(self):
        telem = simulate_raid_degraded_smart_fail()
        path = Path(self.tmpdir) / "telemetry.json"
        generate_json_telemetry(telem, path)

        self.assertTrue(path.exists())
        with open(path) as f:
            data = json.load(f)

        self.assertIn("hostname", data)
        self.assertIn("raid_arrays", data)
        self.assertIn("disks", data)
        self.assertIn("thermals", data)

    def test_json_enum_serialization(self):
        telem = simulate_raid_degraded_smart_fail()
        path = Path(self.tmpdir) / "telemetry.json"
        generate_json_telemetry(telem, path)

        with open(path) as f:
            data = json.load(f)

        # Enums should be serialized as strings
        self.assertEqual(data["overall_severity"], "CRITICAL")
        self.assertEqual(data["raid_arrays"][0]["state"], "degraded")

    def test_all_scenarios_produce_valid_json(self):
        for name, gen_fn in [
            ("degraded", simulate_raid_degraded_smart_fail),
            ("thermal", simulate_thermal_dimm_fault),
            ("healthy", simulate_healthy_server),
        ]:
            with self.subTest(scenario=name):
                telem = gen_fn()
                path = Path(self.tmpdir) / f"{name}.json"
                generate_json_telemetry(telem, path)
                with open(path) as f:
                    data = json.load(f)
                self.assertIsInstance(data, dict)


class TestCliInterface(unittest.TestCase):
    """Test CLI argument parsing."""

    def test_default_args(self):
        parser = build_parser()
        args = parser.parse_args(["--simulate"])
        self.assertTrue(args.simulate)
        self.assertEqual(args.fault_type, "healthy_server")
        self.assertEqual(args.array, "/dev/md0")
        self.assertEqual(args.output_dir, "reports")

    def test_custom_fault_type(self):
        parser = build_parser()
        args = parser.parse_args(["--simulate", "--fault-type", "raid_degraded_smart_fail"])
        self.assertEqual(args.fault_type, "raid_degraded_smart_fail")

    def test_custom_output_dir(self):
        parser = build_parser()
        args = parser.parse_args(["--simulate", "--output-dir", "/tmp/test_reports"])
        self.assertEqual(args.output_dir, "/tmp/test_reports")

    def test_main_simulate_returns_zero(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            rc = main(["--simulate", "--fault-type", "healthy_server",
                        "--output-dir", tmpdir])
            self.assertEqual(rc, 0)

    def test_main_no_simulate_returns_one(self):
        rc = main([])
        self.assertEqual(rc, 1)

    def test_main_all_scenarios(self):
        for scenario in ["raid_degraded_smart_fail", "thermal_dimm_fault", "healthy_server"]:
            with self.subTest(scenario=scenario):
                with tempfile.TemporaryDirectory() as tmpdir:
                    rc = main(["--simulate", "--fault-type", scenario,
                                "--output-dir", tmpdir])
                    self.assertEqual(rc, 0)
                    self.assertTrue((Path(tmpdir) / "hardware_telemetry.json").exists())
                    self.assertTrue((Path(tmpdir) / "hardware_rca_runbook.md").exists())


class TestRaidArrayStatus(unittest.TestCase):
    """Test RaidArrayStatus model methods."""

    def _make_array(self, status_line, state):
        return RaidArrayStatus(
            device="/dev/md0", level="raid5", state=state,
            status_line=status_line, total_devices=4,
            active_devices=3, failed_devices=0, spare_devices=1,
            component_devices=["/dev/loop10", "/dev/loop11", "/dev/loop12", "/dev/loop13"],
        )

    def test_bitmap_property(self):
        arr = self._make_array("[UUU]", RaidState.ACTIVE)
        self.assertEqual(arr.bitmap, "UUU")

    def test_is_healthy_active(self):
        arr = self._make_array("[UUU]", RaidState.ACTIVE)
        self.assertTrue(arr.is_healthy())

    def test_is_healthy_degraded(self):
        arr = self._make_array("[UU_]", RaidState.DEGRADED)
        self.assertFalse(arr.is_healthy())

    def test_is_healthy_wrong_state(self):
        arr = self._make_array("[UUU]", RaidState.REBUILDING)
        self.assertFalse(arr.is_healthy())


if __name__ == "__main__":
    unittest.main()
