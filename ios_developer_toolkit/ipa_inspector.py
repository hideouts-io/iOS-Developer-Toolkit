from __future__ import annotations

import argparse
import json
import plistlib
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Mapping, Sequence


MAX_ARCHIVE_UNCOMPRESSED_BYTES = 8 * 1024 * 1024 * 1024
MAX_INFO_PLIST_BYTES = 10 * 1024 * 1024
MAX_PROVISIONING_PROFILE_BYTES = 20 * 1024 * 1024
BUNDLE_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$")


class IPAInspectionError(RuntimeError):
    """Raised when an IPA cannot be inspected safely or completely."""


@dataclass(frozen=True)
class ArchiveMetadata:
    ipa_path: Path
    app_root: str
    app_name: str
    bundle_identifier: str
    version: str
    build: str
    minimum_os_version: str | None
    executable_name: str
    provisioning_member: str | None
    has_code_resources: bool


@dataclass(frozen=True)
class ProvisioningSummary:
    status: str
    name: str | None
    uuid: str | None
    team_identifiers: tuple[str, ...]
    application_identifier: str | None
    expiration: str | None
    provisioned_device_count: int
    provisions_all_devices: bool
    get_task_allow: bool | None
    developer_certificate_count: int
    detail: str


@dataclass(frozen=True)
class SignatureSummary:
    status: str
    identifier: str | None
    team_identifier: str | None
    authorities: tuple[str, ...]
    detail: str


@dataclass(frozen=True)
class IPAInspection:
    ipa_path: Path
    app_name: str
    bundle_identifier: str
    version: str
    build: str
    minimum_os_version: str | None
    executable_name: str
    provisioning: ProvisioningSummary
    signature: SignatureSummary

    def to_mapping(self) -> Mapping[str, object]:
        return {
            "ipa_path": str(self.ipa_path),
            "app_name": self.app_name,
            "bundle_identifier": self.bundle_identifier,
            "version": self.version,
            "build": self.build,
            "minimum_os_version": self.minimum_os_version,
            "executable_name": self.executable_name,
            "provisioning": {
                "status": self.provisioning.status,
                "name": self.provisioning.name,
                "uuid": self.provisioning.uuid,
                "team_identifiers": list(self.provisioning.team_identifiers),
                "application_identifier": self.provisioning.application_identifier,
                "expiration": self.provisioning.expiration,
                "provisioned_device_count": self.provisioning.provisioned_device_count,
                "provisions_all_devices": self.provisioning.provisions_all_devices,
                "get_task_allow": self.provisioning.get_task_allow,
                "developer_certificate_count": self.provisioning.developer_certificate_count,
                "detail": self.provisioning.detail,
            },
            "signature": {
                "status": self.signature.status,
                "identifier": self.signature.identifier,
                "team_identifier": self.signature.team_identifier,
                "authorities": list(self.signature.authorities),
                "detail": self.signature.detail,
            },
        }


def validate_bundle_identifier(value: str) -> str:
    normalized = value.strip()
    if not BUNDLE_IDENTIFIER_PATTERN.fullmatch(normalized):
        raise IPAInspectionError(
            "Bundle identifier must contain at least two dot-separated alphanumeric or hyphen components"
        )
    return normalized


def format_inspection(inspection: IPAInspection) -> str:
    provisioning = inspection.provisioning
    signature = inspection.signature
    minimum_os = inspection.minimum_os_version or "not declared"
    profile_name = provisioning.name or "not declared"
    profile_expiration = provisioning.expiration or "not declared"
    application_identifier = provisioning.application_identifier or "not declared"
    team_identifiers = ", ".join(provisioning.team_identifiers) or "not declared"
    authority_text = ", ".join(signature.authorities) or "not declared"
    return "\n".join(
        (
            f"App: {inspection.app_name}",
            f"Bundle identifier: {inspection.bundle_identifier}",
            f"Version: {inspection.version} ({inspection.build})",
            f"Minimum iOS: {minimum_os}",
            f"Executable: {inspection.executable_name}",
            "",
            f"Code signature: {signature.status}",
            f"Signing identifier: {signature.identifier or 'not declared'}",
            f"Signing team: {signature.team_identifier or 'not declared'}",
            f"Authorities: {authority_text}",
            f"Verification detail: {signature.detail}",
            "",
            f"Provisioning profile: {provisioning.status}",
            f"Profile name: {profile_name}",
            f"Application identifier: {application_identifier}",
            f"Profile teams: {team_identifiers}",
            f"Expiration: {profile_expiration}",
            f"Provisioned devices: {provisioning.provisioned_device_count}",
            f"All devices: {provisioning.provisions_all_devices}",
            f"Debug entitlement: {provisioning.get_task_allow}",
            f"Developer certificates: {provisioning.developer_certificate_count}",
            f"Provisioning detail: {provisioning.detail}",
        )
    )


