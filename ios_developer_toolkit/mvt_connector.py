from __future__ import annotations

import os
import plistlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from ios_developer_toolkit.runtime import ExecutableCommand
from ios_developer_toolkit.file_integrity import sha256_file


MVT_REPOSITORY_URL = "https://github.com/mvt-project/mvt"
MVT_INSTALLATION_URL = "https://docs.mvt.re/en/latest/install/"
MVT_BACKUP_GUIDE_URL = "https://docs.mvt.re/en/latest/ios/backup/check/"
MVT_ENVIRONMENT_KEYS_TO_REMOVE = (
    "MVT_ANDROID_BACKUP_PASSWORD",
    "MVT_HASH_FILES",
    "MVT_IOS_BACKUP_PASSWORD",
    "MVT_PROFILE",
    "MVT_STIX2",
    "MVT_VT_API_KEY",
)


class MVTValidationError(ValueError):
    """Raised when an external MVT analysis request is unsafe or incomplete."""


@dataclass(frozen=True)
class MVTExecutable:
    path: Path
    sha256: str


@dataclass(frozen=True)
class MVTInstallation:
    executable: MVTExecutable
    version: str


@dataclass(frozen=True)
class MVTBackup:
    path: Path
    encrypted: bool | None


@dataclass(frozen=True)
class MVTAnalysisRequest:
    installation: MVTInstallation
    backup: MVTBackup
    output: Path
    ioc_files: tuple[Path, ...]
    fast: bool
    hashes: bool
    allow_network: bool


def mvt_setup_commands() -> tuple[str, ...]:
    return (
        "brew install python3 pipx sqlite3",
        "pipx ensurepath",
        "pipx install mvt",
    )


def discover_mvt_executables(home: Path, path_environment: str) -> tuple[Path, ...]:
    candidates: list[Path] = []
    path_entries = tuple(Path(entry) for entry in path_environment.split(os.pathsep) if entry)
    locations = (
        *(entry / "mvt-ios" for entry in path_entries),
        home.expanduser() / ".local" / "bin" / "mvt-ios",
        Path("/opt/homebrew/bin/mvt-ios"),
        Path("/usr/local/bin/mvt-ios"),
    )
    for location in locations:
        expanded = location.expanduser()
        if expanded.is_file() and os.access(expanded, os.X_OK):
            resolved = expanded.resolve()
            if resolved not in candidates:
                candidates.append(resolved)
    return tuple(candidates)


def inspect_mvt_executable(path: Path) -> MVTExecutable:
    expanded = path.expanduser()
    if not expanded.is_absolute():
        raise MVTValidationError(f"MVT executable must be an absolute path: {expanded}")
    resolved = expanded.resolve()
    if not resolved.is_file():
        raise MVTValidationError(f"MVT executable does not exist: {resolved}")
    if not os.access(resolved, os.X_OK):
        raise MVTValidationError(f"MVT executable is not executable: {resolved}")
    return MVTExecutable(resolved, sha256_file(resolved))


def mvt_command(executable: MVTExecutable) -> ExecutableCommand:
    environment_arguments = tuple(
        argument
        for key in MVT_ENVIRONMENT_KEYS_TO_REMOVE
        for argument in ("-u", key)
    )
    return ExecutableCommand(Path("/usr/bin/env"), (*environment_arguments, str(executable.path)))


def mvt_version_arguments() -> tuple[str, ...]:
    return ("--disable-update-check", "--disable-indicator-update-check", "version")


def parse_mvt_version_output(output: str) -> str:
    without_ansi = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", output)
    match = re.search(r"(?im)^\s*Version:\s*([A-Za-z0-9][A-Za-z0-9._+-]*)\s*$", without_ansi)
    if match is None:
        raise MVTValidationError("MVT version output did not contain a recognizable 'Version:' line")
    return match.group(1)


def _is_backup_folder(path: Path) -> bool:
    return (path / "Manifest.db").is_file() and (path / "Info.plist").is_file()


def _resolved_backup_folder(path: Path) -> Path:
    expanded = path.expanduser()
    if not expanded.is_absolute():
        raise MVTValidationError(f"MVT backup path must be absolute: {expanded}")
    resolved = expanded.resolve()
    if not resolved.is_dir():
        raise MVTValidationError(f"MVT backup directory does not exist: {resolved}")
    if _is_backup_folder(resolved):
        return resolved
    candidates = tuple(
        candidate
        for candidate in sorted(resolved.iterdir())
        if candidate.is_dir() and _is_backup_folder(candidate)
    )
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        raise MVTValidationError(
            f"Multiple iTunes-style backups were found under {resolved}; choose one folder containing Manifest.db and Info.plist"
        )
    raise MVTValidationError(
        f"No iTunes-style backup was found at {resolved}; expected Manifest.db and Info.plist"
    )


