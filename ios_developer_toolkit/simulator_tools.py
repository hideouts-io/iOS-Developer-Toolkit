from __future__ import annotations

import hashlib
import json
import math
import plistlib
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Mapping
from xml.etree import ElementTree

from ios_developer_toolkit.action_safety import ActionSafetyLevel
from ios_developer_toolkit.apple_tools import AppleToolError, NativeToolOperation, apple_tool, instruments_recording, new_artifact_path, validated_app_bundle, validated_url
from ios_developer_toolkit.installed_apps import InstalledApp, parse_installed_app
from ios_developer_toolkit.ipa_inspector import validate_bundle_identifier
from ios_developer_toolkit.live_logs import LogStreamSpec
from ios_developer_toolkit.location_lab import Coordinates, MAX_GPX_BYTES, validate_coordinates


@dataclass(frozen=True)
class SimulatorTarget:
    identifier: str
    name: str
    state: str
    available: bool
    runtime_identifier: str
    runtime_name: str
    version: str
    device_type: str


@dataclass(frozen=True)
class SimulatorGPXPoint:
    coordinates: Coordinates
    time: datetime | None


@dataclass(frozen=True)
class SimulatorGPXRoute:
    path: Path
    sha256: str
    points: tuple[SimulatorGPXPoint, ...]


def simulator_identifier(value: str) -> str:
    if re.fullmatch(r"[A-Fa-f0-9]{8}-[A-Fa-f0-9]{4}-[A-Fa-f0-9]{4}-[A-Fa-f0-9]{4}-[A-Fa-f0-9]{12}", value) is None:
        raise AppleToolError("Select a concrete simulator UUID; implicit booted/all targets are not accepted")
    return value


def _mapping(value: object, field: str) -> Mapping[object, object]:
    if not isinstance(value, dict):
        raise AppleToolError(f"Simulator {field} must be an object")
    return value


def _string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 1024:
        raise AppleToolError(f"Simulator {field} must be a non-empty string of at most 1024 characters")
    return value


def parse_simulators(payload: bytes) -> tuple[SimulatorTarget, ...]:
    if len(payload) > 16 * 1024 * 1024:
        raise AppleToolError("Simulator inventory exceeds the 16 MiB response limit")
    try:
        raw: object = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as error:
        raise AppleToolError(f"Simulator inventory is invalid JSON: {error}") from error
    root = _mapping(raw, "inventory")
    devices = _mapping(root.get("devices"), "devices")
    runtime_records = root.get("runtimes")
    if not isinstance(runtime_records, list):
        raise AppleToolError("Simulator runtimes must be an array")
    runtimes: dict[str, tuple[str, str]] = {}
    for item in runtime_records:
        record = _mapping(item, "runtime")
        key = _string(record.get("identifier"), "runtime identifier")
        runtimes[key] = (_string(record.get("name"), "runtime name"), _string(record.get("version"), "runtime version"))
    targets: list[SimulatorTarget] = []
    for runtime_key, entries in devices.items():
        runtime = _string(runtime_key, "runtime key")
        if not isinstance(entries, list):
            raise AppleToolError("Each simulator runtime must contain an array of devices")
        for item in entries:
            record = _mapping(item, "device")
            available = record.get("isAvailable")
            if not isinstance(available, bool):
                raise AppleToolError("Simulator isAvailable must be an explicit boolean")
            runtime_name, version = runtimes.get(runtime, (runtime, "unreported"))
            state = _string(record.get("state"), "state")
            if state not in ("Booted", "Shutdown", "Booting", "Shutting Down", "Creating"):
                raise AppleToolError(f"Simulator returned an unrecognized state: {state}")
            targets.append(SimulatorTarget(simulator_identifier(_string(record.get("udid"), "UDID")), _string(record.get("name"), "name"), state, available, runtime, runtime_name, version, _string(record.get("deviceTypeIdentifier"), "device type")))
            if len(targets) > 10000:
                raise AppleToolError("Simulator inventory exceeds the 10000-device limit")
    if len({target.identifier for target in targets}) != len(targets):
        raise AppleToolError("Simulator inventory contains duplicate UUIDs")
    return tuple(sorted(targets, key=lambda target: (target.state != "Booted", target.runtime_name, target.name.casefold())))


def simulator_operation(identifier: str, title: str, target: str, arguments: tuple[str, ...], risk: ActionSafetyLevel, seconds: int, outputs: tuple[Path, ...]) -> NativeToolOperation:
    return NativeToolOperation(identifier, title, apple_tool("xcrun"), ("simctl", *arguments), target, risk, seconds, outputs)


def simulator_inventory() -> NativeToolOperation:
    return simulator_operation("simulator-inventory", "Refresh Simulators", "LOCAL", ("list", "--json"), "read-only", 60, ())


