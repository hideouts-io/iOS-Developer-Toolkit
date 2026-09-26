from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Mapping

from ios_developer_toolkit.command_catalog import command_presets, preset_categories
from ios_developer_toolkit.support_bundle import sanitize_support_text


MAX_WORKSPACE_PROFILE_BYTES = 1_048_576
WORKSPACE_NAMES = (
    "Home",
    "Device & DDI",
    "Capability Matrix",
    "Location Lab",
    "Live Logs",
    "Command Center",
    "Installed Apps",
    "Backup",
    "Sideload IPA",
    "Evidence Capture",
    "Ecosystem Tools",
    "Man Pages",
    "Scope & Safety",
)
DDI_SOURCES = ("personalized", "local-xcode")


class WorkspaceProfileError(ValueError):
    """Raised when a local, shareable workspace profile is invalid."""


@dataclass(frozen=True)
class AppWorkflowPreferences:
    calculate_app_sizes: bool
    install_as_developer_package: bool


@dataclass(frozen=True)
class BackupWorkflowPreferences:
    force_full_backup: bool
    require_encryption: bool


@dataclass(frozen=True)
class EvidenceWorkflowPreferences:
    capture_duration_seconds: int
    include_syslog: bool
    include_oslog: bool
    include_pcap: bool
    include_screenshot: bool
    include_crash_pull: bool


@dataclass(frozen=True)
class LocationWorkflowPreferences:
    timing_randomness_ms: int
    ignore_timing_delays: bool
    route_speed_preset_kmh: int
    route_speed_kmh: int
    route_interval_seconds: int
    route_traversals: int


@dataclass(frozen=True)
class WorkspaceProfile:
    created_with_version: str
    name: str
    description: str
    default_workspace: str
    ddi_source: str
    command_category: str
    command_preset: str
    app_workflow: AppWorkflowPreferences
    backup_workflow: BackupWorkflowPreferences
    evidence_workflow: EvidenceWorkflowPreferences
    location_workflow: LocationWorkflowPreferences


def _validated_text(value: str, label: str, maximum_length: int, allow_empty: bool) -> str:
    if not isinstance(value, str):
        raise WorkspaceProfileError(f"Workspace profile {label} must be a string")
    normalized = value.strip()
    if not normalized and not allow_empty:
        raise WorkspaceProfileError(f"Workspace profile {label} is required")
    if len(normalized) > maximum_length:
        raise WorkspaceProfileError(
            f"Workspace profile {label} exceeds {maximum_length} characters: {len(normalized)}"
        )
    if any(not character.isprintable() for character in normalized):
        raise WorkspaceProfileError(f"Workspace profile {label} contains control characters")
    if sanitize_support_text(normalized, ()) != normalized:
        raise WorkspaceProfileError(
            f"Workspace profile {label} appears to contain a local path, account, device, or network identifier"
        )
    return normalized


def validate_workspace_profile(profile: WorkspaceProfile) -> WorkspaceProfile:
    created_with_version = _validated_text(profile.created_with_version, "toolkit version", 40, False)
    name = _validated_text(profile.name, "name", 100, False)
    description = _validated_text(profile.description, "description", 500, True)
    if profile.default_workspace not in WORKSPACE_NAMES:
        raise WorkspaceProfileError(f"Unknown default workspace: {profile.default_workspace!r}")
    if profile.ddi_source not in DDI_SOURCES:
        raise WorkspaceProfileError(f"Unknown DDI source preference: {profile.ddi_source!r}")
    categories = ("All categories", *preset_categories())
    if profile.command_category not in categories:
        raise WorkspaceProfileError(f"Unknown command category: {profile.command_category!r}")
    presets = {preset.identifier: preset for preset in command_presets()}
    preset = presets.get(profile.command_preset)
    if preset is None:
        raise WorkspaceProfileError(f"Unknown guided command preset: {profile.command_preset!r}")
    if profile.command_category != "All categories" and preset.category != profile.command_category:
        raise WorkspaceProfileError(
            f"Guided preset {profile.command_preset!r} is not in category {profile.command_category!r}"
        )
    boolean_values = {
        "calculate app sizes": profile.app_workflow.calculate_app_sizes,
        "install as developer package": profile.app_workflow.install_as_developer_package,
        "force full backup": profile.backup_workflow.force_full_backup,
        "require encryption": profile.backup_workflow.require_encryption,
        "include syslog": profile.evidence_workflow.include_syslog,
        "include oslog": profile.evidence_workflow.include_oslog,
        "include pcap": profile.evidence_workflow.include_pcap,
        "include screenshot": profile.evidence_workflow.include_screenshot,
        "include crash pull": profile.evidence_workflow.include_crash_pull,
        "ignore timing delays": profile.location_workflow.ignore_timing_delays,
    }
    invalid_boolean_fields = tuple(
        label for label, value in boolean_values.items() if not isinstance(value, bool)
    )
    if invalid_boolean_fields:
        raise WorkspaceProfileError(
            f"Workspace profile boolean fields are invalid: {invalid_boolean_fields}"
        )
    _bounded_integer(profile.evidence_workflow.capture_duration_seconds, "capture duration", 10, 3600)
    _bounded_integer(profile.location_workflow.timing_randomness_ms, "timing randomness", 0, 60000)
    allowed_speed_presets = (5, 10, 20, 40, 100)
    if profile.location_workflow.route_speed_preset_kmh not in allowed_speed_presets:
        raise WorkspaceProfileError(
            f"Route speed preset must be one of {allowed_speed_presets}: "
            f"{profile.location_workflow.route_speed_preset_kmh}"
        )
    _bounded_integer(profile.location_workflow.route_speed_kmh, "route speed", 1, 300)
    _bounded_integer(profile.location_workflow.route_interval_seconds, "route interval", 1, 60)
    _bounded_integer(profile.location_workflow.route_traversals, "route traversals", 1, 20)
    return replace(
        profile,
        created_with_version=created_with_version,
        name=name,
        description=description,
    )


