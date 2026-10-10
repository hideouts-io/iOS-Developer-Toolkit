from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from ios_developer_toolkit.parity_profile import (
    ParityProfileImport,
    load_parity_profile,
    render_parity_profile_json,
    render_parity_profile_preview,
    write_parity_profile,
)
from ios_developer_toolkit.workspace_profile import (
    AppWorkflowPreferences,
    BackupWorkflowPreferences,
    EvidenceWorkflowPreferences,
    LocationWorkflowPreferences,
    WorkspaceProfile,
    WorkspaceProfileError,
    workspace_profile_mapping,
)


def native_profile() -> WorkspaceProfile:
    return WorkspaceProfile(
        "0.3.4", "Release checks", "Shared workflow controls", "Capability Matrix", "personalized",
        "Device Basics", "processes", AppWorkflowPreferences(True, False),
        BackupWorkflowPreferences(False, True), EvidenceWorkflowPreferences(60, True, False, True, False, True),
        LocationWorkflowPreferences(100, False, 5, 7, 2, 3),
    )


def swift_profile() -> dict[str, object]:
    return {
        "schemaVersion": 2, "createdWithVersion": "0.4.0", "name": "Release checks",
        "description": "Shared workflow controls", "defaultWorkspace": "evidence",
        "actionCategory": "Capture & Instruments", "selectedAction": "screenshot",
        "developerImageMechanism": "native",
        "apps": {"calculateSizes": True, "includeSystemApps": True, "installAsDeveloperPackage": False},
        "backup": {"forceFullBackup": True, "requireEncryption": True},
        "evidence": {
            "durationSeconds": 60, "includeClassicSyslog": True, "includeUnifiedLogs": True,
            "includePacketCapture": False, "includeScreenshot": True, "includeCrashReports": True,
            "includeOSLogArchive": True, "includeDVTLogging": True,
        },
        "location": {
            "timingJitterMilliseconds": 150, "ignoreRecordedTiming": False, "routeSpeedKmh": 7,
            "routeIntervalSeconds": 3, "routeTraversals": 2,
        },
    }


class ParityProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="toolkit-profile-parity-")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve()

    def load_record(self, record: dict[str, object]) -> ParityProfileImport:
        path = self.directory / "source.json"
        path.write_text(json.dumps(record), encoding="utf-8")
        return load_parity_profile(path)

    def test_native_old_profile_preserves_unspecified_new_controls(self) -> None:
        imported = self.load_record(workspace_profile_mapping(native_profile()))
        self.assertEqual(imported.profile, native_profile())
        self.assertIsNone(imported.include_system_apps)
        self.assertIsNone(imported.include_oslog_archive)
        self.assertIsNone(imported.include_instruments_logging)
        self.assertEqual(imported.notes, ())
        self.assertIn("preserve current control", render_parity_profile_preview(imported))

    def test_extended_native_profile_roundtrip_is_private_and_refuses_overwrite(self) -> None:
        imported = ParityProfileImport(native_profile(), True, False, True, ("Review-only source note",))
        written = write_parity_profile(self.directory / "native.json", imported)
        parsed = load_parity_profile(written)
        self.assertEqual(parsed.profile, imported.profile)
        self.assertEqual((parsed.include_system_apps, parsed.include_oslog_archive, parsed.include_instruments_logging), (True, False, True))
        self.assertEqual(parsed.notes, ())
        self.assertEqual(os.stat(written).st_mode & 0o777, 0o600)
        self.assertNotIn("Review-only source note", written.read_text())
        with self.assertRaisesRegex(WorkspaceProfileError, "overwrite"):
            write_parity_profile(written, imported)
        link = self.directory / "link.json"
        link.symlink_to(written)
        with self.assertRaisesRegex(WorkspaceProfileError, "overwrite"):
            write_parity_profile(link, imported)
        self.assertEqual(load_parity_profile(written), parsed)

    def test_native_optional_controls_require_explicit_booleans(self) -> None:
        for value in (None, "false", 0, [], {}):
            with self.subTest(value=value):
                record = workspace_profile_mapping(native_profile())
                settings = record["settings"]
                self.assertIsInstance(settings, dict)
                settings["parity_workflow"] = {"include_system_apps": value}
                with self.assertRaisesRegex(WorkspaceProfileError, "boolean"):
                    self.load_record(record)
        record = workspace_profile_mapping(native_profile())
        settings = record["settings"]
        self.assertIsInstance(settings, dict)
        settings["parity_workflow"] = {"include_oslog_archive": False}
        imported = self.load_record(record)
        self.assertFalse(imported.include_oslog_archive)
        self.assertIsNone(imported.include_system_apps)

    def test_actual_swift_camelcase_schema_preserves_preferences_and_visible_notes(self) -> None:
        imported = self.load_record(swift_profile())
        self.assertEqual(imported.profile.default_workspace, "Evidence Capture")
        self.assertEqual(imported.profile.ddi_source, "custom-local")
        self.assertEqual(imported.profile.command_preset, "screenshot")
        self.assertEqual(imported.profile.command_category, "Developer & DVT")
        self.assertEqual(imported.profile.location_workflow.route_speed_kmh, 7)
        self.assertEqual(imported.profile.location_workflow.route_speed_preset_kmh, 5)
        self.assertTrue(imported.profile.evidence_workflow.include_oslog)
        self.assertEqual((imported.include_system_apps, imported.include_oslog_archive, imported.include_instruments_logging), (True, True, True))
        preview = render_parity_profile_preview(imported)
        self.assertIn("different logging services", preview)
        self.assertIn("actual route speed remains 7", preview)
        self.assertIn("Python guided commands use pymobiledevice3", preview)
        native = json.loads(render_parity_profile_json(imported))
        roundtrip = self.load_record(native)
        self.assertEqual(roundtrip.profile, imported.profile)
        self.assertTrue(roundtrip.include_instruments_logging)

    def test_swift_coredevice_and_all_category_are_explicit(self) -> None:
        record = swift_profile()
        record["developerImageMechanism"] = "coreDevice"
        record["actionCategory"] = "All"
        imported = self.load_record(record)
        self.assertEqual(imported.profile.ddi_source, "core-device")
        self.assertEqual(imported.profile.command_category, "All categories")

    def test_swift_newer_optional_capture_fields_follow_source_decoder(self) -> None:
        record = swift_profile()
        evidence = record["evidence"]
        self.assertIsInstance(evidence, dict)
        del evidence["includeOSLogArchive"]
        evidence["includeDVTLogging"] = None
        imported = self.load_record(record)
        self.assertFalse(imported.include_oslog_archive)
        self.assertFalse(imported.include_instruments_logging)
        evidence["includeDVTLogging"] = "false"
        with self.assertRaisesRegex(WorkspaceProfileError, "boolean"):
            self.load_record(record)

    def test_unrepresentable_swift_settings_fail_without_inventing_defaults(self) -> None:
        changes: tuple[tuple[str, object, str], ...] = (
            ("selectedAction", None, "require a selected guided preset"),
            ("selectedAction", "instruments", "no equivalent Python guided preset"),
            ("selectedAction", "sim-erase", "no equivalent Python guided preset"),
            ("selectedAction", "unknown-action", "unknown"),
            ("developerImageMechanism", "automatic", "no automatic"),
            ("developerImageMechanism", None, "no automatic"),
            ("defaultWorkspace", "activity", "no Python profile destination"),
            ("actionCategory", "Device Actions", "not in actionCategory"),
            ("schemaVersion", True, "schemaVersion 2"),
        )
        for key, value, message in changes:
            with self.subTest(key=key, value=value):
                record = swift_profile()
                record[key] = value
                if key == "selectedAction" and value == "sim-erase":
                    record["actionCategory"] = "Simulator"
                with self.assertRaisesRegex(WorkspaceProfileError, message):
                    self.load_record(record)
        for value in (0, 9, 3601):
            with self.subTest(duration=value):
                record = swift_profile()
                evidence = record["evidence"]
                self.assertIsInstance(evidence, dict)
                evidence["durationSeconds"] = value
                with self.assertRaisesRegex(WorkspaceProfileError, "10–3600"):
                    self.load_record(record)

    def test_consumed_swift_inputs_are_strict_bounded_and_sanitized(self) -> None:
        changes: tuple[tuple[str, str, object], ...] = (
            ("apps", "includeSystemApps", 1), ("backup", "requireEncryption", "true"),
            ("location", "routeTraversals", 21), ("location", "routeSpeedKmh", True),
            ("evidence", "durationSeconds", True),
        )
        for section, field, value in changes:
            with self.subTest(section=section, field=field):
                record = swift_profile()
                nested = record[section]
                self.assertIsInstance(nested, dict)
                nested[field] = value
                with self.assertRaises(WorkspaceProfileError):
                    self.load_record(record)
        record = swift_profile()
        record["description"] = "Stored at /Users/private/location"
        with self.assertRaisesRegex(WorkspaceProfileError, "local path"):
            self.load_record(record)

    def test_source_only_private_extra_fields_are_not_forwarded(self) -> None:
        record = swift_profile()
        record["device"] = "PRIVATE-DEVICE"
        record["outputPath"] = "/Users/private/evidence"
        imported = self.load_record(record)
        serialized = render_parity_profile_json(imported)
        self.assertNotIn("PRIVATE-DEVICE", serialized)
        self.assertNotIn("/Users/private", serialized)

    def test_duplicate_schema_invalid_utf8_and_size_boundaries_are_explicit(self) -> None:
        path = self.directory / "source.json"
        for content in (b'{"schema_version":1,"schema_version":1}', b'\xff', b'[' * 2000):
            path.write_bytes(content)
            with self.assertRaises(WorkspaceProfileError):
                load_parity_profile(path)
        record = swift_profile()
        record["schema_version"] = 1
        with self.assertRaisesRegex(WorkspaceProfileError, "conflicting"):
            self.load_record(record)
        record = swift_profile()
        record["unused"] = "x" * 65536
        with self.assertRaisesRegex(WorkspaceProfileError, "64 KiB"):
            self.load_record(record)
        path.write_bytes(b" " * 1048577)
        with self.assertRaisesRegex(WorkspaceProfileError, "exceeds"):
            load_parity_profile(path)


if __name__ == "__main__":
    unittest.main()
