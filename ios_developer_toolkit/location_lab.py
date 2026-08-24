from __future__ import annotations

import hashlib
import json
import math
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping, Sequence
from xml.etree import ElementTree


MAX_GPX_BYTES = 64 * 1024 * 1024
SAVED_LOCATIONS_VERSION = 1


class LocationLabError(ValueError):
    pass


@dataclass(frozen=True)
class Coordinates:
    latitude: float
    longitude: float


@dataclass(frozen=True)
class SavedLocation:
    name: str
    coordinates: Coordinates


@dataclass(frozen=True)
class GPXInspection:
    path: Path
    size_bytes: int
    track_point_count: int
    timed_point_count: int
    first_point: Coordinates
    last_point: Coordinates
    sha256: str


@dataclass(frozen=True)
class GeneratedRoute:
    points: tuple[Coordinates, ...]
    distance_metres: float
    duration_seconds: int
    speed_kmh: float
    interval_seconds: int
    traversal_count: int
    gpx_document: str


@dataclass(frozen=True)
class LocationEvidenceEvent:
    event: str
    status: str
    timestamp: str
    device_identifier: str
    device_name: str
    ios_version: str
    command: tuple[str, ...]
    latitude: float | None
    longitude: float | None
    gpx_path: str | None
    gpx_sha256: str | None
    exit_code: int | None
    detail: str

    def to_mapping(self) -> Mapping[str, str | int | float | None | list[str]]:
        return {
            "event": self.event,
            "status": self.status,
            "timestamp": self.timestamp,
            "device_identifier": self.device_identifier,
            "device_name": self.device_name,
            "ios_version": self.ios_version,
            "command": list(self.command),
            "latitude": self.latitude,
            "longitude": self.longitude,
            "gpx_path": self.gpx_path,
            "gpx_sha256": self.gpx_sha256,
            "exit_code": self.exit_code,
            "detail": self.detail,
        }


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate_coordinate(value: str, label: str, minimum: float, maximum: float) -> float:
    try:
        coordinate = float(value.strip())
    except ValueError as error:
        raise LocationLabError(f"{label} must be a decimal number") from error
    if not math.isfinite(coordinate):
        raise LocationLabError(f"{label} must be a finite decimal number")
    if coordinate < minimum or coordinate > maximum:
        raise LocationLabError(f"{label} must be between {minimum:g} and {maximum:g}")
    return coordinate


def validate_coordinates(latitude: str, longitude: str) -> Coordinates:
    return Coordinates(
        latitude=validate_coordinate(latitude, "Latitude", -90.0, 90.0),
        longitude=validate_coordinate(longitude, "Longitude", -180.0, 180.0),
    )