def simulator_boot(target: str) -> NativeToolOperation:
    selected = simulator_identifier(target)
    return simulator_operation("simulator-boot", "Boot Simulator", selected, ("bootstatus", selected, "-b"), "device-change", 600, ())


def simulator_shutdown(target: str) -> NativeToolOperation:
    selected = simulator_identifier(target)
    return simulator_operation("simulator-shutdown", "Shut Down Simulator", selected, ("shutdown", selected), "device-change", 120, ())


def simulator_show(target: str) -> NativeToolOperation:
    selected = simulator_identifier(target)
    return NativeToolOperation("simulator-show", "Show Simulator", apple_tool("open"), ("-a", "Simulator", "--args", "-CurrentDeviceUDID", selected), selected, "device-change", 30, ())


def simulator_erase(target: str) -> NativeToolOperation:
    selected = simulator_identifier(target)
    return simulator_operation("simulator-erase", "Erase Simulator Contents and Settings", selected, ("erase", selected), "high-impact", 300, ())


def simulator_apps(target: str) -> NativeToolOperation:
    selected = simulator_identifier(target)
    return simulator_operation("simulator-apps", "List Simulator Apps", selected, ("listapps", selected), "read-only", 60, ())


def parse_simulator_apps(payload: bytes) -> tuple[InstalledApp, ...]:
    if len(payload) > 32 * 1024 * 1024:
        raise AppleToolError("Simulator app list exceeds the 32 MiB response limit")
    try:
        raw: object = plistlib.loads(payload)
    except (plistlib.InvalidFileException, ValueError) as error:
        raise AppleToolError(f"Simulator app list is not a supported property list: {error}") from error
    records = _mapping(raw, "app list")
    apps = tuple(parse_installed_app(_string(key, "app key"), value) for key, value in records.items())
    return tuple(sorted(apps, key=lambda app: (app.name.casefold(), app.bundle_identifier)))


