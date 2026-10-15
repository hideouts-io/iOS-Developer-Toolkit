from __future__ import annotations

import json
import plistlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Mapping
from urllib.parse import urlsplit

from ios_developer_toolkit.action_safety import ActionSafetyLevel
from ios_developer_toolkit.ipa_inspector import IPAInspectionError, validate_bundle_identifier
from ios_developer_toolkit.runtime import ExecutableCommand
from ios_developer_toolkit.xcode_handoff import executable_command


class AppleToolError(ValueError):
    """An explicit Apple-tool target, input, or response failed validation."""


@dataclass(frozen=True)
class NativeToolOperation:
    identifier: str
    title: str
    command: ExecutableCommand
    arguments: tuple[str, ...]
    target: str
    risk: ActionSafetyLevel
    timeout_seconds: int
    output_paths: tuple[Path, ...]


@dataclass(frozen=True)
class CoreDeviceTarget:
    identifier: str
    udid: str | None
    name: str
    transport: str
    tunnel_state: str
    pairing_state: str
    version: str


INSTRUMENTS_TEMPLATES = (
    "Activity Monitor", "Network", "Power Profiler", "Time Profiler",
    "System Trace", "Animation Hitches", "Logging",
)


def apple_tool(name: str) -> ExecutableCommand:
    if name not in ("xcrun", "log", "open"):
        raise AppleToolError(f"Unsupported Apple executable: {name!r}")
    return executable_command(name, (Path("/usr/bin") / name,))


