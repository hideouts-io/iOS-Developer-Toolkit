from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


class CaseWorkflowError(ValueError):
    """Raised when guided case intake cannot be created or used safely."""


@dataclass(frozen=True)
class CaseIntake:
    """Local case metadata captured before a bounded collection begins."""

    title: str
    purpose: str
    target_udid: str
    authorization_acknowledged_at: str
    created_at: str


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_udid_fragment(udid: str) -> str:
    allowed = "".join(character for character in udid if character.isalnum())
    if not allowed:
        raise CaseWorkflowError("UDID does not contain any usable alphanumeric characters")
    return allowed[-12:]


def normalized_case_title(title: str) -> str:
    normalized = " ".join(title.split())
    if not normalized:
        raise CaseWorkflowError("Case title is required")
    if len(normalized) > 120:
        raise CaseWorkflowError("Case title must be 120 characters or fewer")
    return normalized


def normalized_case_purpose(purpose: str) -> str:
    normalized = purpose.strip()
    if len(normalized) > 2_000:
        raise CaseWorkflowError("Case purpose must be 2,000 characters or fewer")
    return normalized


def case_directory_path(output_root: Path, udid: str, created_at: datetime) -> Path:
    timestamp = created_at.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return output_root.expanduser().resolve() / f"ios-case-{timestamp}-{safe_udid_fragment(udid)}"


def create_case_directory(output_root: Path, udid: str, created_at: datetime) -> Path:
    root = output_root.expanduser().resolve()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory = case_directory_path(root, udid, created_at)
    try:
        directory.mkdir(mode=0o700, parents=False, exist_ok=False)
    except FileExistsError as error:
        raise CaseWorkflowError(
            f"A case already exists for this device and second: {directory}. Create the case again."
        ) from error
    for name in ("snapshots", "streams", "artifacts"):
        (directory / name).mkdir(mode=0o700)
    return directory


def create_guided_case(
    output_root: Path,
    udid: str,
    title: str,
    purpose: str,
    authorization_acknowledged: bool,
) -> tuple[Path, CaseIntake]:
    if not authorization_acknowledged:
        raise CaseWorkflowError("Confirm that you own the device or are authorized to examine it before creating a case")
    created = datetime.now(timezone.utc)
    intake = CaseIntake(
        title=normalized_case_title(title),
        purpose=normalized_case_purpose(purpose),
        target_udid=udid,
        authorization_acknowledged_at=created.isoformat(),
        created_at=created.isoformat(),
    )
    directory = create_case_directory(output_root, udid, created)
    intake_path = directory / "case-intake.json"
    payload: dict[str, object] = {
        "schema_version": 1,
        "application": "iOS Developer Toolkit",
        "case": asdict(intake),
        "limitations": [
            "The intake records the operator acknowledgement; it does not establish chain of custody.",
            "Hashes are written after collection and detect later changes to the finalized case files.",
        ],
    }
    try:
        intake_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        intake_path.chmod(0o600)
    except OSError as error:
        raise CaseWorkflowError(f"Unable to write the case intake at {intake_path}: {error}") from error
    return directory, intake


def validate_collection_case(case_directory: Path, udid: str) -> Path:
    directory = case_directory.expanduser().resolve()
    intake_path = directory / "case-intake.json"
    if not directory.is_dir():
        raise CaseWorkflowError(f"Guided case directory does not exist: {directory}")
    if (directory / "manifest.json").exists():
        raise CaseWorkflowError(f"Guided case is already finalized: {directory}. Create a new case for another collection.")
    if not intake_path.is_file():
        raise CaseWorkflowError(f"Guided case intake is missing: {intake_path}")
    try:
        payload = json.loads(intake_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CaseWorkflowError(f"Guided case intake is unreadable: {intake_path}: {error}") from error
    if not isinstance(payload, dict):
        raise CaseWorkflowError(f"Guided case intake must contain a JSON object: {intake_path}")
    case_data = payload.get("case")
    if not isinstance(case_data, dict) or case_data.get("target_udid") != udid:
        raise CaseWorkflowError("Guided case target does not match the selected device")
    for name in ("snapshots", "streams", "artifacts"):
        child = directory / name
        if not child.is_dir():
            raise CaseWorkflowError(f"Guided case directory is missing required folder: {child}")
    return directory
