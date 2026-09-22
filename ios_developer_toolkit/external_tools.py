from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Mapping

from ios_developer_toolkit.file_integrity import sha256_file
from ios_developer_toolkit.runtime import ExecutableCommand


ExternalToolIdentifier = Literal["go-ios", "idb", "ipsw"]


class ExternalToolValidationError(ValueError):
    """Raised when an optional external-tool adapter cannot be used safely."""


@dataclass(frozen=True)
class ExternalToolSpec:
    identifier: ExternalToolIdentifier
    title: str
    executable_name: str
    repository_url: str
    documentation_url: str
    license_name: str
    setup_commands: tuple[str, ...]
    version_arguments: tuple[str, ...]
    probe_arguments: tuple[str, ...]
    probe_title: str
    scope: str
    environment_keys_to_remove: tuple[str, ...]


@dataclass(frozen=True)
class ExternalToolExecutable:
    spec_identifier: ExternalToolIdentifier
    path: Path
    sha256: str


@dataclass(frozen=True)
class ExternalToolInstallation:
    executable: ExternalToolExecutable
    version_or_build: str


def external_tool_specs() -> tuple[ExternalToolSpec, ...]:
    return (
        ExternalToolSpec(
            "go-ios",
            "go-ios",
            "ios",
            "https://github.com/danielpaulus/go-ios",
            "https://github.com/danielpaulus/go-ios#readme",
            "MIT",
            ("npm install -g go-ios",),
            ("--version",),
            ("list", "--details"),
            "List devices with go-ios",
            "A separate cross-platform iOS protocol stack. The probe asks go-ios to enumerate devices using its own pairing and tunnel state.",
            ("GO_IOS_DEVICEKIT_URL", "GO_IOS_WDA_URL", "P12_PASSWORD"),
        ),
        ExternalToolSpec(
            "idb",
            "Meta idb Companion",
            "idb_companion",
            "https://github.com/facebook/idb",
            "https://fbidb.io/",
            "MIT",
            ("brew install facebook/fb/idb",),
            ("--version",),
            ("--list", "1"),
            "List idb targets",
            "The macOS companion for idb simulator and device automation. The probe lists targets visible to the companion without starting its server mode.",
            ("IDB_COMPANION", "IDB_COMPANION_TLS", "IDB_UDID"),
        ),
        ExternalToolSpec(
            "ipsw",
            "blacktop ipsw",
            "ipsw",
            "https://github.com/blacktop/ipsw",
            "https://blacktop.github.io/ipsw/",
            "MIT",
            ("brew install blacktop/tap/ipsw",),
            ("version",),
            ("idev", "list"),
            "List devices with ipsw idev",
            "A firmware and Apple-platform research suite. The probe uses its optional idev surface only to enumerate locally visible devices.",
            (
                "GITHUB_TOKEN",
                "GH_TOKEN",
                "IPSW_APPSTORE_API_KEY",
                "IPSW_APPSTORE_API_SECRET",
            ),
        ),
    )


def external_tool_spec(identifier: ExternalToolIdentifier) -> ExternalToolSpec:
    matches = tuple(spec for spec in external_tool_specs() if spec.identifier == identifier)
    if len(matches) != 1:
        raise ExternalToolValidationError(
            f"Expected one external tool specification for {identifier!r}, found {len(matches)}"
        )
    return matches[0]


def discover_external_tool_executables(
    spec: ExternalToolSpec,
    home: Path,
    path_environment: str,
) -> tuple[Path, ...]:
    path_entries = tuple(Path(entry) for entry in path_environment.split(os.pathsep) if entry)
    locations = (
        *(entry / spec.executable_name for entry in path_entries),
        home.expanduser() / ".local" / "bin" / spec.executable_name,
        Path("/opt/homebrew/bin") / spec.executable_name,
        Path("/usr/local/bin") / spec.executable_name,
    )
    candidates: list[Path] = []
    for location in locations:
        expanded = location.expanduser()
        if expanded.is_file() and os.access(expanded, os.X_OK):
            resolved = expanded.resolve()
            if resolved not in candidates:
                candidates.append(resolved)
    return tuple(candidates)


