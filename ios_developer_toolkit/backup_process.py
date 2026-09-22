from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Literal, Mapping

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal

from ios_developer_toolkit.backup_protocol import (
    BackupAction,
    BackupEvent,
    BackupRequest,
    BackupRequestError,
    parse_backup_event,
    serialize_backup_request,
)
from ios_developer_toolkit.qt_process import OperationResult, ProcessOutcome
from ios_developer_toolkit.runtime import ExecutableCommand, command_arguments, command_argv


class BackupProcessController(QObject):
    """Own one backup worker while keeping credentials out of process arguments."""

    event_received = Signal(object)
    stderr_received = Signal(bytes)
    completed = Signal(object)

    def __init__(self, parent: QObject) -> None:
        super().__init__(parent)
        self._process: QProcess | None = None
        self._command: ExecutableCommand | None = None
        self._arguments: tuple[str, ...] = ()
        self._terminate_grace_milliseconds = 0
        self._request_payload = b""
        self._stdout = bytearray()
        self._stdout_line = bytearray()
        self._stderr = bytearray()
        self._started_at = ""
        self._error_message: str | None = None
        self._stop_outcome: Literal["cancelled"] | None = None
        self._protocol_failed = False
        self._completed = False
        self._kill_timer = QTimer(self)
        self._kill_timer.setSingleShot(True)
        self._kill_timer.timeout.connect(self._kill)

    def is_running(self) -> bool:
        return self._process is not None

    def start(
        self,
        command: ExecutableCommand,
        action: BackupAction,
        request: BackupRequest,
        environment: Mapping[str, str],
        terminate_grace_milliseconds: int,
    ) -> None:
        if self.is_running():
            raise RuntimeError("Cannot start a backup process while another backup process is running")
        if action not in ("status", "backup"):
            raise ValueError(f"Unsupported backup action: {action}")
        if terminate_grace_milliseconds <= 0:
            raise ValueError(f"Backup termination grace period must be positive: {terminate_grace_milliseconds}")
        self._command = command
        self._arguments = (action,)
        self._terminate_grace_milliseconds = terminate_grace_milliseconds
        self._request_payload = serialize_backup_request(request)
        self._stdout.clear()
        self._stdout_line.clear()
        self._stderr.clear()
        self._started_at = datetime.now(timezone.utc).isoformat()
        self._error_message = None
        self._stop_outcome = None
        self._protocol_failed = False
        self._completed = False

        process = QProcess(self)
        process.setProgram(str(command.program))
        process.setArguments(list(command_arguments(command, self._arguments)))
        process_environment = QProcessEnvironment.systemEnvironment()
        for key, value in sorted(environment.items()):
            process_environment.insert(key, value)
        process.setProcessEnvironment(process_environment)
        process.started.connect(self._write_request)
        process.readyReadStandardOutput.connect(self._drain_output)
        process.readyReadStandardError.connect(self._drain_output)
        process.errorOccurred.connect(self._process_error)
        process.finished.connect(self._finished)
        self._process = process
        process.start()

    def cancel(self) -> None:
        if not self.is_running():
            return
        self._stop_outcome = "cancelled"
        self._terminate()

    def shutdown(self, terminate_timeout_milliseconds: int, kill_timeout_milliseconds: int) -> None:
        if terminate_timeout_milliseconds <= 0:
            raise ValueError(f"Shutdown termination timeout must be positive: {terminate_timeout_milliseconds}")
        if kill_timeout_milliseconds <= 0:
            raise ValueError(f"Shutdown kill timeout must be positive: {kill_timeout_milliseconds}")
        process = self._process
        if process is None:
            return
        if process.state() == QProcess.ProcessState.NotRunning:
            self._finish_once("cancelled", process.exitCode())
            return
        self._stop_outcome = "cancelled"
        self._kill_timer.stop()
        process.terminate()
        if not process.waitForFinished(terminate_timeout_milliseconds):
            process.kill()
            if not process.waitForFinished(kill_timeout_milliseconds):
                raise RuntimeError(f"Backup process did not stop after terminate and kill: {process.program()}")

    def _write_request(self) -> None:
        process = self._process
        if process is None:
            raise RuntimeError("Backup process started without an active process")
        accepted_bytes = process.write(self._request_payload)
        if accepted_bytes != len(self._request_payload):
            self._protocol_failure(
                f"Backup helper accepted {accepted_bytes} of {len(self._request_payload)} request bytes"
            )
            return
        process.closeWriteChannel()
        self._request_payload = b""

    def _drain_output(self) -> None:
        process = self._process
        if process is None:
            return
        stdout = bytes(process.readAllStandardOutput())
        stderr = bytes(process.readAllStandardError())
        if stdout:
            self._stdout.extend(stdout)
            self._stdout_line.extend(stdout)
            self._consume_complete_lines()
        if stderr:
            self._stderr.extend(stderr)
            self.stderr_received.emit(stderr)

    def _consume_complete_lines(self) -> None:
        while b"\n" in self._stdout_line and not self._protocol_failed:
            line, _, remainder = self._stdout_line.partition(b"\n")
            self._stdout_line = bytearray(remainder)
            if line.strip():
                self._consume_event_line(line)

    def _consume_event_line(self, line: bytes) -> None:
        try:
            event = parse_backup_event(line.decode("utf-8"))
        except (BackupRequestError, json.JSONDecodeError, UnicodeDecodeError) as error:
            self._protocol_failure(f"Invalid backup helper event: {error}")
            return
        self.event_received.emit(event)

    def _protocol_failure(self, message: str) -> None:
        if self._protocol_failed:
            return
        self._protocol_failed = True
        self._error_message = message
        process = self._process
        if process is not None and process.state() != QProcess.ProcessState.NotRunning:
            self._terminate()

    def _process_error(self, process_error: QProcess.ProcessError) -> None:
        process = self._process
        if process is None:
            raise RuntimeError("Backup process reported an error without an active process")
        if self._error_message is None:
            self._error_message = process.errorString()
        if process_error == QProcess.ProcessError.FailedToStart:
            self._finish_once("launch-failed", None)

    def _finished(self, exit_code: int, exit_status: QProcess.ExitStatus) -> None:
        self._drain_output()
        if self._stdout_line.strip() and not self._protocol_failed:
            line = bytes(self._stdout_line)
            self._stdout_line.clear()
            self._consume_event_line(line)
        if self._stop_outcome is not None:
            outcome: ProcessOutcome = self._stop_outcome
        elif self._protocol_failed:
            outcome = "failed"
        elif exit_status == QProcess.ExitStatus.CrashExit:
            outcome = "crashed"
        elif exit_code == 0:
            outcome = "succeeded"
        else:
            outcome = "failed"
        self._finish_once(outcome, exit_code)

    def _terminate(self) -> None:
        process = self._process
        if process is None:
            raise RuntimeError("Cannot terminate a backup process without an active process")
        if self._terminate_grace_milliseconds <= 0:
            raise RuntimeError("Backup process has no valid termination grace period")
        process.terminate()
        self._kill_timer.start(self._terminate_grace_milliseconds)

    def _kill(self) -> None:
        process = self._process
        if process is not None and process.state() != QProcess.ProcessState.NotRunning:
            process.kill()

    def _finish_once(self, outcome: ProcessOutcome, exit_code: int | None) -> None:
        if self._completed:
            return
        command = self._command
        if command is None:
            raise RuntimeError("Backup process completed without a command")
        self._drain_output()
        self._completed = True
        self._kill_timer.stop()
        self._request_payload = b""
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
