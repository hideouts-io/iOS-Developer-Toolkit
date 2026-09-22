from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Mapping, Sequence

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal

from ios_developer_toolkit.qt_process import OperationResult, ProcessOutcome
from ios_developer_toolkit.runtime import ExecutableCommand, command_arguments, command_argv


class InteractiveProcessController(QObject):
    """Own one user-stoppable process without imposing an arbitrary runtime limit."""

    stdout_received = Signal(bytes)
    stderr_received = Signal(bytes)
    completed = Signal(object)

    def __init__(self, parent: QObject) -> None:
        super().__init__(parent)
        self._process: QProcess | None = None
        self._command: ExecutableCommand | None = None
        self._arguments: tuple[str, ...] = ()
        self._terminate_grace_milliseconds = 0
        self._stdout = bytearray()
        self._stderr = bytearray()
        self._started_at = ""
        self._error_message: str | None = None
        self._stop_outcome: Literal["cancelled"] | None = None
        self._completed = False
        self._kill_timer = QTimer(self)
        self._kill_timer.setSingleShot(True)
        self._kill_timer.timeout.connect(self._kill)

    def is_running(self) -> bool:
        return self._process is not None

    def start(
        self,
        command: ExecutableCommand,
        arguments: Sequence[str],
        environment: Mapping[str, str],
        working_directory: Path,
        terminate_grace_milliseconds: int,
    ) -> None:
        if self.is_running():
            raise RuntimeError("Cannot start an interactive process while another process is running")
        if terminate_grace_milliseconds <= 0:
            raise ValueError(
                f"Interactive process termination grace period must be positive: {terminate_grace_milliseconds}"
            )
        resolved_working_directory = working_directory.expanduser().resolve()
        if not resolved_working_directory.is_dir():
            raise ValueError(f"Interactive process working directory does not exist: {resolved_working_directory}")
        self._command = command
        self._arguments = tuple(arguments)
        self._terminate_grace_milliseconds = terminate_grace_milliseconds
        self._stdout.clear()
        self._stderr.clear()
        self._started_at = datetime.now(timezone.utc).isoformat()
        self._error_message = None
        self._stop_outcome = None
        self._completed = False

        process = QProcess(self)
        process.setProgram(str(command.program))
        process.setArguments(list(command_arguments(command, self._arguments)))
        process.setWorkingDirectory(str(resolved_working_directory))
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
        process.terminate()
        self._kill_timer.start(self._terminate_grace_milliseconds)

    def shutdown(self, terminate_timeout_milliseconds: int, kill_timeout_milliseconds: int) -> None:
        if terminate_timeout_milliseconds <= 0:
            raise ValueError(f"Shutdown termination timeout must be positive: {terminate_timeout_milliseconds}")
        if kill_timeout_milliseconds <= 0:
            raise ValueError(f"Shutdown kill timeout must be positive: {kill_timeout_milliseconds}")
        process = self._process
        if process is None:
            return
        self._stop_outcome = "cancelled"
        self._kill_timer.stop()
        if process.state() != QProcess.ProcessState.NotRunning:
            process.terminate()
            if not process.waitForFinished(terminate_timeout_milliseconds):
                process.kill()
                if not process.waitForFinished(kill_timeout_milliseconds):
                    raise RuntimeError(f"Interactive process did not stop after terminate and kill: {process.program()}")
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
            self.stdout_received.emit(stdout)
        if stderr:
            self._stderr.extend(stderr)
            self.stderr_received.emit(stderr)

    def _process_error(self, process_error: QProcess.ProcessError) -> None:
        process = self._process
        if process is None:
            raise RuntimeError("Interactive process reported an error without an active process")
        self._error_message = process.errorString()
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

    def _kill(self) -> None:
        process = self._process
        if process is not None and process.state() != QProcess.ProcessState.NotRunning:
            process.kill()

    def _finish_once(self, outcome: ProcessOutcome, exit_code: int | None) -> None:
        if self._completed:
            return
        command = self._command
        if command is None:
            raise RuntimeError("Interactive process completed without a command")
        self._drain_output()
        self._completed = True
        self._kill_timer.stop()
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
