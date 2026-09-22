from __future__ import annotations

import sys
import time
import unittest
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QCoreApplication

from ios_developer_toolkit.collection_process import CollectionProcessController
from ios_developer_toolkit.collection_protocol import CollectionEvent
from ios_developer_toolkit.qt_process import OperationResult
from ios_developer_toolkit.runtime import ExecutableCommand


class CollectionProcessControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QCoreApplication.instance() or QCoreApplication(["collection-process-tests"])

    def test_reassembles_fragmented_json_event_and_drains_terminal_output(self) -> None:
        controller = CollectionProcessController(self.application)
        events: list[CollectionEvent] = []
        results: list[OperationResult] = []
        controller.event_received.connect(events.append)
        controller.completed.connect(results.append)
        first = '{"event":"case-created","message":"created",'
        second = '"timestamp":"2026-09-22T00:00:00+00:00","path":"/tmp/toolkit-case"}'
        script = (
            f"import sys,time; sys.stdout.write({first!r}); sys.stdout.flush(); time.sleep(0.05); "
            f"sys.stdout.write({second!r})"
        )

        controller.start(
            ExecutableCommand(Path(sys.executable), ("-c", script)),
            (),
            {},
            3_000,
        )
        self._wait_for(lambda: bool(results), 3)

        self.assertEqual(results[0].outcome, "succeeded")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event, "case-created")
        self.assertEqual(events[0].path, Path("/tmp/toolkit-case"))
        self.assertEqual(results[0].stdout, (first + second).encode("utf-8"))

    def test_cancel_waits_for_case_finalization_event(self) -> None:
        controller = CollectionProcessController(self.application)
        events: list[CollectionEvent] = []
        results: list[OperationResult] = []
        controller.event_received.connect(events.append)
        controller.completed.connect(results.append)
        script = """
import json
import signal
import time

stop = False

def request_stop(signum, frame):
    global stop
    del signum, frame
    stop = True

signal.signal(signal.SIGTERM, request_stop)
print(json.dumps({"event":"case-created","message":"created","timestamp":"2026-09-22T00:00:00+00:00","path":"/tmp/toolkit-case"}), flush=True)
while not stop:
    time.sleep(0.01)
print(json.dumps({"event":"case-finished","message":"finalized","timestamp":"2026-09-22T00:00:01+00:00","path":"/tmp/toolkit-case","status":"cancelled","failures":0}), flush=True)
"""

        controller.start(
            ExecutableCommand(Path(sys.executable), ("-c", script)),
            (),
            {},
            3_000,
        )
        self._wait_for(lambda: len(events) == 1, 3)
        controller.cancel()
        self._wait_for(lambda: bool(results), 3)

        self.assertEqual(results[0].outcome, "cancelled")
        self.assertEqual([event.event for event in events], ["case-created", "case-finished"])
        self.assertEqual(events[-1].status, "cancelled")
        self.assertFalse(controller.is_running())

    def test_protocol_failure_requests_finalization_and_preserves_root_cause(self) -> None:
        controller = CollectionProcessController(self.application)
        events: list[CollectionEvent] = []
        results: list[OperationResult] = []
        controller.event_received.connect(events.append)
        controller.completed.connect(results.append)
        script = """
import json
import signal
import time

stop = False

def request_stop(signum, frame):
    global stop
    del signum, frame
    stop = True

signal.signal(signal.SIGTERM, request_stop)
print("not-json", flush=True)
while not stop:
    time.sleep(0.01)
print(json.dumps({"event":"case-finished","message":"finalized","timestamp":"2026-09-22T00:00:01+00:00","path":"/tmp/toolkit-case","status":"cancelled","failures":1}), flush=True)
"""

        controller.start(
            ExecutableCommand(Path(sys.executable), ("-c", script)),
            (),
            {},
            3_000,
        )
        self._wait_for(lambda: bool(results), 3)

        self.assertEqual(results[0].outcome, "failed")
        self.assertIn("Invalid collector event", results[0].error_message or "")
        self.assertEqual([event.event for event in events], ["case-finished"])
        self.assertFalse(controller.is_running())

    def test_forces_stop_when_finalization_deadline_expires(self) -> None:
        controller = CollectionProcessController(self.application)
        events: list[CollectionEvent] = []
        results: list[OperationResult] = []
        controller.event_received.connect(events.append)
        controller.completed.connect(results.append)
        script = """
import json
import signal
import time

signal.signal(signal.SIGTERM, signal.SIG_IGN)
print(json.dumps({"event":"case-created","message":"created","timestamp":"2026-09-22T00:00:00+00:00","path":"/tmp/toolkit-case"}), flush=True)
time.sleep(10)
"""

        controller.start(
            ExecutableCommand(Path(sys.executable), ("-c", script)),
            (),
            {},
            100,
        )
        self._wait_for(lambda: len(events) == 1, 3)
        controller.cancel()
        self._wait_for(lambda: bool(results), 3)

        self.assertEqual(results[0].outcome, "timed-out")
        self.assertIn("finalization", results[0].error_message or "")
        self.assertFalse(controller.is_running())

    def _wait_for(self, predicate: Callable[[], bool], timeout_seconds: int) -> None:
        deadline = time.monotonic() + timeout_seconds
        while not predicate() and time.monotonic() < deadline:
            self.application.processEvents()
            time.sleep(0.01)
        self.application.processEvents()


if __name__ == "__main__":
    unittest.main()
