from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from ios_developer_toolkit.command_catalog import command_presets
from ios_developer_toolkit.workspace_profile import (
    AppWorkflowPreferences,
    BackupWorkflowPreferences,
    EvidenceWorkflowPreferences,
    LocationWorkflowPreferences,
    WorkspaceProfile,
    WorkspaceProfileError,
    load_workspace_profile,
    parse_workspace_profile,
    render_workspace_profile_json,
    render_workspace_profile_preview,
    workspace_profile_mapping,
    write_workspace_profile,
)


def example_profile() -> WorkspaceProfile:
    preset = command_presets()[0]
    return WorkspaceProfile(
        created_with_version="0.3.4",
        name="Release validation",
        description="Shared control defaults without targets or paths",
        default_workspace="Capability Matrix",
        ddi_source="personalized",
        command_category=preset.category,
        command_preset=preset.identifier,
        app_workflow=AppWorkflowPreferences(True, False),
        backup_workflow=BackupWorkflowPreferences(True, True),
        evidence_workflow=EvidenceWorkflowPreferences(300, True, True, True, False, True),
        location_workflow=LocationWorkflowPreferences(100, False, 5, 7, 2, 3),
    )


class WorkspaceProfileTests(unittest.TestCase):
    def test_round_trips_only_reviewed_non_sensitive_control_defaults(self) -> None:
        profile = example_profile()

        payload = workspace_profile_mapping(profile)
        parsed = parse_workspace_profile(json.loads(render_workspace_profile_json(profile)))
        preview = render_workspace_profile_preview(profile)

        self.assertEqual(parsed, profile)
        privacy = payload["privacy"]
        self.assertIsInstance(privacy, dict)
        self.assertIn("device identity and targets", privacy["schema_excludes"])
        serialized = json.dumps(payload, sort_keys=True)
        self.assertNotIn("/Users/", serialized)
        self.assertNotIn("PRIVATE-UDID", serialized)
        self.assertNotIn("password", serialized.casefold())
        self.assertIn("Importing changes visible controls only", preview)

    def test_parser_requires_known_workspace_preset_and_bounded_values(self) -> None:
        profile = example_profile()
        with self.assertRaises(WorkspaceProfileError):
            workspace_profile_mapping(replace(profile, default_workspace="Unknown"))
        with self.assertRaises(WorkspaceProfileError):
            workspace_profile_mapping(replace(profile, command_preset="unknown-preset"))
        with self.assertRaises(WorkspaceProfileError):
            workspace_profile_mapping(replace(profile, description="Stored at /Users/private/team"))
        invalid_location = replace(profile.location_workflow, route_traversals=21)
        with self.assertRaises(WorkspaceProfileError):
            workspace_profile_mapping(replace(profile, location_workflow=invalid_location))

    def test_parser_ignores_unrelated_extra_fields(self) -> None:
        payload = workspace_profile_mapping(example_profile())
        payload["future_top_level"] = "ignored"
        settings = payload["settings"]
        self.assertIsInstance(settings, dict)
        settings["future_setting"] = {"ignored": True}

        parsed = parse_workspace_profile(payload)

        self.assertEqual(parsed, example_profile())

    def test_private_file_round_trip_refuses_overwrite_and_oversize_input(self) -> None:
        temporary_directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, temporary_directory)
        destination = temporary_directory / "team-profile.json"

        written = write_workspace_profile(destination, example_profile())

        self.assertEqual(os.stat(written).st_mode & 0o777, 0o600)
        self.assertEqual(load_workspace_profile(written), example_profile())
        with self.assertRaises(WorkspaceProfileError):
            write_workspace_profile(destination, example_profile())
        oversized = temporary_directory / "oversized.json"
        oversized.write_bytes(b" " * 1_048_577)
        with self.assertRaises(WorkspaceProfileError):
            load_workspace_profile(oversized)


if __name__ == "__main__":
    unittest.main()