def inspect_external_tool_executable(
    spec: ExternalToolSpec,
    path: Path,
) -> ExternalToolExecutable:
    expanded = path.expanduser()
    if not expanded.is_absolute():
        raise ExternalToolValidationError(
            f"{spec.title} executable path must be absolute: {expanded}"
        )
    resolved = expanded.resolve()
    if not resolved.is_file():
        raise ExternalToolValidationError(
            f"{spec.title} executable does not exist: {resolved}"
        )
    if not os.access(resolved, os.X_OK):
        raise ExternalToolValidationError(
            f"{spec.title} path is not executable: {resolved}"
        )
    return ExternalToolExecutable(spec.identifier, resolved, sha256_file(resolved))


def validate_external_tool_installation(
    spec: ExternalToolSpec,
    installation: ExternalToolInstallation,
) -> ExternalToolInstallation:
    if installation.executable.spec_identifier != spec.identifier:
        raise ExternalToolValidationError(
            f"Validated executable belongs to {installation.executable.spec_identifier}, not {spec.identifier}"
        )
    current = inspect_external_tool_executable(spec, installation.executable.path)
    if current.sha256 != installation.executable.sha256:
        raise ExternalToolValidationError(
            f"{spec.title} executable changed after validation; validate it again before running a probe"
        )
    return installation


def external_tool_command(
    spec: ExternalToolSpec,
    executable: ExternalToolExecutable,
) -> ExecutableCommand:
    if executable.spec_identifier != spec.identifier:
        raise ExternalToolValidationError(
            f"Cannot run {executable.spec_identifier} executable as {spec.identifier}"
        )
    environment_arguments = tuple(
        argument
        for key in spec.environment_keys_to_remove
        for argument in ("-u", key)
    )
    return ExecutableCommand(
        Path("/usr/bin/env"),
        (*environment_arguments, str(executable.path)),
    )


def external_tool_environment(base: Mapping[str, str], spec: ExternalToolSpec) -> Mapping[str, str]:
    environment = {
        key: value
        for key, value in base.items()
        if key not in spec.environment_keys_to_remove
    }
    environment["NO_COLOR"] = "1"
    environment["PYTHONUNBUFFERED"] = "1"
    return environment


def parse_external_tool_version(spec: ExternalToolSpec, output: str) -> str:
    without_ansi = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", output).strip()
    if spec.identifier == "go-ios":
        return _parse_go_ios_version(without_ansi)
    if spec.identifier == "idb":
        return _parse_idb_build(without_ansi)
    if spec.identifier == "ipsw":
        return _parse_ipsw_version(without_ansi)
    raise ExternalToolValidationError(f"Unsupported external tool identifier: {spec.identifier}")


def _parse_go_ios_version(output: str) -> str:
    for payload in _json_object_lines(output):
        version = payload.get("version")
        if isinstance(version, str) and version.strip():
            return version.strip()
    match = re.search(r"(?im)^\s*(?:go-ios\s+)?([A-Za-z0-9][A-Za-z0-9._+-]*)\s*$", output)
    if match is None:
        raise ExternalToolValidationError("go-ios version output was not recognized")
    return match.group(1)


def _parse_idb_build(output: str) -> str:
    for payload in _json_object_lines(output):
        build_date = payload.get("build_date")
        build_time = payload.get("build_time")
        if isinstance(build_date, str) and build_date.strip() and isinstance(build_time, str) and build_time.strip():
            return f"build {build_date.strip()} {build_time.strip()}"
    raise ExternalToolValidationError("idb companion build output did not contain build_date and build_time")


def _parse_ipsw_version(output: str) -> str:
    match = re.search(r"(?im)^\s*Version:\s*([^,\s]+)", output)
    if match is None:
        raise ExternalToolValidationError("ipsw version output did not contain a recognizable Version line")
    return match.group(1)


def _json_object_lines(output: str) -> tuple[dict[str, object], ...]:
    objects: list[dict[str, object]] = []
    for line in output.splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and all(isinstance(key, str) for key in payload):
            objects.append(payload)
    return tuple(objects)
