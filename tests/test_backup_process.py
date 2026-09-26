from __future__ import annotations

import sys
import time
import unittest
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QCoreApplication

from ios_developer_toolkit.backup_process import BackupProcessController
from ios_developer_toolkit.backup_protocol import BackupEvent, BackupRequest
from ios_developer_toolkit.qt_process import OperationResult
from ios_developer_toolkit.runtime import ExecutableCommand


class BackupProcessControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QCoreApplication.instance() or QCoreApplication(["backup-process-tests"])

    def test_sends_private_request_over_stdin_and_emits_typed_event(self) -> None:
        controller = BackupProcessController(self.application)
        events: list[BackupEvent] = []
        results: list[OperationResult] = []
        controller.event_received.connect(events.append)
        controller.completed.connect(results.append)
        password = "private-smoke-password"
        script = (
            "import json,sys; request=json.load(sys.stdin); "
            "print(json.dumps({'event':'encryption-state','message':'checked','encrypted':"
            "request['require_encryption']}))"
        )

        controller.start(
            ExecutableCommand(Path(sys.executable), ("-c", script)),
            "status",
            BackupRequest("test-device", Path("/tmp"), True, password, False),
            {},
            500,
        )
        self._wait_for(lambda: bool(results), 3)

        self.assertEqual(results[0].outcome, "succeeded")
        self.assertEqual(events, [BackupEvent("encryption-state", "checked", None, True, None)])
        self.assertNotIn(password, results[0].argv)
        self.assertNotIn(password.encode("utf-8"), results[0].stdout)
        self.assertFalse(controller.is_running())

    def test_rejects_malformed_worker_event_and_stops_process(self) -> None:
        controller = BackupProcessController(self.application)
        results: list[OperationResult] = []
        controller.completed.connect(results.append)
        script = "import sys,time; sys.stdin.read(); print('not-json', flush=True); time.sleep(10)"

        controller.start(
            ExecutableCommand(Path(sys.executable), ("-c", script)),
            "backup",
            BackupRequest("test-device", Path("/tmp"), False, "", False),
            {},
            500,
        )
        self._wait_for(lambda: bool(results), 3)

        self.assertEqual(results[0].outcome, "failed")
        self.assertIsNotNone(results[0].error_message)
        self.assertIn("Invalid backup helper event", results[0].error_message or "")
        self.assertFalse(controller.is_running())

    def test_cancels_long_running_worker_once(self) -> None:
        controller = BackupProcessController(self.application)
        results: list[OperationResult] = []
        controller.completed.connect(results.append)
        script = "import sys,time; sys.stdin.read(); time.sleep(10)"

        controller.start(
            ExecutableCommand(Path(sys.executable), ("-c", script)),
            "backup",
            BackupRequest("test-device", Path("/tmp"), False, "", False),
            {},
            500,
        )
        self._wait_for(controller.is_running, 1)
        controller.cancel()
        self._wait_for(lambda: bool(results), 3)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].outcome, "cancelled")
        self.assertFalse(controller.is_running())

    def _wait_for(self, predicate: Callable[[], bool], timeout_seconds: int) -> None:
        deadline = time.monotonic() + timeout_seconds
        while not predicate() and time.monotonic() < deadline:
            self.application.processEvents()
            time.sleep(0.01)
        self.application.processEvents()


if __name__ == "__main__":
    unittest.main()