def _backup_encryption_state(backup: Path) -> bool | None:
    manifest_path = backup / "Manifest.plist"
    if not manifest_path.is_file():
        return None
    try:
        with manifest_path.open("rb") as manifest_file:
            manifest = plistlib.load(manifest_file)
    except (OSError, plistlib.InvalidFileException) as error:
        raise MVTValidationError(f"Could not read backup encryption metadata at {manifest_path}: {error}") from error
    if not isinstance(manifest, Mapping):
        raise MVTValidationError(f"Backup Manifest.plist root is not a dictionary: {manifest_path}")
    encrypted = manifest.get("IsEncrypted")
    if encrypted is None:
        return None
    if not isinstance(encrypted, bool):
        raise MVTValidationError(f"Backup Manifest.plist has a non-boolean IsEncrypted value: {manifest_path}")
    return encrypted


def inspect_mvt_backup(path: Path) -> MVTBackup:
    resolved = _resolved_backup_folder(path)
    encrypted = _backup_encryption_state(resolved)
    if encrypted is True:
        raise MVTValidationError(
            "The selected backup is encrypted. Decrypt a protected working copy with MVT outside this toolkit, then select that copy. "
            "The toolkit does not request, retain, transmit, or place backup passwords in command arguments."
        )
    return MVTBackup(resolved, encrypted)


def validate_mvt_output(path: Path, backup: MVTBackup) -> Path:
    expanded = path.expanduser()
    if not expanded.is_absolute():
        raise MVTValidationError(f"MVT output path must be absolute: {expanded}")
    resolved = expanded.resolve(strict=False)
    if resolved == Path(resolved.anchor):
        raise MVTValidationError(f"MVT output cannot be a filesystem root: {resolved}")
    if resolved.exists():
        raise MVTValidationError(
            f"MVT output already exists: {resolved}. Choose a new empty analysis path so results cannot mix with an earlier run."
        )
    if resolved.is_relative_to(backup.path):
        raise MVTValidationError(
            f"MVT output cannot be inside the source backup: {resolved}"
        )
    return resolved


def validate_mvt_ioc_files(paths: tuple[Path, ...]) -> tuple[Path, ...]:
    supported_suffixes = {".json", ".stix", ".stix2"}
    validated: list[Path] = []
    for path in paths:
        expanded = path.expanduser()
        if not expanded.is_absolute():
            raise MVTValidationError(f"MVT IOC path must be absolute: {expanded}")
        resolved = expanded.resolve()
        if not resolved.is_file():
            raise MVTValidationError(f"MVT IOC file does not exist: {resolved}")
        if resolved.suffix.casefold() not in supported_suffixes:
            raise MVTValidationError(
                f"MVT IOC file must use .stix, .stix2, or .json: {resolved}"
            )
        if resolved not in validated:
            validated.append(resolved)
    return tuple(validated)


def create_mvt_analysis_request(
    installation: MVTInstallation,
    backup_path: Path,
    output_path: Path,
    ioc_paths: tuple[Path, ...],
    fast: bool,
    hashes: bool,
    allow_network: bool,
) -> MVTAnalysisRequest:
    current_executable = inspect_mvt_executable(installation.executable.path)
    if current_executable.sha256 != installation.executable.sha256:
        raise MVTValidationError("The MVT executable changed after validation; validate the installation again")
    backup = inspect_mvt_backup(backup_path)
    output = validate_mvt_output(output_path, backup)
    ioc_files = validate_mvt_ioc_files(ioc_paths)
    return MVTAnalysisRequest(installation, backup, output, ioc_files, fast, hashes, allow_network)


def mvt_analysis_arguments(request: MVTAnalysisRequest) -> tuple[str, ...]:
    arguments = [
        "--disable-update-check",
        "--disable-indicator-update-check",
        "check-backup",
        "--output",
        str(request.output),
    ]
    if request.fast:
        arguments.append("--fast")
    if request.hashes:
        arguments.append("--hashes")
    for ioc_file in request.ioc_files:
        arguments.extend(("--iocs", str(ioc_file)))
    arguments.append(str(request.backup.path))
    return tuple(arguments)


def mvt_environment(
    base: Mapping[str, str],
    config_directory: Path,
    allow_network: bool,
) -> Mapping[str, str]:
    resolved_config = config_directory.expanduser().resolve()
    if not resolved_config.is_dir():
        raise MVTValidationError(f"MVT temporary configuration directory does not exist: {resolved_config}")
    environment = {
        key: value
        for key, value in base.items()
        if key not in MVT_ENVIRONMENT_KEYS_TO_REMOVE
    }
    environment["MVT_CONFIG_FOLDER"] = str(resolved_config)
    environment["MVT_NETWORK_ACCESS_ALLOWED"] = "true" if allow_network else "false"
    environment["MVT_NETWORK_TIMEOUT"] = "15"
    environment["NO_COLOR"] = "1"
    environment["PYTHONUNBUFFERED"] = "1"
    return environment
