from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Mapping, Sequence

from ios_developer_toolkit import APP_VERSION
from ios_developer_toolkit.catalog import snapshot_commands
from ios_developer_toolkit.models import CommandResult, CommandSpec
from ios_developer_toolkit.models import DeviceDataError, parse_devices_json
from ios_developer_toolkit.runtime import (
    ExecutableCommand,
    command_argv,
    command_text,
    device_environment,
    pymobiledevice3_command,
)
from ios_developer_toolkit.validation import output_indicates_failure


class CollectionError(RuntimeError):
    """Base error for evidence collection failures."""


class RequiredCommandError(CollectionError):
    """Raised when a command required to identify the target fails."""


class PartialCollectionError(CollectionError):
    """Raised after artifacts are finalized when one or more optional commands failed."""


@dataclass(frozen=True)
class StreamSpec:
    identifier: str
    title: str
    arguments: tuple[str, ...]
    log_path: Path
    artifact_path: Path | None


@dataclass
class StreamHandle:
    spec: StreamSpec
    process: subprocess.Popen[bytes]
    output_file: IO[bytes]
    started_at: str
    ended_early: bool


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_udid_fragment(udid: str) -> str:
    allowed = "".join(character for character in udid if character.isalnum())
    if not allowed:
        raise ValueError("UDID does not contain any usable alphanumeric characters")
    return allowed[-12:]


def create_case_directory(output_root: Path, udid: str, created_at: datetime) -> Path:
    root = output_root.expanduser().resolve()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    timestamp = created_at.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    directory = root / f"ios-case-{timestamp}-{safe_udid_fragment(udid)}"
    try:
        directory.mkdir(mode=0o700, parents=False, exist_ok=False)
    except FileExistsError as error:
        raise CollectionError(f"A case already exists for this device and second: {directory}. Start the collection again.") from error
    for name in ("snapshots", "streams", "artifacts"):
        (directory / name).mkdir(mode=0o700)
    return directory


def emit(event: str, message: str, fields: Mapping[str, object]) -> None:
    record: dict[str, object] = {"event": event, "message": message, "timestamp": utc_now()}
    record.update(fields)
    print(json.dumps(record, sort_keys=True), flush=True)


