from __future__ import annotations

import argparse
import os
import plistlib
import selectors
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from ios_developer_toolkit.runtime import device_environment, pymobiledevice3_executable
from ios_developer_toolkit.validation import output_indicates_failure


class LocalDDIError(RuntimeError):
    """Base error for failures in the local Xcode DDI workflow."""


class DDIAttachError(LocalDDIError):
    """Raised when the outer Xcode DDI cannot be attached read-only."""


class DDILayoutError(LocalDDIError):
    """Raised when the attached DDI does not contain an expected Restore directory."""


class DDIDetachError(LocalDDIError):
    """Raised when the temporary Mac-side DDI mount cannot be detached."""


@dataclass(frozen=True)
class AttachedImage:
    device_entry: str
    mount_point: Path


def parse_attached_image(payload: bytes) -> AttachedImage:
    parsed: object = plistlib.loads(payload)
    if not isinstance(parsed, dict):
        raise DDIAttachError("hdiutil returned a property list with an unexpected root type")
    entities = parsed.get("system-entities")
    if not isinstance(entities, list):
        raise DDIAttachError("hdiutil output did not contain system-entities")
    for entity in entities:
        if not isinstance(entity, dict):
            continue
        mount_point = entity.get("mount-point")
        device_entry = entity.get("dev-entry")
        if isinstance(mount_point, str) and isinstance(device_entry, str):
            return AttachedImage(device_entry=device_entry, mount_point=Path(mount_point))
    raise DDIAttachError("hdiutil did not report a mounted filesystem")


def attach_candidate(candidate: Path) -> AttachedImage:
    if not candidate.is_file():
        raise FileNotFoundError(f"The Xcode candidate DDI does not exist: {candidate}")
    completed = subprocess.run(
        ["/usr/bin/hdiutil", "attach", "-readonly", "-nobrowse", "-noautoopen", "-plist", str(candidate)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        message = completed.stderr.decode("utf-8", errors="replace").strip()
        raise DDIAttachError(f"Failed to attach {candidate} read-only: {message}")
    return parse_attached_image(completed.stdout)


def detach_candidate(attached: AttachedImage) -> None:
    completed = subprocess.run(
        ["/usr/bin/hdiutil", "detach", attached.device_entry],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if completed.returncode != 0:
        message = completed.stdout.decode("utf-8", errors="replace").strip()
        raise DDIDetachError(f"Failed to detach temporary DDI mount {attached.device_entry}: {message}")


class CryptexInstaller:
    """Coordinates the external hdiutil and pymobiledevice3 processes."""

    def __init__(self, candidate: Path, udid: str, executable: Path) -> None:
        self._candidate = candidate
        self._udid = udid
        self._executable = executable
        self._stop_requested = False
        self._active_process: subprocess.Popen[bytes] | None = None

    def request_stop(self, signum: int, frame: object) -> None:
        del signum, frame
        self._stop_requested = True
        active_process = self._active_process
        if active_process is not None and active_process.poll() is None:
            os.killpg(active_process.pid, signal.SIGINT)

    def install(self) -> None:
        print(f"Attaching Apple Xcode DDI read-only: {self._candidate}", flush=True)
        attached = attach_candidate(self._candidate)
        primary_error: LocalDDIError | subprocess.SubprocessError | None = None
        try:
            restore_directory = attached.mount_point / "Restore"
            manifest_path = restore_directory / "BuildManifest.plist"
            if not restore_directory.is_dir() or not manifest_path.is_file():
                raise DDILayoutError(
                    f"The mounted candidate does not contain Restore/BuildManifest.plist: {attached.mount_point}"
                )
            print(f"Using local Restore payload: {restore_directory}", flush=True)
            environment: Mapping[str, str] = device_environment(self._udid)
            self._active_process = subprocess.Popen(
                [str(self._executable), "cryptex", "auto-install", "--restore-dir", str(restore_directory)],
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            if self._active_process.stdout is None:
                raise LocalDDIError("Could not open the pymobiledevice3 output stream")
            captured_output = bytearray()
            selector = selectors.DefaultSelector()
            selector.register(self._active_process.stdout, selectors.EVENT_READ)
            while self._active_process.poll() is None:
                if self._stop_requested:
                    try:
                        self._active_process.wait(timeout=8)
                    except subprocess.TimeoutExpired:
                        os.killpg(self._active_process.pid, signal.SIGKILL)
                        self._active_process.wait(timeout=5)
                    break
                for key, _ in selector.select(timeout=0.2):
                    chunk = key.fileobj.read1(4096)
                    if chunk:
                        captured_output.extend(chunk)
                        print(chunk.decode("utf-8", errors="replace"), end="", flush=True)
            remainder = self._active_process.stdout.read()
            if remainder:
                captured_output.extend(remainder)
                print(remainder.decode("utf-8", errors="replace"), end="", flush=True)
            selector.close()
            if self._stop_requested:
                raise LocalDDIError("Local Xcode DDI installation was cancelled")
            if self._active_process.returncode != 0 or output_indicates_failure(bytes(captured_output)):
                raise LocalDDIError(
                    f"pymobiledevice3 cryptex installation failed with exit code {self._active_process.returncode}; "
                    "review the preceding device-service error"
                )
        except (LocalDDIError, subprocess.SubprocessError) as error:
            primary_error = error
        finally:
            self._active_process = None
            print(f"Detaching temporary Mac-side image: {attached.device_entry}", flush=True)
            try:
                detach_candidate(attached)
            except DDIDetachError as detach_error:
                if primary_error is not None:
                    raise DDIDetachError(f"{primary_error}; cleanup also failed: {detach_error}") from detach_error
                raise
        if primary_error is not None:
            raise primary_error
        print("Local Apple DDI Cryptex installed successfully at /System/Developer.", flush=True)


def parse_args(arguments: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Install a local Xcode candidate DDI as a personalized Cryptex")
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--udid", required=True)
    return parser.parse_args(arguments)


def main() -> int:
    options = parse_args(sys.argv[1:])
    try:
        executable = pymobiledevice3_executable()
        installer = CryptexInstaller(options.candidate.expanduser().resolve(), options.udid, executable)
        signal.signal(signal.SIGINT, installer.request_stop)
        signal.signal(signal.SIGTERM, installer.request_stop)
        installer.install()
    except (LocalDDIError, FileNotFoundError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
