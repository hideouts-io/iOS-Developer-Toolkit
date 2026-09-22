from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


class CollectionProtocolError(ValueError):
    """Raised when a collector event does not match the documented JSON-line schema."""


@dataclass(frozen=True)
class CollectionEvent:
    event: str
    message: str
    timestamp: str
    path: Path | None
    status: str | None
    failures: int | None


def required_string(record: Mapping[str, object], field_name: str) -> str:
    value = record.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise CollectionProtocolError(f"{field_name} must be a non-empty string")
    return value.strip()


def optional_string(record: Mapping[str, object], field_name: str) -> str | None:
    value = record.get(field_name)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise CollectionProtocolError(f"{field_name} must be a non-empty string when present")
    return value.strip()


def optional_nonnegative_integer(record: Mapping[str, object], field_name: str) -> int | None:
    value = record.get(field_name)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise CollectionProtocolError(f"{field_name} must be a non-negative integer when present")
    return value


def parse_collection_event(payload: str) -> CollectionEvent:
    raw: object = json.loads(payload)
    if not isinstance(raw, dict) or not all(isinstance(key, str) for key in raw):
        raise CollectionProtocolError("collector event must be a string-keyed JSON object")
    record: Mapping[str, object] = raw
    path_text = optional_string(record, "path")
    path = Path(path_text) if path_text is not None else None
    if path is not None and not path.is_absolute():
        raise CollectionProtocolError("collector event path must be absolute")
    return CollectionEvent(
        event=required_string(record, "event"),
        message=required_string(record, "message"),
        timestamp=required_string(record, "timestamp"),
        path=path,
        status=optional_string(record, "status"),
        failures=optional_nonnegative_integer(record, "failures"),
    )
