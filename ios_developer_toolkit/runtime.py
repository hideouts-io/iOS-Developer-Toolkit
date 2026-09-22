from __future__ import annotations

import os
import plistlib
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Mapping, Sequence


INTERNAL_PYMOBILEDEVICE3_FLAG = "--toolkit-internal-pymobiledevice3"
INTERNAL_WORKER_FLAG = "--toolkit-internal-worker"
INTERNAL_SMOKE_TEST_FLAG = "--toolkit-internal-smoke-test"

ToolkitWorker = Literal["backup", "capability", "collector", "ipa-inspector", "local-ddi"]


@dataclass(frozen=True)
class ExecutableCommand:
    program: Path
    prefix_arguments: tuple[str, ...]


class FrozenExecutableError(RuntimeError):
    """Raised when a packaged runtime cannot locate its bundle launcher."""


def is_frozen_runtime() -> bool:
    if "__compiled__" in globals():
        return True
    frozen_marker = getattr(sys, "frozen", False)
    return frozen_marker is True


def _bundle_contents_directories(anchors: Sequence[Path]) -> tuple[Path, ...]:
    directories: list[Path] = []
    for anchor in anchors:
        resolved_anchor = anchor.expanduser().resolve(strict=False)
        for candidate in (resolved_anchor, *resolved_anchor.parents):
            if candidate.name == "Contents" and candidate not in directories:
                directories.append(candidate)
    return tuple(directories)


def macos_bundle_executable(argument_zero: Path, module_path: Path) -> Path:
    contents_directories = _bundle_contents_directories((argument_zero, module_path))
    plist_paths = tuple(directory / "Info.plist" for directory in contents_directories)
    existing_plists = tuple(path for path in plist_paths if path.is_file())
    if not existing_plists:
        checked = ", ".join(str(path) for path in plist_paths) or "no enclosing .app Contents directory"
        raise FrozenExecutableError(f"Packaged macOS runtime could not find Info.plist; checked: {checked}")

    plist_path = existing_plists[0]
    try:
        with plist_path.open("rb") as plist_file:
            plist = plistlib.load(plist_file)
    except (OSError, plistlib.InvalidFileException) as error:
        raise FrozenExecutableError(f"Packaged macOS runtime could not read {plist_path}: {error}") from error
    if not isinstance(plist, Mapping):
        raise FrozenExecutableError(
            f"Packaged macOS runtime expected a dictionary at the root of {plist_path}, "
            f"received {type(plist).__name__}"
        )

    executable_name = plist.get("CFBundleExecutable")
    if not isinstance(executable_name, str) or not executable_name or Path(executable_name).name != executable_name:
        raise FrozenExecutableError(
            f"Packaged macOS runtime has an invalid CFBundleExecutable in {plist_path}: {executable_name!r}"
        )
    executable_path = plist_path.parent / "MacOS" / executable_name
    if not executable_path.is_file():
        raise FrozenExecutableError(
            f"Packaged macOS runtime launcher named by {plist_path} does not exist: {executable_path}"
        )
    if not os.access(executable_path, os.X_OK):
        raise FrozenExecutableError(
            f"Packaged macOS runtime launcher is not executable: {executable_path}"
        )
    return executable_path


def frozen_executable_path(
    argument_zero: Path,
    module_path: Path,
    interpreter_path: Path,
    platform_name: str,
) -> Path:
    if platform_name == "darwin":
        return macos_bundle_executable(argument_zero, module_path)
    if not interpreter_path.is_file() or not os.access(interpreter_path, os.X_OK):
        raise FrozenExecutableError(f"Packaged runtime executable is missing or not executable: {interpreter_path}")
    return interpreter_path


def active_frozen_executable() -> Path:
    return frozen_executable_path(Path(sys.argv[0]), Path(__file__), Path(sys.executable), sys.platform)


def command_arguments(command: ExecutableCommand, arguments: Sequence[str]) -> tuple[str, ...]:
    return (*command.prefix_arguments, *arguments)


def command_argv(command: ExecutableCommand, arguments: Sequence[str]) -> tuple[str, ...]:
    return (str(command.program), *command_arguments(command, arguments))


def command_text(command: ExecutableCommand, arguments: Sequence[str]) -> str:
    return shlex.join(command_argv(command, arguments))


def pymobiledevice3_command() -> ExecutableCommand:
    if is_frozen_runtime():
        return ExecutableCommand(active_frozen_executable(), (INTERNAL_PYMOBILEDEVICE3_FLAG,))
    candidate = Path(sys.executable).with_name("pymobiledevice3")
    if not candidate.is_file():
        raise FileNotFoundError(
            f"pymobiledevice3 executable was not found next to the active Python interpreter: {candidate}"
        )
    return ExecutableCommand(candidate, ())


def worker_module(worker: ToolkitWorker) -> str:
    if worker == "backup":
        return "ios_developer_toolkit.backup_worker"
    if worker == "capability":
        return "ios_developer_toolkit.capability_matrix_worker"
    if worker == "collector":
        return "ios_developer_toolkit.collector"
    if worker == "ipa-inspector":
        return "ios_developer_toolkit.ipa_inspector"
    if worker == "local-ddi":
        return "ios_developer_toolkit.local_ddi"
    raise ValueError(f"Unsupported toolkit worker: {worker}")


def worker_command(worker: ToolkitWorker) -> ExecutableCommand:
    if is_frozen_runtime():
        return ExecutableCommand(active_frozen_executable(), (INTERNAL_WORKER_FLAG, worker))
    return ExecutableCommand(Path(sys.executable), ("-m", worker_module(worker)))


def device_environment(udid: str) -> Mapping[str, str]:
    environment = dict(os.environ)
    environment["PYMOBILEDEVICE3_UDID"] = udid
    environment["PYTHONUNBUFFERED"] = "1"
    environment["NO_COLOR"] = "1"
    return environment
