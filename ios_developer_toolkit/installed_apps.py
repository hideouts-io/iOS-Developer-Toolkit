from __future__ import annotations

import json
from dataclasses import dataclass


class InstalledAppsDataError(ValueError):
    pass


@dataclass(frozen=True)
class InstalledApp:
    name: str
    bundle_identifier: str
    version: str
    build: str
    application_type: str
    static_bytes: int | None
    dynamic_bytes: int | None

    @property
    def total_bytes(self) -> int | None:
        sizes = tuple(size for size in (self.static_bytes, self.dynamic_bytes) if size is not None)
        return sum(sizes) if sizes else None


def required_string(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InstalledAppsDataError(f"{field_name} must be a non-empty string")
    return value.strip()


def optional_string(value: object, field_name: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise InstalledAppsDataError(f"{field_name} must be a string when present")
    return value.strip()


def optional_nonnegative_integer(value: object, field_name: str) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise InstalledAppsDataError(f"{field_name} must be a non-negative integer when present")
    return value


def parse_installed_app(bundle_key: str, raw_metadata: object) -> InstalledApp:
    if not isinstance(raw_metadata, dict):
        raise InstalledAppsDataError(f"metadata for {bundle_key!r} must be an object")
    metadata: dict[object, object] = raw_metadata
    bundle_identifier = required_string(metadata.get("CFBundleIdentifier", bundle_key), "CFBundleIdentifier")
    if bundle_identifier != bundle_key:
        raise InstalledAppsDataError(
            f"app dictionary key {bundle_key!r} does not match CFBundleIdentifier {bundle_identifier!r}"
        )
    display_name = metadata.get("CFBundleDisplayName") or metadata.get("CFBundleName") or bundle_identifier
    return InstalledApp(
        name=required_string(display_name, "CFBundleDisplayName"),
        bundle_identifier=bundle_identifier,
        version=optional_string(metadata.get("CFBundleShortVersionString"), "CFBundleShortVersionString"),
        build=optional_string(metadata.get("CFBundleVersion"), "CFBundleVersion"),
        application_type=optional_string(metadata.get("ApplicationType"), "ApplicationType") or "Unknown",
        static_bytes=optional_nonnegative_integer(metadata.get("StaticDiskUsage"), "StaticDiskUsage"),
        dynamic_bytes=optional_nonnegative_integer(metadata.get("DynamicDiskUsage"), "DynamicDiskUsage"),
    )


def parse_installed_apps_json(payload: str) -> tuple[InstalledApp, ...]:
    raw: object = json.loads(payload)
    if not isinstance(raw, dict):
        raise InstalledAppsDataError("installed-app output must be a JSON object keyed by bundle identifier")
    apps = tuple(
        parse_installed_app(required_string(bundle_key, "bundle identifier key"), raw_metadata)
        for bundle_key, raw_metadata in raw.items()
    )
    return tuple(sorted(apps, key=lambda app: (app.name.casefold(), app.bundle_identifier.casefold())))


def format_byte_count(byte_count: int | None) -> str:
    if byte_count is None:
        return "—"
    units = ("B", "KB", "MB", "GB", "TB")
    value = float(byte_count)
    unit = units[0]
    for candidate in units:
        unit = candidate
        if value < 1000 or candidate == units[-1]:
            break
        value /= 1000
    return f"{value:.1f} {unit}" if unit != "B" else f"{byte_count} B"
