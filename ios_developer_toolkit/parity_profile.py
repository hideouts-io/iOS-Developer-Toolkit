"""Translate reviewed local Swift profiles into Python control preferences without executing them."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from ios_developer_toolkit.command_catalog import command_presets
from ios_developer_toolkit.workspace_profile import (
    MAX_WORKSPACE_PROFILE_BYTES,
    AppWorkflowPreferences,
    BackupWorkflowPreferences,
    EvidenceWorkflowPreferences,
    LocationWorkflowPreferences,
    WorkspaceProfile,
    WorkspaceProfileError,
    parse_workspace_profile,
    render_workspace_profile_preview,
    validate_workspace_profile,
    workspace_profile_mapping,
)


@dataclass(frozen=True)
class ParityProfileImport:
    profile: WorkspaceProfile
    include_system_apps: bool | None
    include_oslog_archive: bool | None
    include_instruments_logging: bool | None
    notes: tuple[str, ...]


_SWIFT_WORKSPACES = {
    "overview": "Home", "device": "Device & DDI", "developerImage": "Device & DDI",
    "firmware": "Firmware", "readiness": "Capability Matrix", "apps": "Installed Apps",
    "installApp": "Sideload IPA", "location": "Location Lab", "liveLogs": "Live Logs",
    "actions": "Command Center", "backup": "Backup", "evidence": "Evidence Capture",
    "securityAnalysis": "Security Analysis", "externalTools": "Ecosystem Tools",
    "help": "Man Pages", "safety": "Scope & Safety",
}

# The inverse of Swift's LegacyWorkspaceProfile.presets, choosing the matching service view.
_SWIFT_ACTION_PRESETS = {
    "device-details": "core-device-info", "lockdown-values": "lockdown",
    "activation-state": "activation", "developer-mode-status": "developer-mode",
    "diagnostics": "diagnostics", "battery": "battery", "ioregistry": "ioregistry",
    "mobilegestalt": "mobilegestalt", "processes": "processes", "lock-state": "core-lock",
    "displays": "core-display", "configuration-profiles": "profiles",
    "provisioning-profiles": "provisioning", "orientation": "orientation",
    "icon-metrics": "icon-metrics", "app-query": "apps-query", "media-list": "afc-list",
    "crash-list": "crash-list", "crash-pull": "crash-pull", "mounted-images": "mounted-images",
    "personalization": "personalization", "screenshot": "screenshot",
    "packet-capture": "pcap", "bluetooth-capture": "btlogger", "web-tabs": "web-tabs",
    "bonjour": "bonjour-rsd", "launch-app": "launch-app", "open-url": "open-url",
    "set-location": "location-set", "clear-location": "location-clear",
}

_SWIFT_ACTION_CATEGORIES = {
    **dict.fromkeys(("device-details", "lockdown-values", "activation-state", "developer-mode-status",
                     "diagnostics", "battery", "ioregistry", "mobilegestalt", "processes", "lock-state",
                     "displays", "configuration-profiles", "provisioning-profiles", "orientation", "icon-metrics"), "Device Basics"),
    **dict.fromkeys(("app-query", "media-list", "crash-list", "crash-pull"), "Apps & Files"),
    **dict.fromkeys(("ddi-status", "ddi-prepare", "mounted-images", "personalization", "ddi-unmount",
                     "host-ddis-update", "preferred-ddi"), "Developer Services"),
    **dict.fromkeys(("screenshot", "sysdiagnose", "instruments", "packet-capture", "bluetooth-capture"), "Capture & Instruments"),
    **dict.fromkeys(("web-tabs", "bonjour", "rvi"), "Network & Discovery"),
    **dict.fromkeys(("launch-app", "terminate", "open-url", "set-location", "clear-location", "reboot"), "Device Actions"),
    **dict.fromkeys(("sim-boot", "sim-open", "sim-shutdown", "sim-dark", "sim-light", "sim-erase"), "Simulator"),
}


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise WorkspaceProfileError(f"Profile {label} must be a JSON object with string keys")
    return value


def _string(record: Mapping[str, object], key: str) -> str:
    value = record.get(key)
    if not isinstance(value, str):
        raise WorkspaceProfileError(f"Profile {key!r} must be a string")
    return value


def _boolean(record: Mapping[str, object], key: str) -> bool:
    value = record.get(key)
    if not isinstance(value, bool):
        raise WorkspaceProfileError(f"Profile {key!r} must be a boolean")
    return value


def _integer(record: Mapping[str, object], key: str) -> int:
    value = record.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise WorkspaceProfileError(f"Profile {key!r} must be an integer")
    return value


def _optional_boolean(record: Mapping[str, object], key: str) -> bool | None:
    if key not in record:
        return None
    return _boolean(record, key)


def _swift_collection_boolean(record: Mapping[str, object], key: str) -> bool:
    # Swift CollectionOptions.decodeIfPresent treats an absent or null newer option as false.
    if record.get(key) is None:
        return False
    return _boolean(record, key)


def _native_import(record: Mapping[str, object]) -> ParityProfileImport:
    profile = parse_workspace_profile(record)
    settings = _mapping(record.get("settings"), "settings")
    if "parity_workflow" not in settings:
        return ParityProfileImport(profile, None, None, None, ())
    parity = _mapping(settings["parity_workflow"], "settings.parity_workflow")
    return ParityProfileImport(
        profile, _optional_boolean(parity, "include_system_apps"),
        _optional_boolean(parity, "include_oslog_archive"),
        _optional_boolean(parity, "include_instruments_logging"), (),
    )


def _swift_import(record: Mapping[str, object]) -> ParityProfileImport:
    version = record.get("schemaVersion")
    if not isinstance(version, int) or isinstance(version, bool) or version != 2:
        raise WorkspaceProfileError("Swift workspace profile must use schemaVersion 2")
    workspace = _string(record, "defaultWorkspace")
    mapped_workspace = _SWIFT_WORKSPACES.get(workspace)
    if mapped_workspace is None:
        raise WorkspaceProfileError(f"Swift workspace {workspace!r} has no Python profile destination; choose a supported workspace before exporting")
    action = record.get("selectedAction")
    if not isinstance(action, str):
        raise WorkspaceProfileError("Swift selectedAction must name an action; Python profiles require a selected guided preset. Choose an action before exporting")
    source_category = _string(record, "actionCategory")
    action_category = _SWIFT_ACTION_CATEGORIES.get(action)
    if action_category is None:
        raise WorkspaceProfileError(f"Swift selectedAction {action!r} is unknown")
    if source_category != "All" and source_category != action_category:
        raise WorkspaceProfileError(f"Swift action {action!r} is not in actionCategory {source_category!r}")
    preset_identifier = _SWIFT_ACTION_PRESETS.get(action)
    if preset_identifier is None:
        raise WorkspaceProfileError(f"Swift action {action!r} has no equivalent Python guided preset. Its workspace control may exist, but a profile cannot select it; choose a supported action before exporting")
    presets = {preset.identifier: preset for preset in command_presets()}
    preset = presets.get(preset_identifier)
    if preset is None:
        raise WorkspaceProfileError(f"Python guided preset {preset_identifier!r} is unavailable in this version")
    category = "All categories" if source_category == "All" else preset.category
    mechanism = record.get("developerImageMechanism")
    if mechanism not in ("native", "coreDevice"):
        raise WorkspaceProfileError("Swift developerImageMechanism must explicitly be 'native' or 'coreDevice'; Python has no automatic or unspecified mechanism preference. Choose one before exporting")
    ddi_source = "custom-local" if mechanism == "native" else "core-device"
    apps = _mapping(record.get("apps"), "apps")
    backup = _mapping(record.get("backup"), "backup")
    evidence = _mapping(record.get("evidence"), "evidence")
    location = _mapping(record.get("location"), "location")
    duration = _integer(evidence, "durationSeconds")
    if duration < 10 or duration > 3600:
        raise WorkspaceProfileError(f"Swift durationSeconds {duration} cannot be represented: Python capture duration must be 10–3600 seconds; duration 0 means no streams in Swift")
    speed = _integer(location, "routeSpeedKmh")
    if speed < 1 or speed > 300:
        raise WorkspaceProfileError("Swift routeSpeedKmh must be 1–300")
    speed_preset = min((5, 10, 20, 40, 100), key=lambda value: (abs(value - speed), value))
    unified_logs = _boolean(evidence, "includeUnifiedLogs")
    profile = validate_workspace_profile(WorkspaceProfile(
        _string(record, "createdWithVersion"), _string(record, "name"), _string(record, "description"),
        mapped_workspace, ddi_source, category, preset_identifier,
        AppWorkflowPreferences(_boolean(apps, "calculateSizes"), _boolean(apps, "installAsDeveloperPackage")),
        BackupWorkflowPreferences(_boolean(backup, "forceFullBackup"), _boolean(backup, "requireEncryption")),
        EvidenceWorkflowPreferences(duration, _boolean(evidence, "includeClassicSyslog"), unified_logs,
                                    _boolean(evidence, "includePacketCapture"), _boolean(evidence, "includeScreenshot"),
                                    _boolean(evidence, "includeCrashReports")),
        LocationWorkflowPreferences(_integer(location, "timingJitterMilliseconds"), _boolean(location, "ignoreRecordedTiming"),
                                    speed_preset, speed, _integer(location, "routeIntervalSeconds"), _integer(location, "routeTraversals")),
    ))
    notes = [
        f"Imported Swift schema 2. Workspace {workspace!r} selects {mapped_workspace!r}.",
        f"Swift action {action!r} selects Python guided preset {preset_identifier!r}. Python guided commands use pymobiledevice3; their service view, requirements, formats, and target support can differ from the Swift action. Review the preset before running it.",
    ]
    if source_category != "All" and source_category != category:
        notes.append(f"Swift category {source_category!r} selects Python category {category!r} for this preset.")
    if mechanism == "native":
        notes.append("Swift built-in developer-image mounting selects Python custom local images. Select the matching image files separately; paths and images are excluded from profiles. Python will still personalize newer images with Apple when required.")
    else:
        notes.append("Swift CoreDevice mounting selects Python Xcode's device service. A concrete device must be selected separately before preparing its developer image.")
    if unified_logs:
        notes.append("Swift live Unified Logging selects Python DVT OSLog. These use different logging services and prerequisites, and may expose different records; this translation does not preserve an identical capture mechanism. Saved OSLog archives and Instruments Logging remain separate options.")
    if speed_preset != speed:
        notes.append(f"Swift's actual route speed remains {speed} km/h. Python's auxiliary speed preset selector is {speed_preset} km/h; the actual speed field is applied afterwards and controls playback.")
    return ParityProfileImport(
        profile, _boolean(apps, "includeSystemApps"),
        _swift_collection_boolean(evidence, "includeOSLogArchive"),
        _swift_collection_boolean(evidence, "includeDVTLogging"), tuple(notes),
    )


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise WorkspaceProfileError(f"Profile JSON repeats a field: {key!r}")
        result[key] = value
    return result


def load_parity_profile(source: Path) -> ParityProfileImport:
    """Read a bounded JSON snapshot, validate consumed fields, and return controls plus review notes."""
    path = source.expanduser().resolve()
    if path.suffix.casefold() != ".json" or not path.is_file():
        raise WorkspaceProfileError(f"Choose a readable .json workspace profile: {path}")
    try:
        with path.open("rb") as stream:
            content = stream.read(MAX_WORKSPACE_PROFILE_BYTES + 1)
    except OSError as error:
        raise WorkspaceProfileError(f"Could not read workspace profile at {path}: {error}") from error
    if len(content) > MAX_WORKSPACE_PROFILE_BYTES:
        raise WorkspaceProfileError(f"Workspace profile exceeds {MAX_WORKSPACE_PROFILE_BYTES} bytes")
    try:
        payload: object = json.loads(content.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise WorkspaceProfileError(f"Workspace profile is not valid bounded UTF-8 JSON: {error}") from error
    record = _mapping(payload, "root")
    if "schemaVersion" in record and "schema_version" in record:
        raise WorkspaceProfileError("Profile contains conflicting Swift and Python schema fields")
    if "schemaVersion" in record:
        if len(content) > 64 * 1024:
            raise WorkspaceProfileError("Swift workspace profile exceeds its 64 KiB file limit")
        return _swift_import(record)
    return _native_import(record)


def _parity_preferences(imported: ParityProfileImport) -> dict[str, bool]:
    values = {
        "include_system_apps": imported.include_system_apps,
        "include_oslog_archive": imported.include_oslog_archive,
        "include_instruments_logging": imported.include_instruments_logging,
    }
    result: dict[str, bool] = {}
    for key, value in values.items():
        if value is not None:
            if not isinstance(value, bool):
                raise WorkspaceProfileError(f"Parity preference {key!r} must be a boolean or omitted")
            result[key] = value
    return result


def render_parity_profile_json(imported: ParityProfileImport) -> str:
    """Export native schema 1 with only explicit optional parity booleans; omit review/source data."""
    record = workspace_profile_mapping(imported.profile)
    settings = dict(_mapping(record["settings"], "settings"))
    preferences = _parity_preferences(imported)
    if preferences:
        settings["parity_workflow"] = preferences
    record["settings"] = settings
    return json.dumps(record, indent=2, sort_keys=True) + "\n"


def render_parity_profile_preview(imported: ParityProfileImport) -> str:
    preferences = _parity_preferences(imported)
    labels = {
        "include_system_apps": "Include system apps",
        "include_oslog_archive": "Include saved OSLog archive (last hour)",
        "include_instruments_logging": "Include Instruments Logging trace and XML",
    }
    lines = [render_workspace_profile_preview(imported.profile), "Additional controls"]
    for key, label in labels.items():
        value = preferences.get(key)
        lines.append(f"  {label}: {'preserve current control' if value is None else value}")
    if imported.notes:
        lines.extend(("", "Translation notes to review", *imported.notes))
    return "\n".join(lines) + "\n"


def write_parity_profile(destination: Path, imported: ParityProfileImport) -> Path:
    """Create an owner-only native profile, refusing overwrite including symlink destinations."""
    requested = destination.expanduser().absolute()
    path = requested.parent.resolve() / requested.name
    if path.suffix.casefold() != ".json" or not path.parent.is_dir():
        raise WorkspaceProfileError(f"Choose a .json profile destination in an existing directory: {path}")
    content = render_parity_profile_json(imported).encode("utf-8")
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as error:
        raise WorkspaceProfileError(f"Refusing to overwrite an existing workspace profile: {path}") from error
    except OSError as error:
        raise WorkspaceProfileError(f"Could not create workspace profile at {path}: {error}") from error
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as error:
        path.unlink(missing_ok=True)
        raise WorkspaceProfileError(f"Could not write workspace profile at {path}: {error}") from error
    return path
