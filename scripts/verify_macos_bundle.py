from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


MACH_O_MAGICS = frozenset(
    {
        b"\xce\xfa\xed\xfe",
        b"\xfe\xed\xfa\xce",
        b"\xcf\xfa\xed\xfe",
        b"\xfe\xed\xfa\xcf",
        b"\xca\xfe\xba\xbe",
        b"\xbe\xba\xfe\xca",
        b"\xca\xfe\xba\xbf",
        b"\xbf\xba\xfe\xca",
    }
)


class MacOSBundleValidationError(RuntimeError):
    """Raised when a bundled Mach-O cannot satisfy the advertised platform floor."""


@dataclass(frozen=True)
class MachORecord:
    path: Path
    architectures: tuple[str, ...]
    minimum_macos_versions: tuple[str, ...]


def version_parts(value: str) -> tuple[int, int, int]:
    components = value.split(".")
    if not components or len(components) > 3 or any(not component.isdigit() for component in components):
        raise MacOSBundleValidationError(f"Invalid macOS version in Mach-O load command: {value!r}")
    numbers = tuple(int(component) for component in components)
    return (numbers + (0, 0, 0))[:3]


def parse_minimum_macos_versions(otool_output: str) -> tuple[str, ...]:
    versions: list[str] = []
    active_command = ""
    for raw_line in otool_output.splitlines():
        line = raw_line.strip()
        if line == "cmd LC_BUILD_VERSION":
            active_command = "build"
            continue
        if line == "cmd LC_VERSION_MIN_MACOSX":
            active_command = "legacy"
            continue
        if active_command == "build" and line.startswith("minos "):
            versions.append(line.removeprefix("minos ").split()[0])
            active_command = ""
            continue
        if active_command == "legacy" and line.startswith("version "):
            versions.append(line.removeprefix("version ").split()[0])
            active_command = ""
    return tuple(versions)


def is_mach_o(path: Path) -> bool:
    try:
        with path.open("rb") as stream:
            return stream.read(4) in MACH_O_MAGICS
    except OSError as error:
        raise MacOSBundleValidationError(f"Could not read bundle file {path}: {error}") from error


def run_tool(arguments: Sequence[str], target: Path) -> str:
    try:
        completed = subprocess.run(
            (*arguments, str(target)),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
    except OSError as error:
        raise MacOSBundleValidationError(
            f"Could not execute {arguments[0]} for {target}: {error}"
        ) from error
    if completed.returncode != 0:
        raise MacOSBundleValidationError(
            f"{' '.join((*arguments, str(target)))} exited {completed.returncode}. "
            f"stdout={completed.stdout.strip()!r} stderr={completed.stderr.strip()!r}"
        )
    return completed.stdout


def inspect_mach_o(path: Path) -> MachORecord:
    architecture_output = run_tool(("/usr/bin/lipo", "-archs"), path)
    architectures = tuple(architecture_output.split())
    if not architectures:
        raise MacOSBundleValidationError(f"lipo returned no architectures for bundled Mach-O: {path}")
    load_commands = run_tool(("/usr/bin/otool", "-l"), path)
    minimum_versions = parse_minimum_macos_versions(load_commands)
    if not minimum_versions:
        raise MacOSBundleValidationError(
            f"Bundled Mach-O has no LC_BUILD_VERSION or LC_VERSION_MIN_MACOSX floor: {path}"
        )
    return MachORecord(path, architectures, minimum_versions)


def validate_mach_o_records(
    records: Sequence[MachORecord],
    expected_architecture: str,
    maximum_macos_version: str,
) -> None:
    maximum_parts = version_parts(maximum_macos_version)
    for record in records:
        if expected_architecture not in record.architectures:
            raise MacOSBundleValidationError(
                f"Bundled Mach-O {record.path} does not contain required architecture "
                f"{expected_architecture}; found {', '.join(record.architectures)}"
            )
        for minimum_version in record.minimum_macos_versions:
            if version_parts(minimum_version) > maximum_parts:
                raise MacOSBundleValidationError(
                    f"Bundled Mach-O {record.path} requires macOS {minimum_version}, newer than the "
                    f"advertised macOS {maximum_macos_version} floor"
                )


def inspect_application_bundle(application_path: Path) -> tuple[MachORecord, ...]:
    if not application_path.is_dir() or application_path.suffix != ".app":
        raise MacOSBundleValidationError(f"Application bundle does not exist: {application_path}")
    records = tuple(
        inspect_mach_o(path)
        for path in sorted(application_path.rglob("*"))
        if path.is_file() and is_mach_o(path)
    )
    if not records:
        raise MacOSBundleValidationError(f"Application bundle contains no Mach-O files: {application_path}")
    return records


def main(arguments: Sequence[str]) -> int:
    if len(arguments) != 4:
        raise MacOSBundleValidationError(
            "Usage: verify_macos_bundle.py APPLICATION_PATH EXPECTED_ARCHITECTURE MAXIMUM_MACOS_VERSION"
        )
    application_path = Path(arguments[1]).resolve()
    expected_architecture = arguments[2]
    maximum_macos_version = arguments[3]
    records = inspect_application_bundle(application_path)
    validate_mach_o_records(records, expected_architecture, maximum_macos_version)
    observed_versions = sorted(
        {version for record in records for version in record.minimum_macos_versions},
        key=version_parts,
    )
    print(
        f"Validated {len(records)} bundled Mach-O files for {expected_architecture}; "
        f"observed macOS floors: {', '.join(observed_versions)}; advertised floor: {maximum_macos_version}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv))
    except MacOSBundleValidationError as error:
        print(f"macOS bundle validation failed: {error}", file=sys.stderr)
        raise SystemExit(1)
