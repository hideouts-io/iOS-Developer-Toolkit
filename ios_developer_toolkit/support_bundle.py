from __future__ import annotations

import hashlib
import json
import platform
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


JsonDocumentValue = str | int | bool | list[str] | dict[str, str] | dict[str, int]


class SupportBundleError(ValueError):
    pass


@dataclass(frozen=True)
class SupportStatus:
    identifier: str
    value: str


@dataclass(frozen=True)
class SupportBundleContext:
    app_version: str
    workspace: str
    device_count: int
    selected_device_present: bool
    capability_state_counts: tuple[tuple[str, int], ...]
    command_drift_report: str
    statuses: tuple[SupportStatus, ...]
    redactions: tuple[str, ...]
    frozen_runtime: bool


@dataclass(frozen=True)
class SupportBundleResult:
    path: Path
    entries: tuple[str, ...]


def create_sanitized_support_bundle(destination: Path, context: SupportBundleContext) -> SupportBundleResult:
    _validate_destination(destination)
    _validate_context(context)
    entries = _support_entries(context)
    try:
        with zipfile.ZipFile(destination, mode="x", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, content in entries:
                archive.writestr(name, content)
    except OSError as error:
        raise SupportBundleError(f"Could not create support bundle at {destination}: {error}") from error
    except zipfile.BadZipFile as error:
        raise SupportBundleError(f"Could not write a valid ZIP support bundle at {destination}: {error}") from error
    return SupportBundleResult(destination, tuple(name for name, _ in entries))


def sanitize_support_text(value: str, redactions: tuple[str, ...]) -> str:
    sanitized = value
    sanitized = re.sub(r"\b[0-9A-Fa-f]{8}-[0-9A-Fa-f]{16,}\b", "<device-identifier>", sanitized)
    sanitized = re.sub(r"\b[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}\b", "<uuid>", sanitized)
    sanitized = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "<ipv4-address>", sanitized)
    sanitized = re.sub(r"\b[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}\b", "<mac-address>", sanitized)
    sanitized = re.sub(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "<email-address>", sanitized)
    sanitized = re.sub(r"/(?:Users|private|var|Volumes|Library|Applications|System|opt|tmp)(?:/[^\s\\\"']+)+", "<local-path>", sanitized)
    for redaction in tuple(sorted((item for item in redactions if item), key=len, reverse=True)):
        sanitized = sanitized.replace(redaction, "<redacted>")
    return sanitized[:8_000]


def _support_entries(context: SupportBundleContext) -> tuple[tuple[str, str], ...]:
    created_at = datetime.now(timezone.utc).isoformat()
    status_values = {
        status.identifier: sanitize_support_text(status.value, context.redactions)
        for status in context.statuses
    }
    capability_counts = {state: count for state, count in context.capability_state_counts}
    environment: dict[str, JsonDocumentValue] = {
        "app_version": context.app_version,
        "created_at": created_at,
        "operating_system": platform.system(),
        "operating_system_release": platform.release(),
        "macos_version": platform.mac_ver()[0],
        "architecture": platform.machine(),
        "python_implementation": platform.python_implementation(),
        "python_version": platform.python_version(),
        "runtime": "frozen-app" if context.frozen_runtime else "source-python",
        "pymobiledevice3_version": _installed_package_version("pymobiledevice3"),
        "pyside6_version": _installed_package_version("PySide6"),
    }
    context_document: dict[str, JsonDocumentValue] = {
        "workspace": context.workspace,
        "detected_device_count": context.device_count,
        "selected_device_present": context.selected_device_present,
        "capability_state_counts": capability_counts,
        "active_statuses": status_values,
        "included": [
            "toolkit and dependency versions",
            "macOS and Python version metadata",
            "workspace name and device count without device identity",
            "capability state counts without evidence payloads",
            "sanitized UI status summaries",
            "sanitized command-drift summary",
        ],
        "excluded": [
            "device names, UDIDs, serial numbers, and pairing records",
            "backups, cases, captures, screenshots, raw logs, PCAPs, crash reports, and IPA files",
            "command output, live-log payloads, passwords, and user-entered values",
            "host name, user name, home directory, full filesystem paths, network addresses, and email addresses",
        ],
    }
    command_drift = sanitize_support_text(context.command_drift_report, context.redactions)
    pre_manifest_entries = (
        ("README.txt", _support_readme()),
        ("environment.json", _json_document(environment)),
        ("context.json", _json_document(context_document)),
        ("command-drift.txt", command_drift or "No command-drift check has been completed in this app session.\n"),
    )
    manifest: dict[str, JsonDocumentValue] = {
        "created_at": created_at,
        "entries": {
            name: hashlib.sha256(content.encode("utf-8")).hexdigest()
            for name, content in pre_manifest_entries
        },
    }
    return (*pre_manifest_entries, ("SHA256SUMS.json", _json_document(manifest)))


def _support_readme() -> str:
    return (
        "iOS Developer Toolkit sanitized support bundle\n"
        "\n"
        "This archive is generated locally and is never uploaded by the application. It contains application and "
        "environment metadata, aggregate readiness states, sanitized UI status summaries, and a sanitized command-drift report.\n"
        "\n"
        "It intentionally excludes device identity, pairing material, backups, evidence cases, screenshots, IPA files, "
        "raw logs, packets, crash reports, command output, passwords, and user-entered values. Review this ZIP before sharing.\n"
    )


def _json_document(value: dict[str, JsonDocumentValue]) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def _installed_package_version(package: str) -> str:
    try:
        return version(package)
    except PackageNotFoundError:
        return "not-installed"


def _validate_destination(destination: Path) -> None:
    if not destination.is_absolute():
        raise SupportBundleError(f"Support bundle destination must be absolute: {destination}")
    if destination.suffix.casefold() != ".zip":
        raise SupportBundleError(f"Support bundle destination must have a .zip extension: {destination}")
    if not destination.parent.is_dir():
        raise SupportBundleError(f"Support bundle destination parent does not exist: {destination.parent}")
    if destination.exists():
        raise SupportBundleError(f"Support bundle destination already exists and will not be overwritten: {destination}")


def _validate_context(context: SupportBundleContext) -> None:
    if not context.app_version.strip():
        raise SupportBundleError("Support bundle app version is required")
    if not context.workspace.strip():
        raise SupportBundleError("Support bundle workspace is required")
    if context.device_count < 0:
        raise SupportBundleError(f"Support bundle device count cannot be negative: {context.device_count}")
    identifiers = tuple(status.identifier for status in context.statuses)
    if len(set(identifiers)) != len(identifiers):
        raise SupportBundleError("Support bundle status identifiers must be unique")
    if any(not identifier.strip() for identifier in identifiers):
        raise SupportBundleError("Support bundle status identifiers must be non-empty")
    if any(count < 0 for _, count in context.capability_state_counts):
        raise SupportBundleError("Support bundle capability counts cannot be negative")
