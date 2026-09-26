from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Literal, Mapping, Sequence

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal

from ios_developer_toolkit.collection_protocol import (
    CollectionEvent,
    CollectionProtocolError,
    parse_collection_event,
)
from ios_developer_toolkit.qt_process import OperationResult, ProcessOutcome
from ios_developer_toolkit.runtime import ExecutableCommand, command_arguments, command_argv


class CollectionProcessController(QObject):
    """Own one evidence collector and preserve its graceful finalization window."""

    stdout_received = Signal(bytes)
    stderr_received = Signal(bytes)
    event_received = Signal(object)
    completed = Signal(object)

    def __init__(self, parent: QObject) -> None:
        super().__init__(parent)
        self._process: QProcess | None = None
        self._command: ExecutableCommand | None = None
        self._arguments: tuple[str, ...] = ()
        self._stdout = bytearray()
        self._stdout_line = bytearray()
        self._stderr = bytearray()
        self._started_at = ""
        self._error_message: str | None = None
        self._stop_outcome: Literal["cancelled", "timed-out"] | None = None
        self._protocol_failed = False
        self._completed = False
        self._finalization_timeout_milliseconds = 0
        self._finalization_timer = QTimer(self)
        self._finalization_timer.setSingleShot(True)
        self._finalization_timer.timeout.connect(self._force_stop_after_finalization_timeout)

    def is_running(self) -> bool:
        return self._process is not None

    def start(
        self,
        command: ExecutableCommand,
        arguments: Sequence[str],
        environment: Mapping[str, str],
        finalization_timeout_milliseconds: int,
    ) -> None:
        if self.is_running():
            raise RuntimeError("Cannot start an evidence collection while another collection is running")
        if finalization_timeout_milliseconds <= 0:
            raise ValueError(
                f"Collection finalization timeout must be positive: {finalization_timeout_milliseconds}"
            )
        self._command = command
        self._arguments = tuple(arguments)
        self._stdout.clear()
        self._stdout_line.clear()
        self._stderr.clear()
        self._started_at = datetime.now(timezone.utc).isoformat()
        self._error_message = None
        self._stop_outcome = None
        self._protocol_failed = False
        self._completed = False
        self._finalization_timeout_milliseconds = finalization_timeout_milliseconds

        process = QProcess(self)
        process.setProgram(str(command.program))
        process.setArguments(list(command_arguments(command, self._arguments)))
        process_environment = QProcessEnvironment.systemEnvironment()
        for key, value in sorted(environment.items()):
            process_environment.insert(key, value)
        process.setProcessEnvironment(process_environment)
        process.readyReadStandardOutput.connect(self._drain_output)
        process.readyReadStandardError.connect(self._drain_output)
        process.errorOccurred.connect(self._process_error)
        process.finished.connect(self._finished)
        self._process = process
        process.start()

    def cancel(self) -> None:
        process = self._process
        if process is None or process.state() == QProcess.ProcessState.NotRunning:
            return
        if self._stop_outcome == "cancelled":
            return
        self._stop_outcome = "cancelled"
        self._request_graceful_stop()

    def shutdown(self, terminate_timeout_milliseconds: int, kill_timeout_milliseconds: int) -> None:
        if terminate_timeout_milliseconds <= 0:
            raise ValueError(f"Shutdown termination timeout must be positive: {terminate_timeout_milliseconds}")
        if kill_timeout_milliseconds <= 0:
            raise ValueError(f"Shutdown kill timeout must be positive: {kill_timeout_milliseconds}")
        process = self._process
        if process is None:
            return
        self._stop_outcome = "cancelled"
        self._finalization_timer.stop()
        if process.state() != QProcess.ProcessState.NotRunning:
            process.terminate()
            if not process.waitForFinished(terminate_timeout_milliseconds):
                process.kill()
                if not process.waitForFinished(kill_timeout_milliseconds):
                    raise RuntimeError(f"Collector did not stop after terminate and kill: {process.program()}")
        else:
            self._finish_once("cancelled", process.exitCode())

    def _drain_output(self) -> None:
        process = self._process
        if process is None:
            return
        stdout = bytes(process.readAllStandardOutput())
        stderr = bytes(process.readAllStandardError())
        if stdout:
            self._stdout.extend(stdout)
            self._stdout_line.extend(stdout)
            self.stdout_received.emit(stdout)
            self._consume_complete_lines()
        if stderr:
            self._stderr.extend(stderr)
            self.stderr_received.emit(stderr)

    def _consume_complete_lines(self) -> None:
        while b"\n" in self._stdout_line:
            line, _, remainder = self._stdout_line.partition(b"\n")
            self._stdout_line = bytearray(remainder)
            if line.strip():
                self._consume_event_line(line)

    def _consume_event_line(self, line: bytes) -> None:
        try:
            event = parse_collection_event(line.decode("utf-8"))
        except (CollectionProtocolError, json.JSONDecodeError, UnicodeDecodeError) as error:
            self._protocol_failure(f"Invalid collector event: {error}")
            return
        self.event_received.emit(event)

    def _protocol_failure(self, message: str) -> None:
        if self._protocol_failed:
            return
        self._protocol_failed = True
        self._error_message = message
        process = self._process
        if process is not None and process.state() != QProcess.ProcessState.NotRunning:
            self._request_graceful_stop()

    def _request_graceful_stop(self) -> None:
        process = self._process
        if process is None:
            raise RuntimeError("Cannot stop evidence collection without an active process")
        if process.state() == QProcess.ProcessState.NotRunning:
            return
        process.terminate()
        self._finalization_timer.start(self._finalization_timeout_milliseconds)

    def _force_stop_after_finalization_timeout(self) -> None:
        process = self._process
        if process is None or process.state() == QProcess.ProcessState.NotRunning:
            return
        if not self._protocol_failed:
            self._stop_outcome = "timed-out"
            self._error_message = "Collector did not finish evidence finalization before the safety deadline"
        process.kill()

    def _process_error(self, process_error: QProcess.ProcessError) -> None:
        process = self._process
        if process is None:
            raise RuntimeError("Collector reported an error without an active process")
        if self._error_message is None:
            self._error_message = process.errorString()
        if process_error == QProcess.ProcessError.FailedToStart:
            self._finish_once("launch-failed", None)

    def _finished(self, exit_code: int, exit_status: QProcess.ExitStatus) -> None:
        self._drain_output()
        if self._stdout_line.strip():
            line = bytes(self._stdout_line)
            self._stdout_line.clear()
            self._consume_event_line(line)
        if self._protocol_failed:
            outcome: ProcessOutcome = "failed"
        elif self._stop_outcome is not None:
            outcome = self._stop_outcome
        elif exit_status == QProcess.ExitStatus.CrashExit:
            outcome = "crashed"
        elif exit_code == 0:
            outcome = "succeeded"
        else:
            outcome = "failed"
        self._finish_once(outcome, exit_code)

    def _finish_once(self, outcome: ProcessOutcome, exit_code: int | None) -> None:
        if self._completed:
            return
        command = self._command
        if command is None:
            raise RuntimeError("Collector completed without a command")
        self._drain_output()
        self._completed = True
        self._finalization_timer.stop()
        result = OperationResult(
            command_argv(command, self._arguments),
            outcome,
            self._started_at,
            datetime.now(timezone.utc).isoformat(),
            exit_code,
            self._error_message,
            bytes(self._stdout),
            bytes(self._stderr),
        )
        process = self._process
        self._process = None
        if process is not None:
            process.deleteLater()
        self.completed.emit(result)
