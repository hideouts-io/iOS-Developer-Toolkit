from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ios_developer_toolkit.catalog import is_potentially_mutating
from ios_developer_toolkit.command_catalog import RiskLevel


ActionSafetyLevel = Literal["read-only", "host-write", "device-change", "high-impact"]


@dataclass(frozen=True)
class ActionSafetyProfile:
    """The confirmation boundary for a command before it reaches a device or local filesystem."""

    level: ActionSafetyLevel
    impact: str
    requires_typed_acknowledgement: bool
    requires_backup_acknowledgement: bool


def guided_action_safety(risk: RiskLevel) -> ActionSafetyProfile:
    """Return the execution safety profile for a reviewed guided preset."""

    if risk == "read-only":
        return ActionSafetyProfile("read-only", "This command is categorized as read-oriented.", False, False)
    if risk == "host-write":
        return ActionSafetyProfile(
            "host-write",
            "This command writes device-derived data to a local path on the Mac.",
            False,
            False,
        )
    return ActionSafetyProfile(
        "device-change",
        "This command changes device application, UI, location, process, or mounted-image state.",
        True,
        False,
    )


def advanced_action_safety(arguments: tuple[str, ...]) -> ActionSafetyProfile:
    """Classify an arbitrary pymobiledevice3 argument vector before execution."""

    if _has_prefix(arguments, _HIGH_IMPACT_PREFIXES):
        return ActionSafetyProfile(
            "high-impact",
            "This command can erase data, restore firmware, alter activation, reboot, shut down, or make another high-impact device change.",
            True,
            True,
        )
    if _has_prefix(arguments, _HOST_WRITE_PREFIXES):
        return ActionSafetyProfile(
            "host-write",
            "This command writes device-derived data to a local path on the Mac.",
            False,
            False,
        )
    if is_potentially_mutating(arguments):
        return ActionSafetyProfile(
            "device-change",
            "This command is not in the read-only allowlist and may change device or host state.",
            True,
            False,
        )
    return ActionSafetyProfile("read-only", "This command is in the read-only allowlist.", False, False)


def confirmation_phrase(profile: ActionSafetyProfile, device_identifier: str | None) -> str:
    """Create the exact acknowledgement phrase needed for state-changing actions."""

    if not profile.requires_typed_acknowledgement:
        raise ValueError("A typed acknowledgement was requested for an action that does not require one")
    target = _target_suffix(device_identifier)
    if profile.level == "high-impact":
        return f"IRREVERSIBLE {target}"
    return f"RUN {target}"


def _has_prefix(arguments: tuple[str, ...], prefixes: tuple[tuple[str, ...], ...]) -> bool:
    return any(arguments[: len(prefix)] == prefix for prefix in prefixes)


def _target_suffix(device_identifier: str | None) -> str:
    if device_identifier is None:
        return "LOCAL"
    cleaned = "".join(character for character in device_identifier.upper() if character.isalnum())
    if len(cleaned) < 6:
        raise ValueError("The selected device identifier is too short to create a confirmation phrase")
    return cleaned[-6:]


_HIGH_IMPACT_PREFIXES: tuple[tuple[str, ...], ...] = (
    ("restore",),
    ("profile", "erase-device"),
    ("profile", "supervise"),
    ("backup2", "erase-device"),
    ("backup2", "restore"),
    ("diagnostics", "restart"),
    ("diagnostics", "shutdown"),
    ("activation", "activate"),
    ("activation", "deactivate"),
    ("mounter", "roll-personalization-nonce"),
    ("mounter", "roll-cryptex-nonce"),
)

_HOST_WRITE_PREFIXES: tuple[tuple[str, ...], ...] = (
    ("pcap",),
    ("btlogger",),
    ("crash", "pull"),
    ("afc", "pull"),
    ("developer", "dvt", "screenshot"),
)
