from __future__ import annotations

import json
import plistlib
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton, QWidget

from ios_developer_toolkit.apple_tools import (
    AppleToolError, coredevice_install, coredevice_inventory, coredevice_mount_ddi, coredevice_open_url,
    coredevice_result, device_log_archive, export_trace_logs, instruments_recording,
    native_help, new_artifact_path, parse_coredevice_targets, update_host_ddis,
    validated_app_bundle, validated_target, validated_url,
)
from ios_developer_toolkit.apple_tools_page import AppleToolsPage
from ios_developer_toolkit.location_lab import Coordinates
from ios_developer_toolkit.operation_history import OperationContext
from ios_developer_toolkit.qt_process import OperationResult
from ios_developer_toolkit.simulator_page import SimulatorToolsPage
from ios_developer_toolkit.simulator_tools import (
    SimulatorGPXPoint, decode_simulator_apps, fixed_gpx_offsets, load_simulator_gpx,
    parse_simulators, recorded_gpx_offsets, simulator_boot, simulator_clear_location,
    simulator_erase, simulator_identifier, simulator_install, simulator_log_spec,
    simulator_open_url, simulator_set_location, simulator_shutdown,
)


SIMULATOR_ID = "AAAAAAAA-1111-2222-3333-444444444444"
DEVICE_ID = "00008110-001122334455001E"


def coredevice_payload(record: dict[str, object]) -> bytes:
    return json.dumps({"info": {"outcome": "success"}, "result": {"devices": [record]}}).encode()


class AppleSimulatorToolsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.directory)

    def app_bundle(self, platform: str) -> Path:
        bundle = self.directory / f"{platform}.app"
        bundle.mkdir()
        (bundle / "Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": "com.example.test", "CFBundleExecutable": "Test", "CFBundleSupportedPlatforms": [platform]}))
        (bundle / "Test").write_bytes(b"synthetic-executable")
        return bundle

    def test_rejects_implicit_targets_and_credential_urls(self) -> None:
        for target in ("booted", "all", "local", "--device", "My iPhone", ""):
            with self.subTest(target=target), self.assertRaises(AppleToolError):
                validated_target(target)
        for target in (DEVICE_ID, "booted", "all", ""):
            with self.subTest(target=target), self.assertRaises(AppleToolError):
                simulator_identifier(target)
        for url in ("https://user:secret@example.com", "https://", "missing-scheme", "https://example.com/a b"):
            with self.subTest(url=url), self.assertRaises(AppleToolError):
                validated_url(url)
        self.assertEqual(validated_url("test-app://open/item"), "test-app://open/item")

    def test_app_installation_enforces_platform_and_bundle_metadata(self) -> None:
        device = self.app_bundle("iPhoneOS")
        simulator = self.app_bundle("iPhoneSimulator")
        self.assertEqual(validated_app_bundle(device, "iPhoneOS"), device)
        self.assertIn(str(device), coredevice_install(DEVICE_ID, device).arguments)
        self.assertIn(str(simulator), simulator_install(SIMULATOR_ID, simulator).arguments)
        with self.assertRaises(AppleToolError):
            simulator_install(SIMULATOR_ID, device)
        with self.assertRaises(AppleToolError):
            coredevice_install(DEVICE_ID, simulator)
        (simulator / "Test").unlink()
        with self.assertRaises(AppleToolError):
            validated_app_bundle(simulator, "iPhoneSimulator")

    def test_output_paths_refuse_overwrite_and_symlinks(self) -> None:
        destination = self.directory / "test.trace"
        self.assertEqual(new_artifact_path(destination, ".trace"), destination)
        destination.symlink_to(self.directory / "absent")
        with self.assertRaises(AppleToolError):
            new_artifact_path(destination, ".trace")
        with self.assertRaises(AppleToolError):
            new_artifact_path(self.directory / "wrong.txt", ".trace")

    def test_parses_both_coredevice_schemas_without_inventing_reachability(self) -> None:
        old = {"identifier": SIMULATOR_ID, "hardwareProperties": {"udid": DEVICE_ID}, "deviceProperties": {"name": "Test iPhone", "osVersionNumber": "26.0"}, "connectionProperties": {"transportType": "localNetwork", "tunnelState": "disconnected", "pairingState": "paired"}}
        modern = {"identifier": SIMULATOR_ID, "properties": {"hardware": {"udid": DEVICE_ID}, "device": {"name": "Test iPhone", "osVersionNumber": "26.0"}, "connection": {"transportType": "localNetwork", "tunnelState": "disconnected", "pairingState": "paired"}}}
        self.assertEqual(parse_coredevice_targets(coredevice_payload(old)), parse_coredevice_targets(coredevice_payload(modern)))
        target = parse_coredevice_targets(coredevice_payload(modern))[0]
        self.assertEqual(target.tunnel_state, "disconnected")
        self.assertEqual(target.udid, DEVICE_ID)
        with self.assertRaises(AppleToolError):
            coredevice_result(b'{"info":{"outcome":"failure"},"result":{}}')
        with self.assertRaises(AppleToolError):
            parse_coredevice_targets(coredevice_payload({"identifier": SIMULATOR_ID, "properties": {"device": {"name": 7}}}))

    def test_simulator_inventory_keeps_state_and_unavailable_entries(self) -> None:
        runtime = "com.apple.CoreSimulator.SimRuntime.iOS-26-0"
        payload = {"runtimes": [{"identifier": runtime, "name": "iOS 26.0", "version": "26.0"}], "devices": {runtime: [{"udid": SIMULATOR_ID, "name": "Test iPhone", "state": "Shutdown", "isAvailable": False, "deviceTypeIdentifier": "com.apple.CoreSimulator.SimDeviceType.iPhone-17"}]}}
        targets = parse_simulators(json.dumps(payload).encode())
        self.assertEqual(targets[0].state, "Shutdown")
        self.assertFalse(targets[0].available)
        payload["devices"][runtime][0]["isAvailable"] = "true"
        with self.assertRaises(AppleToolError):
            parse_simulators(json.dumps(payload).encode())

    def test_all_native_operations_bind_explicit_targets(self) -> None:
        for operation in (simulator_boot(SIMULATOR_ID), simulator_shutdown(SIMULATOR_ID), simulator_erase(SIMULATOR_ID), simulator_clear_location(SIMULATOR_ID), simulator_set_location(SIMULATOR_ID, Coordinates(10, 20)), simulator_open_url(SIMULATOR_ID, "https://example.com")):
            self.assertIn(SIMULATOR_ID, operation.arguments)
            self.assertEqual(operation.target, SIMULATOR_ID)
            self.assertGreater(operation.timeout_seconds, 0)
        self.assertIn(DEVICE_ID, coredevice_open_url(DEVICE_ID, "https://example.com").arguments)
        ddi = coredevice_mount_ddi(DEVICE_ID)
        self.assertIn(DEVICE_ID, ddi.arguments)
        self.assertIn("--auto-mount-ddis", ddi.arguments)
        self.assertEqual(ddi.risk, "device-change")
        self.assertIn(SIMULATOR_ID, simulator_log_spec(SIMULATOR_ID).arguments)
        self.assertIn("--no-clean", update_host_ddis().arguments)
        self.assertEqual(coredevice_inventory().risk, "read-only")

    def test_recordings_and_log_archives_enforce_bounds_and_extensions(self) -> None:
        trace = self.directory / "test.trace"
        operation = instruments_recording(SIMULATOR_ID, "Logging", 15, trace)
        self.assertIn("15s", operation.arguments)
        with self.assertRaises(AppleToolError):
            instruments_recording(SIMULATOR_ID, "Unknown", 15, trace)
        with self.assertRaises(AppleToolError):
            device_log_archive(DEVICE_ID, 86401, self.directory / "logs.logarchive")
        trace.mkdir()
        exported = export_trace_logs(trace, self.directory / "logs.xml")
        self.assertIn('/trace-toc/run[@number="1"]/data/table[@schema="os-log"]', exported.arguments)

    def test_gpx_validates_coordinates_timestamps_and_schedules(self) -> None:
        path = self.directory / "route.gpx"
        path.write_text('<gpx><trk><trkseg><trkpt lat="10" lon="20"><time>2026-01-01T00:00:00Z</time></trkpt><trkpt lat="11" lon="21"><time>2026-01-01T00:00:05Z</time></trkpt></trkseg></trk></gpx>')
        route = load_simulator_gpx(path)
        self.assertEqual(recorded_gpx_offsets(route.points), (0.0, 5.0))
        self.assertEqual(fixed_gpx_offsets(route.points, 0.5), (0.0, 0.5))
        descending = (SimulatorGPXPoint(Coordinates(0, 0), datetime(2026, 1, 1, tzinfo=timezone.utc)), SimulatorGPXPoint(Coordinates(1, 1), datetime(2025, 1, 1, tzinfo=timezone.utc)))
        self.assertEqual(recorded_gpx_offsets(descending), (0.0, 0.5))
        with self.assertRaises(AppleToolError):
            fixed_gpx_offsets(route.points, 0.1)
        path.write_text('<gpx><trkpt lat="10" lon="20"><time>not-a-date</time></trkpt></gpx>')
        with self.assertRaises(AppleToolError):
            load_simulator_gpx(path)
        for invalid in ('<gpx><trkpt lon="20"/></gpx>', '<gpx><trkpt lat="10" lon="20"><time>2026-01-01T00:00:00Z</time><time>2026-01-01T00:00:01Z</time></trkpt></gpx>', '<!--' + ' ' * 17000 + '--><!DOCTYPE gpx [<!ENTITY x "1">]><gpx><trkpt lat="10" lon="20"/></gpx>'):
            path.write_text(invalid)
            with self.subTest(invalid=invalid[:40]), self.assertRaises(AppleToolError):
                load_simulator_gpx(path)

    @unittest.skipUnless(Path("/usr/bin/plutil").is_file(), "Apple plutil is unavailable")
    def test_reads_real_apple_parser_openstep_and_xml_app_inventories(self) -> None:
        openstep = b'{ "com.example.test" = { CFBundleIdentifier = "com.example.test"; CFBundleName = "Test"; ApplicationType = "User"; }; }'
        xml = plistlib.dumps({"com.example.test": {"CFBundleIdentifier": "com.example.test", "CFBundleName": "Test", "ApplicationType": "User"}})
        self.assertEqual(decode_simulator_apps(openstep), decode_simulator_apps(xml))
        with self.assertRaises(AppleToolError):
            decode_simulator_apps(b"invalid plist")


class NativeToolPageIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_simulator_close_requires_explicit_leave_and_defaults_to_cancel(self) -> None:
        self.parent = QWidget()
        page = SimulatorToolsPage(self.parent)
        self.addCleanup(page.shutdown)
        self.addCleanup(self.parent.close)
        self.assertTrue(page.prepare_close())
        page._operation_started(simulator_set_location(SIMULATOR_ID, Coordinates(10, 20)))
        reviewed: list[str] = []

        def cancel_review() -> None:
            modal = QApplication.activeModalWidget()
            self.assertIsInstance(modal, QMessageBox)
            self.assertEqual(modal.objectName(), "simulatorLocationCloseReview")
            button = modal.findChild(QPushButton, "simulatorCancelCloseButton")
            self.assertIsNotNone(button)
            self.assertIs(modal.defaultButton(), button)
            reviewed.append("cancel")
            button.click()

        QTimer.singleShot(0, cancel_review)
        self.assertFalse(page.prepare_close())
        self.assertEqual(page._location_target, SIMULATOR_ID)

        def leave_review() -> None:
            modal = QApplication.activeModalWidget()
            self.assertIsInstance(modal, QMessageBox)
            button = modal.findChild(QPushButton, "simulatorLeaveLocationAndCloseButton")
            self.assertIsNotNone(button)
            reviewed.append("leave")
            button.click()

        QTimer.singleShot(0, leave_review)
        self.assertTrue(page.prepare_close())
        self.assertEqual(reviewed, ["cancel", "leave"])
        self.assertEqual(page._location_target, SIMULATOR_ID)

    @unittest.skipUnless(Path("/usr/bin/xcrun").is_file(), "Apple xcrun is unavailable")
    def test_qt_page_runs_real_installed_help_and_emits_typed_history(self) -> None:
        self.parent = QWidget()
        parent = self.parent
        self.page = AppleToolsPage(parent)
        page = self.page
        self.simulator = SimulatorToolsPage(parent)
        simulator = self.simulator
        self.addCleanup(parent.close)
        self.addCleanup(page.shutdown)
        self.addCleanup(simulator.shutdown)
        event_loop = QEventLoop()
        results: list[tuple[object, object]] = []

        def completed(context: object, result: object) -> None:
            results.append((context, result))
            event_loop.quit()

        page.operation_completed.connect(completed)
        page.help_tool.setCurrentText("simctl")
        page.help_route.setText("location")
        page.help_button.click()
        QTimer.singleShot(35000, event_loop.quit)
        if not results:
            event_loop.exec()
        self.assertEqual(len(results), 1)
        self.assertIsInstance(results[0][0], OperationContext)
        self.assertIsInstance(results[0][1], OperationResult)
        result = results[0][1]
        self.assertEqual(result.outcome, "succeeded")
        self.assertIn(b"Control a device's simulated location", result.stdout + result.stderr)
        page.set_device(None, True)
        simulator.set_demo_mode(True)
        self.assertFalse(page.refresh_button.isEnabled())
        self.assertFalse(simulator.refresh_button.isEnabled())
        self.assertFalse(simulator.play_gpx_button.isEnabled())
        self.assertEqual(native_help("simctl", "location").risk, "read-only")


if __name__ == "__main__":
    unittest.main()