def parse_route_waypoints(payload: str) -> tuple[Coordinates, ...]:
    points: list[Coordinates] = []
    for line_number, raw_line in enumerate(payload.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        values = tuple(value.strip() for value in line.split(","))
        if len(values) != 2:
            raise LocationLabError(
                f"Route waypoint line {line_number} must contain latitude,longitude; received {raw_line!r}"
            )
        try:
            points.append(validate_coordinates(values[0], values[1]))
        except LocationLabError as error:
            raise LocationLabError(f"Route waypoint line {line_number}: {error}") from error
    if len(points) < 2:
        raise LocationLabError("A generated route requires at least two latitude,longitude waypoints")
    return tuple(points)


def haversine_distance_metres(start: Coordinates, end: Coordinates) -> float:
    earth_radius_metres = 6_371_008.8
    start_latitude = math.radians(start.latitude)
    end_latitude = math.radians(end.latitude)
    latitude_delta = end_latitude - start_latitude
    longitude_delta = math.radians(end.longitude - start.longitude)
    haversine = (
        math.sin(latitude_delta / 2.0) ** 2
        + math.cos(start_latitude) * math.cos(end_latitude) * math.sin(longitude_delta / 2.0) ** 2
    )
    return 2.0 * earth_radius_metres * math.asin(min(1.0, math.sqrt(haversine)))


def move_coordinates(origin: Coordinates, bearing_degrees: float, distance_metres: float) -> Coordinates:
    if not math.isfinite(bearing_degrees):
        raise LocationLabError("Nudge bearing must be finite")
    if not math.isfinite(distance_metres) or distance_metres <= 0.0 or distance_metres > 100_000.0:
        raise LocationLabError("Nudge distance must be greater than 0 and at most 100000 metres")
    earth_radius_metres = 6_371_008.8
    angular_distance = distance_metres / earth_radius_metres
    bearing = math.radians(bearing_degrees)
    latitude = math.radians(origin.latitude)
    longitude = math.radians(origin.longitude)
    destination_latitude = math.asin(
        math.sin(latitude) * math.cos(angular_distance)
        + math.cos(latitude) * math.sin(angular_distance) * math.cos(bearing)
    )
    destination_longitude = longitude + math.atan2(
        math.sin(bearing) * math.sin(angular_distance) * math.cos(latitude),
        math.cos(angular_distance) - math.sin(latitude) * math.sin(destination_latitude),
    )
    normalized_longitude = (math.degrees(destination_longitude) + 540.0) % 360.0 - 180.0
    return Coordinates(latitude=math.degrees(destination_latitude), longitude=normalized_longitude)


def _shortest_longitude_delta(start: float, end: float) -> float:
    return (end - start + 540.0) % 360.0 - 180.0


def _interpolate_segment(start: Coordinates, end: Coordinates, step_count: int) -> tuple[Coordinates, ...]:
    if step_count <= 0:
        raise LocationLabError("Route interpolation step count must be positive")
    longitude_delta = _shortest_longitude_delta(start.longitude, end.longitude)
    return tuple(
        Coordinates(
            latitude=start.latitude + (end.latitude - start.latitude) * (index / step_count),
            longitude=(start.longitude + longitude_delta * (index / step_count) + 540.0) % 360.0 - 180.0,
        )
        for index in range(1, step_count + 1)
    )


def _route_traversal(
    waypoints: Sequence[Coordinates],
    metres_per_step: float,
    maximum_points: int,
) -> tuple[Coordinates, ...]:
    points: list[Coordinates] = [waypoints[0]]
    for start, end in zip(waypoints, waypoints[1:]):
        distance = haversine_distance_metres(start, end)
        step_count = max(1, math.ceil(distance / metres_per_step))
        if len(points) + step_count > maximum_points:
            raise LocationLabError(
                "Generated route exceeds 100000 points; increase speed/interval or reduce traversals"
            )
        points.extend(_interpolate_segment(start, end, step_count))
    return tuple(points)


def build_route(
    waypoints: Sequence[Coordinates],
    speed_kmh: float,
    interval_seconds: int,
    traversal_count: int,
    start_time: datetime,
) -> GeneratedRoute:
    if len(waypoints) < 2:
        raise LocationLabError("A generated route requires at least two waypoints")
    if not math.isfinite(speed_kmh) or speed_kmh <= 0.0 or speed_kmh > 300.0:
        raise LocationLabError("Route speed must be greater than 0 and at most 300 km/h")
    if interval_seconds < 1 or interval_seconds > 60:
        raise LocationLabError("Route interval must be between 1 and 60 seconds")
    if traversal_count < 1 or traversal_count > 20:
        raise LocationLabError("Route traversal count must be between 1 and 20")
    if start_time.tzinfo is None:
        raise LocationLabError("Route start time must include timezone information")
    validated_waypoints = tuple(
        Coordinates(
            latitude=validate_coordinate(str(point.latitude), "Latitude", -90.0, 90.0),
            longitude=validate_coordinate(str(point.longitude), "Longitude", -180.0, 180.0),
        )
        for point in waypoints
    )
    metres_per_step = speed_kmh * 1000.0 / 3600.0 * interval_seconds
    all_points: list[Coordinates] = []
    total_distance = 0.0
    for traversal_index in range(traversal_count):
        traversal_waypoints = validated_waypoints if traversal_index % 2 == 0 else tuple(reversed(validated_waypoints))
        sampled = _route_traversal(traversal_waypoints, metres_per_step, 100_000 - len(all_points))
        if all_points:
            sampled = sampled[1:]
        all_points.extend(sampled)
        total_distance += sum(
            haversine_distance_metres(start, end)
            for start, end in zip(traversal_waypoints, traversal_waypoints[1:])
        )
        if len(all_points) > 100_000:
            raise LocationLabError(
                "Generated route exceeds 100000 points; increase speed/interval or reduce traversals"
            )
    duration_seconds = max(0, len(all_points) - 1) * interval_seconds
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<gpx version="1.1" creator="iOS Developer Toolkit" xmlns="http://www.topografix.com/GPX/1/1">',
        "  <trk><name>Toolkit QA route</name><trkseg>",
    ]
    normalized_start = start_time.astimezone(timezone.utc)
    for index, point in enumerate(all_points):
        timestamp = (normalized_start + timedelta(seconds=index * interval_seconds)).isoformat().replace("+00:00", "Z")
        lines.append(
            f'    <trkpt lat="{point.latitude:.9f}" lon="{point.longitude:.9f}"><time>{timestamp}</time></trkpt>'
        )
    lines.extend(("  </trkseg></trk>", "</gpx>"))
    return GeneratedRoute(
        points=tuple(all_points),
        distance_metres=total_distance,
        duration_seconds=duration_seconds,
        speed_kmh=speed_kmh,
        interval_seconds=interval_seconds,
        traversal_count=traversal_count,
        gpx_document="\n".join(lines) + "\n",
    )


