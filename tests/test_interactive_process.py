from __future__ import annotations

import sys
import time
import unittest
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QCoreApplication

from ios_developer_toolkit.interactive_process import InteractiveProcessController
from ios_developer_toolkit.qt_process import OperationResult
from ios_developer_toolkit.runtime import ExecutableCommand


class InteractiveProcessControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QCoreApplication.instance() or QCoreApplication(["interactive-process-tests"])

    def test_returns_terminal_output_and_status(self) -> None:
        controller = InteractiveProcessController(self.application)
        results: list[OperationResult] = []
        controller.completed.connect(results.append)

        controller.start(
            ExecutableCommand(Path(sys.executable), ()),
            ("-c", "import sys; sys.stdout.write('ready'); sys.stderr.write('notice')"),
            {},
            Path.cwd(),
            500,
        )
        self._wait_for(lambda: bool(results), 3)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].outcome, "succeeded")
        self.assertEqual(results[0].stdout, b"ready")
        self.assertEqual(results[0].stderr, b"notice")
        self.assertFalse(controller.is_running())

    def test_cancels_streaming_command_once(self) -> None:
        controller = InteractiveProcessController(self.application)
        results: list[OperationResult] = []
        controller.completed.connect(results.append)

        controller.start(
            ExecutableCommand(Path(sys.executable), ()),
            ("-c", "import time; time.sleep(10)"),
            {},
            Path.cwd(),
            500,
        )
        controller.cancel()
        controller.cancel()
        self._wait_for(lambda: bool(results), 3)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].outcome, "cancelled")
        self.assertFalse(controller.is_running())

    def test_reports_launch_failure(self) -> None:
        controller = InteractiveProcessController(self.application)
        results: list[OperationResult] = []
        controller.completed.connect(results.append)

        controller.start(ExecutableCommand(Path("/missing/interactive-tool"), ()), (), {}, Path.cwd(), 500)
        self._wait_for(lambda: bool(results), 3)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].outcome, "launch-failed")
        self.assertTrue(results[0].error_message)
        self.assertFalse(controller.is_running())

    def _wait_for(self, predicate: Callable[[], bool], timeout_seconds: int) -> None:
        deadline = time.monotonic() + timeout_seconds
        while not predicate() and time.monotonic() < deadline:
            self.application.processEvents()
            time.sleep(0.01)
        self.application.processEvents()


if __name__ == "__main__":
    unittest.main()