def run_snapshot(
    executable: ExecutableCommand,
    environment: Mapping[str, str],
    case_directory: Path,
    spec: CommandSpec,
    attempts_limit: int,
    stop_requested: threading.Event,
    target_udid: str,
) -> CommandResult:
    output_path = case_directory / spec.output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    started_at = utc_now()
    final_exit_code = 1
    attempts = 0
    combined_output = bytearray()
    final_output = b""
    for attempt in range(1, attempts_limit + 1):
        attempts = attempt
        emit("step-start", spec.title, {"command": command_text(executable, spec.arguments), "attempt": attempt})
        process = subprocess.Popen(
            command_argv(executable, spec.arguments),
            cwd=case_directory,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        deadline = time.monotonic() + spec.timeout_seconds
        timed_out = False
        cancelled = False
        while process.poll() is None:
            if stop_requested.is_set():
                cancelled = True
                os.killpg(process.pid, signal.SIGINT)
                break
            if time.monotonic() >= deadline:
                timed_out = True
                os.killpg(process.pid, signal.SIGKILL)
                break
            time.sleep(0.2)
        try:
            stdout, _ = process.communicate(timeout=8)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            stdout, _ = process.communicate(timeout=5)
        final_exit_code = process.returncode if process.returncode is not None else 125
        final_output = stdout
        if timed_out:
            final_exit_code = 124
        combined_output.extend(f"\n--- attempt {attempt} ---\n".encode())
        combined_output.extend(stdout)
        if timed_out:
            combined_output.extend(f"\nCommand timed out after {spec.timeout_seconds}s.\n".encode())
        if cancelled:
            combined_output.extend(b"\nCommand cancelled by user request.\n")
        validation_error = snapshot_validation_error(spec, stdout, target_udid, case_directory)
        if final_exit_code == 0 and validation_error is not None:
            final_exit_code = 70
            combined_output.extend(f"\nSemantic validation failed: {validation_error}\n".encode())
        if final_exit_code == 0 or cancelled:
            break
        if attempt < attempts_limit:
            emit("warning", f"{spec.title} failed; retrying", {"exit_code": final_exit_code, "attempt": attempt})
            time.sleep(1)
    output_path.write_bytes(final_output)
    command_log_path = output_path.with_suffix(output_path.suffix + ".command.log")
    command_log_path.write_bytes(bytes(combined_output))
    ended_at = utc_now()
    status = "cancelled" if stop_requested.is_set() else ("completed" if final_exit_code == 0 else "failed")
    result = CommandResult(
        identifier=spec.identifier,
        title=spec.title,
        arguments=spec.arguments,
        output_path=str(spec.output_path),
        started_at=started_at,
        ended_at=ended_at,
        exit_code=final_exit_code,
        attempts=attempts,
        status=status,
    )
    emit("step-finish", spec.title, {"status": status, "exit_code": final_exit_code, "output": str(output_path)})
    return result


def snapshot_validation_error(
    spec: CommandSpec,
    output: bytes,
    target_udid: str,
    case_directory: Path,
) -> str | None:
    if output_indicates_failure(output):
        return "pymobiledevice3 logged a device or service error"
    if spec.identifier == "usbmux":
        try:
            devices = parse_devices_json(output.decode("utf-8"))
        except (DeviceDataError, json.JSONDecodeError, UnicodeDecodeError) as error:
            return f"usbmux output is not valid device JSON: {error}"
        if not any(device.identifier == target_udid for device in devices):
            return f"selected UDID is not present in the connected-device inventory: {target_udid}"
    if spec.identifier == "screenshot" and not (case_directory / "artifacts/screen.png").is_file():
        return "screenshot command exited without creating artifacts/screen.png"
    if spec.identifier == "crash-pull" and not (case_directory / "artifacts/crashes").is_dir():
        return "crash pull exited without creating artifacts/crashes"
    return None


def stream_specs(include_syslog: bool, include_oslog: bool, include_pcap: bool) -> tuple[StreamSpec, ...]:
    specs: list[StreamSpec] = []
    if include_syslog:
        specs.append(StreamSpec("syslog", "Classic syslog stream", ("syslog", "live"), Path("streams/syslog.txt"), None))
    if include_oslog:
        specs.append(
            StreamSpec(
                "dvt-oslog",
                "DVT structured unified logging stream",
                ("developer", "dvt", "oslog"),
                Path("streams/dvt-oslog.txt"),
                None,
            )
        )
    if include_pcap:
        specs.append(
            StreamSpec(
                "pcap",
                "Device network packet capture",
                ("pcap", "--out", "streams/network.pcap"),
                Path("streams/pcap-metadata.txt"),
                Path("streams/network.pcap"),
            )
        )
    return tuple(specs)


def start_stream(
    executable: ExecutableCommand,
    environment: Mapping[str, str],
    case_directory: Path,
    spec: StreamSpec,
) -> StreamHandle:
    output_path = case_directory / spec.log_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_file = output_path.open("wb")
    emit("stream-start", spec.title, {"command": command_text(executable, spec.arguments), "output": str(output_path)})
    try:
        process = subprocess.Popen(
            command_argv(executable, spec.arguments),
            cwd=case_directory,
            env=environment,
            stdout=output_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except OSError:
        output_file.close()
        raise
    return StreamHandle(spec=spec, process=process, output_file=output_file, started_at=utc_now(), ended_early=False)


def stop_stream(handle: StreamHandle) -> CommandResult:
    process = handle.process
    if process.poll() is None:
        os.killpg(process.pid, signal.SIGINT)
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
    handle.output_file.close()
    exit_code = process.returncode if process.returncode is not None else 125
    artifact_missing = False
    if handle.spec.artifact_path is not None:
        artifact = Path(handle.output_file.name).parent.parent / handle.spec.artifact_path
        artifact_missing = not artifact.is_file() or artifact.stat().st_size <= 24
    status = "completed" if exit_code in (0, -signal.SIGINT) and not handle.ended_early and not artifact_missing else "failed"
    emit("stream-finish", handle.spec.title, {"status": status, "exit_code": exit_code})
    return CommandResult(
        identifier=handle.spec.identifier,
        title=handle.spec.title,
        arguments=handle.spec.arguments,
        output_path=str(handle.spec.log_path),
        started_at=handle.started_at,
        ended_at=utc_now(),
        exit_code=exit_code,
        attempts=1,
        status=status,
    )


def collect_streams(
    executable: ExecutableCommand,
    environment: Mapping[str, str],
    case_directory: Path,
    duration_seconds: int,
    include_syslog: bool,
    include_oslog: bool,
    include_pcap: bool,
    stop_requested: threading.Event,
) -> tuple[CommandResult, ...]:
    handles: list[StreamHandle] = []
    try:
        for spec in stream_specs(include_syslog, include_oslog, include_pcap):
            handles.append(start_stream(executable, environment, case_directory, spec))
    except OSError:
        for handle in handles:
            stop_stream(handle)
        raise
    deadline = time.monotonic() + duration_seconds
    while time.monotonic() < deadline and not stop_requested.is_set():
        for handle in handles:
            exit_code = handle.process.poll()
            if exit_code is not None:
                if not handle.ended_early:
                    handle.ended_early = True
                    emit(
                        "warning",
                        f"{handle.spec.title} exited before the capture timer finished",
                        {"exit_code": exit_code},
                    )
        time.sleep(0.5)
    return tuple(stop_stream(handle) for handle in handles)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_hashes(case_directory: Path) -> None:
    candidates = sorted(
        path for path in case_directory.rglob("*") if path.is_file() and path.name != "SHA256SUMS.txt"
    )
    lines = [f"{sha256_file(path)}  {path.relative_to(case_directory)}" for path in candidates]
    (case_directory / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_manifest(
    case_directory: Path,
    udid: str,
    duration_seconds: int,
    started_at: str,
    results: Sequence[CommandResult],
) -> None:
    manifest: Mapping[str, object] = {
        "schema_version": 1,
        "application": "iOS Developer Toolkit",
        "application_version": APP_VERSION,
        "pymobiledevice3_version": importlib.metadata.version("pymobiledevice3"),
        "developer_disk_image_version": importlib.metadata.version("developer-disk-image"),
        "target_udid": udid,
        "started_at": started_at,
        "ended_at": utc_now(),
        "requested_stream_duration_seconds": duration_seconds,
        "limitations": [
            "Developer services do not provide unrestricted access to all app containers or protected data.",
            "PCAP attribution is limited to packets visible through pcapd and does not decrypt TLS.",
            "A failed command is a coverage gap, not proof that the corresponding data is absent.",
        ],
        "commands": [result.to_mapping() for result in results],
    }
    (case_directory / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run_collection(
    udid: str,
    output_root: Path,
    duration_seconds: int,
    include_syslog: bool,
    include_oslog: bool,
    include_pcap: bool,
    include_screenshot: bool,
    include_crash_pull: bool,
    stop_requested: threading.Event,
) -> Path:
    if duration_seconds < 1:
        raise ValueError("Capture duration must be at least one second")
    executable = pymobiledevice3_command()
    environment = device_environment(udid)
    case_directory = create_case_directory(output_root, udid, datetime.now(timezone.utc))
    emit("case-created", "Created evidence case directory", {"path": str(case_directory)})
    started_at = utc_now()
    results: list[CommandResult] = []
    required_error: RequiredCommandError | None = None
    for spec in snapshot_commands(include_screenshot, include_crash_pull):
        result = run_snapshot(executable, environment, case_directory, spec, 2, stop_requested, udid)
        results.append(result)
        if spec.required and result.status == "failed":
            required_error = RequiredCommandError(
                f"Required command failed after {result.attempts} attempts: "
                f"{command_text(executable, spec.arguments)}. See {case_directory / spec.output_path}"
            )
            break
        if stop_requested.is_set():
            break
    if not stop_requested.is_set() and required_error is None:
        results.extend(
            collect_streams(
                executable,
                environment,
                case_directory,
                duration_seconds,
                include_syslog,
                include_oslog,
                include_pcap,
                stop_requested,
            )
        )
    write_manifest(case_directory, udid, duration_seconds, started_at, results)
    write_hashes(case_directory)
    failures = [result for result in results if result.status == "failed"]
    if stop_requested.is_set():
        case_status = "cancelled"
    elif failures:
        case_status = "partial"
    else:
        case_status = "completed"
    emit(
        "case-finished",
        "Evidence collection finished",
        {"path": str(case_directory), "failures": len(failures), "status": case_status},
    )
    if required_error is not None:
        raise required_error
    if failures and not stop_requested.is_set():
        raise PartialCollectionError(
            f"Collection finalized with {len(failures)} coverage gap(s). Review manifest.json and the command outputs in {case_directory}"
        )
    return case_directory


def parse_args(arguments: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect read-oriented iOS diagnostics through pymobiledevice3")
    parser.add_argument("--udid", required=True)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--duration", required=True, type=int)
    parser.add_argument("--include-syslog", action="store_true")
    parser.add_argument("--include-oslog", action="store_true")
    parser.add_argument("--include-pcap", action="store_true")
    parser.add_argument("--include-screenshot", action="store_true")
    parser.add_argument("--include-crash-pull", action="store_true")
    return parser.parse_args(arguments)


def main() -> int:
    options = parse_args(sys.argv[1:])
    stop_requested = threading.Event()

    def request_stop(signum: int, frame: object) -> None:
        del signum, frame
        stop_requested.set()
        emit("stop-requested", "Stopping after the current operation", {})

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    try:
        run_collection(
            options.udid,
            options.output_root,
            options.duration,
            options.include_syslog,
            options.include_oslog,
            options.include_pcap,
            options.include_screenshot,
            options.include_crash_pull,
            stop_requested,
        )
    except PartialCollectionError as error:
        emit("partial", str(error), {"error_type": type(error).__name__})
        return 2
    except (CollectionError, OSError, ValueError) as error:
        emit("fatal", str(error), {"error_type": type(error).__name__})
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