def validate_location_name(value: str) -> str:
    name = value.strip()
    if not name:
        raise LocationLabError("Saved location name is required")
    if len(name) > 80:
        raise LocationLabError("Saved location name must be 80 characters or fewer")
    if any(ord(character) < 32 for character in name):
        raise LocationLabError("Saved location name cannot contain control characters")
    return name


def parse_ios_major(version: str) -> int:
    match = re.match(r"^(\d+)(?:\.|$)", version.strip())
    if match is None:
        raise LocationLabError(f"Could not determine the iOS major version from {version!r}")
    return int(match.group(1))


def set_location_arguments(version: str, coordinates: Coordinates) -> tuple[str, ...]:
    values = (format(coordinates.latitude, ".12g"), format(coordinates.longitude, ".12g"))
    if parse_ios_major(version) >= 17:
        return ("developer", "dvt", "simulate-location", "set", "--", *values)
    return ("developer", "simulate-location", "set", "--", *values)


def clear_location_arguments(version: str) -> tuple[str, ...]:
    if parse_ios_major(version) >= 17:
        return ("developer", "dvt", "simulate-location", "clear")
    return ("developer", "simulate-location", "clear")


def play_location_arguments(
    version: str,
    gpx_path: Path,
    timing_randomness_milliseconds: int,
    disable_sleep: bool,
) -> tuple[str, ...]:
    if timing_randomness_milliseconds < 0 or timing_randomness_milliseconds > 60000:
        raise LocationLabError("Timing randomness must be between 0 and 60000 milliseconds")
    resolved_path = gpx_path.expanduser().resolve()
    if parse_ios_major(version) >= 17:
        arguments = [
            "developer",
            "dvt",
            "simulate-location",
            "play",
            str(resolved_path),
            "--timing-randomness-range",
            str(timing_randomness_milliseconds),
        ]
    else:
        arguments = [
            "developer",
            "simulate-location",
            "play",
            str(resolved_path),
            str(timing_randomness_milliseconds),
        ]
    if disable_sleep:
        arguments.append("--disable-sleep")
    return tuple(arguments)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise LocationLabError(f"Could not hash GPX file {path}: {error}") from error
    return digest.hexdigest()


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def inspect_gpx(path: Path) -> GPXInspection:
    resolved_path = path.expanduser().resolve()
    if not resolved_path.is_file():
        raise LocationLabError(f"GPX file does not exist: {resolved_path}")
    if resolved_path.suffix.casefold() != ".gpx":
        raise LocationLabError(f"Route file must use the .gpx extension: {resolved_path}")
    try:
        size_bytes = resolved_path.stat().st_size
    except OSError as error:
        raise LocationLabError(f"Could not read GPX file metadata for {resolved_path}: {error}") from error
    if size_bytes <= 0:
        raise LocationLabError(f"GPX file is empty: {resolved_path}")
    if size_bytes > MAX_GPX_BYTES:
        raise LocationLabError(f"GPX file exceeds the {MAX_GPX_BYTES // (1024 * 1024)} MiB validation limit")
    try:
        raw_xml = resolved_path.read_bytes()
    except OSError as error:
        raise LocationLabError(f"Could not read GPX file {resolved_path}: {error}") from error
    upper_prefix = raw_xml[:16384].upper()
    if b"<!DOCTYPE" in upper_prefix or b"<!ENTITY" in upper_prefix:
        raise LocationLabError("GPX files containing DTD or entity declarations are not accepted")
    try:
        root = ElementTree.fromstring(raw_xml)
    except ElementTree.ParseError as error:
        raise LocationLabError(f"GPX XML is malformed: {error}") from error
    if _local_name(root.tag) != "gpx":
        raise LocationLabError(f"Expected a GPX root element, found {_local_name(root.tag)!r}")
    points: list[Coordinates] = []
    timed_point_count = 0
    for element in root.iter():
        if _local_name(element.tag) != "trkpt":
            continue
        latitude = element.attrib.get("lat")
        longitude = element.attrib.get("lon")
        if latitude is None or longitude is None:
            raise LocationLabError("Every GPX track point must contain lat and lon attributes")
        points.append(validate_coordinates(latitude, longitude))
        if any(_local_name(child.tag) == "time" and (child.text or "").strip() for child in element):
            timed_point_count += 1
    if not points:
        raise LocationLabError("GPX must contain at least one trkpt element; waypoints and routes alone are not replayed")
    return GPXInspection(
        path=resolved_path,
        size_bytes=size_bytes,
        track_point_count=len(points),
        timed_point_count=timed_point_count,
        first_point=points[0],
        last_point=points[-1],
        sha256=sha256_file(resolved_path),
    )