def validated_target(value: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{5,127}", value) is None:
        raise AppleToolError("Select a concrete device identifier; names, options, and implicit targets are not accepted")
    if value.casefold() in ("booted", "all", "local", "host"):
        raise AppleToolError("Select a concrete device identifier instead of an implicit target")
    return value


def validated_url(value: str) -> str:
    if not value or len(value) > 8192 or any(character.isspace() or ord(character) < 32 for character in value):
        raise AppleToolError("Enter a complete URL of at most 8192 characters without whitespace or control characters")
    try:
        parsed = urlsplit(value)
    except ValueError as error:
        raise AppleToolError(f"The URL could not be parsed: {error}") from error
    if not parsed.scheme or (parsed.scheme.casefold() in ("http", "https") and not parsed.hostname):
        raise AppleToolError("Enter a complete URL including its scheme and, for a web URL, its hostname")
    if parsed.username is not None or parsed.password is not None:
        raise AppleToolError("URLs containing embedded credentials are not accepted")
    return value


def new_artifact_path(path: Path, suffix: str) -> Path:
    expanded = path.expanduser().absolute()
    if expanded.suffix.casefold() != suffix or not expanded.parent.is_dir():
        raise AppleToolError(f"Choose a {suffix} destination in an existing directory: {expanded}")
    if expanded.exists() or expanded.is_symlink():
        raise AppleToolError(f"Refusing to overwrite an existing artifact: {expanded}")
    return expanded


def validated_app_bundle(path: Path, platform: Literal["iPhoneOS", "iPhoneSimulator"]) -> Path:
    resolved = path.expanduser().resolve()
    if resolved.suffix.casefold() != ".app" or not resolved.is_dir():
        raise AppleToolError(f"Choose an existing .app bundle built for {platform}: {resolved}")
    plist_path = resolved / "Info.plist"
    try:
        if plist_path.stat().st_size > 4 * 1024 * 1024:
            raise AppleToolError("The app Info.plist exceeds the 4 MiB inspection limit")
        raw: object = plistlib.loads(plist_path.read_bytes())
    except (OSError, plistlib.InvalidFileException, ValueError) as error:
        raise AppleToolError(f"Could not inspect the app Info.plist: {error}") from error
    if not isinstance(raw, dict):
        raise AppleToolError("The app Info.plist root must be a dictionary")
    metadata: Mapping[object, object] = raw
    identifier = metadata.get("CFBundleIdentifier")
    if not isinstance(identifier, str):
        raise AppleToolError("The app must declare a CFBundleIdentifier")
    try:
        validate_bundle_identifier(identifier)
    except IPAInspectionError as error:
        raise AppleToolError(str(error)) from error
    platforms = metadata.get("CFBundleSupportedPlatforms")
    if not isinstance(platforms, list) or platform not in platforms or not all(isinstance(item, str) for item in platforms):
        raise AppleToolError(f"The app must declare {platform} in CFBundleSupportedPlatforms; build for the correct Xcode destination")
    executable = metadata.get("CFBundleExecutable")
    if not isinstance(executable, str) or Path(executable).name != executable or not executable:
        raise AppleToolError("The app must declare a basename CFBundleExecutable")
    if not (resolved / executable).is_file():
        raise AppleToolError(f"The app executable does not exist: {executable}")
    return resolved


def _mapping(value: object, label: str) -> Mapping[object, object]:
    if not isinstance(value, dict):
        raise AppleToolError(f"{label} must be a JSON object")
    return value


def _text(record: Mapping[object, object], field: str, absent: str) -> str:
    value = record.get(field)
    if value is None:
        return absent
    if not isinstance(value, str) or not value:
        raise AppleToolError(f"CoreDevice {field} must be a non-empty string when present")
    return value


def coredevice_result(payload: bytes) -> Mapping[object, object]:
    if len(payload) > 16 * 1024 * 1024:
        raise AppleToolError("CoreDevice JSON exceeds the 16 MiB response limit")
    try:
        raw: object = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as error:
        raise AppleToolError(f"CoreDevice returned invalid JSON: {error}") from error
    root = _mapping(raw, "CoreDevice response")
    info = _mapping(root.get("info"), "CoreDevice info")
    if info.get("outcome") != "success":
        raise AppleToolError("CoreDevice reported an unsuccessful operation; review the device connection and command diagnostics")
    return _mapping(root.get("result"), "CoreDevice result")


def parse_coredevice_targets(payload: bytes) -> tuple[CoreDeviceTarget, ...]:
    devices = coredevice_result(payload).get("devices")
    if not isinstance(devices, list) or len(devices) > 10000:
        raise AppleToolError("CoreDevice devices must be an array with at most 10000 entries")
    targets: list[CoreDeviceTarget] = []
    for item in devices:
        record = _mapping(item, "CoreDevice device")
        identifier = validated_target(_text(record, "identifier", ""))
        if "properties" in record:
            properties = _mapping(record["properties"], "CoreDevice properties")
            device = _mapping(properties.get("device", {}), "CoreDevice device properties")
            hardware = _mapping(properties.get("hardware", {}), "CoreDevice hardware properties")
            connection = _mapping(properties.get("connection", {}), "CoreDevice connection properties")
        else:
            device = _mapping(record.get("deviceProperties", {}), "CoreDevice device properties")
            hardware = _mapping(record.get("hardwareProperties", {}), "CoreDevice hardware properties")
            connection = _mapping(record.get("connectionProperties", {}), "CoreDevice connection properties")
        udid = _text(hardware, "udid", "")
        targets.append(CoreDeviceTarget(
            identifier, validated_target(udid) if udid else None,
            _text(device, "name", identifier), _text(connection, "transportType", "unreported"),
            _text(connection, "tunnelState", "unreported"), _text(connection, "pairingState", "unreported"),
            _text(device, "osVersionNumber", "unreported"),
        ))
    if len({target.identifier for target in targets}) != len(targets):
        raise AppleToolError("CoreDevice returned duplicate target identifiers")
    return tuple(sorted(targets, key=lambda target: (target.name.casefold(), target.identifier)))


def coredevice_operation(identifier: str, title: str, route: tuple[str, ...], target: str, risk: ActionSafetyLevel, seconds: int, outputs: tuple[Path, ...]) -> NativeToolOperation:
    if seconds < 1 or seconds > 3600:
        raise AppleToolError("CoreDevice operation timeout must be between 1 and 3600 seconds")
    return NativeToolOperation(identifier, title, apple_tool("xcrun"), ("devicectl", *route, "--timeout", str(seconds), "--json-output", "-"), target, risk, seconds + 15, outputs)


def coredevice_inventory() -> NativeToolOperation:
    return coredevice_operation("coredevice-inventory", "Refresh CoreDevice Targets", ("list", "devices"), "LOCAL", "read-only", 30, ())


def preferred_ddi() -> NativeToolOperation:
    return coredevice_operation("preferred-ddi", "Inspect Preferred iOS DDI", ("list", "preferredDDI", "--platform", "iOS"), "LOCAL", "read-only", 60, ())


def update_host_ddis() -> NativeToolOperation:
    return coredevice_operation("update-host-ddis", "Update Host DDIs", ("manage", "ddis", "update", "--no-clean"), "LOCAL", "device-change", 900, ())


def coredevice_details(target: str) -> NativeToolOperation:
    selected = validated_target(target)
    return coredevice_operation("coredevice-details", "Inspect CoreDevice Details", ("device", "info", "details", "--device", selected), selected, "read-only", 30, ())


def coredevice_mount_ddi(target: str) -> NativeToolOperation:
    selected = validated_target(target)
    return coredevice_operation("coredevice-mount-ddi", "Prepare CoreDevice Developer Image", ("device", "info", "ddiServices", "--device", selected, "--auto-mount-ddis"), selected, "device-change", 900, ())


def coredevice_apps(target: str) -> NativeToolOperation:
    selected = validated_target(target)
    return coredevice_operation("coredevice-apps", "Inspect CoreDevice Apps", ("device", "info", "apps", "--device", selected, "--include-default-apps"), selected, "read-only", 90, ())


def coredevice_install(target: str, path: Path) -> NativeToolOperation:
    selected = validated_target(target)
    bundle = validated_app_bundle(path, "iPhoneOS")
    return coredevice_operation("coredevice-install", "Install Device App Bundle", ("device", "install", "app", "--device", selected, str(bundle)), selected, "device-change", 600, ())


def coredevice_launch(target: str, bundle: str) -> NativeToolOperation:
    selected = validated_target(target)
    identifier = validate_bundle_identifier(bundle)
    return coredevice_operation("coredevice-launch", "Launch Device App", ("device", "process", "launch", "--device", selected, "--terminate-existing", identifier), selected, "device-change", 120, ())


def coredevice_open_url(target: str, url: str) -> NativeToolOperation:
    selected = validated_target(target)
    return coredevice_operation("coredevice-url", "Open Device URL", ("device", "process", "openURL", "--device", selected, validated_url(url)), selected, "device-change", 60, ())


def device_sysdiagnose(target: str, path: Path) -> NativeToolOperation:
    selected = validated_target(target)
    directory = path.expanduser().resolve()
    if not directory.is_dir():
        raise AppleToolError(f"Choose an existing diagnostic destination directory: {directory}")
    return NativeToolOperation("sysdiagnose", "Collect Device Sysdiagnose", apple_tool("xcrun"), ("devicectl", "device", "sysdiagnose", "--device", selected, "--destination", str(directory), "--timeout", "1800"), selected, "host-write", 1815, (directory,))


def device_log_archive(udid: str, seconds: int, path: Path) -> NativeToolOperation:
    selected = validated_target(udid)
    if seconds < 1 or seconds > 86400:
        raise AppleToolError("Saved log history must be between 1 second and 24 hours")
    destination = new_artifact_path(path, ".logarchive")
    return NativeToolOperation("log-archive", "Collect Device Log Archive", apple_tool("log"), ("collect", "--device-udid", selected, "--last", f"{seconds}s", "--output", str(destination)), selected, "host-write", 900, (destination,))


def instruments_recording(target: str, template: str, seconds: int, path: Path) -> NativeToolOperation:
    selected = validated_target(target)
    if template not in INSTRUMENTS_TEMPLATES or seconds < 1 or seconds > 3600:
        raise AppleToolError("Choose a listed Instruments template and a recording length between 1 and 3600 seconds")
    destination = new_artifact_path(path, ".trace")
    return NativeToolOperation("instruments-record", f"Record {template}", apple_tool("xcrun"), ("xctrace", "record", "--template", template, "--device", selected, "--all-processes", "--time-limit", f"{seconds}s", "--output", str(destination)), selected, "host-write", seconds + 180, (destination,))


def export_trace_logs(trace: Path, output: Path) -> NativeToolOperation:
    source = trace.expanduser().resolve()
    if source.suffix != ".trace" or not source.is_dir():
        raise AppleToolError(f"Choose an existing Logging .trace recording: {source}")
    destination = new_artifact_path(output, ".xml")
    return NativeToolOperation("instruments-export", "Export Recorded OSLog XML", apple_tool("xcrun"), ("xctrace", "export", "--input", str(source), "--xpath", '/trace-toc/run[@number="1"]/data/table[@schema="os-log"]', "--output", str(destination)), "LOCAL", "host-write", 1800, (destination,))


def open_native_artifact(path: Path) -> NativeToolOperation:
    artifact = path.expanduser().resolve()
    if artifact.suffix.casefold() not in (".trace", ".logarchive", ".xcresult") or not artifact.is_dir():
        raise AppleToolError("Choose an existing .trace, .logarchive, or .xcresult bundle")
    return NativeToolOperation("open-native-artifact", "Open Apple Artifact", apple_tool("open"), (str(artifact),), "LOCAL", "read-only", 30, ())


def native_help(tool: str, route: str) -> NativeToolOperation:
    if tool not in ("devicectl", "simctl", "xctrace"):
        raise AppleToolError("Choose devicectl, simctl, or xctrace help")
    parts = tuple(route.split())
    if len(parts) > 8 or any(re.fullmatch(r"[A-Za-z][A-Za-z0-9-]*", part) is None for part in parts):
        raise AppleToolError("Help routes accept at most eight command words without options")
    return NativeToolOperation("native-help", f"Installed {tool} Help", apple_tool("xcrun"), (tool, "help", *parts), "LOCAL", "read-only", 30, ())
