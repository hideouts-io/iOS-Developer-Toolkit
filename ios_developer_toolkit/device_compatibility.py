from __future__ import annotations

import hashlib
import html
import json
import os
import platform
from dataclasses import asdict, dataclass
from pathlib import Path
from sys import version as python_runtime_version
from typing import Mapping, Sequence

from ios_developer_toolkit.capability_matrix import CapabilityMatrixError, CapabilityResult, parse_capability_result
from ios_developer_toolkit.models import IOSDevice
from ios_developer_toolkit.support_bundle import installed_package_version, sanitize_support_text


class DeviceCompatibilityError(ValueError):
    """Raised when local real-device compatibility history is invalid or cannot be preserved."""


@dataclass(frozen=True)
class DeviceCompatibilityObservation:
    """A completed capability probe observed against one locally connected physical device."""

    recorded_at: str
    device_fingerprint: str
    product_type: str
    product_version: str
    build_version: str
    connection_type: str
    results: tuple[CapabilityResult, ...]


@dataclass(frozen=True)
class CompatibilityReportEnvironment:
    """Host and toolchain metadata that explains one exported compatibility report."""

    toolkit_version: str
    macos_version: str
    architecture: str
    python_version: str
    runtime: str
    pymobiledevice3_version: str
    pyside6_version: str


@dataclass(frozen=True)
class CompatibilityReport:
    """A shareable report that deliberately omits stable device identity."""

    generated_at: str
    environment: CompatibilityReportEnvironment
    observations: tuple[DeviceCompatibilityObservation, ...]


def compatibility_history_path(home: Path) -> Path:
    return (
        home.expanduser().resolve()
        / "Library"
        / "Application Support"
        / "iOS Developer Toolkit"
        / "Compatibility"
        / "real-device-observations.jsonl"
    )


def device_fingerprint(identifier: str) -> str:
    if not identifier:
        raise DeviceCompatibilityError("Cannot create a compatibility observation without a device identifier")
    return hashlib.sha256(identifier.encode("utf-8")).hexdigest()[:16]


def _redact_identifier(value: str, identifier: str) -> str:
    return value.replace(identifier, "<selected-device>")


def _redacted_result(result: CapabilityResult, identifier: str) -> CapabilityResult:
    return CapabilityResult(
        identifier=result.identifier,
        layer=result.layer,
        title=result.title,
        state=result.state,
        summary=_redact_identifier(result.summary, identifier),
        evidence=_redact_identifier(result.evidence, identifier),
        remediation=_redact_identifier(result.remediation, identifier),
    )


def create_observation(
    recorded_at: str,
    device: IOSDevice,
    results: Sequence[CapabilityResult],
) -> DeviceCompatibilityObservation:
    if not results:
        raise DeviceCompatibilityError("Cannot record a compatibility observation without capability results")
    identifiers = tuple(result.identifier for result in results)
    if len(set(identifiers)) != len(identifiers):
        raise DeviceCompatibilityError("Capability observation contains duplicate result identifiers")
    redacted_results = tuple(_redacted_result(result, device.identifier) for result in results)
    return DeviceCompatibilityObservation(
        recorded_at=recorded_at,
        device_fingerprint=device_fingerprint(device.identifier),
        product_type=device.product_type,
        product_version=device.product_version,
        build_version=device.build_version,
        connection_type=device.connection_type,
        results=redacted_results,
    )


def observation_mapping(observation: DeviceCompatibilityObservation) -> dict[str, object]:
    return {
        "schema_version": 1,
        "recorded_at": observation.recorded_at,
        "device_fingerprint": observation.device_fingerprint,
        "product_type": observation.product_type,
        "product_version": observation.product_version,
        "build_version": observation.build_version,
        "connection_type": observation.connection_type,
        "results": [asdict(result) for result in observation.results],
    }


