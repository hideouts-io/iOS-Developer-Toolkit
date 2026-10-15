from __future__ import annotations

import ctypes
import math
import sys
import time
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, Signal

from ios_developer_toolkit.firmware_models import FirmwareError, FirmwareInstallPlan, helper_environment, install_arguments


class FirmwareTerminationProtection:
    """Hold macOS sudden and automatic termination protection during firmware writes."""

    def __init__(self) -> None:
        self._active = False
        self._process_info = 0
        self._reason = 0
        self._objc: ctypes.CDLL | None = None

    def _send(self, receiver: int, selector: str, argument: int | None) -> int:
        runtime = self._objc
        if runtime is None:
            raise FirmwareError("macOS firmware termination protection is not initialized")
        runtime.sel_registerName.argtypes = [ctypes.c_char_p]
        runtime.sel_registerName.restype = ctypes.c_void_p
        selected = runtime.sel_registerName(selector.encode("ascii"))
        runtime.objc_msgSend.restype = ctypes.c_void_p
        runtime.objc_msgSend.argtypes = [ctypes.c_void_p, ctypes.c_void_p] if argument is None else [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        result = runtime.objc_msgSend(receiver, selected) if argument is None else runtime.objc_msgSend(receiver, selected, argument)
        return int(result) if result is not None else 0

    def acquire(self) -> None:
        if self._active:
            raise FirmwareError("Firmware termination protection is already active")
        if sys.platform != "darwin":
            raise FirmwareError("Firmware installation requires macOS")
        ctypes.CDLL("/System/Library/Frameworks/Foundation.framework/Foundation")
        runtime = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
        runtime.objc_getClass.argtypes = [ctypes.c_char_p]
        runtime.objc_getClass.restype = ctypes.c_void_p
        self._objc = runtime
        process_class = runtime.objc_getClass(b"NSProcessInfo")
        string_class = runtime.objc_getClass(b"NSString")
        self._process_info = self._send(process_class, "processInfo", None)
        reason_buffer = ctypes.create_string_buffer(b"iOS Developer Toolkit firmware installation")
        self._reason = self._send(string_class, "stringWithUTF8String:", ctypes.addressof(reason_buffer))
        if not self._process_info or not self._reason:
            raise FirmwareError("macOS could not establish firmware termination protection")
        self._send(self._reason, "retain", None)
        self._send(self._process_info, "disableSuddenTermination", None)
        self._send(self._process_info, "disableAutomaticTermination:", self._reason)
        self._active = True

    def release(self) -> None:
        if not self._active:
            return
        self._send(self._process_info, "enableAutomaticTermination:", self._reason)
        self._send(self._process_info, "enableSuddenTermination", None)
        self._send(self._reason, "release", None)
        self._active = False


class FirmwareInstallController(QObject):
    """Own the installer without stop, kill, cancellation, or runtime timeout paths.

    Normal Quit is refused by the page while this connector owns the process.
    Protection is held for the complete helper lifetime, including unknown stages.
    """

    output_received = Signal(str)
    progress_received = Signal(str, int)
    completed = Signal(int, str)

    def __init__(self, parent: QObject) -> None:
        super().__init__(parent)
        self._process: QProcess | None = None
        self._protection = FirmwareTerminationProtection()
        self._error = ""
        self._stdout_buffer = ""
        self._stderr_buffer = ""

    def is_running(self) -> bool:
        return self._process is not None

    def start(self, plan: FirmwareInstallPlan, cache: Path, logfile: Path) -> None:
        if self.is_running():
            raise FirmwareError("Another firmware installation is already running")
        arguments = install_arguments(plan, cache, logfile, time.time())
        environment = helper_environment(cache)
        if logfile.parent != cache or logfile.exists() or logfile.is_symlink():
            raise FirmwareError("The firmware installer requires a new log file in its private working directory")
        self._protection.acquire()
        self._error = ""
        self._stdout_buffer = ""
        self._stderr_buffer = ""
        process = QProcess(self)
        process.setProgram(str(plan.helper_bundle.installer.path))
        process.setArguments(list(arguments))
        process.setWorkingDirectory(str(cache))
        process_environment = QProcessEnvironment()
        for key, value in environment.items():
            process_environment.insert(key, value)
        process.setProcessEnvironment(process_environment)
        process.readyReadStandardOutput.connect(self._read_output)
        process.readyReadStandardError.connect(self._read_output)
        process.errorOccurred.connect(self._process_error)
        process.finished.connect(self._finished)
        self._process = process
        process.start()

    def _read_output(self) -> None:
        process = self._process
        if process is None:
            return
        stdout = bytes(process.readAllStandardOutput()).decode("utf-8", errors="replace")
        stderr = bytes(process.readAllStandardError()).decode("utf-8", errors="replace")
        if stdout:
            self.output_received.emit(stdout)
            self._stdout_buffer = self._consume_progress(self._stdout_buffer + stdout)
        if stderr:
            self.output_received.emit(stderr)
            self._stderr_buffer = self._consume_progress(self._stderr_buffer + stderr)

    def _consume_progress(self, text: str) -> str:
        stages = ("Detecting device", "Preparing firmware", "Entering recovery", "Preparing restore", "Restoring filesystem",
                  "Verifying filesystem", "Installing firmware", "Installing baseband firmware", "Updating accessories firmware", "Sending images")
        lines = text.split("\n")
        for line in lines[:-1]:
            if not line.startswith("progress:"):
                continue
            parts = line.split()
            if len(parts) != 3:
                self.progress_received.emit("Unknown installer stage; termination remains blocked", -1)
                continue
            try:
                stage, fraction = int(parts[1]), float(parts[2])
            except ValueError:
                self.progress_received.emit("Unknown installer stage; termination remains blocked", -1)
                continue
            if not 0 <= stage < len(stages) or not math.isfinite(fraction) or not 0 <= fraction <= 1:
                self.progress_received.emit("Unknown installer stage; termination remains blocked", -1)
                continue
            self.progress_received.emit(stages[stage], round(fraction * 100))
        return lines[-1][-65536:]

    def _process_error(self, error: QProcess.ProcessError) -> None:
        process = self._process
        if process is None:
            raise FirmwareError("Firmware installer reported an error without an owned process")
        self._error = process.errorString()
        if error == QProcess.ProcessError.FailedToStart:
            self._finish(-1, f"The firmware installer could not start: {self._error}")

    def _finished(self, exit_code: int, status: QProcess.ExitStatus) -> None:
        self._read_output()
        if status == QProcess.ExitStatus.CrashExit:
            self._finish(exit_code, "The firmware installer crashed. Keep the device connected and review the private installer log")
        elif exit_code == 0:
            self._finish(exit_code, "The firmware helper completed successfully; verify the device boots and check its firmware version")
        else:
            self._finish(exit_code, f"The firmware helper failed with exit code {exit_code}. Review its private log before choosing a recovery action")

    def _finish(self, exit_code: int, message: str) -> None:
        process = self._process
        self._process = None
        self._protection.release()
        if process is not None:
            process.deleteLater()
        self.completed.emit(exit_code, message)

    def shutdown(self) -> None:
        if self.is_running():
            raise FirmwareError("Firmware installation is running; normal Quit and process termination are blocked until the helper exits")
