from __future__ import annotations

import plistlib
import sys
import tempfile
import unittest
from pathlib import Path

from ios_developer_toolkit.entrypoint import dispatch_internal, parsed_worker
from ios_developer_toolkit.runtime import (
    ExecutableCommand,
    FrozenExecutableError,
    command_arguments,
    command_argv,
    command_text,
    frozen_executable_path,
    macos_bundle_executable,
    pymobiledevice3_command,
    worker_command,
)


class ExecutableCommandTests(unittest.TestCase):
    def test_composes_prefix_and_requested_arguments_without_mutation(self) -> None:
        command = ExecutableCommand(Path("/Applications/Toolkit"), ("--internal", "pmd3"))
        requested = ("usbmux", "list")

        self.assertEqual(
            command_arguments(command, requested),
            ("--internal", "pmd3", "usbmux", "list"),
        )
        self.assertEqual(
            command_argv(command, requested),
            ("/Applications/Toolkit", "--internal", "pmd3", "usbmux", "list"),
        )
        self.assertEqual(requested, ("usbmux", "list"))

    def test_command_text_quotes_paths_for_display(self) -> None:
        command = ExecutableCommand(Path("/Applications/iOS Developer Toolkit"), ())

        self.assertEqual(
            command_text(command, ("apps", "install", "/tmp/Test App.ipa")),
            "'/Applications/iOS Developer Toolkit' apps install '/tmp/Test App.ipa'",
        )

    def test_source_commands_use_installed_entry_points_and_modules(self) -> None:
        self.assertEqual(pymobiledevice3_command().program, Path(sys.executable).with_name("pymobiledevice3"))
        self.assertEqual(
            worker_command("collector").prefix_arguments,
            ("-m", "ios_developer_toolkit.collector"),
        )

    def test_frozen_macos_runtime_uses_bundle_plist_launcher(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            contents = Path(temporary_directory) / "Toolkit.app" / "Contents"
            macos_directory = contents / "MacOS"
            macos_directory.mkdir(parents=True)
            launcher = macos_directory / "ToolkitLauncher"
            launcher.write_bytes(b"launcher")
            launcher.chmod(0o755)
            with (contents / "Info.plist").open("wb") as plist_file:
                plistlib.dump({"CFBundleExecutable": launcher.name}, plist_file)

            resolved = frozen_executable_path(
                macos_directory / "python",
                macos_directory / "ios_developer_toolkit" / "runtime.py",
                macos_directory / "python",
                "darwin",
            )

            self.assertEqual(resolved, launcher.resolve())

    def test_frozen_macos_runtime_rejects_missing_bundle_launcher(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            contents = Path(temporary_directory) / "Toolkit.app" / "Contents"
            macos_directory = contents / "MacOS"
            macos_directory.mkdir(parents=True)
            with (contents / "Info.plist").open("wb") as plist_file:
                plistlib.dump({"CFBundleExecutable": "MissingLauncher"}, plist_file)

            with self.assertRaisesRegex(FrozenExecutableError, "does not exist"):
                macos_bundle_executable(
                    macos_directory / "python",
                    macos_directory / "ios_developer_toolkit" / "runtime.py",
                )

    def test_non_macos_frozen_runtime_requires_an_executable_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            executable = Path(temporary_directory) / "toolkit"
            executable.write_bytes(b"launcher")
            executable.chmod(0o644)

            with self.assertRaisesRegex(FrozenExecutableError, "missing or not executable"):
                frozen_executable_path(Path("unused"), Path("unused"), executable, "linux")


class InternalDispatchTests(unittest.TestCase):
    def test_dispatch_requires_a_supported_internal_mode_and_worker(self) -> None:
        self.assertIsNone(dispatch_internal(()))
        self.assertIsNone(dispatch_internal(("--unrelated",)))
        with self.assertRaisesRegex(ValueError, "Unsupported internal worker"):
            parsed_worker("unknown")


if __name__ == "__main__":
    unittest.main()
