from __future__ import annotations

import os
import shutil
from pathlib import Path

from ios_developer_toolkit.runtime import ExecutableCommand


class XcodeHandoffError(ValueError):
    """Raised when an Apple developer-tool handoff cannot be built safely."""


def executable_command(name: str, fixed_candidates: tuple[Path, ...]) -> ExecutableCommand:
    if not name or Path(name).name != name:
        raise XcodeHandoffError(f"Developer tool name must be a basename: {name!r}")
    candidates = (*fixed_candidates, *(Path(path) for path in (shutil.which(name),) if path is not None))
    executable = next((path for path in candidates if path.is_file() and os.access(path, os.X_OK)), None)
    if executable is None:
        checked = ", ".join(str(path) for path in candidates) or "no candidate paths"
        raise XcodeHandoffError(f"Could not find executable developer tool {name}; checked {checked}")
    return ExecutableCommand(executable.resolve(), ())


def coredevice_details_handoff(udid: str) -> tuple[ExecutableCommand, tuple[str, ...]]:
    normalized_udid = udid.strip()
    if not normalized_udid:
        raise XcodeHandoffError("CoreDevice details require a selected device identifier")
    command = executable_command("xcrun", (Path("/usr/bin/xcrun"),))
    return command, (
        "devicectl",
        "device",
        "info",
        "details",
        "--device",
        normalized_udid,
        "--timeout",
        "30",
    )


def rvi_list_handoff() -> tuple[ExecutableCommand, tuple[str, ...]]:
    command = executable_command(
        "rvictl",
        (Path("/Library/Apple/usr/bin/rvictl"), Path("/usr/bin/rvictl")),
    )
    return command, ("-l",)


def validated_xcode_target(path: Path, allowed_suffixes: tuple[str, ...], allowed_names: tuple[str, ...]) -> Path:
    resolved = path.expanduser().resolve()
    normalized_suffixes = tuple(suffix.casefold() for suffix in allowed_suffixes)
    normalized_names = tuple(name.casefold() for name in allowed_names)
    if resolved.suffix.casefold() not in normalized_suffixes and resolved.name.casefold() not in normalized_names:
        expected = ", ".join((*allowed_suffixes, *allowed_names))
        raise XcodeHandoffError(f"Unsupported Xcode handoff target {resolved}; expected one of: {expected}")
    if not resolved.exists():
        raise XcodeHandoffError(f"Xcode handoff target does not exist: {resolved}")
    return resolved


def validated_xcode_project(path: Path) -> Path:
    return validated_xcode_target(path, (".xcodeproj", ".xcworkspace"), ("Package.swift",))


def xcode_project_handoff(path: Path) -> tuple[ExecutableCommand, tuple[str, ...]]:
    target = validated_xcode_project(path)
    command = executable_command("xcrun", (Path("/usr/bin/xcrun"),))
    return command, ("xed", str(target))


def validated_xcode_artifact(path: Path) -> Path:
    return validated_xcode_target(path, (".xcresult", ".trace"), ())
