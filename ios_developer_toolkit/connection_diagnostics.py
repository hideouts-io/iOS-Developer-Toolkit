from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal


ConnectionDiagnosticState = Literal[
    "not-scanned",
    "launch-failed",
    "discovery-failed",
    "malformed-output",
    "no-devices",
    "devices-available",
]


@dataclass(frozen=True)
class ConnectionDiagnostic:
    """A privacy-safe result for one usbmux discovery attempt."""

    state: ConnectionDiagnosticState
    checked_at: str
    device_count: int
    detail: str

    def report(self) -> str:
        device_count = f" Devices available: {self.device_count}." if self.state == "devices-available" else ""
        return f"{self.detail}{device_count} Checked: {self.checked_at}."


def initial_connection_diagnostic() -> ConnectionDiagnostic:
    return _diagnostic("not-scanned", 0, "Discovery has not run yet")


def launch_failed_connection_diagnostic() -> ConnectionDiagnostic:
    return _diagnostic("launch-failed", 0, "The usbmux discovery process could not start")


def failed_connection_diagnostic(exit_code: int) -> ConnectionDiagnostic:
    return _diagnostic("discovery-failed", 0, f"usbmux discovery exited with status {exit_code}")


def process_error_connection_diagnostic() -> ConnectionDiagnostic:
    return _diagnostic("discovery-failed", 0, "The usbmux discovery process stopped before returning a device list")


def malformed_output_connection_diagnostic() -> ConnectionDiagnostic:
    return _diagnostic("malformed-output", 0, "usbmux returned output that was not a valid device list")


def devices_connection_diagnostic(device_count: int) -> ConnectionDiagnostic:
    if device_count < 0:
        raise ValueError(f"Device count cannot be negative: {device_count}")
    if device_count == 0:
        return _diagnostic("no-devices", 0, "usbmux completed successfully but found no devices")
    return _diagnostic("devices-available", device_count, "usbmux completed successfully")


def _diagnostic(state: ConnectionDiagnosticState, device_count: int, detail: str) -> ConnectionDiagnostic:
    return ConnectionDiagnostic(state, datetime.now(timezone.utc).isoformat(), device_count, detail)
