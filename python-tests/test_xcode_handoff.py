from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from ios_developer_toolkit.xcode_handoff import (
    XcodeHandoffError,
    coredevice_details_handoff,
    rvi_list_handoff,
    validated_xcode_artifact,
    validated_xcode_project,
    xcode_project_handoff,
)


class XcodeHandoffTests(unittest.TestCase):
    @unittest.skipIf(shutil.which("xcrun") is None, "xcrun is unavailable")
    def test_builds_selected_device_coredevice_details_command(self) -> None:
        command, arguments = coredevice_details_handoff(" 00008110-001122334455001E ")

        self.assertTrue(command.program.is_file())
        self.assertTrue(os.access(command.program, os.X_OK))
        self.assertEqual(
            arguments,
            (
                "devicectl",
                "device",
                "info",
                "details",
                "--device",
                "00008110-001122334455001E",
                "--timeout",
                "30",
            ),
        )

    @unittest.skipUnless(
        Path("/Library/Apple/usr/bin/rvictl").is_file() or shutil.which("rvictl") is not None,
        "rvictl is unavailable",
    )
    def test_resolves_read_only_rvi_inventory_command(self) -> None:
        command, arguments = rvi_list_handoff()

        self.assertTrue(command.program.is_file())
        self.assertTrue(os.access(command.program, os.X_OK))
        self.assertEqual(arguments, ("-l",))

    def test_validates_native_xcode_projects_results_and_traces(self) -> None:
        temporary_directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, temporary_directory)
        project = temporary_directory / "Toolkit.xcodeproj"
        result = temporary_directory / "Toolkit.xcresult"
        trace = temporary_directory / "Toolkit.trace"
        package = temporary_directory / "Package.swift"
        for directory in (project, result, trace):
            directory.mkdir()
        package.write_text("// swift-tools-version: 6.0\n", encoding="utf-8")

        self.assertEqual(validated_xcode_project(project), project.resolve())
        self.assertEqual(validated_xcode_project(package), package.resolve())
        self.assertEqual(validated_xcode_artifact(result), result.resolve())
        self.assertEqual(validated_xcode_artifact(trace), trace.resolve())
        command, arguments = xcode_project_handoff(project)
        self.assertTrue(command.program.is_file())
        self.assertEqual(arguments, ("xed", str(project.resolve())))

    def test_rejects_missing_or_unrelated_handoff_target(self) -> None:
        temporary_directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, temporary_directory)
        unrelated = temporary_directory / "notes.txt"
        unrelated.write_text("not an Xcode artifact\n", encoding="utf-8")

        with self.assertRaises(XcodeHandoffError):
            validated_xcode_artifact(unrelated)
        with self.assertRaises(XcodeHandoffError):
            validated_xcode_project(temporary_directory / "Missing.xcodeproj")


if __name__ == "__main__":
    unittest.main()
