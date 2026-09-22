from __future__ import annotations

import json
import plistlib
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from ios_developer_toolkit.action_safety import advanced_action_safety, confirmation_phrase, guided_action_safety
from ios_developer_toolkit.backup_protocol import BackupRequestError, parse_backup_event, parse_backup_request
from ios_developer_toolkit.catalog import is_potentially_mutating, snapshot_commands
from ios_developer_toolkit.command_catalog import (
    CommandCatalogError,
    command_presets,
    manpage_entries,
    preset_by_identifier,
    render_preset_arguments,
)
from ios_developer_toolkit.command_drift import (
    HelpRouteProbe,
    evaluate_command_drift,
    expected_option_tokens,
    help_routes_for_presets,
)
from ios_developer_toolkit.case_workflow import CaseWorkflowError, create_guided_case, validate_collection_case
from ios_developer_toolkit.connection_diagnostics import (
    devices_connection_diagnostic,
    failed_connection_diagnostic,
    launch_failed_connection_diagnostic,
    malformed_output_connection_diagnostic,
    process_error_connection_diagnostic,
)
from ios_developer_toolkit.collector import safe_udid_fragment
from ios_developer_toolkit.demo_mode import DEMO_DEVICE_IDENTIFIER, demo_connection_banner, demo_device
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
    coordinates_to_map_fractions,
    inspect_gpx,
    map_fractions_to_coordinates,
    move_coordinates,
    parse_location_input,
    parse_route_waypoints,
    parse_saved_locations,
    play_location_arguments,
    set_location_arguments,
    validate_coordinates,
)
from ios_developer_toolkit.live_logs import (
    LiveLogInvestigationReport,
    LiveLogError,
    append_finding,
    annotation_path_for,
    compile_line_filter,
    create_finding,
    parse_finding_tags,
    render_investigation_report,
    create_spool_paths,
    line_matches,
    sha256_file,
    stream_spec,
)
from ios_developer_toolkit.models import DeviceDataError, parse_devices_json
from ios_developer_toolkit.support_bundle import (
    SupportBundleContext,
    SupportBundleError,
    SupportStatus,
    create_sanitized_support_bundle,
)
from ios_developer_toolkit.ufade_connector import (
    UFADEValidationError,
    checkout_python_path,
    developer_images_are_available,
    macos_setup_commands,
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


class DemoModeTests(unittest.TestCase):
    def test_simulated_device_is_visibly_labeled_and_never_looks_like_usbmux_data(self) -> None:
        device = demo_device()
        self.assertEqual(device.identifier, DEMO_DEVICE_IDENTIFIER)
        self.assertIn("simulated", device.name)
        self.assertEqual(device.connection_type, "Demo")
        self.assertTrue(demo_connection_banner().startswith("DEMO MODE"))


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


class ActionSafetyTests(unittest.TestCase):
    def test_classifies_read_only_host_write_device_change_and_high_impact_actions(self) -> None:
        self.assertEqual(advanced_action_safety(("apps", "list")).level, "read-only")
        self.assertEqual(advanced_action_safety(("pcap", "--out", "/tmp/capture.pcap")).level, "host-write")
        self.assertEqual(advanced_action_safety(("apps", "install", "/tmp/application.ipa")).level, "device-change")
        self.assertEqual(advanced_action_safety(("restore", "update")).level, "high-impact")

    def test_typed_acknowledgement_binds_to_the_selected_device_suffix(self) -> None:
        device_identifier = "00008110-001122334455001E"
        self.assertEqual(
            confirmation_phrase(guided_action_safety("device-change"), device_identifier),
            "RUN 55001E",
        )
        self.assertEqual(
            confirmation_phrase(advanced_action_safety(("restore", "update")), device_identifier),
            "IRREVERSIBLE 55001E",
        )


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

    def test_streaming_service_presets_require_explicit_stop_controls(self) -> None:
        for identifier in ("dvt-netstat", "core-apps"):
            with self.subTest(identifier=identifier):
                self.assertTrue(preset_by_identifier(identifier).long_running)


class CommandDriftTests(unittest.TestCase):
    def test_live_help_evaluation_flags_missing_options_and_routes_without_running_a_preset(self) -> None:
        pcap = preset_by_identifier("pcap")
        apps = preset_by_identifier("apps-list")
        probes = (
            HelpRouteProbe(("pcap",), 0, "Usage: pcap [--capture FILE]", "", None),
            HelpRouteProbe(("apps", "list"), 2, "", "No such command", None),
        )
        results = evaluate_command_drift((pcap, apps), probes)
        self.assertEqual(expected_option_tokens(pcap), ("--out",))
        self.assertEqual(help_routes_for_presets((pcap, apps)), (("pcap",), ("apps", "list")))
        self.assertEqual(results[0].state, "option-mismatch")
        self.assertEqual(results[1].state, "route-missing")

    def test_live_help_evaluation_accepts_exact_option_boundaries(self) -> None:
        pcap = preset_by_identifier("pcap")
        matching = HelpRouteProbe(("pcap",), 0, "Usage: pcap --out=PATH", "", None)
        nonmatching = HelpRouteProbe(("pcap",), 0, "Usage: pcap --output=PATH", "", None)
        self.assertEqual(evaluate_command_drift((pcap,), (matching,))[0].state, "verified")
        self.assertEqual(evaluate_command_drift((pcap,), (nonmatching,))[0].state, "option-mismatch")


class EvidenceNamingTests(unittest.TestCase):
    def test_udid_fragment_is_sanitized_and_bounded(self) -> None:
        self.assertEqual(safe_udid_fragment("00008110-001122334455001E"), "22334455001E")

    def test_guided_case_records_authorized_intake_and_validates_target(self) -> None:
        temporary_directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, temporary_directory)
        udid = "00008110-001122334455001E"
        case_path, intake = create_guided_case(
            temporary_directory,
            udid,
            "  Device   validation  ",
            "Authorized release testing.",
            True,
        )
        self.assertEqual(intake.title, "Device validation")
        self.assertEqual(validate_collection_case(case_path, udid), case_path)
        self.assertTrue((case_path / "case-intake.json").is_file())
        self.assertTrue((case_path / "snapshots").is_dir())

    def test_guided_case_requires_authorization_and_matching_target(self) -> None:
        temporary_directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, temporary_directory)
        with self.assertRaises(CaseWorkflowError):
            create_guided_case(temporary_directory, "TARGET1", "Case", "", False)
        case_path, _ = create_guided_case(temporary_directory, "TARGET1", "Case", "", True)
        with self.assertRaises(CaseWorkflowError):
            validate_collection_case(case_path, "TARGET2")

    def test_guided_case_cannot_be_reused_after_finalization(self) -> None:
        temporary_directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, temporary_directory)
        case_path, _ = create_guided_case(temporary_directory, "TARGET1", "Case", "", True)
        (case_path / "manifest.json").write_text("{}\n", encoding="utf-8")
        with self.assertRaises(CaseWorkflowError):
            validate_collection_case(case_path, "TARGET1")


