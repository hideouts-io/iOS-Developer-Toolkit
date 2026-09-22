from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


class BackupRequestError(ValueError):
    """Raised when a backup-worker request or event has an invalid schema."""


@dataclass(frozen=True)
class BackupRequest:
    udid: str
    destination: Path
    require_encryption: bool
    new_password: str
    full: bool


@dataclass(frozen=True)
class BackupEvent:
    event: str
    message: str
    percent: int | None
    encrypted: bool | None
    path: Path | None


def required_string(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BackupRequestError(f"{field_name} must be a non-empty string")
    return value.strip()


def required_boolean(value: object, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise BackupRequestError(f"{field_name} must be a boolean")
    return value


def optional_integer(value: object, field_name: str) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise BackupRequestError(f"{field_name} must be an integer when present")
    return value


def optional_boolean(value: object, field_name: str) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise BackupRequestError(f"{field_name} must be a boolean when present")
    return value


def parse_backup_request(payload: str) -> BackupRequest:
    raw: object = json.loads(payload)
    if not isinstance(raw, dict):
        raise BackupRequestError("backup request must be a JSON object")
    request: dict[object, object] = raw
    destination = Path(required_string(request.get("destination"), "destination")).expanduser()
    if not destination.is_absolute():
        raise BackupRequestError("destination must be an absolute path")
    password_value = request.get("new_password")
    if not isinstance(password_value, str):
        raise BackupRequestError("new_password must be a string")
    return BackupRequest(
        udid=required_string(request.get("udid"), "udid"),
        destination=destination.resolve(),
        require_encryption=required_boolean(request.get("require_encryption"), "require_encryption"),
        new_password=password_value,
        full=required_boolean(request.get("full"), "full"),
    )


def parse_backup_event(payload: str) -> BackupEvent:
    raw: object = json.loads(payload)
    if not isinstance(raw, dict):
        raise BackupRequestError("backup event must be a JSON object")
    event: dict[object, object] = raw
    path_value = event.get("path")
    if path_value is not None and not isinstance(path_value, str):
        raise BackupRequestError("path must be a string when present")
    return BackupEvent(
        event=required_string(event.get("event"), "event"),
        message=required_string(event.get("message"), "message"),
        percent=optional_integer(event.get("percent"), "percent"),
        encrypted=optional_boolean(event.get("encrypted"), "encrypted"),
        path=Path(path_value) if isinstance(path_value, str) else None,
    )