def required_string(mapping: Mapping[str, object], key: str, source: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise IPAInspectionError(f"{source} is missing required string field {key}")
    return value


def optional_string(mapping: Mapping[str, object], key: str, source: str) -> str | None:
    value = mapping.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise IPAInspectionError(f"{source} field {key} must be a string when present")
    return value


def validate_archive_members(infos: Sequence[zipfile.ZipInfo]) -> None:
    observed_names: set[str] = set()
    total_size = 0
    for info in infos:
        name = info.filename
        if name in observed_names:
            raise IPAInspectionError(f"IPA contains a duplicate archive member: {name}")
        observed_names.add(name)
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or "\\" in name:
            raise IPAInspectionError(f"IPA contains an unsafe archive path: {name}")
        unix_mode = info.external_attr >> 16
        if stat.S_ISLNK(unix_mode):
            raise IPAInspectionError(f"IPA contains a symbolic link that cannot be inspected safely: {name}")
        if info.flag_bits & 0x1:
            raise IPAInspectionError(f"IPA contains an encrypted archive member: {name}")
        total_size += info.file_size
        if total_size > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
            raise IPAInspectionError(
                f"IPA expands beyond the {MAX_ARCHIVE_UNCOMPRESSED_BYTES // (1024 ** 3)} GiB inspection limit"
            )


def parse_plist_mapping(payload: bytes, source: str) -> Mapping[str, object]:
    try:
        parsed: object = plistlib.loads(payload)
    except plistlib.InvalidFileException as error:
        raise IPAInspectionError(f"{source} is not a valid property list: {error}") from error
    if not isinstance(parsed, dict) or not all(isinstance(key, str) for key in parsed):
        raise IPAInspectionError(f"{source} does not contain a string-keyed property-list dictionary")
    return parsed


def read_archive_metadata(ipa_path: Path) -> ArchiveMetadata:
    resolved_path = ipa_path.expanduser().resolve()
    if not resolved_path.is_file():
        raise FileNotFoundError(f"IPA file does not exist: {resolved_path}")
    if resolved_path.suffix.lower() != ".ipa":
        raise IPAInspectionError(f"Expected a .ipa package, received: {resolved_path.name}")
    try:
        with zipfile.ZipFile(resolved_path) as archive:
            infos = archive.infolist()
            validate_archive_members(infos)
            info_plists = sorted(
                info.filename
                for info in infos
                if len(PurePosixPath(info.filename).parts) == 3
                and info.filename.startswith("Payload/")
                and info.filename.endswith(".app/Info.plist")
            )
            if len(info_plists) != 1:
                raise IPAInspectionError(
                    f"IPA must contain exactly one Payload/<name>.app/Info.plist; found {len(info_plists)}"
                )
            info_plist_member = info_plists[0]
            info_record = archive.getinfo(info_plist_member)
            if info_record.file_size > MAX_INFO_PLIST_BYTES:
                raise IPAInspectionError(f"IPA Info.plist exceeds the {MAX_INFO_PLIST_BYTES} byte limit")
            info_plist = parse_plist_mapping(archive.read(info_record), info_plist_member)
            app_root = info_plist_member.removesuffix("/Info.plist")
            provisioning_member = f"{app_root}/embedded.mobileprovision"
            member_names = {info.filename for info in infos}
            if provisioning_member not in member_names:
                provisioning_member = None
            return ArchiveMetadata(
                ipa_path=resolved_path,
                app_root=app_root,
                app_name=optional_string(info_plist, "CFBundleDisplayName", info_plist_member)
                or required_string(info_plist, "CFBundleName", info_plist_member),
                bundle_identifier=required_string(info_plist, "CFBundleIdentifier", info_plist_member),
                version=required_string(info_plist, "CFBundleShortVersionString", info_plist_member),
                build=required_string(info_plist, "CFBundleVersion", info_plist_member),
                minimum_os_version=optional_string(info_plist, "MinimumOSVersion", info_plist_member),
                executable_name=required_string(info_plist, "CFBundleExecutable", info_plist_member),
                provisioning_member=provisioning_member,
                has_code_resources=f"{app_root}/_CodeSignature/CodeResources" in member_names,
            )
    except zipfile.BadZipFile as error:
        raise IPAInspectionError(f"IPA is not a valid ZIP archive: {resolved_path}") from error


def optional_profile_string(mapping: Mapping[str, object], key: str) -> str | None:
    value = mapping.get(key)
    return value if isinstance(value, str) else None


def string_tuple(value: object, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise IPAInspectionError(f"Provisioning profile field {field} must be a string array")
    return tuple(value)


def provisioning_summary(profile_path: Path | None) -> ProvisioningSummary:
    if profile_path is None:
        return ProvisioningSummary("absent", None, None, (), None, None, 0, False, None, 0, "No embedded.mobileprovision")
    completed = subprocess.run(
        ["/usr/bin/security", "cms", "-D", "-i", str(profile_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        return ProvisioningSummary("invalid", None, None, (), None, None, 0, False, None, 0, detail)
    profile = parse_plist_mapping(completed.stdout, "embedded.mobileprovision")
    entitlements_object = profile.get("Entitlements")
    if not isinstance(entitlements_object, dict) or not all(isinstance(key, str) for key in entitlements_object):
        raise IPAInspectionError("Provisioning profile is missing a string-keyed Entitlements dictionary")
    entitlements: Mapping[str, object] = entitlements_object
    application_identifier = optional_profile_string(entitlements, "application-identifier")
    if application_identifier is None:
        application_identifier = optional_profile_string(entitlements, "com.apple.application-identifier")
    expiration_object = profile.get("ExpirationDate")
    if expiration_object is not None and not isinstance(expiration_object, datetime):
        raise IPAInspectionError("Provisioning profile ExpirationDate must be a date")
    devices = string_tuple(profile.get("ProvisionedDevices"), "ProvisionedDevices")
    certificates_object = profile.get("DeveloperCertificates")
    if certificates_object is None:
        certificate_count = 0
    elif isinstance(certificates_object, list) and all(isinstance(item, bytes) for item in certificates_object):
        certificate_count = len(certificates_object)
    else:
        raise IPAInspectionError("Provisioning profile DeveloperCertificates must be a data array")
    get_task_allow_object = entitlements.get("get-task-allow")
    if get_task_allow_object is not None and not isinstance(get_task_allow_object, bool):
        raise IPAInspectionError("Provisioning entitlement get-task-allow must be a boolean")
    provisions_all_devices_object = profile.get("ProvisionsAllDevices", False)
    if not isinstance(provisions_all_devices_object, bool):
        raise IPAInspectionError("Provisioning profile ProvisionsAllDevices must be a boolean")
    return ProvisioningSummary(
        status="decoded",
        name=optional_profile_string(profile, "Name"),
        uuid=optional_profile_string(profile, "UUID"),
        team_identifiers=string_tuple(profile.get("TeamIdentifier"), "TeamIdentifier"),
        application_identifier=application_identifier,
        expiration=expiration_object.isoformat() if expiration_object is not None else None,
        provisioned_device_count=len(devices),
        provisions_all_devices=provisions_all_devices_object,
        get_task_allow=get_task_allow_object,
        developer_certificate_count=certificate_count,
        detail="CMS payload decoded",
    )


def extract_app(archive: zipfile.ZipFile, metadata: ArchiveMetadata, destination: Path) -> Path:
    app_destination = destination / PurePosixPath(metadata.app_root)
    prefix = f"{metadata.app_root}/"
    for info in archive.infolist():
        if info.filename != metadata.app_root and not info.filename.startswith(prefix):
            continue
        relative_path = PurePosixPath(info.filename)
        target = destination.joinpath(*relative_path.parts)
        if info.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(info) as source, target.open("wb") as output:
            shutil.copyfileobj(source, output)
        unix_mode = (info.external_attr >> 16) & 0o777
        if unix_mode:
            target.chmod(unix_mode)
    if not app_destination.is_dir():
        raise IPAInspectionError(f"IPA extraction did not create the expected app bundle: {metadata.app_root}")
    return app_destination


def parse_codesign_details(output: str) -> tuple[str | None, str | None, tuple[str, ...]]:
    identifier: str | None = None
    team_identifier: str | None = None
    authorities: list[str] = []
    for line in output.splitlines():
        if line.startswith("Identifier="):
            identifier = line.removeprefix("Identifier=")
        elif line.startswith("TeamIdentifier="):
            team_identifier = line.removeprefix("TeamIdentifier=")
        elif line.startswith("Authority="):
            authorities.append(line.removeprefix("Authority="))
    return identifier, team_identifier, tuple(authorities)


def signature_summary(app_path: Path, has_code_resources: bool) -> SignatureSummary:
    if not has_code_resources:
        return SignatureSummary("missing", None, None, (), "_CodeSignature/CodeResources is absent")
    verification = subprocess.run(
        ["/usr/bin/codesign", "--verify", "--deep", "--strict", "--verbose=2", str(app_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    details = subprocess.run(
        ["/usr/bin/codesign", "--display", "--verbose=4", str(app_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    identifier, team_identifier, authorities = parse_codesign_details(details.stdout)
    verification_detail = verification.stdout.strip() or "codesign verification produced no diagnostic text"
    status = "valid" if verification.returncode == 0 else "invalid"
    return SignatureSummary(status, identifier, team_identifier, authorities, verification_detail)


def inspect_ipa(ipa_path: Path) -> IPAInspection:
    metadata = read_archive_metadata(ipa_path)
    with tempfile.TemporaryDirectory(prefix="ios-developer-toolkit-ipa-") as temporary_directory:
        temporary_root = Path(temporary_directory)
        profile_path: Path | None = None
        with zipfile.ZipFile(metadata.ipa_path) as archive:
            validate_archive_members(archive.infolist())
            if metadata.provisioning_member is not None:
                profile_info = archive.getinfo(metadata.provisioning_member)
                if profile_info.file_size > MAX_PROVISIONING_PROFILE_BYTES:
                    raise IPAInspectionError(
                        f"embedded.mobileprovision exceeds the {MAX_PROVISIONING_PROFILE_BYTES} byte limit"
                    )
                profile_path = temporary_root / "embedded.mobileprovision"
                profile_path.write_bytes(archive.read(profile_info))
            provisioning = provisioning_summary(profile_path)
            if metadata.has_code_resources:
                app_path = extract_app(archive, metadata, temporary_root)
                signature = signature_summary(app_path, metadata.has_code_resources)
            else:
                signature = signature_summary(temporary_root, metadata.has_code_resources)
    return IPAInspection(
        ipa_path=metadata.ipa_path,
        app_name=metadata.app_name,
        bundle_identifier=metadata.bundle_identifier,
        version=metadata.version,
        build=metadata.build,
        minimum_os_version=metadata.minimum_os_version,
        executable_name=metadata.executable_name,
        provisioning=provisioning,
        signature=signature,
    )


def required_nonnegative_integer(mapping: Mapping[str, object], key: str, source: str) -> int:
    value = mapping.get(key)
    if type(value) is not int or value < 0:
        raise IPAInspectionError(f"{source} field {key} must be a nonnegative integer")
    return value


def required_boolean(mapping: Mapping[str, object], key: str, source: str) -> bool:
    value = mapping.get(key)
    if type(value) is not bool:
        raise IPAInspectionError(f"{source} field {key} must be a boolean")
    return value


def nullable_string(mapping: Mapping[str, object], key: str, source: str) -> str | None:
    value = mapping.get(key)
    if value is None or isinstance(value, str):
        return value
    raise IPAInspectionError(f"{source} field {key} must be a string or null")


def parsed_string_tuple(mapping: Mapping[str, object], key: str, source: str) -> tuple[str, ...]:
    value = mapping.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise IPAInspectionError(f"{source} field {key} must be a string array")
    return tuple(value)


def parse_inspection_json(payload: str) -> IPAInspection:
    try:
        parsed: object = json.loads(payload)
    except json.JSONDecodeError as error:
        raise IPAInspectionError(f"IPA inspector returned invalid JSON: {error}") from error
    if not isinstance(parsed, dict) or not all(isinstance(key, str) for key in parsed):
        raise IPAInspectionError("IPA inspector JSON root must be a string-keyed object")
    provisioning_object = parsed.get("provisioning")
    signature_object = parsed.get("signature")
    if not isinstance(provisioning_object, dict) or not all(isinstance(key, str) for key in provisioning_object):
        raise IPAInspectionError("IPA inspector provisioning field must be a string-keyed object")
    if not isinstance(signature_object, dict) or not all(isinstance(key, str) for key in signature_object):
        raise IPAInspectionError("IPA inspector signature field must be a string-keyed object")
    provisioning: Mapping[str, object] = provisioning_object
    signature: Mapping[str, object] = signature_object
    get_task_allow_object = provisioning.get("get_task_allow")
    if get_task_allow_object is not None and not isinstance(get_task_allow_object, bool):
        raise IPAInspectionError("IPA inspector get_task_allow must be a boolean or null")
    return IPAInspection(
        ipa_path=Path(required_string(parsed, "ipa_path", "IPA inspector")),
        app_name=required_string(parsed, "app_name", "IPA inspector"),
        bundle_identifier=required_string(parsed, "bundle_identifier", "IPA inspector"),
        version=required_string(parsed, "version", "IPA inspector"),
        build=required_string(parsed, "build", "IPA inspector"),
        minimum_os_version=nullable_string(parsed, "minimum_os_version", "IPA inspector"),
        executable_name=required_string(parsed, "executable_name", "IPA inspector"),
        provisioning=ProvisioningSummary(
            status=required_string(provisioning, "status", "IPA inspector provisioning"),
            name=nullable_string(provisioning, "name", "IPA inspector provisioning"),
            uuid=nullable_string(provisioning, "uuid", "IPA inspector provisioning"),
            team_identifiers=parsed_string_tuple(provisioning, "team_identifiers", "IPA inspector provisioning"),
            application_identifier=nullable_string(
                provisioning, "application_identifier", "IPA inspector provisioning"
            ),
            expiration=nullable_string(provisioning, "expiration", "IPA inspector provisioning"),
            provisioned_device_count=required_nonnegative_integer(
                provisioning, "provisioned_device_count", "IPA inspector provisioning"
            ),
            provisions_all_devices=required_boolean(
                provisioning, "provisions_all_devices", "IPA inspector provisioning"
            ),
            get_task_allow=get_task_allow_object,
            developer_certificate_count=required_nonnegative_integer(
                provisioning, "developer_certificate_count", "IPA inspector provisioning"
            ),
            detail=required_string(provisioning, "detail", "IPA inspector provisioning"),
        ),
        signature=SignatureSummary(
            status=required_string(signature, "status", "IPA inspector signature"),
            identifier=nullable_string(signature, "identifier", "IPA inspector signature"),
            team_identifier=nullable_string(signature, "team_identifier", "IPA inspector signature"),
            authorities=parsed_string_tuple(signature, "authorities", "IPA inspector signature"),
            detail=required_string(signature, "detail", "IPA inspector signature"),
        ),
    )


def parse_args(arguments: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect an iOS IPA signature and provisioning metadata")
    parser.add_argument("ipa", type=Path)
    return parser.parse_args(arguments)


def main() -> int:
    options = parse_args(sys.argv[1:])
    try:
        inspection = inspect_ipa(options.ipa)
    except (FileNotFoundError, IPAInspectionError, OSError, zipfile.BadZipFile) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(json.dumps(inspection.to_mapping(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
