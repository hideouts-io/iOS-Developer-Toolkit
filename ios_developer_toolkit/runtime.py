from __future__ import annotations

import os
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


def is_frozen_runtime() -> bool:
    if "__compiled__" in globals():
        return True
    frozen_marker = getattr(sys, "frozen", False)
    return frozen_marker is True


def command_arguments(command: ExecutableCommand, arguments: Sequence[str]) -> tuple[str, ...]:
    return (*command.prefix_arguments, *arguments)


def command_argv(command: ExecutableCommand, arguments: Sequence[str]) -> tuple[str, ...]:
    return (str(command.program), *command_arguments(command, arguments))


def command_text(command: ExecutableCommand, arguments: Sequence[str]) -> str:
    return shlex.join(command_argv(command, arguments))


def pymobiledevice3_command() -> ExecutableCommand:
    if is_frozen_runtime():
        return ExecutableCommand(Path(sys.executable), (INTERNAL_PYMOBILEDEVICE3_FLAG,))
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
        return ExecutableCommand(Path(sys.executable), (INTERNAL_WORKER_FLAG, worker))
    return ExecutableCommand(Path(sys.executable), ("-m", worker_module(worker)))


def device_environment(udid: str) -> Mapping[str, str]:
    environment = dict(os.environ)
    environment["PYMOBILEDEVICE3_UDID"] = udid
    environment["PYTHONUNBUFFERED"] = "1"
    environment["NO_COLOR"] = "1"
    return environment
