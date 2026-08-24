from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TextIO

from pymobiledevice3.lockdown import LockdownClient, create_using_usbmux
from pymobiledevice3.services.mobilebackup2 import Mobilebackup2Service


class BackupRequestError(ValueError):
    pass


BackupAction = Literal["status", "backup"]


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


def emit_event(
    event: str,
    message: str,
    percent: int | None,
    encrypted: bool | None,
    path: Path | None,
) -> None:
    record: dict[str, str | int | bool] = {"event": event, "message": message}
    if percent is not None:
        record["percent"] = percent
    if encrypted is not None:
        record["encrypted"] = encrypted
    if path is not None:
        record["path"] = str(path)
    print(json.dumps(record, sort_keys=True), flush=True)


async def read_encryption_state(lockdown: LockdownClient) -> bool:
    value: object = await lockdown.get_value("com.apple.mobile.backup", "WillEncrypt")
    if not isinstance(value, bool):
        raise BackupRequestError(
            "the device did not return a boolean com.apple.mobile.backup/WillEncrypt value"
        )
    return value


async def execute_request(action: BackupAction, request: BackupRequest) -> None:
    lockdown = await create_using_usbmux(serial=request.udid)
    try:
        async with Mobilebackup2Service(lockdown) as backup_client:
            encrypted = await read_encryption_state(lockdown)
            encryption_enabled_now = False
            if action == "status":
                state = "enabled" if encrypted else "disabled"
                emit_event("encryption-state", f"Backup encryption is {state} on this device.", None, encrypted, None)
                return

            request.destination.mkdir(parents=True, exist_ok=True)
            if request.require_encryption and not encrypted:
                if not request.new_password:
                    raise BackupRequestError(
                        "backup encryption is disabled and a non-empty new password was not provided"
                    )
                emit_event(
                    "encryption-change",
                    "Enabling persistent local-backup encryption on the device before backup.",
                    None,
                    None,
                    None,
                )
                await backup_client.change_password(request.destination, old="", new=request.new_password)
                encrypted = await read_encryption_state(lockdown)
                if not encrypted:
                    raise BackupRequestError("the device did not report encryption enabled after the password change")
                encryption_enabled_now = True
            elif encrypted:
                emit_event(
                    "encryption-state",
                    "The device already requires encrypted local backups; the existing password was not requested or changed.",
                    None,
                    True,
                    None,
                )

            backup_path = request.destination / request.udid
            effective_full = request.full or encryption_enabled_now
            if encryption_enabled_now and not request.full:
                emit_event(
                    "backup-mode",
                    "Encryption was just enabled, so this run is forced to a full backup instead of reusing unencrypted local state.",
                    None,
                    True,
                    backup_path,
                )
            emit_event("backup-started", f"Writing the device backup under {backup_path}", 0, encrypted, backup_path)

            def update_progress(percentage: float) -> None:
                bounded_percent = max(0, min(100, round(percentage)))
                emit_event("progress", f"Backup progress: {bounded_percent}%", bounded_percent, None, None)

            await backup_client.backup(
                full=effective_full,
                backup_directory=request.destination,
                progress_callback=update_progress,
                filter_callback=None,
                password="",
                unback=False,
                patch_manifest=False,
            )
            emit_event("backup-complete", f"Backup completed under {backup_path}", 100, encrypted, backup_path)
    finally:
        await lockdown.close()


def parse_action(value: str) -> BackupAction:
    if value == "status":
        return "status"
    if value == "backup":
        return "backup"
    raise BackupRequestError(f"unsupported backup action: {value}")


def read_request(stream: TextIO) -> BackupRequest:
    payload = stream.read()
    if not payload:
        raise BackupRequestError("backup worker requires one JSON request on standard input")
    return parse_backup_request(payload)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a device backup without exposing passwords in process arguments.")
    parser.add_argument("action", choices=("status", "backup"))
    arguments = parser.parse_args()
    action = parse_action(arguments.action)
    request = read_request(sys.stdin)
    asyncio.run(execute_request(action, request))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