def _required_string(record: Mapping[str, object], key: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value:
        raise DeviceCompatibilityError(f"Compatibility observation is missing required string field: {key}")
    return value


def parse_observation(record: Mapping[str, object]) -> DeviceCompatibilityObservation:
    if record.get("schema_version") != 1:
        raise DeviceCompatibilityError("Compatibility observation has an unsupported schema version")
    raw_results = record.get("results")
    if not isinstance(raw_results, list) or not raw_results:
        raise DeviceCompatibilityError("Compatibility observation must contain one or more results")
    results: list[CapabilityResult] = []
    for raw_result in raw_results:
        if not isinstance(raw_result, dict):
            raise DeviceCompatibilityError("Compatibility observation results must be JSON objects")
        try:
            results.append(parse_capability_result(raw_result))
        except CapabilityMatrixError as error:
            raise DeviceCompatibilityError(f"Compatibility observation contains an invalid result: {error}") from error
    fingerprint = _required_string(record, "device_fingerprint")
    if len(fingerprint) != 16 or any(character not in "0123456789abcdef" for character in fingerprint):
        raise DeviceCompatibilityError("Compatibility observation has an invalid device fingerprint")
    return DeviceCompatibilityObservation(
        recorded_at=_required_string(record, "recorded_at"),
        device_fingerprint=fingerprint,
        product_type=_required_string(record, "product_type"),
        product_version=_required_string(record, "product_version"),
        build_version=_required_string(record, "build_version"),
        connection_type=_required_string(record, "connection_type"),
        results=tuple(results),
    )


def append_observation(path: Path, observation: DeviceCompatibilityObservation) -> None:
    record = json.dumps(observation_mapping(observation), sort_keys=True) + "\n"
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as output:
            output.write(record)
            output.flush()
            os.fsync(output.fileno())
        path.chmod(0o600)
    except OSError as error:
        raise DeviceCompatibilityError(f"Could not preserve compatibility observation at {path}: {error}") from error


def load_observations(path: Path) -> tuple[DeviceCompatibilityObservation, ...]:
    if not path.exists():
        return ()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise DeviceCompatibilityError(f"Could not read compatibility history at {path}: {error}") from error
    observations: list[DeviceCompatibilityObservation] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as error:
            raise DeviceCompatibilityError(f"Compatibility history line {line_number} is not valid JSON: {error}") from error
        if not isinstance(payload, dict):
            raise DeviceCompatibilityError(f"Compatibility history line {line_number} must be a JSON object")
        observations.append(parse_observation(payload))
    return tuple(observations)


def latest_observations(
    observations: Sequence[DeviceCompatibilityObservation],
) -> tuple[DeviceCompatibilityObservation, ...]:
    latest_by_device: dict[str, DeviceCompatibilityObservation] = {}
    for observation in observations:
        existing = latest_by_device.get(observation.device_fingerprint)
        if existing is None or observation.recorded_at > existing.recorded_at:
            latest_by_device[observation.device_fingerprint] = observation
    return tuple(sorted(latest_by_device.values(), key=lambda item: item.recorded_at))


def current_report_environment(toolkit_version: str, frozen_runtime: bool) -> CompatibilityReportEnvironment:
    if not toolkit_version.strip():
        raise DeviceCompatibilityError("Toolkit version is required for a compatibility report")
    return CompatibilityReportEnvironment(
        toolkit_version=toolkit_version,
        macos_version=platform.mac_ver()[0] or "unavailable",
        architecture=platform.machine() or "unavailable",
        python_version=platform.python_version() or python_runtime_version.split()[0],
        runtime="frozen-app" if frozen_runtime else "source-python",
        pymobiledevice3_version=installed_package_version("pymobiledevice3"),
        pyside6_version=installed_package_version("PySide6"),
    )


def create_compatibility_report(
    generated_at: str,
    environment: CompatibilityReportEnvironment,
    observations: Sequence[DeviceCompatibilityObservation],
) -> CompatibilityReport:
    if not generated_at.strip():
        raise DeviceCompatibilityError("Compatibility report generation time is required")
    environment_values = asdict(environment)
    missing_environment_fields = tuple(
        key for key, value in environment_values.items() if not isinstance(value, str) or not value.strip()
    )
    if missing_environment_fields:
        raise DeviceCompatibilityError(
            f"Compatibility report environment fields must be non-empty: {missing_environment_fields}"
        )
    latest = latest_observations(observations)
    if not latest:
        raise DeviceCompatibilityError("Cannot export a compatibility report without completed device observations")
    return CompatibilityReport(generated_at, environment, latest)


def compatibility_report_mapping(report: CompatibilityReport) -> dict[str, object]:
    devices: list[dict[str, object]] = []
    for index, observation in enumerate(report.observations, start=1):
        devices.append(
            {
                "report_device": f"device-{index}",
                "observed_at": observation.recorded_at,
                "product_type": observation.product_type,
                "product_version": observation.product_version,
                "build_version": observation.build_version,
                "connection_type": observation.connection_type,
                "capabilities": [
                    {
                        "identifier": result.identifier,
                        "layer": result.layer,
                        "title": result.title,
                        "state": result.state,
                        "summary": sanitize_support_text(result.summary, ()),
                        "evidence": sanitize_support_text(result.evidence, ()),
                        "remediation": sanitize_support_text(result.remediation, ()),
                    }
                    for result in observation.results
                ],
            }
        )
    return {
        "schema_version": 1,
        "generated_at": report.generated_at,
        "environment": asdict(report.environment),
        "privacy": {
            "raw_device_identifiers_included": False,
            "device_names_included": False,
            "device_fingerprints_included": False,
            "local_paths_redacted": True,
            "warning": (
                "Device model, iOS version and build, connection type, host/toolchain versions, and sanitized "
                "capability evidence remain in this report. Review it before sharing."
            ),
        },
        "devices": devices,
    }


def render_compatibility_json(report: CompatibilityReport) -> str:
    return json.dumps(compatibility_report_mapping(report), indent=2, sort_keys=True) + "\n"


def _markdown_cell(value: str) -> str:
    sanitized = sanitize_support_text(value, ())
    return html.escape(sanitized, quote=False).replace("|", "\\|").replace("\n", "<br>")


def render_compatibility_markdown(report: CompatibilityReport) -> str:
    environment = report.environment
    lines = [
        "# iOS Developer Toolkit compatibility report",
        "",
        f"Generated: `{report.generated_at}`",
        "",
        "> This sanitized export omits device names, raw identifiers, and stored device fingerprints. It retains device "
        "model, iOS version/build, connection type, host/toolchain versions, and sanitized capability evidence. Review "
        "it before sharing.",
        "",
        "## Host and toolchain",
        "",
        "| Item | Value |",
        "|---|---|",
        f"| Toolkit | {_markdown_cell(environment.toolkit_version)} |",
        f"| macOS | {_markdown_cell(environment.macos_version)} |",
        f"| Architecture | {_markdown_cell(environment.architecture)} |",
        f"| Runtime | {_markdown_cell(environment.runtime)} |",
        f"| Python | {_markdown_cell(environment.python_version)} |",
        f"| pymobiledevice3 | {_markdown_cell(environment.pymobiledevice3_version)} |",
        f"| PySide6 | {_markdown_cell(environment.pyside6_version)} |",
        "",
    ]
    for index, observation in enumerate(report.observations, start=1):
        lines.extend(
            (
                f"## Observed device {index}",
                "",
                "| Item | Value |",
                "|---|---|",
                f"| Observed at | {_markdown_cell(observation.recorded_at)} |",
                f"| Product type | {_markdown_cell(observation.product_type)} |",
                f"| iOS | {_markdown_cell(observation.product_version)} |",
                f"| Build | {_markdown_cell(observation.build_version)} |",
                f"| Connection | {_markdown_cell(observation.connection_type)} |",
                "",
                "| State | Layer | Capability | Summary | Evidence | Next step |",
                "|---|---|---|---|---|---|",
            )
        )
        for result in observation.results:
            lines.append(
                f"| {_markdown_cell(result.state)} | {_markdown_cell(result.layer)} | "
                f"{_markdown_cell(result.title)} | {_markdown_cell(result.summary)} | "
                f"{_markdown_cell(result.evidence)} | {_markdown_cell(result.remediation)} |"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _write_private_report(destination: Path, expected_suffix: str, content: str) -> Path:
    path = destination.expanduser().resolve()
    if path.suffix.casefold() != expected_suffix:
        raise DeviceCompatibilityError(
            f"Compatibility report destination must end in {expected_suffix}: {path}"
        )
    if not path.parent.is_dir():
        raise DeviceCompatibilityError(f"Compatibility report parent directory does not exist: {path.parent}")
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as error:
        raise DeviceCompatibilityError(f"Refusing to overwrite existing compatibility report: {path}") from error
    except OSError as error:
        raise DeviceCompatibilityError(f"Could not create compatibility report at {path}: {error}") from error
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(content.encode("utf-8"))
            output.flush()
            os.fsync(output.fileno())
    except OSError as error:
        path.unlink(missing_ok=True)
        raise DeviceCompatibilityError(f"Could not write compatibility report at {path}: {error}") from error
    return path


def write_compatibility_json_report(destination: Path, report: CompatibilityReport) -> Path:
    return _write_private_report(destination, ".json", render_compatibility_json(report))


def write_compatibility_markdown_report(destination: Path, report: CompatibilityReport) -> Path:
    return _write_private_report(destination, ".md", render_compatibility_markdown(report))
