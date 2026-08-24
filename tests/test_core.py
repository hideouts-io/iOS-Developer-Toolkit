from __future__ import annotations

import plistlib
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

from ios_developer_toolkit.backup_worker import BackupRequestError, parse_backup_event, parse_backup_request
from ios_developer_toolkit.catalog import is_potentially_mutating, snapshot_commands
from ios_developer_toolkit.command_catalog import (
    CommandCatalogError,
    command_presets,
    manpage_entries,
    preset_by_identifier,
    render_preset_arguments,
)
from ios_developer_toolkit.collector import safe_udid_fragment
from ios_developer_toolkit.installed_apps import InstalledAppsDataError, format_byte_count, parse_installed_apps_json
from ios_developer_toolkit.ipa_inspector import (
    IPAInspectionError,
    inspect_ipa,
    read_archive_metadata,
    validate_bundle_identifier,
)
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


class GuidedCommandCatalogTests(unittest.TestCase):
    def test_catalog_has_unique_presets_and_broad_command_families(self) -> None:
        presets = command_presets()
        identifiers = {preset.identifier for preset in presets}
        categories = {preset.category for preset in presets}
        self.assertEqual(len(identifiers), len(presets))
        self.assertGreaterEqual(len(presets), 40)
        self.assertEqual(
            categories,
            {"Device Basics", "Apps & Files", "Logging & Capture", "Developer & DVT", "Web & Discovery", "Device Actions"},
        )

    def test_renders_validated_parameter_without_shell_parsing(self) -> None:
        preset = preset_by_identifier("location-set")
        arguments = render_preset_arguments(preset, {"latitude": "34.0522", "longitude": "-118.2437"})
        self.assertEqual(
            arguments,
            ("developer", "dvt", "simulate-location", "set", "--", "34.0522", "-118.2437"),
        )

    def test_rejects_invalid_bundle_and_url_parameters(self) -> None:
        with self.assertRaises(CommandCatalogError):
            render_preset_arguments(preset_by_identifier("apps-query"), {"bundle_id": "../../unsafe"})
        with self.assertRaises(CommandCatalogError):
            render_preset_arguments(preset_by_identifier("open-url"), {"url": "file:///etc/passwd"})

    def test_manpages_cover_every_top_level_group_from_attached_inventory(self) -> None:
        paths = {entry.command_path for entry in manpage_entries()}
        expected = {
            ("activation",), ("afc",), ("amfi",), ("apps",), ("backup2",), ("btlogger",),
            ("bonjour",), ("companion",), ("crash",), ("cryptex",), ("developer",),
            ("diagnostics",), ("idam",), ("lockdown",), ("mounter",), ("notification",),
            ("pcap",), ("power-assertion",), ("processes",), ("profile",), ("provision",),
            ("remote",), ("restore",), ("springboard",), ("syslog",), ("usbmux",),
            ("webinspector",), ("version",),
        }
        self.assertTrue(expected <= paths)

    def test_every_preset_has_a_live_help_route_and_no_erase_or_restore_shortcut(self) -> None:
        help_paths = {entry.command_path for entry in manpage_entries()}
        forbidden_prefixes = (("restore",), ("profile", "erase-device"), ("backup2", "erase-device"))
        for preset in command_presets():
            self.assertTrue(
                any(path and preset.manpage_path[: len(path)] == path for path in help_paths),
                msg=f"missing live help route for {preset.identifier}",
            )
            self.assertFalse(
                any(preset.argument_template[: len(prefix)] == prefix for prefix in forbidden_prefixes),
                msg=f"high-impact shortcut exposed by {preset.identifier}",
            )


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


