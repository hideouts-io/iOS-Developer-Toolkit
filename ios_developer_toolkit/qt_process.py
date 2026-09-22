from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Mapping, Sequence

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal

from ios_developer_toolkit.runtime import ExecutableCommand, command_arguments, command_argv


ProcessOutcome = Literal["succeeded", "failed", "crashed", "launch-failed", "timed-out", "cancelled"]


@dataclass(frozen=True)
class FiniteProcessRequest:
    command: ExecutableCommand
    arguments: tuple[str, ...]
    environment: tuple[tuple[str, str], ...]
    timeout_milliseconds: int
    terminate_grace_milliseconds: int


@dataclass(frozen=True)
class OperationResult:
    argv: tuple[str, ...]
    outcome: ProcessOutcome
    started_at: str
    finished_at: str
    exit_code: int | None
    stdout: bytes
    stderr: bytes


def finite_process_request(
    command: ExecutableCommand,
    arguments: Sequence[str],
    environment: Mapping[str, str],
    timeout_milliseconds: int,
    terminate_grace_milliseconds: int,
) -> FiniteProcessRequest:
    if timeout_milliseconds <= 0:
        raise ValueError(f"Process timeout must be positive: {timeout_milliseconds}")
    if terminate_grace_milliseconds <= 0:
        raise ValueError(f"Process termination grace period must be positive: {terminate_grace_milliseconds}")
    return FiniteProcessRequest(
        command,
        tuple(arguments),
        tuple(sorted(environment.items())),
        timeout_milliseconds,
        terminate_grace_milliseconds,
    )


class FiniteProcessController(QObject):
    """Own one bounded QProcess and emit one terminal typed result."""

    stdout_received = Signal(bytes)
    stderr_received = Signal(bytes)
    completed = Signal(object)

    def __init__(self, parent: QObject) -> None:
        super().__init__(parent)
        self._process: QProcess | None = None
        self._request: FiniteProcessRequest | None = None
        self._stdout = bytearray()
        self._stderr = bytearray()
        self._started_at = ""
        self._stop_outcome: Literal["timed-out", "cancelled"] | None = None
        self._completed = False
        self._timeout_timer = QTimer(self)
        self._timeout_timer.setSingleShot(True)
        self._timeout_timer.timeout.connect(self._timeout)
        self._kill_timer = QTimer(self)
        self._kill_timer.setSingleShot(True)
        self._kill_timer.timeout.connect(self._kill)

    def is_running(self) -> bool:
        process = self._process
        return process is not None and process.state() != QProcess.ProcessState.NotRunning

    def start(self, request: FiniteProcessRequest) -> None:
        if self.is_running():
            raise RuntimeError("Cannot start a finite process while another process is running")
        self._request = request
        self._stdout.clear()
        self._stderr.clear()
        self._started_at = datetime.now(timezone.utc).isoformat()
        self._stop_outcome = None
        self._completed = False

        process = QProcess(self)
        process.setProgram(str(request.command.program))
        process.setArguments(list(command_arguments(request.command, request.arguments)))
        process_environment = QProcessEnvironment.systemEnvironment()
        for key, value in request.environment:
            process_environment.insert(key, value)
        process.setProcessEnvironment(process_environment)
        process.readyReadStandardOutput.connect(self._drain_output)
        process.readyReadStandardError.connect(self._drain_output)
        process.errorOccurred.connect(self._process_error)
        process.finished.connect(self._finished)
        self._process = process
        self._timeout_timer.start(request.timeout_milliseconds)
        process.start()

    def cancel(self) -> None:
        if not self.is_running():
            return
        self._stop_process("cancelled")

    def shutdown(self, terminate_timeout_milliseconds: int, kill_timeout_milliseconds: int) -> None:
        if terminate_timeout_milliseconds <= 0:
            raise ValueError(f"Shutdown termination timeout must be positive: {terminate_timeout_milliseconds}")
        if kill_timeout_milliseconds <= 0:
            raise ValueError(f"Shutdown kill timeout must be positive: {kill_timeout_milliseconds}")
        process = self._process
        if process is None or process.state() == QProcess.ProcessState.NotRunning:
            return
        self._stop_outcome = "cancelled"
        self._timeout_timer.stop()
        self._kill_timer.stop()
        process.terminate()
        if not process.waitForFinished(terminate_timeout_milliseconds):
            process.kill()
            if not process.waitForFinished(kill_timeout_milliseconds):
                raise RuntimeError(f"Process did not stop after terminate and kill: {process.program()}")

    def _drain_output(self) -> None:
        process = self._process
        if process is None:
            return
        stdout = bytes(process.readAllStandardOutput())
        stderr = bytes(process.readAllStandardError())
        if stdout:
            self._stdout.extend(stdout)
            self.stdout_received.emit(stdout)
        if stderr:
            self._stderr.extend(stderr)
            self.stderr_received.emit(stderr)

    def _process_error(self, process_error: QProcess.ProcessError) -> None:
        if process_error == QProcess.ProcessError.FailedToStart:
            self._finish_once("launch-failed", None)

    def _finished(self, exit_code: int, exit_status: QProcess.ExitStatus) -> None:
        self._drain_output()
        if self._stop_outcome is not None:
            outcome: ProcessOutcome = self._stop_outcome
        elif exit_status == QProcess.ExitStatus.CrashExit:
            outcome = "crashed"
        elif exit_code == 0:
            outcome = "succeeded"
        else:
            outcome = "failed"
        self._finish_once(outcome, exit_code)

    def _timeout(self) -> None:
        if self.is_running():
            self._stop_process("timed-out")

    def _stop_process(self, outcome: Literal["timed-out", "cancelled"]) -> None:
        process = self._process
        request = self._request
        if process is None or request is None:
            raise RuntimeError("Cannot stop a finite process without an active request")
        self._stop_outcome = outcome
        self._timeout_timer.stop()
        process.terminate()
        self._kill_timer.start(request.terminate_grace_milliseconds)

    def _kill(self) -> None:
        process = self._process
        if process is not None and process.state() != QProcess.ProcessState.NotRunning:
            process.kill()

    def _finish_once(self, outcome: ProcessOutcome, exit_code: int | None) -> None:
        if self._completed:
            return
        request = self._request
        if request is None:
            raise RuntimeError("Finite process completed without a request")
        self._drain_output()
        self._completed = True
        self._timeout_timer.stop()
        self._kill_timer.stop()
        result = OperationResult(
            command_argv(request.command, request.arguments),
            outcome,
            self._started_at,
            datetime.now(timezone.utc).isoformat(),
            exit_code,
            bytes(self._stdout),
            bytes(self._stderr),
        )
        process = self._process
        self._process = None
        if process is not None:
            process.deleteLater()
        self.completed.emit(result)