def decode_simulator_apps(payload: bytes) -> tuple[InstalledApp, ...]:
    """Convert simctl's OpenStep property list through Apple's parser at the I/O boundary."""

    if len(payload) > 32 * 1024 * 1024:
        raise AppleToolError("Simulator app list exceeds the 32 MiB response limit")
    if payload.lstrip().startswith((b"<?xml", b"<plist", b"bplist")):
        return parse_simulator_apps(payload)
    try:
        converted = subprocess.run(("/usr/bin/plutil", "-convert", "xml1", "-o", "-", "-"), input=payload, capture_output=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise AppleToolError(f"Apple's property-list parser could not convert the simulator inventory: {error}") from error
    if converted.returncode != 0:
        detail = converted.stderr.decode("utf-8", errors="replace")[:500]
        raise AppleToolError(f"Apple's property-list parser rejected the simulator inventory (exit {converted.returncode}): {detail}")
    return parse_simulator_apps(converted.stdout)


def simulator_launch(target: str, bundle: str) -> NativeToolOperation:
    selected = simulator_identifier(target)
    return simulator_operation("simulator-launch", "Launch Simulator App", selected, ("launch", "--terminate-running-process", selected, validate_bundle_identifier(bundle)), "device-change", 120, ())


def simulator_terminate(target: str, bundle: str) -> NativeToolOperation:
    selected = simulator_identifier(target)
    return simulator_operation("simulator-terminate", "Terminate Simulator App", selected, ("terminate", selected, validate_bundle_identifier(bundle)), "device-change", 60, ())


def simulator_uninstall(target: str, bundle: str) -> NativeToolOperation:
    selected = simulator_identifier(target)
    return simulator_operation("simulator-uninstall", "Remove Simulator App and Data", selected, ("uninstall", selected, validate_bundle_identifier(bundle)), "high-impact", 120, ())


def simulator_install(target: str, path: Path) -> NativeToolOperation:
    selected = simulator_identifier(target)
    bundle = validated_app_bundle(path, "iPhoneSimulator")
    return simulator_operation("simulator-install", "Install Simulator App Bundle", selected, ("install", selected, str(bundle)), "device-change", 600, ())


def simulator_open_url(target: str, url: str) -> NativeToolOperation:
    selected = simulator_identifier(target)
    return simulator_operation("simulator-url", "Open Simulator URL", selected, ("openurl", selected, validated_url(url)), "device-change", 60, ())


def simulator_screenshot(target: str, path: Path) -> NativeToolOperation:
    selected = simulator_identifier(target)
    destination = new_artifact_path(path, ".png")
    return simulator_operation("simulator-screenshot", "Capture Simulator Screenshot", selected, ("io", selected, "screenshot", "--type=png", str(destination)), "host-write", 60, (destination,))


def simulator_dark(target: str) -> NativeToolOperation:
    selected = simulator_identifier(target)
    return simulator_operation("simulator-dark", "Set Simulator Dark Appearance", selected, ("ui", selected, "appearance", "dark"), "device-change", 60, ())


def simulator_light(target: str) -> NativeToolOperation:
    selected = simulator_identifier(target)
    return simulator_operation("simulator-light", "Set Simulator Light Appearance", selected, ("ui", selected, "appearance", "light"), "device-change", 60, ())


def simulator_set_location(target: str, coordinates: Coordinates) -> NativeToolOperation:
    selected = simulator_identifier(target)
    coordinate = validate_coordinates(str(coordinates.latitude), str(coordinates.longitude))
    pair = f"{coordinate.latitude:.12g},{coordinate.longitude:.12g}"
    return simulator_operation("simulator-set-location", "Set Simulator Location", selected, ("location", selected, "set", pair), "device-change", 60, ())


def simulator_clear_location(target: str) -> NativeToolOperation:
    selected = simulator_identifier(target)
    return simulator_operation("simulator-clear-location", "Clear Simulator Location", selected, ("location", selected, "clear"), "device-change", 60, ())


def simulator_log_spec(target: str) -> LogStreamSpec:
    selected = simulator_identifier(target)
    return LogStreamSpec("simulator-unified", "Simulator Unified Logs", "Local simulator Unified Logs, preserved as raw NDJSON with the existing findings and export viewer.", ("simctl", "spawn", selected, "log", "stream", "--style", "ndjson", "--level", "info"), False, True)


def load_simulator_gpx(path: Path) -> SimulatorGPXRoute:
    source = path.expanduser().resolve()
    if source.suffix.casefold() != ".gpx" or not source.is_file():
        raise AppleToolError(f"Choose an existing .gpx track: {source}")
    with source.open("rb") as stream:
        payload = stream.read(MAX_GPX_BYTES + 1)
    if not payload or len(payload) > MAX_GPX_BYTES:
        raise AppleToolError("GPX tracks must be non-empty and no larger than 64 MiB")
    try:
        xml = payload.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise AppleToolError("Simulator GPX tracks must use UTF-8 XML encoding") from error
    if "<!DOCTYPE" in xml.upper() or "<!ENTITY" in xml.upper() or "\x00" in xml:
        raise AppleToolError("GPX DTD, entity declarations, and embedded NUL characters are not accepted")
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as error:
        raise AppleToolError(f"GPX XML is malformed: {error}") from error
    if root.tag.rsplit("}", 1)[-1] != "gpx":
        raise AppleToolError("Simulator GPX tracks must use a gpx root element")
    points: list[SimulatorGPXPoint] = []
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] != "trkpt":
            continue
        latitude = element.attrib.get("lat")
        longitude = element.attrib.get("lon")
        if latitude is None or longitude is None:
            raise AppleToolError("Every GPX track point must contain lat and lon attributes")
        coordinates = validate_coordinates(latitude, longitude)
        timestamp: datetime | None = None
        for child in element:
            if child.tag.rsplit("}", 1)[-1] == "time":
                if timestamp is not None:
                    raise AppleToolError("Each GPX track point may contain only one time element")
                value = (child.text or "").strip()
                try:
                    timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
                except ValueError as error:
                    raise AppleToolError(f"GPX track timestamp is invalid: {value!r}") from error
                if timestamp.tzinfo is None:
                    raise AppleToolError("GPX track timestamps must include a timezone")
        points.append(SimulatorGPXPoint(coordinates, timestamp))
        if len(points) > 100000:
            raise AppleToolError("GPX playback exceeds the 100000-point limit")
    if not points:
        raise AppleToolError("GPX must contain at least one trkpt element; waypoints and routes alone are not replayed")
    return SimulatorGPXRoute(source, hashlib.sha256(payload).hexdigest(), tuple(points))


def recorded_gpx_offsets(points: tuple[SimulatorGPXPoint, ...]) -> tuple[float, ...]:
    start = next((point.time for point in points if point.time is not None), None)
    offsets: list[float] = []
    previous = -0.5
    for point in points:
        offset = (point.time - start).total_seconds() if point.time is not None and start is not None else previous + 1
        offset = max(offset, previous + 0.5)
        if not math.isfinite(offset) or offset > 86400:
            raise AppleToolError("Recorded GPX playback duration must be no longer than 24 hours")
        offsets.append(offset)
        previous = offset
    return tuple(offsets)


def fixed_gpx_offsets(points: tuple[SimulatorGPXPoint, ...], seconds: float) -> tuple[float, ...]:
    if not math.isfinite(seconds) or seconds < 0.5 or seconds > 3600:
        raise AppleToolError("The GPX playback interval must be between 0.5 and 3600 seconds")
    offsets = tuple(index * seconds for index in range(len(points)))
    if offsets and offsets[-1] > 86400:
        raise AppleToolError("Fixed GPX playback duration must be no longer than 24 hours")
    return offsets


def simulator_recording(target: str, template: str, seconds: int, path: Path) -> NativeToolOperation:
    return instruments_recording(simulator_identifier(target), template, seconds, path)
