from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


class DeviceDataError(ValueError):
    """Raised when usbmux returns malformed or incomplete device data."""


def _required_string(record: Mapping[str, object], key: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value:
        raise DeviceDataError(f"Device record is missing required string field: {key}")
    return value


def _optional_string(record: Mapping[str, object], key: str) -> str:
    value = record.get(key)
    if value is None:
        return "Unknown"
    if not isinstance(value, str):
        raise DeviceDataError(f"Device field {key} must be a string when present")
    return value


@dataclass(frozen=True)
class IOSDevice:
    identifier: str
    name: str
    product_type: str
    product_version: str
    build_version: str
    connection_type: str

    @classmethod
    def from_mapping(cls, record: Mapping[str, object]) -> IOSDevice:
        return cls(
            identifier=_required_string(record, "Identifier"),
            name=_optional_string(record, "DeviceName"),
            product_type=_optional_string(record, "ProductType"),
            product_version=_optional_string(record, "ProductVersion"),
            build_version=_optional_string(record, "BuildVersion"),
            connection_type=_optional_string(record, "ConnectionType"),
        )

    def display_name(self) -> str:
        return f"{self.name} — iOS {self.product_version} ({self.connection_type})"


def parse_devices_json(payload: str) -> tuple[IOSDevice, ...]:
    parsed: object = json.loads(payload)
    if not isinstance(parsed, list):
        raise DeviceDataError("usbmux output must be a JSON array")
    devices: list[IOSDevice] = []
    for item in parsed:
        if not isinstance(item, dict):
            raise DeviceDataError("Each usbmux device entry must be a JSON object")
        devices.append(IOSDevice.from_mapping(item))
    return tuple(devices)


@dataclass(frozen=True)
class CommandSpec:
    identifier: str
    title: str
    arguments: tuple[str, ...]
    output_path: Path
    required: bool
    timeout_seconds: int


@dataclass(frozen=True)
class CommandResult:
    identifier: str
    title: str
    arguments: tuple[str, ...]
    output_path: str
    started_at: str
    ended_at: str
    exit_code: int
    attempts: int
    status: str

    def to_mapping(self) -> Mapping[str, object]:
        return {
            "identifier": self.identifier,
            "title": self.title,
            "arguments": list(self.arguments),
            "output_path": self.output_path,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "exit_code": self.exit_code,
            "attempts": self.attempts,
            "status": self.status,
        }