class OutputValidationTests(unittest.TestCase):
    def test_detects_zero_exit_device_error_text(self) -> None:
        output = "2026-08-23 main[123] ERROR Device not found: TEST-DEVICE"
        self.assertTrue(output_indicates_failure(output))

    def test_success_information_is_not_an_error(self) -> None:
        output = "INFO DeveloperDiskImage mounted successfully"
        self.assertFalse(output_indicates_failure(output))


class ConnectionDiagnosticTests(unittest.TestCase):
    def test_reports_each_discovery_outcome_without_raw_device_data(self) -> None:
        diagnostics = (
            launch_failed_connection_diagnostic(),
            failed_connection_diagnostic(7),
            process_error_connection_diagnostic(),
            malformed_output_connection_diagnostic(),
            devices_connection_diagnostic(0),
            devices_connection_diagnostic(2),
        )
        self.assertEqual(
            tuple(diagnostic.state for diagnostic in diagnostics),
            (
                "launch-failed",
                "discovery-failed",
                "discovery-failed",
                "malformed-output",
                "no-devices",
                "devices-available",
            ),
        )
        self.assertIn("status 7", diagnostics[1].report())
        self.assertIn("stopped before returning", diagnostics[2].report())
        self.assertIn("Devices available: 2", diagnostics[-1].report())
        self.assertNotIn("Identifier", "\n".join(diagnostic.report() for diagnostic in diagnostics))


