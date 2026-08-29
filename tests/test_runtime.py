from __future__ import annotations

import sys
import unittest
from pathlib import Path

from ios_developer_toolkit.entrypoint import dispatch_internal, parsed_worker
from ios_developer_toolkit.runtime import (
    ExecutableCommand,
    command_arguments,
    command_argv,
    command_text,
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


class InternalDispatchTests(unittest.TestCase):
    def test_dispatch_requires_a_supported_internal_mode_and_worker(self) -> None:
        self.assertIsNone(dispatch_internal(()))
        self.assertIsNone(dispatch_internal(("--unrelated",)))
        with self.assertRaisesRegex(ValueError, "Unsupported internal worker"):
            parsed_worker("unknown")


if __name__ == "__main__":
    unittest.main()
