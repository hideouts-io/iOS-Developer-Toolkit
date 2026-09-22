from __future__ import annotations

import sys
import time
import unittest
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QCoreApplication

from ios_developer_toolkit.qt_process import FiniteProcessController, OperationResult, finite_process_request
from ios_developer_toolkit.runtime import ExecutableCommand


class FiniteProcessControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QCoreApplication.instance() or QCoreApplication(["finite-process-tests"])

    def test_returns_terminal_stdout_stderr_and_exit_status(self) -> None:
        controller = FiniteProcessController(self.application)
        results: list[OperationResult] = []
        controller.completed.connect(results.append)
        request = finite_process_request(
            ExecutableCommand(Path(sys.executable), ()),
            ("-c", "import sys; sys.stdout.write('ready'); sys.stderr.write('notice')"),
            {},
            3_000,
            500,
        )

        controller.start(request)
        self._wait_for(lambda: bool(results), 3)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].outcome, "succeeded")
        self.assertEqual(results[0].exit_code, 0)
        self.assertEqual(results[0].stdout, b"ready")
        self.assertEqual(results[0].stderr, b"notice")
        self.assertEqual(results[0].argv, (sys.executable, "-c", request.arguments[1]))
        self.assertFalse(controller.is_running())

    def test_times_out_a_finite_process_once(self) -> None:
        controller = FiniteProcessController(self.application)
        results: list[OperationResult] = []
        controller.completed.connect(results.append)
        request = finite_process_request(
            ExecutableCommand(Path(sys.executable), ()),
            ("-c", "import time; time.sleep(10)"),
            {},
            100,
            500,
        )

        controller.start(request)
        self._wait_for(lambda: bool(results), 3)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].outcome, "timed-out")
        self.assertFalse(controller.is_running())

    def test_cancels_a_finite_process_once(self) -> None:
        controller = FiniteProcessController(self.application)
        results: list[OperationResult] = []
        controller.completed.connect(results.append)
        request = finite_process_request(
            ExecutableCommand(Path(sys.executable), ()),
            ("-c", "import time; time.sleep(10)"),
            {},
            3_000,
            500,
        )

        controller.start(request)
        controller.cancel()
        self._wait_for(lambda: bool(results), 3)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].outcome, "cancelled")
        self.assertFalse(controller.is_running())

    def test_relaunches_without_reusing_previous_output(self) -> None:
        controller = FiniteProcessController(self.application)
        results: list[OperationResult] = []
        controller.completed.connect(results.append)
        first = finite_process_request(
            ExecutableCommand(Path(sys.executable), ()),
            ("-c", "print('first')"),
            {},
            3_000,
            500,
        )
        second = finite_process_request(
            ExecutableCommand(Path(sys.executable), ()),
            ("-c", "print('second')"),
            {},
            3_000,
            500,
        )

        controller.start(first)
        self._wait_for(lambda: len(results) == 1, 3)
        controller.start(second)
        self._wait_for(lambda: len(results) == 2, 3)

        self.assertEqual(results[0].stdout, b"first\n")
        self.assertEqual(results[1].stdout, b"second\n")
        self.assertFalse(controller.is_running())

    def test_reports_operating_system_error_when_launch_fails(self) -> None:
        controller = FiniteProcessController(self.application)
        results: list[OperationResult] = []
        controller.completed.connect(results.append)
        request = finite_process_request(
            ExecutableCommand(Path("/path/that/does/not/exist"), ()),
            (),
            {},
            3_000,
            500,
        )

        controller.start(request)
        self._wait_for(lambda: bool(results), 3)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].outcome, "launch-failed")
        self.assertIsNotNone(results[0].error_message)
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