def saved_locations_path(home: Path) -> Path:
    resolved_home = home.expanduser().resolve()
    return resolved_home / "Library" / "Application Support" / "iOS Developer Toolkit" / "locations.json"


def _required_saved_number(record: Mapping[str, object], key: str, label: str, minimum: float, maximum: float) -> float:
    value = record.get(key)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise LocationLabError(f"Saved location {label} must be numeric")
    return validate_coordinate(str(value), label, minimum, maximum)


def parse_saved_locations(payload: str) -> tuple[SavedLocation, ...]:
    try:
        decoded: object = json.loads(payload)
    except json.JSONDecodeError as error:
        raise LocationLabError(f"Saved locations JSON is malformed: {error}") from error
    if not isinstance(decoded, dict):
        raise LocationLabError("Saved locations document must be a JSON object")
    version = decoded.get("version")
    if version != SAVED_LOCATIONS_VERSION:
        raise LocationLabError(f"Unsupported saved locations version: {version!r}")
    records = decoded.get("locations")
    if not isinstance(records, list):
        raise LocationLabError("Saved locations document must contain a locations array")
    locations: list[SavedLocation] = []
    names: set[str] = set()
    for record in records:
        if not isinstance(record, dict):
            raise LocationLabError("Each saved location must be a JSON object")
        raw_name = record.get("name")
        if not isinstance(raw_name, str):
            raise LocationLabError("Each saved location must contain a string name")
        name = validate_location_name(raw_name)
        normalized_name = name.casefold()
        if normalized_name in names:
            raise LocationLabError(f"Saved location names must be unique: {name}")
        names.add(normalized_name)
        coordinates = Coordinates(
            latitude=_required_saved_number(record, "latitude", "Latitude", -90.0, 90.0),
            longitude=_required_saved_number(record, "longitude", "Longitude", -180.0, 180.0),
        )
        locations.append(SavedLocation(name=name, coordinates=coordinates))
    return tuple(locations)


def load_saved_locations(path: Path) -> tuple[SavedLocation, ...]:
    resolved_path = path.expanduser().resolve()
    if not resolved_path.exists():
        return ()
    if not resolved_path.is_file():
        raise LocationLabError(f"Saved locations path is not a file: {resolved_path}")
    try:
        payload = resolved_path.read_text(encoding="utf-8")
    except OSError as error:
        raise LocationLabError(f"Could not read saved locations from {resolved_path}: {error}") from error
    return parse_saved_locations(payload)


def add_saved_location(
    locations: Sequence[SavedLocation],
    name: str,
    coordinates: Coordinates,
) -> tuple[SavedLocation, ...]:
    validated_name = validate_location_name(name)
    if any(location.name.casefold() == validated_name.casefold() for location in locations):
        raise LocationLabError(f"A saved location named {validated_name!r} already exists")
    return (*locations, SavedLocation(name=validated_name, coordinates=coordinates))


def remove_saved_location(
    locations: Sequence[SavedLocation],
    name: str,
) -> tuple[SavedLocation, ...]:
    matching = tuple(location for location in locations if location.name == name)
    if len(matching) != 1:
        raise LocationLabError(f"Expected one saved location named {name!r}, found {len(matching)}")
    return tuple(location for location in locations if location.name != name)


def save_saved_locations(path: Path, locations: Sequence[SavedLocation]) -> None:
    resolved_path = path.expanduser().resolve()
    document = {
        "version": SAVED_LOCATIONS_VERSION,
        "locations": [
            {
                "name": location.name,
                "latitude": location.coordinates.latitude,
                "longitude": location.coordinates.longitude,
            }
            for location in locations
        ],
    }
    temporary_path = resolved_path.with_name(f".{resolved_path.name}.{os.getpid()}.new")
    try:
        resolved_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary_path.replace(resolved_path)
    except OSError as error:
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError as cleanup_error:
            raise LocationLabError(
                f"Could not save locations to {resolved_path}: {error}; "
                f"temporary-file cleanup also failed: {cleanup_error}"
            ) from error
        raise LocationLabError(f"Could not save locations to {resolved_path}: {error}") from error


def append_evidence_event(directory: Path, event: LocationEvidenceEvent) -> Path:
    resolved_directory = directory.expanduser().resolve()
    log_path = resolved_directory / "location-events.jsonl"
    try:
        resolved_directory.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as output:
            output.write(json.dumps(event.to_mapping(), sort_keys=True) + "\n")
    except OSError as error:
        raise LocationLabError(f"Could not append Location Lab evidence to {log_path}: {error}") from error
    return log_path
