from __future__ import annotations

import os
import plistlib
import stat
import tempfile
import unittest
from pathlib import Path

from ios_developer_toolkit.mvt_connector import (
    MVTAnalysisRequest,
    MVTBackup,
    MVTInstallation,
    MVTValidationError,
    create_mvt_analysis_request,
    inspect_mvt_backup,
    inspect_mvt_executable,
    mvt_analysis_arguments,
    mvt_command,
    mvt_environment,
    parse_mvt_version_output,
    validate_mvt_output,
)


class MVTConnectorTests(unittest.TestCase):
    def test_inspects_executable_and_removes_inherited_secret_routes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            executable_path = root / "mvt-ios"
            executable_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            executable_path.chmod(executable_path.stat().st_mode | stat.S_IXUSR)
            executable = inspect_mvt_executable(executable_path)
            command = mvt_command(executable)

            self.assertEqual(len(executable.sha256), 64)
            self.assertEqual(command.program, Path("/usr/bin/env"))
            self.assertIn("MVT_IOS_BACKUP_PASSWORD", command.prefix_arguments)
            self.assertEqual(command.prefix_arguments[-1], str(executable_path.resolve()))

    def test_parses_current_version_output_and_rejects_unrecognized_output(self) -> None:
        self.assertEqual(
            parse_mvt_version_output("MVT - Mobile Verification Toolkit\nVersion: 2026.9.21\n"),
            "2026.9.21",
        )
        with self.assertRaises(MVTValidationError):
            parse_mvt_version_output("unknown program\n")

    def test_resolves_one_backup_and_rejects_encrypted_input(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            backup = root / "device-backup"
            backup.mkdir()
            (backup / "Manifest.db").write_bytes(b"database")
            (backup / "Info.plist").write_bytes(plistlib.dumps({"Device Name": "Example"}))

            inspection = inspect_mvt_backup(root)
            self.assertEqual(inspection.path, backup.resolve())
            self.assertIsNone(inspection.encrypted)

            (backup / "Manifest.plist").write_bytes(plistlib.dumps({"IsEncrypted": True}))
            with self.assertRaises(MVTValidationError):
                inspect_mvt_backup(backup)

    def test_requires_new_output_outside_the_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            backup_path = root / "backup"
            backup_path.mkdir()
            backup = MVTBackup(backup_path.resolve(), False)
            isolated_output = validate_mvt_output(root / "analysis", backup)
            self.assertEqual(isolated_output, (root / "analysis").resolve())
            with self.assertRaises(MVTValidationError):
                validate_mvt_output(backup_path / "analysis", backup)
            existing = root / "existing"
            existing.mkdir()
            with self.assertRaises(MVTValidationError):
                validate_mvt_output(existing, backup)

    def test_builds_offline_analysis_without_password_or_implicit_iocs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            executable_path = root / "mvt-ios"
            executable_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            executable_path.chmod(executable_path.stat().st_mode | stat.S_IXUSR)
            executable = inspect_mvt_executable(executable_path)
            installation = MVTInstallation(executable, "2026.9.21")
            backup_path = root / "backup"
            backup_path.mkdir()
            (backup_path / "Manifest.db").write_bytes(b"database")
            (backup_path / "Info.plist").write_bytes(plistlib.dumps({}))
            ioc_path = root / "indicators.stix2"
            ioc_path.write_text("{}", encoding="utf-8")
            request = create_mvt_analysis_request(
                installation,
                backup_path,
                root / "analysis",
                (ioc_path,),
                True,
                True,
                False,
            )
            arguments = mvt_analysis_arguments(request)

            self.assertIsInstance(request, MVTAnalysisRequest)
            self.assertIn("--fast", arguments)
            self.assertIn("--hashes", arguments)
            self.assertIn(str(ioc_path.resolve()), arguments)
            self.assertNotIn("password", " ".join(arguments).casefold())

            config_directory = root / "config"
            config_directory.mkdir()
            environment = mvt_environment(
                {
                    "PATH": os.environ.get("PATH", ""),
                    "MVT_IOS_BACKUP_PASSWORD": "secret",
                    "MVT_STIX2": "/unexpected",
                },
                config_directory,
                False,
            )
            self.assertNotIn("MVT_IOS_BACKUP_PASSWORD", environment)
            self.assertNotIn("MVT_STIX2", environment)
            self.assertEqual(environment["MVT_NETWORK_ACCESS_ALLOWED"], "false")


if __name__ == "__main__":
    unittest.main()
