from __future__ import annotations

import plistlib
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from datetime import datetime, timezone
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
from ios_developer_toolkit.location_lab import (
    Coordinates,
    LocationLabError,
    add_saved_location,
    build_route,
    clear_location_arguments,
    inspect_gpx,
    move_coordinates,
    parse_route_waypoints,
    parse_saved_locations,
    play_location_arguments,
    set_location_arguments,
    validate_coordinates,
)
from ios_developer_toolkit.live_logs import (
    LiveLogError,
    compile_line_filter,
    create_spool_paths,
    line_matches,
    stream_spec,
)
from ios_developer_toolkit.models import DeviceDataError, parse_devices_json
from ios_developer_toolkit.ufade_connector import (
    UFADEValidationError,
    parse_python_version,
    validate_ufade_checkout,
)
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
        with self.assertRaises(CommandCatalogError):
            render_preset_arguments(
                preset_by_identifier("location-set"),
                {"latitude": "nan", "longitude": "0"},
            )

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


class LocationLabTests(unittest.TestCase):
    def test_builds_version_specific_location_commands(self) -> None:
        coordinates = Coordinates(latitude=34.0522, longitude=-118.2437)
        self.assertEqual(
            set_location_arguments("26.3.1", coordinates),
            ("developer", "dvt", "simulate-location", "set", "--", "34.0522", "-118.2437"),
        )
        self.assertEqual(
            clear_location_arguments("16.7.12"),
            ("developer", "simulate-location", "clear"),
        )
        self.assertEqual(
            play_location_arguments("16.7.12", Path("/tmp/route.gpx"), 250, True),
            (
                "developer",
                "simulate-location",
                "play",
                str(Path("/tmp/route.gpx").resolve()),
                "250",
                "--disable-sleep",
            ),
        )

    def test_rejects_nonfinite_and_out_of_range_coordinates(self) -> None:
        for latitude, longitude in (("nan", "0"), ("91", "0"), ("0", "-181")):
            with self.subTest(latitude=latitude, longitude=longitude):
                with self.assertRaises(LocationLabError):
                    validate_coordinates(latitude, longitude)

    def test_inspects_track_points_and_hashes_gpx(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            route = Path(temporary_directory) / "route.gpx"
            route.write_text(
                """<?xml version="1.0" encoding="UTF-8"?>
                <gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1">
                  <trk><trkseg>
                    <trkpt lat="34.0522" lon="-118.2437"><time>2026-08-23T12:00:00Z</time></trkpt>
                    <trkpt lat="34.0523" lon="-118.2436" />
                  </trkseg></trk>
                </gpx>
                """,
                encoding="utf-8",
            )
            inspection = inspect_gpx(route)
            self.assertEqual(inspection.track_point_count, 2)
            self.assertEqual(inspection.timed_point_count, 1)
            self.assertEqual(inspection.first_point, Coordinates(34.0522, -118.2437))
            self.assertEqual(len(inspection.sha256), 64)

    def test_rejects_gpx_without_track_points(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            route = Path(temporary_directory) / "waypoints.gpx"
            route.write_text('<gpx version="1.1"><wpt lat="1" lon="2" /></gpx>', encoding="utf-8")
            with self.assertRaises(LocationLabError):
                inspect_gpx(route)

    def test_saved_location_schema_is_strict_and_names_are_unique(self) -> None:
        locations = parse_saved_locations(
            '{"version": 1, "locations": [{"name": "Lab", "latitude": 1.5, "longitude": 2.5}]}'
        )
        self.assertEqual(locations[0].name, "Lab")
        with self.assertRaises(LocationLabError):
            add_saved_location(locations, "lab", Coordinates(3.0, 4.0))
        with self.assertRaises(LocationLabError):
            parse_saved_locations('{"version": 1, "locations": [{"name": "Broken", "latitude": "1"}]}')

    def test_builds_bounded_timestamped_ping_pong_route(self) -> None:
        waypoints = parse_route_waypoints("34.0522,-118.2437\n34.0523,-118.2436")
        route = build_route(waypoints, 5.0, 2, 2, datetime(2026, 8, 24, tzinfo=timezone.utc))
        self.assertGreater(len(route.points), 2)
        self.assertAlmostEqual(route.points[0].latitude, route.points[-1].latitude, places=9)
        self.assertAlmostEqual(route.points[0].longitude, route.points[-1].longitude, places=9)
        self.assertIn("2026-08-24T00:00:00Z", route.gpx_document)
        self.assertEqual(route.gpx_document.count("<trkpt"), len(route.points))

    def test_nudges_coordinates_without_changing_input(self) -> None:
        origin = Coordinates(34.0522, -118.2437)
        moved = move_coordinates(origin, 90.0, 100.0)
        self.assertEqual(origin, Coordinates(34.0522, -118.2437))
        self.assertAlmostEqual(moved.latitude, origin.latitude, places=4)
        self.assertGreater(moved.longitude, origin.longitude)

    def test_rejects_unbounded_generated_route(self) -> None:
        with self.assertRaises(LocationLabError):
            build_route(
                (Coordinates(0.0, 0.0), Coordinates(0.0, 179.0)),
                1.0,
                1,
                20,
                datetime(2026, 8, 24, tzinfo=timezone.utc),
            )


class LiveLogTests(unittest.TestCase):
    def test_stream_catalog_uses_distinct_current_pymobiledevice3_services(self) -> None:
        self.assertEqual(stream_spec("unified").arguments, ("syslog", "live", "--format", "json", "--label"))
        self.assertEqual(stream_spec("classic").arguments, ("syslog", "live-old"))
        self.assertTrue(stream_spec("dvt-oslog").requires_developer_services)

    def test_literal_and_regex_filters_are_explicit(self) -> None:
        literal = compile_line_filter("process[1]", False, False)
        self.assertTrue(line_matches("PROCESS[1] started", literal))
        self.assertFalse(line_matches("process1 started", literal))
        regex = compile_line_filter(r"error\s+\d+", True, False)
        self.assertTrue(line_matches("Error 42", regex))
        with self.assertRaises(LiveLogError):
            compile_line_filter("[", True, True)

    def test_spool_paths_are_sanitized_and_keep_structured_extension(self) -> None:
        raw, metadata = create_spool_paths(Path("/tmp/logs"), stream_spec("unified"), "device/../../unsafe")
        self.assertEqual(raw.parent, Path("/tmp/logs").resolve())
        self.assertEqual(raw.suffix, ".jsonl")
        self.assertNotIn("/../", str(raw))
        self.assertEqual(metadata.suffixes, [".meta", ".json"])


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


class UFADEConnectorTests(unittest.TestCase):
    def test_validates_external_gpl_checkout_and_reads_version(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            checkout = Path(temporary_directory)
            (checkout / "ufade.py").write_text('u_version = "1.0.4"\n', encoding="utf-8")
            (checkout / "LICENSE").write_text(
                "GNU GENERAL PUBLIC LICENSE\nVersion 3, 29 June 2007\n",
                encoding="utf-8",
            )
            (checkout / "requirements.txt").write_text("pymobiledevice3==7.8.3\n", encoding="utf-8")
            resolved_checkout, script, version = validate_ufade_checkout(checkout)
            self.assertEqual(resolved_checkout, checkout.resolve())
            self.assertEqual(script, checkout.resolve() / "ufade.py")
            self.assertEqual(version, "1.0.4")

    def test_rejects_checkout_without_expected_license(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            checkout = Path(temporary_directory)
            (checkout / "ufade.py").write_text('u_version = "1.0.4"\n', encoding="utf-8")
            (checkout / "LICENSE").write_text("MIT License\n", encoding="utf-8")
            (checkout / "requirements.txt").write_text("pymobiledevice3==7.8.3\n", encoding="utf-8")
            with self.assertRaises(UFADEValidationError):
                validate_ufade_checkout(checkout)

    def test_parses_exact_python_version_triplet(self) -> None:
        self.assertEqual(parse_python_version("3.11.9"), (3, 11, 9))
        with self.assertRaises(UFADEValidationError):
            parse_python_version("Python 3.11.9")


if __name__ == "__main__":
    unittest.main()