class IPAInspectionTests(unittest.TestCase):
    def test_validates_bundle_identifier_before_uninstall(self) -> None:
        self.assertEqual(validate_bundle_identifier("com.example.application"), "com.example.application")
        with self.assertRaises(IPAInspectionError):
            validate_bundle_identifier("../../unsafe")

    def test_verifies_an_ad_hoc_signed_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            app_path = root / "Payload" / "Signed.app"
            app_path.mkdir(parents=True)
            executable_path = app_path / "SignedApp"
            shutil.copyfile("/usr/bin/true", executable_path)
            executable_path.chmod(0o755)
            (app_path / "Info.plist").write_bytes(
                plistlib.dumps(
                    {
                        "CFBundleName": "Signed App",
                        "CFBundleIdentifier": "com.example.signed",
                        "CFBundleShortVersionString": "1.0",
                        "CFBundleVersion": "1",
                        "CFBundleExecutable": "SignedApp",
                    }
                )
            )
            subprocess.run(
                ["/usr/bin/codesign", "--force", "--sign", "-", str(app_path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
            ipa_path = root / "Signed.ipa"
            with zipfile.ZipFile(ipa_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for source_path in sorted(app_path.rglob("*")):
                    if source_path.is_file():
                        archive.write(source_path, source_path.relative_to(root))
            inspection = inspect_ipa(ipa_path)
            self.assertEqual(inspection.bundle_identifier, "com.example.signed")
            self.assertEqual(inspection.signature.status, "valid")
            self.assertEqual(inspection.provisioning.status, "absent")

    def test_reads_metadata_and_reports_invalid_synthetic_signature(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            ipa_path = Path(temporary_directory) / "Example.ipa"
            info_plist = plistlib.dumps(
                {
                    "CFBundleDisplayName": "Example App",
                    "CFBundleIdentifier": "com.example.app",
                    "CFBundleShortVersionString": "1.2.3",
                    "CFBundleVersion": "45",
                    "CFBundleExecutable": "ExampleApp",
                    "MinimumOSVersion": "17.0",
                }
            )
            with zipfile.ZipFile(ipa_path, "w") as archive:
                archive.writestr("Payload/Example.app/Info.plist", info_plist)
                archive.writestr("Payload/Example.app/ExampleApp", b"not-a-mach-o")
                archive.writestr("Payload/Example.app/_CodeSignature/CodeResources", b"not-a-signature")
            metadata = read_archive_metadata(ipa_path)
            inspection = inspect_ipa(ipa_path)
            self.assertEqual(metadata.bundle_identifier, "com.example.app")
            self.assertEqual(inspection.app_name, "Example App")
            self.assertEqual(inspection.provisioning.status, "absent")
            self.assertEqual(inspection.signature.status, "invalid")

    def test_rejects_unsafe_archive_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            ipa_path = Path(temporary_directory) / "Unsafe.ipa"
            with zipfile.ZipFile(ipa_path, "w") as archive:
                archive.writestr("../outside", b"unsafe")
            with self.assertRaises(IPAInspectionError):
                read_archive_metadata(ipa_path)


class InstalledAppsTests(unittest.TestCase):
    def test_parses_and_sorts_app_inventory(self) -> None:
        apps = parse_installed_apps_json(
            """{
              "com.example.zeta": {
                "CFBundleIdentifier": "com.example.zeta",
                "CFBundleDisplayName": "Zeta",
                "CFBundleShortVersionString": "2.0",
                "CFBundleVersion": "20",
                "ApplicationType": "User",
                "StaticDiskUsage": 1500000,
                "DynamicDiskUsage": 500000
              },
              "com.apple.alpha": {
                "CFBundleIdentifier": "com.apple.alpha",
                "CFBundleName": "Alpha",
                "ApplicationType": "System"
              }
            }"""
        )
        self.assertEqual(tuple(app.name for app in apps), ("Alpha", "Zeta"))
        self.assertEqual(apps[1].total_bytes, 2000000)
        self.assertEqual(format_byte_count(apps[1].total_bytes), "2.0 MB")

    def test_rejects_mismatched_bundle_identifier(self) -> None:
        with self.assertRaises(InstalledAppsDataError):
            parse_installed_apps_json(
                '{"com.example.expected": {"CFBundleIdentifier": "com.example.different"}}'
            )


class BackupWorkerParsingTests(unittest.TestCase):
    def test_parses_secure_backup_request(self) -> None:
        request = parse_backup_request(
            """{
              "udid": "00008110-001122334455001E",
              "destination": "/tmp/iOS Backups",
              "require_encryption": true,
              "new_password": "local-backup-secret",
              "full": false
            }"""
        )
        self.assertEqual(request.destination, Path("/tmp/iOS Backups").resolve())
        self.assertTrue(request.require_encryption)
        self.assertEqual(request.new_password, "local-backup-secret")

    def test_rejects_relative_backup_destination(self) -> None:
        with self.assertRaises(BackupRequestError):
            parse_backup_request(
                '{"udid":"device","destination":"relative","require_encryption":false,"new_password":"","full":false}'
            )

    def test_parses_backup_progress_event(self) -> None:
        event = parse_backup_event(
            '{"event":"progress","message":"Backup progress: 42%","percent":42}'
        )
        self.assertEqual(event.percent, 42)
        self.assertIsNone(event.encrypted)


if __name__ == "__main__":
    unittest.main()