def _bounded_integer(value: int, label: str, minimum: int, maximum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum or value > maximum:
        raise WorkspaceProfileError(
            f"Workspace profile {label} must be between {minimum} and {maximum}: {value!r}"
        )
    return value


def workspace_profile_mapping(profile: WorkspaceProfile) -> dict[str, object]:
    validated = validate_workspace_profile(profile)
    return {
        "schema_version": 1,
        "created_with_version": validated.created_with_version,
        "name": validated.name,
        "description": validated.description,
        "default_workspace": validated.default_workspace,
        "settings": {
            "ddi_source": validated.ddi_source,
            "command": {
                "category": validated.command_category,
                "preset": validated.command_preset,
            },
            "app_workflow": asdict(validated.app_workflow),
            "backup_workflow": asdict(validated.backup_workflow),
            "evidence_workflow": asdict(validated.evidence_workflow),
            "location_workflow": asdict(validated.location_workflow),
        },
        "privacy": {
            "schema_excludes": [
                "device identity and targets",
                "credentials and authorization acknowledgements",
                "local paths and coordinates",
                "command parameters",
                "case text and capture output",
            ],
            "user_supplied_text_fields": ["name", "description"],
            "warning": "Review the user-supplied name and description before sharing.",
        },
    }


def render_workspace_profile_json(profile: WorkspaceProfile) -> str:
    return json.dumps(workspace_profile_mapping(profile), indent=2, sort_keys=True) + "\n"


def render_workspace_profile_preview(profile: WorkspaceProfile) -> str:
    validated = validate_workspace_profile(profile)
    evidence = validated.evidence_workflow
    location = validated.location_workflow
    return (
        f"Profile: {validated.name}\n"
        f"Description: {validated.description or '(none)'}\n"
        f"Created with toolkit: {validated.created_with_version}\n"
        f"Default workspace: {validated.default_workspace}\n"
        f"DDI source: {validated.ddi_source}\n"
        f"Guided command category: {validated.command_category}\n"
        f"Guided command preset: {validated.command_preset}\n"
        "\n"
        "App workflow\n"
        f"  Calculate app sizes: {validated.app_workflow.calculate_app_sizes}\n"
        f"  Install as developer package: {validated.app_workflow.install_as_developer_package}\n"
        "\n"
        "Backup workflow\n"
        f"  Force full backup: {validated.backup_workflow.force_full_backup}\n"
        f"  Require encryption: {validated.backup_workflow.require_encryption}\n"
        "\n"
        "Evidence workflow\n"
        f"  Capture duration: {evidence.capture_duration_seconds} seconds\n"
        f"  Classic syslog: {evidence.include_syslog}\n"
        f"  DVT OSLog: {evidence.include_oslog}\n"
        f"  PCAP: {evidence.include_pcap}\n"
        f"  Screenshot: {evidence.include_screenshot}\n"
        f"  Crash pull: {evidence.include_crash_pull}\n"
        "\n"
        "Location workflow\n"
        f"  Timing randomness: {location.timing_randomness_ms} ms\n"
        f"  Ignore timing delays: {location.ignore_timing_delays}\n"
        f"  Route speed preset: {location.route_speed_preset_kmh} km/h\n"
        f"  Route speed: {location.route_speed_kmh} km/h\n"
        f"  Point interval: {location.route_interval_seconds} seconds\n"
        f"  Traversals: {location.route_traversals}\n"
        "\n"
        "Excluded by schema: device identity, credentials, paths, coordinates, command parameters, case text, and output.\n"
        "Importing changes visible controls only. It never runs a command or starts a device operation.\n"
    )


def _required_mapping(record: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = record.get(key)
    if not isinstance(value, dict):
        raise WorkspaceProfileError(f"Workspace profile field {key!r} must be a JSON object")
    return value


def _required_string(record: Mapping[str, object], key: str) -> str:
    value = record.get(key)
    if not isinstance(value, str):
        raise WorkspaceProfileError(f"Workspace profile field {key!r} must be a string")
    return value


def _required_boolean(record: Mapping[str, object], key: str) -> bool:
    value = record.get(key)
    if not isinstance(value, bool):
        raise WorkspaceProfileError(f"Workspace profile field {key!r} must be a boolean")
    return value


def _required_integer(record: Mapping[str, object], key: str) -> int:
    value = record.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise WorkspaceProfileError(f"Workspace profile field {key!r} must be an integer")
    return value


def parse_workspace_profile(record: Mapping[str, object]) -> WorkspaceProfile:
    schema_version = record.get("schema_version")
    if not isinstance(schema_version, int) or isinstance(schema_version, bool) or schema_version != 1:
        raise WorkspaceProfileError("Workspace profile has an unsupported schema version")
    settings = _required_mapping(record, "settings")
    command = _required_mapping(settings, "command")
    app_workflow = _required_mapping(settings, "app_workflow")
    backup_workflow = _required_mapping(settings, "backup_workflow")
    evidence_workflow = _required_mapping(settings, "evidence_workflow")
    location_workflow = _required_mapping(settings, "location_workflow")
    profile = WorkspaceProfile(
        created_with_version=_required_string(record, "created_with_version"),
        name=_required_string(record, "name"),
        description=_required_string(record, "description"),
        default_workspace=_required_string(record, "default_workspace"),
        ddi_source=_required_string(settings, "ddi_source"),
        command_category=_required_string(command, "category"),
        command_preset=_required_string(command, "preset"),
        app_workflow=AppWorkflowPreferences(
            calculate_app_sizes=_required_boolean(app_workflow, "calculate_app_sizes"),
            install_as_developer_package=_required_boolean(app_workflow, "install_as_developer_package"),
        ),
        backup_workflow=BackupWorkflowPreferences(
            force_full_backup=_required_boolean(backup_workflow, "force_full_backup"),
            require_encryption=_required_boolean(backup_workflow, "require_encryption"),
        ),
        evidence_workflow=EvidenceWorkflowPreferences(
            capture_duration_seconds=_required_integer(evidence_workflow, "capture_duration_seconds"),
            include_syslog=_required_boolean(evidence_workflow, "include_syslog"),
            include_oslog=_required_boolean(evidence_workflow, "include_oslog"),
            include_pcap=_required_boolean(evidence_workflow, "include_pcap"),
            include_screenshot=_required_boolean(evidence_workflow, "include_screenshot"),
            include_crash_pull=_required_boolean(evidence_workflow, "include_crash_pull"),
        ),
        location_workflow=LocationWorkflowPreferences(
            timing_randomness_ms=_required_integer(location_workflow, "timing_randomness_ms"),
            ignore_timing_delays=_required_boolean(location_workflow, "ignore_timing_delays"),
            route_speed_preset_kmh=_required_integer(location_workflow, "route_speed_preset_kmh"),
            route_speed_kmh=_required_integer(location_workflow, "route_speed_kmh"),
            route_interval_seconds=_required_integer(location_workflow, "route_interval_seconds"),
            route_traversals=_required_integer(location_workflow, "route_traversals"),
        ),
    )
    return validate_workspace_profile(profile)


def load_workspace_profile(source: Path) -> WorkspaceProfile:
    path = source.expanduser().resolve()
    if path.suffix.casefold() != ".json":
        raise WorkspaceProfileError(f"Workspace profile must have a .json extension: {path}")
    if not path.is_file():
        raise WorkspaceProfileError(f"Workspace profile is not a readable file: {path}")
    try:
        size = path.stat().st_size
        if size > MAX_WORKSPACE_PROFILE_BYTES:
            raise WorkspaceProfileError(
                f"Workspace profile exceeds {MAX_WORKSPACE_PROFILE_BYTES} bytes: {size}"
            )
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise WorkspaceProfileError(f"Could not read workspace profile at {path}: {error}") from error
    except json.JSONDecodeError as error:
        raise WorkspaceProfileError(f"Workspace profile is not valid JSON: {error}") from error
    if not isinstance(payload, dict):
        raise WorkspaceProfileError("Workspace profile root must be a JSON object")
    return parse_workspace_profile(payload)


def write_workspace_profile(destination: Path, profile: WorkspaceProfile) -> Path:
    path = destination.expanduser().resolve()
    if path.suffix.casefold() != ".json":
        raise WorkspaceProfileError(f"Workspace profile destination must end in .json: {path}")
    if not path.parent.is_dir():
        raise WorkspaceProfileError(f"Workspace profile parent directory does not exist: {path.parent}")
    content = render_workspace_profile_json(profile).encode("utf-8")
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as error:
        raise WorkspaceProfileError(f"Refusing to overwrite existing workspace profile: {path}") from error
    except OSError as error:
        raise WorkspaceProfileError(f"Could not create workspace profile at {path}: {error}") from error
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
    except OSError as error:
        path.unlink(missing_ok=True)
        raise WorkspaceProfileError(f"Could not write workspace profile at {path}: {error}") from error
    return path
