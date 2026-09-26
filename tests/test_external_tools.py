from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path

from ios_developer_toolkit.external_tools import (
    ExternalToolInstallation,
    ExternalToolValidationError,
    discover_external_tool_executables,
    external_tool_command,
    external_tool_environment,
    external_tool_spec,
    external_tool_specs,
    inspect_external_tool_executable,
    parse_external_tool_version,
    validate_external_tool_installation,
)


class ExternalToolTests(unittest.TestCase):
    def test_catalog_has_unique_current_adapters(self) -> None:
        specs = external_tool_specs()
        self.assertEqual(tuple(spec.identifier for spec in specs), ("go-ios", "idb", "ipsw"))
        self.assertEqual(len({spec.executable_name for spec in specs}), len(specs))
        self.assertTrue(all(spec.license_name == "MIT" for spec in specs))
        self.assertTrue(all(spec.version_arguments and spec.probe_arguments for spec in specs))

    def test_discovers_expected_executable_and_records_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            spec = external_tool_spec("go-ios")
            executable_path = root / spec.executable_name
            executable_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            executable_path.chmod(executable_path.stat().st_mode | stat.S_IXUSR)

            candidates = discover_external_tool_executables(spec, root, str(root))
            executable = inspect_external_tool_executable(spec, executable_path)

            self.assertEqual(candidates, (executable_path.resolve(),))
            self.assertEqual(executable.spec_identifier, "go-ios")
            self.assertEqual(len(executable.sha256), 64)

    def test_rejects_relative_path_and_changed_executable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            spec = external_tool_spec("ipsw")
            with self.assertRaises(ExternalToolValidationError):
                inspect_external_tool_executable(spec, Path("ipsw"))

            executable_path = root / spec.executable_name
            executable_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            executable_path.chmod(0o700)
            executable = inspect_external_tool_executable(spec, executable_path)
            installation = ExternalToolInstallation(executable, "3.1.723")
            executable_path.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
            with self.assertRaises(ExternalToolValidationError):
                validate_external_tool_installation(spec, installation)

    def test_parses_each_upstream_version_or_build_shape(self) -> None:
        self.assertEqual(
            parse_external_tool_version(
                external_tool_spec("go-ios"),
                'diagnostic line\n{"version":"1.3.2"}\n',
            ),
            "1.3.2",
        )
        self.assertEqual(
            parse_external_tool_version(
                external_tool_spec("idb"),
                '{"build_date":"2026-09-22","build_time":"11:12:36"}',
            ),
            "build 2026-09-22 11:12:36",
        )
        self.assertEqual(
            parse_external_tool_version(
                external_tool_spec("ipsw"),
                "Version: 3.1.723, BuildCommit: abc123\n",
            ),
            "3.1.723",
        )
        with self.assertRaises(ExternalToolValidationError):
            parse_external_tool_version(external_tool_spec("idb"), "idb")

    def test_command_removes_tool_routing_and_secret_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            spec = external_tool_spec("idb")
            executable_path = root / spec.executable_name
            executable_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            executable_path.chmod(0o700)
            executable = inspect_external_tool_executable(spec, executable_path)
            command = external_tool_command(spec, executable)
            environment = external_tool_environment(
                {
                    "PATH": os.environ.get("PATH", ""),
                    "IDB_COMPANION": "remote.example:1234",
                    "IDB_UDID": "sensitive-target",
                },
                spec,
            )

            self.assertEqual(command.program, Path("/usr/bin/env"))
            self.assertIn("IDB_COMPANION", command.prefix_arguments)
            self.assertIn("IDB_UDID", command.prefix_arguments)
            self.assertEqual(command.prefix_arguments[-1], str(executable.path))
            self.assertNotIn("IDB_COMPANION", environment)
            self.assertNotIn("IDB_UDID", environment)


if __name__ == "__main__":
    unittest.main()
