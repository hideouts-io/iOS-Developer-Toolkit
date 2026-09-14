from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping, Sequence

from ios_developer_toolkit.capability_matrix import CapabilityMatrixError, CapabilityResult, parse_capability_result
from ios_developer_toolkit.models import IOSDevice


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
