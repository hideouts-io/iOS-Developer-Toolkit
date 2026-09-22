from __future__ import annotations

import json
import sys
import time
import unittest
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QProcess

from ios_developer_toolkit.app import DeviceScanner
from ios_developer_toolkit.models import IOSDevice
from ios_developer_toolkit.runtime import ExecutableCommand


class DeviceScannerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QCoreApplication.instance() or QCoreApplication(["device-scanner-tests"])

    def test_consumes_valid_discovery_json_after_child_exit(self) -> None:
        payload = json.dumps(
            [
                {
                    "Identifier": "00008110-001122334455001E",
                    "DeviceName": "Test iPhone",
                    "ProductType": "iPhone14,5",
                    "ProductVersion": "26.3.1",
                    "BuildVersion": "23D123",
                    "ConnectionType": "USB",
                }
            ]
        )
        process = QProcess()
        process.setProgram(sys.executable)
        process.setArguments(("-c", f"import sys; sys.stdout.write({payload!r})"))
        process.start()
        self.assertTrue(process.waitForStarted(3_000))
        self.assertTrue(process.waitForFinished(3_000))

        scanner = DeviceScanner(ExecutableCommand(Path(sys.executable), ()))
        observed_devices: list[tuple[IOSDevice, ...]] = []
        observed_errors: list[str] = []
        observed_diagnostics: list[object] = []
        scanner.devices_changed.connect(observed_devices.append)
        scanner.scan_error.connect(observed_errors.append)
        scanner.diagnostic_changed.connect(observed_diagnostics.append)
        scanner._process = process

        scanner._finished(0, QProcess.ExitStatus.NormalExit)

        self.assertEqual(observed_errors, [])
        self.assertEqual(len(observed_devices), 1)
        self.assertEqual(observed_devices[0][0].identifier, "00008110-001122334455001E")
        self.assertEqual(len(observed_diagnostics), 1)
        self.assertEqual(observed_diagnostics[0].state, "devices-available")
        self.assertEqual(observed_diagnostics[0].device_count, 1)

    def test_reports_failed_process_launch_without_exposing_qprocess_details(self) -> None:
        scanner = DeviceScanner(ExecutableCommand(Path("/missing-ios-toolkit-pymobiledevice3"), ()))
        observed_errors: list[str] = []
        observed_diagnostics: list[object] = []
        scanner.scan_error.connect(observed_errors.append)
        scanner.diagnostic_changed.connect(observed_diagnostics.append)
        scanner.scan()

        deadline = time.monotonic() + 3
        while not observed_diagnostics and time.monotonic() < deadline:
            self.application.processEvents()
            time.sleep(0.01)

        self.assertEqual(len(observed_diagnostics), 1)
        self.assertEqual(observed_diagnostics[0].state, "launch-failed")
        self.assertEqual(observed_errors, ["The usbmux discovery process could not start"])


if __name__ == "__main__":
    unittest.main()
