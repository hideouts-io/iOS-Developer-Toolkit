from __future__ import annotations

import plistlib
import unittest
from pathlib import Path

from ios_developer_toolkit.catalog import is_potentially_mutating, snapshot_commands
from ios_developer_toolkit.collector import safe_udid_fragment
from ios_developer_toolkit.local_ddi import parse_attached_image
from ios_developer_toolkit.models import DeviceDataError, parse_devices_json
from ios_developer_toolkit.validation import output_indicates_failure


class DeviceParsingTests(unittest.TestCase):
    def test_parses_current_usbmux_shape(self) -> None:
        devices = parse_devices_json(
            """[
              {
                "Identifier": "00008110-001122334455001E",
                "DeviceName": "Research iPhone",
                "ProductType": "iPhone14,5",
                "ProductVersion": "26.3.1",
                "BuildVersion": "23D123",
                "ConnectionType": "USB",
                "IgnoredFutureField": true
              }
            ]"""
        )
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0].identifier, "00008110-001122334455001E")
        self.assertEqual(devices[0].product_version, "26.3.1")

    def test_rejects_missing_identifier(self) -> None:
        with self.assertRaises(DeviceDataError):
            parse_devices_json('[{"DeviceName": "Unnamed"}]')


class CommandPolicyTests(unittest.TestCase):
    def test_read_commands_do_not_require_mutation_confirmation(self) -> None:
        self.assertFalse(is_potentially_mutating(("developer", "dvt", "ls", "/")))
        self.assertFalse(is_potentially_mutating(("pcap", "--out", "capture.pcap")))

    def test_state_changing_commands_require_confirmation(self) -> None:
        self.assertTrue(is_potentially_mutating(("profile", "erase-device")))
        self.assertTrue(is_potentially_mutating(("developer", "dvt", "launch", "com.example.app")))

    def test_collection_catalog_contains_requested_coverage(self) -> None:
        identifiers = {spec.identifier for spec in snapshot_commands(True, True)}
        self.assertTrue({"dvt-device", "dvt-processes", "dvt-filesystem", "screenshot", "crash-pull"} <= identifiers)


class EvidenceNamingTests(unittest.TestCase):
    def test_udid_fragment_is_sanitized_and_bounded(self) -> None:
        self.assertEqual(safe_udid_fragment("00008110-001122334455001E"), "22334455001E")


class OutputValidationTests(unittest.TestCase):
    def test_detects_zero_exit_device_error_text(self) -> None:
        output = "2026-08-23 main[123] ERROR Device not found: TEST-DEVICE"
        self.assertTrue(output_indicates_failure(output))

    def test_success_information_is_not_an_error(self) -> None:
        output = "INFO DeveloperDiskImage mounted successfully"
        self.assertFalse(output_indicates_failure(output))


class LocalDDITests(unittest.TestCase):
    def test_parses_hdiutil_plist(self) -> None:
        payload = plistlib.dumps(
            {
                "system-entities": [
                    {"dev-entry": "/dev/disk99"},
                    {"dev-entry": "/dev/disk99s1", "mount-point": "/Volumes/Test DDI"},
                ]
            }
        )
        attached = parse_attached_image(payload)
        self.assertEqual(attached.device_entry, "/dev/disk99s1")
        self.assertEqual(attached.mount_point, Path("/Volumes/Test DDI"))


if __name__ == "__main__":
    unittest.main()
