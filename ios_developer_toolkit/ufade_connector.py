from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


UFADE_REPOSITORY_URL = "https://github.com/prosch88/UFADE"
UFADE_INSTALLATION_URL = f"{UFADE_REPOSITORY_URL}#installation"
UFADE_USAGE_URL = f"{UFADE_REPOSITORY_URL}#usage"
UFADE_REQUIRED_PYTHON = (3, 11)
UFADE_RUNTIME_IMPORTS = (
    "tkinter",
    "customtkinter",
    "PIL",
    "pandas",
    "pyarrow",
    "pymobiledevice3",
    "iOSbackup",
    "pyiosbackup",
    "paramiko",
    "simpleaudio",
    "tkcalendar",
    "crossfiledialog",
    "exifread",
    "pdfme",
    "imagehash",
    "numpy",
    "cryptography",
)


class UFADEValidationError(ValueError):
    pass


@dataclass(frozen=True)
class UFADEInstallation:
    checkout: Path
    script: Path
    python: Path
    ufade_version: str
    python_version: str
    developer_images_available: bool


def macos_setup_commands() -> tuple[str, ...]:
    return (
        "brew install python@3.11 python-tk@3.11",
        "git clone --recurse-submodules https://github.com/prosch88/UFADE.git",
        "cd UFADE",
        "python3.11 -m venv .venv",
        ".venv/bin/python -m pip install --upgrade pip",
        ".venv/bin/python -m pip install -r requirements.txt",
    )


def checkout_python_path(checkout: Path) -> Path:
    return checkout.expanduser().resolve() / ".venv" / "bin" / "python"


def developer_images_are_available(checkout: Path) -> bool:
    return (checkout.expanduser().resolve() / "ufade_developer" / "Developer").is_dir()


def required_absolute_path(value: Path, label: str) -> Path:
    expanded = value.expanduser()
    if not expanded.is_absolute():
        raise UFADEValidationError(f"{label} must be an absolute path: {expanded}")
    return expanded.resolve()


def read_ufade_version(script: Path) -> str:
    source = script.read_text(encoding="utf-8")
    match = re.search(r'^u_version\s*=\s*["\']([^"\']+)["\']\s*$', source, flags=re.MULTILINE)
    if match is None:
        raise UFADEValidationError(f"Could not find UFADE's u_version declaration in {script}")
    return match.group(1)


def validate_ufade_checkout(checkout: Path) -> tuple[Path, Path, str]:
    resolved_checkout = required_absolute_path(checkout, "UFADE checkout")
    if not resolved_checkout.is_dir():
        raise UFADEValidationError(f"UFADE checkout directory does not exist: {resolved_checkout}")
    script = resolved_checkout / "ufade.py"
    license_path = resolved_checkout / "LICENSE"
    requirements_path = resolved_checkout / "requirements.txt"
    for required_file in (script, license_path, requirements_path):
        if not required_file.is_file():
            raise UFADEValidationError(f"UFADE checkout is missing required file: {required_file}")
    license_text = license_path.read_text(encoding="utf-8", errors="replace")
    if "GNU GENERAL PUBLIC LICENSE" not in license_text or "Version 3" not in license_text:
        raise UFADEValidationError(f"UFADE checkout does not contain the expected GPL-3.0 license: {license_path}")
    version = read_ufade_version(script)
    return resolved_checkout, script, version


def parse_python_version(value: str) -> tuple[int, int, int]:
    fields = value.strip().split(".")
    if len(fields) != 3 or any(not field.isdigit() for field in fields):
        raise UFADEValidationError(f"Python returned an unexpected version string: {value!r}")
    return int(fields[0]), int(fields[1]), int(fields[2])


def validate_ufade_python(python: Path) -> tuple[Path, str]:
    resolved_python = required_absolute_path(python, "UFADE Python executable")
    if not resolved_python.is_file():
        raise UFADEValidationError(f"UFADE Python executable does not exist: {resolved_python}")
    completed = subprocess.run(
        [str(resolved_python), "-c", "import sys; print('.'.join(str(value) for value in sys.version_info[:3]))"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        timeout=10,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise UFADEValidationError(f"Could not query UFADE Python at {resolved_python}: {detail}")
    version = completed.stdout.strip()
    major, minor, _ = parse_python_version(version)
    if (major, minor) != UFADE_REQUIRED_PYTHON:
        raise UFADEValidationError(
            f"UFADE requires Python 3.11, but {resolved_python} reports Python {version}"
        )
    return resolved_python, version


def validate_ufade_dependencies(checkout: Path, python: Path) -> None:
    import_statement = ", ".join(UFADE_RUNTIME_IMPORTS)
    completed = subprocess.run(
        [str(python), "-c", f"import {import_statement}"],
        cwd=checkout,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        timeout=30,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise UFADEValidationError(
            "UFADE's Python 3.11 environment is incomplete. Run "
            f"{python} -m pip install -r {checkout / 'requirements.txt'} in that separate environment. "
            f"Runtime import check failed: {detail}"
        )


def inspect_ufade_installation(checkout: Path, python: Path) -> UFADEInstallation:
    resolved_checkout, script, ufade_version = validate_ufade_checkout(checkout)
    resolved_python, python_version = validate_ufade_python(python)
    validate_ufade_dependencies(resolved_checkout, resolved_python)
    return UFADEInstallation(
        checkout=resolved_checkout,
        script=script,
        python=resolved_python,
        ufade_version=ufade_version,
        python_version=python_version,
        developer_images_available=developer_images_are_available(resolved_checkout),
    )