class SupportBundleTests(unittest.TestCase):
    def test_creates_a_reviewable_zip_without_known_device_or_host_identifiers(self) -> None:
        temporary_directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, temporary_directory)
        destination = temporary_directory / "support.zip"
        context = SupportBundleContext(
            "0.3.1",
            "Command Center",
            1,
            True,
            (("ready", 2), ("not-tested", 8)),
            "Verified: 49. Device 00008110-001122334455001E at 192.168.1.8.",
            (
                SupportStatus("connection", "Julian iPhone 00008110-001122334455001E"),
                SupportStatus("capability_matrix", "Saved in /Users/julian/Private/diagnostics"),
            ),
            ("00008110-001122334455001E", "Julian iPhone"),
            False,
        )
        result = create_sanitized_support_bundle(destination, context)
        self.assertEqual(result.path, destination)
        self.assertEqual(
            result.entries,
            ("README.txt", "environment.json", "context.json", "command-drift.txt", "SHA256SUMS.json"),
        )
        with zipfile.ZipFile(destination) as archive:
            combined = "\n".join(archive.read(name).decode("utf-8") for name in archive.namelist())
        self.assertNotIn("00008110-001122334455001E", combined)
        self.assertNotIn("Julian iPhone", combined)
        self.assertNotIn("192.168.1.8", combined)
        self.assertNotIn("/Users/julian", combined)
        self.assertIn("<device-identifier>", combined)
        self.assertIn("<ipv4-address>", combined)
        self.assertIn("<local-path>", combined)

    def test_refuses_to_overwrite_an_existing_support_zip(self) -> None:
        temporary_directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, temporary_directory)
        destination = temporary_directory / "support.zip"
        destination.write_bytes(b"existing")
        context = SupportBundleContext(
            "0.3.1", "Home", 0, False, (), "", (), (), False,
        )
        with self.assertRaises(SupportBundleError):
            create_sanitized_support_bundle(destination, context)


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
    def test_imports_coordinates_and_full_map_links_without_network_resolution(self) -> None:
        cases = (
            ("34.0522,-118.2437", Coordinates(34.0522, -118.2437)),
            ("geo:37.3349,-122.0090", Coordinates(37.3349, -122.0090)),
            (
                "https://maps.apple.com/?ll=51.5007%2C-0.1246",
                Coordinates(51.5007, -0.1246),
            ),
            (
                "https://www.google.com/maps/@35.6586,139.7454,15z",
                Coordinates(35.6586, 139.7454),
            ),
            (
                "https://www.google.com/maps/search/?api=1&query=-33.8568%2C151.2153",
                Coordinates(-33.8568, 151.2153),
            ),
        )
        for payload, expected in cases:
            with self.subTest(payload=payload):
                self.assertEqual(parse_location_input(payload), expected)
        for unsupported in ("https://maps.app.goo.gl/short", "https://maps.apple.com/?q=Coffee"):
            with self.subTest(unsupported=unsupported):
                with self.assertRaises(LocationLabError):
                    parse_location_input(unsupported)

    def test_round_trips_coordinates_through_offline_map_fractions(self) -> None:
        original = Coordinates(latitude=34.0522, longitude=-118.2437)
        horizontal, vertical = coordinates_to_map_fractions(original)
        restored = map_fractions_to_coordinates(horizontal, vertical)
        self.assertAlmostEqual(restored.latitude, original.latitude, places=12)
        self.assertAlmostEqual(restored.longitude, original.longitude, places=12)
        with self.assertRaises(LocationLabError):
            map_fractions_to_coordinates(1.1, 0.5)

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

    def test_finding_preserves_selected_text_and_context_separately_from_raw_log(self) -> None:
        temporary_directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, temporary_directory)
        raw_path = temporary_directory / "capture.jsonl"
        findings_path = annotation_path_for(raw_path)
        finding = create_finding(
            "Investigate this authentication failure.",
            "2026-09-14 authd: failed login",
            "unified",
            "DEVICE-1",
            384,
            "authd",
            False,
            False,
            "lead",
            ("authentication", "review"),
        )
        append_finding(findings_path, finding)
        record = json.loads(findings_path.read_text(encoding="utf-8"))
        self.assertEqual(record["note"], "Investigate this authentication failure.")
        self.assertEqual(record["raw_bytes_observed"], 384)
        self.assertEqual(record["assessment"], "lead")
        self.assertEqual(record["tags"], ["authentication", "review"])

    def test_finding_requires_a_note_and_selection(self) -> None:
        with self.assertRaises(LiveLogError):
            create_finding("", "line", "unified", "DEVICE-1", 0, "", False, False, "observation", ())
        with self.assertRaises(LiveLogError):
            create_finding("note", "", "unified", "DEVICE-1", 0, "", False, False, "observation", ())

    def test_finding_tags_are_normalized_and_invalid_tags_are_rejected(self) -> None:
        self.assertEqual(parse_finding_tags("Auth, network, auth"), ("auth", "network"))
        with self.assertRaises(LiveLogError):
            parse_finding_tags("contains spaces")

    def test_investigation_report_separates_capture_facts_from_analyst_annotations(self) -> None:
        finding = create_finding(
            "Correlate with the application crash report.",
            "2026-09-14 process[12]: failed request",
            "unified",
            "DEVICE-1",
            512,
            "failed",
            False,
            False,
            "needs-corroboration",
            ("network",),
        )
        report = LiveLogInvestigationReport(
            stream="unified",
            stream_title="Unified Logs",
            device_name="Research iPhone",
            device_identifier="DEVICE-1",
            started_at="2026-09-14T00:00:00+00:00",
            finished_at="2026-09-14T00:02:00+00:00",
            raw_filename="capture.jsonl",
            raw_sha256="a" * 64,
            raw_bytes=1024,
            decoded_lines=7,
            investigation_reference="CASE-42",
        )
        rendered = render_investigation_report(report, (finding,))
        self.assertIn("## Capture facts", rendered)
        self.assertIn("## Analyst findings", rendered)
        self.assertIn("not device-generated facts", rendered)
        self.assertIn("CASE-42", rendered)

    def test_hashes_raw_log_bytes(self) -> None:
        temporary_directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, temporary_directory)
        raw_path = temporary_directory / "capture.log"
        raw_path.write_bytes(b"forensic log bytes\n")
        self.assertEqual(sha256_file(raw_path), "2f64c1a1b0a217c8dbaca08eeaedee479eedb04f3acafaa8b063c3c77dcf4a86")


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
    def test_builds_isolated_macos_setup_and_checkout_python_path(self) -> None:
        commands = macos_setup_commands()
        self.assertIn("--recurse-submodules", commands[1])
        self.assertIn("python3.11 -m venv .venv", commands)
        self.assertEqual(commands[-1], ".venv/bin/python -m pip install -r requirements.txt")
        self.assertEqual(
            checkout_python_path(Path("/Applications/UFADE")),
            Path("/Applications/UFADE/.venv/bin/python"),
        )

    def test_reports_whether_developer_image_submodule_is_populated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            checkout = Path(temporary_directory)
            self.assertFalse(developer_images_are_available(checkout))
            (checkout / "ufade_developer" / "Developer").mkdir(parents=True)
            self.assertTrue(developer_images_are_available(checkout))

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
