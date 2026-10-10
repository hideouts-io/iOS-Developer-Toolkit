from __future__ import annotations

import hashlib
import json
import math
import os
import plistlib
import re
import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Literal
from urllib.parse import urlsplit, urlunsplit


InstallMode = Literal["update", "restore"]
DeviceMode = Literal["normal", "recovery", "dfu"]
SigningState = Literal["signed", "not-signed"]
CATALOG_URL = "https://itunes.apple.com/check/version"
TSS_URL = "https://gs.apple.com/TSS/controller?action=2"
PINNED_INSTALLER_REVISION = "60192e97f87d1bbab5c493684e0a245b0966363f"
# Standalone packaging reads these literal policy pins through bounded AST parsing.
PINNED_TLS_PATCH_SHA256 = "18609e43bbe67b71af34e5a1c52959f3e9f988a4d5dc26cab8e2a335b10c11c7"
PINNED_RESTORE_RECIPE_SHA256 = "6a398185ee29e06fbe6edae5f32f226296f8e10451e5792bd8ad7081789536ff"
MAX_MANIFEST_BYTES = 32 * 1024 * 1024
MAX_CATALOG_BYTES = 64 * 1024 * 1024
MAX_IPSW_BYTES = 64 * 1024 * 1024 * 1024
MAX_PLAN_AGE_SECONDS = 120


class FirmwareError(RuntimeError):
    """A firmware operation failed; installation must not proceed after this error."""


class FirmwareCancelled(FirmwareError):
    """A cancellable firmware read or download was explicitly stopped."""


@dataclass(frozen=True)
class FirmwareRelease:
    product_type: str
    version: str
    build: str
    url: str
    sha1: str | None

    @property
    def filename(self) -> str:
        return Path(urlsplit(self.url).path).name


@dataclass(frozen=True)
class FileIdentity:
    path: Path
    device: int
    inode: int
    size: int
    mtime_ns: int
    ctime_ns: int
    sha1: str
    sha256: str


@dataclass(frozen=True)
class BuildIdentity:
    device_class: str
    variant: str
    behavior: str
    chip_id: int
    board_id: int
    security_domain: int
    unique_build_id: bytes
    serialized_identity: bytes


@dataclass(frozen=True)
class FirmwareFile:
    path: Path
    version: str
    build: str
    product_types: tuple[str, ...]
    identities: tuple[BuildIdentity, ...]


@dataclass(frozen=True)
class InstallerDevice:
    ecid: int
    product_type: str
    device_class: str
    mode: DeviceMode


@dataclass(frozen=True)
class RecoveryDevice:
    ecid: int
    product_type: str
    device_class: str
    chip_id: int
    board_id: int
    mode: DeviceMode


@dataclass(frozen=True)
class HelperBundle:
    directory: Path
    installer: FileIdentity
    recovery: FileIdentity
    manifest: FileIdentity
    files: tuple[FileIdentity, ...]


@dataclass(frozen=True)
class FirmwareInstallPlan:
    firmware: FirmwareFile
    file_identity: FileIdentity
    helper_bundle: HelperBundle
    target: InstallerDevice
    selected_identifier: str
    mode: InstallMode
    identity: BuildIdentity
    catalog_sha1: str | None
    provenance: str
    signing_state: SigningState
    signing_checked_at: float
    preflight_finished_at: float
    created_at: float


def required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 4096:
        raise FirmwareError(f"Firmware field {field} must be a nonempty bounded string")
    return value.strip()


def required_mapping(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise FirmwareError(f"Firmware field {field} must be an object with string keys")
    return {key: item for key, item in value.items()}


def integer_value(value: object, field: str) -> int:
    if isinstance(value, bool):
        raise FirmwareError(f"Firmware field {field} must be an integer")
    if isinstance(value, int):
        number = value
    elif isinstance(value, str) and re.fullmatch(r"(?:0[xX][0-9a-fA-F]+|[0-9]+)", value):
        number = int(value, 16 if value.lower().startswith("0x") else 10)
    else:
        raise FirmwareError(f"Firmware field {field} must be an integer")
    if number < 0 or number > 2**64 - 1:
        raise FirmwareError(f"Firmware field {field} is outside its unsigned 64-bit range")
    return number


def validated_apple_url(value: str) -> str:
    parts = urlsplit(value)
    hostname = parts.hostname or ""
    if parts.scheme == "http":
        parts = parts._replace(scheme="https")
    allowed = hostname == "apple.com" or hostname.endswith(".apple.com")
    allowed = allowed or hostname == "cdn-apple.com" or hostname.endswith(".cdn-apple.com")
    if not allowed or parts.scheme != "https" or parts.username or parts.password or parts.port not in (None, 443):
        raise FirmwareError("Firmware URLs must use an Apple HTTPS server without embedded credentials")
    if parts.fragment:
        raise FirmwareError("Firmware URLs must not contain a fragment")
    return urlunsplit(parts)


def parse_catalog(payload: bytes, product_type: str) -> tuple[FirmwareRelease, ...]:
    if len(payload) > MAX_CATALOG_BYTES:
        raise FirmwareError("Apple's firmware catalog exceeds the 64 MiB limit")
    try:
        root = required_mapping(plistlib.loads(payload), "catalog")
    except (plistlib.InvalidFileException, ValueError, TypeError, OverflowError) as error:
        raise FirmwareError("Apple's firmware catalog is not a valid property list") from error
    groups = required_mapping(root.get("MobileDeviceSoftwareVersionsByVersion"), "catalog versions")
    releases: dict[str, FirmwareRelease] = {}
    for group_value in groups.values():
        group = required_mapping(group_value, "catalog version group")
        models = required_mapping(group.get("MobileDeviceSoftwareVersions"), "catalog models")
        if product_type not in models:
            continue
        builds = required_mapping(models[product_type], "catalog model builds")
        for entry_value in builds.values():
            entry = required_mapping(entry_value, "catalog build")
            if "Restore" not in entry:
                continue
            restore = required_mapping(entry["Restore"], "catalog restore")
            url = validated_apple_url(required_text(restore.get("FirmwareURL"), "FirmwareURL"))
            if not urlsplit(url).path.lower().endswith(".ipsw"):
                raise FirmwareError("Apple's catalog returned a firmware URL without an .ipsw extension")
            digest = restore.get("FirmwareSHA1")
            if digest is not None and (not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", digest)):
                raise FirmwareError("Apple's catalog checksum is not a SHA-1 digest")
            release = FirmwareRelease(product_type, required_text(restore.get("ProductVersion"), "ProductVersion"),
                                      required_text(restore.get("BuildVersion"), "BuildVersion"), url,
                                      digest.lower() if isinstance(digest, str) else None)
            previous = releases.get(release.build)
            if previous is not None and previous != release:
                raise FirmwareError("Apple's catalog has conflicting entries for one firmware build")
            releases[release.build] = release
    return tuple(sorted(releases.values(), key=lambda item: (item.version, item.build), reverse=True))


def parse_manifest(payload: bytes, path: Path) -> FirmwareFile:
    if len(payload) > MAX_MANIFEST_BYTES:
        raise FirmwareError("BuildManifest.plist exceeds the 32 MiB limit")
    try:
        root = required_mapping(plistlib.loads(payload), "BuildManifest")
    except (plistlib.InvalidFileException, ValueError, TypeError, OverflowError) as error:
        raise FirmwareError("The IPSW build manifest is not a valid property list") from error
    products = root.get("SupportedProductTypes")
    identities = root.get("BuildIdentities")
    if not isinstance(products, list) or not products or len(products) > 1000:
        raise FirmwareError("BuildManifest must list its supported product types")
    if not isinstance(identities, list) or not identities or len(identities) > 1000:
        raise FirmwareError("BuildManifest must contain bounded build identities")
    parsed: list[BuildIdentity] = []
    for raw in identities:
        identity = required_mapping(raw, "BuildIdentity")
        info = required_mapping(identity.get("Info"), "BuildIdentity.Info")
        manifest = required_mapping(identity.get("Manifest"), "BuildIdentity.Manifest")
        if not manifest or len(manifest) > 4096:
            raise FirmwareError("BuildIdentity.Manifest must contain bounded firmware components")
        for name, component_value in manifest.items():
            component = required_mapping(component_value, f"Manifest.{name}")
            if "Info" in component:
                required_mapping(component["Info"], f"Manifest.{name}.Info")
            if "Digest" in component and not isinstance(component["Digest"], bytes):
                raise FirmwareError(f"Manifest.{name}.Digest must be binary data")
            if "Trusted" in component and not isinstance(component["Trusted"], bool):
                raise FirmwareError(f"Manifest.{name}.Trusted must be boolean")
        unique = identity.get("UniqueBuildID")
        if not isinstance(unique, bytes) or not unique or len(unique) > 1024:
            raise FirmwareError("BuildIdentity.UniqueBuildID must be nonempty bounded binary data")
        parsed.append(BuildIdentity(required_text(info.get("DeviceClass"), "DeviceClass").lower(),
                                    required_text(info.get("Variant"), "Variant"),
                                    required_text(info.get("RestoreBehavior"), "RestoreBehavior"),
                                    integer_value(identity.get("ApChipID"), "ApChipID"),
                                    integer_value(identity.get("ApBoardID"), "ApBoardID"),
                                    integer_value(identity.get("ApSecurityDomain"), "ApSecurityDomain"),
                                    unique, plistlib.dumps(identity, fmt=plistlib.FMT_BINARY)))
    return FirmwareFile(path, required_text(root.get("ProductVersion"), "ProductVersion"),
                        required_text(root.get("ProductBuildVersion"), "ProductBuildVersion"),
                        tuple(required_text(value, "SupportedProductTypes") for value in products), tuple(parsed))


def inspect_ipsw(path: Path) -> FirmwareFile:
    selected = path.expanduser().absolute()
    info = selected.lstat()
    if not stat.S_ISREG(info.st_mode) or selected.suffix.lower() != ".ipsw" or not 0 < info.st_size <= MAX_IPSW_BYTES:
        raise FirmwareError("Choose a nonempty regular .ipsw file of at most 64 GiB; symbolic links are refused")
    try:
        with zipfile.ZipFile(selected, "r", allowZip64=True) as archive:
            entries = archive.infolist()
            if len(entries) > 100_000 or sum(entry.file_size for entry in entries) > MAX_IPSW_BYTES:
                raise FirmwareError("The IPSW exceeds its archive entry or expanded-size limit")
            matches = [entry for entry in entries if entry.filename == "BuildManifest.plist"]
            if len(matches) != 1:
                raise FirmwareError("The IPSW must contain exactly one root BuildManifest.plist")
            entry = matches[0]
            if entry.flag_bits & 1 or entry.file_size > MAX_MANIFEST_BYTES or entry.compress_size > MAX_MANIFEST_BYTES:
                raise FirmwareError("The IPSW manifest is encrypted or exceeds the 32 MiB limit")
            with archive.open(entry, "r") as source:
                payload = source.read(MAX_MANIFEST_BYTES + 1)
            if len(payload) != entry.file_size:
                raise FirmwareError("The IPSW manifest size does not match its ZIP record")
            return parse_manifest(payload, selected)
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError) as error:
        raise FirmwareError(f"Could not read IPSW archive {selected.name}: {error}") from error


def file_identity(path: Path, cancelled: Callable[[], bool]) -> FileIdentity:
    selected = path.expanduser().absolute()
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    descriptor = os.open(selected, flags)
    with os.fdopen(descriptor, "rb") as source:
        before = os.fstat(source.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size <= 0:
            raise FirmwareError("Firmware validation requires a nonempty regular file")
        sha1 = hashlib.sha1()
        sha256 = hashlib.sha256()
        while True:
            if cancelled():
                raise FirmwareCancelled("Firmware file validation was stopped")
            block = source.read(8 * 1024 * 1024)
            if not block:
                break
            sha1.update(block)
            sha256.update(block)
        after = os.fstat(source.fileno())
    fields = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
    if fields(before) != fields(after) or fields(after) != fields(selected.lstat()):
        raise FirmwareError("The firmware or helper changed while its hashes were being computed")
    return FileIdentity(selected, *fields(after), sha1.hexdigest(), sha256.hexdigest())


def require_file_unchanged(identity: FileIdentity) -> None:
    info = identity.path.lstat()
    if not stat.S_ISREG(info.st_mode) or (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns) != (
        identity.device, identity.inode, identity.size, identity.mtime_ns, identity.ctime_ns
    ):
        raise FirmwareError("The firmware or helper changed after validation; run Check Before Installing again")


def validate_helper_bundle(directory: Path, cancelled: Callable[[], bool]) -> HelperBundle:
    """Check schema-2 local bytes against the reviewed patched recipe identity.

    Recipe and patch digests are declared build records, not authentication of
    upstream origin or proof of a binary's transport behavior.
    """
    selected = directory.expanduser().absolute()
    if selected.is_symlink() or not selected.is_dir():
        raise FirmwareError("Choose a regular firmware helper folder, not a symbolic link")
    manifest_path = selected / "restore-helper-manifest.json"
    manifest_identity = file_identity(manifest_path, cancelled)
    if manifest_identity.size > 1_048_576:
        raise FirmwareError("The helper manifest exceeds its 1 MiB limit")
    try:
        root = required_mapping(json.loads(manifest_path.read_bytes()), "helper manifest")
    except (ValueError, UnicodeDecodeError) as error:
        raise FirmwareError("The helper bundle manifest is invalid JSON") from error
    if set(root) != {"schema_version", "source_revision", "minimum_macos", "security_patch_sha256", "recipe_sha256", "files"} or type(root.get("schema_version")) is not int or root.get("schema_version") != 2:
        raise FirmwareError("The helper bundle requires the patched recipe's closed schema 2; rebuild the firmware helpers into a fresh output folder")
    if root.get("minimum_macos") != "14.0":
        raise FirmwareError("The helper bundle must declare its reviewed macOS 14.0 deployment target")
    if root.get("source_revision") != PINNED_INSTALLER_REVISION:
        raise FirmwareError("The helper manifest does not declare the reviewed installer source revision")
    if root.get("security_patch_sha256") != PINNED_TLS_PATCH_SHA256 or root.get("recipe_sha256") != PINNED_RESTORE_RECIPE_SHA256:
        raise FirmwareError("The helper manifest differs from the reviewed security patch or recipe; rebuild the firmware helpers into a fresh output folder")
    files = required_mapping(root.get("files"), "helper files")
    if not {"bin/idevicerestore", "bin/irecovery", "SOURCES.txt", "STAMP"}.issubset(files) or len(files) > 1024 or not any(name.startswith("licenses/") for name in files):
        raise FirmwareError("The helper bundle must inventory both binaries, source pins, stamp, and licenses")
    identities: list[FileIdentity] = []
    for relative, expected in files.items():
        parts = PurePosixPath(relative)
        if parts.is_absolute() or ".." in parts.parts or "\\" in relative or not relative:
            raise FirmwareError("The helper manifest contains an unsafe relative path")
        if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise FirmwareError("The helper manifest must contain lowercase SHA-256 digests")
        path = selected / relative
        if any(component.is_symlink() for component in (path, *path.parents) if component != selected.parent):
            raise FirmwareError("Symbolic links are not permitted in firmware helper paths")
        identity = file_identity(path, cancelled)
        if identity.sha256 != expected:
            raise FirmwareError(f"The firmware helper inventory hash does not match {relative}")
        identities.append(identity)
    require_file_unchanged(manifest_identity)
    indexed = {identity.path.relative_to(selected).as_posix(): identity for identity in identities}
    stamp = indexed["STAMP"]
    if stamp.size != 65 or stamp.path.read_bytes() != (PINNED_RESTORE_RECIPE_SHA256 + "\n").encode("ascii"):
        raise FirmwareError("The helper STAMP differs from the reviewed patched recipe; rebuild the firmware helpers into a fresh output folder")
    require_file_unchanged(stamp)
    installer, recovery = indexed["bin/idevicerestore"], indexed["bin/irecovery"]
    if not os.access(installer.path, os.X_OK) or not os.access(recovery.path, os.X_OK):
        raise FirmwareError("Both firmware helpers must be executable")
    return HelperBundle(selected, installer, recovery, manifest_identity, tuple(identities))


def parse_installer_device(output: str) -> InstallerDevice:
    lines = [line.strip() for line in output.splitlines()]
    ecids = [line[6:] for line in lines if line.startswith("ECID: ")]
    devices = [line[21:] for line in lines if line.startswith("Identified device as ")]
    modes = [line[16:-5].lower() for line in lines if line.startswith("Found device in ") and line.endswith(" mode")]
    if len(ecids) != 1 or len(devices) != 1 or len(modes) != 1 or modes[0] not in ("normal", "recovery", "dfu"):
        raise FirmwareError("The installer did not report one exact device identity and supported mode")
    fields = [part.strip() for part in devices[0].split(",", 1)]
    ecid = integer_value(ecids[0], "installer ECID")
    if len(fields) != 2 or not all(fields) or not ecid:
        raise FirmwareError("The installer reported an incomplete device identity")
    mode: DeviceMode
    if modes[0] == "normal":
        mode = "normal"
    elif modes[0] == "recovery":
        mode = "recovery"
    else:
        mode = "dfu"
    return InstallerDevice(ecid, fields[1], fields[0].lower(), mode)


def parse_recovery_device(output: str) -> RecoveryDevice:
    fields: dict[str, str] = {}
    for line in output.splitlines():
        key, delimiter, value = line.partition(":")
        key = key.strip()
        if delimiter and key in {"ECID", "CPID", "BDID", "PRODUCT", "MODEL", "MODE"}:
            if key in fields:
                raise FirmwareError(f"Recovery query repeated the identity field {key}")
            fields[key] = value.strip()
    mode = required_text(fields.get("MODE"), "MODE").lower()
    if mode not in ("recovery", "dfu"):
        raise FirmwareError("Recovery query did not report recovery or DFU mode")
    ecid = integer_value(fields.get("ECID"), "ECID")
    if ecid == 0:
        raise FirmwareError("Recovery query returned a zero ECID; an exact target is required")
    return RecoveryDevice(ecid, required_text(fields.get("PRODUCT"), "PRODUCT"),
                          required_text(fields.get("MODEL"), "MODEL").lower(), integer_value(fields.get("CPID"), "CPID"),
                          integer_value(fields.get("BDID"), "BDID"), "recovery" if mode == "recovery" else "dfu")


def matching_install_identity(firmware: FirmwareFile, target: InstallerDevice, mode: InstallMode) -> BuildIdentity:
    if mode not in ("update", "restore") or target.mode not in ("normal", "recovery", "dfu") or not 0 < target.ecid <= 2**64 - 1:
        raise FirmwareError("The install mode or device identity is invalid")
    if target.product_type not in firmware.product_types:
        raise FirmwareError("This IPSW does not support the connected device model")
    if mode == "update" and target.mode == "dfu":
        raise FirmwareError("Update is unavailable in DFU mode; Restore requires separate erase confirmation")
    variant = "Customer Upgrade Install (IPSW)" if mode == "update" else "Customer Erase Install (IPSW)"
    behavior = "Update" if mode == "update" else "Erase"
    matches = [identity for identity in firmware.identities if identity.device_class == target.device_class and identity.variant == variant]
    if len(matches) != 1 or matches[0].behavior != behavior or matches[0].chip_id <= 0 or matches[0].security_domain <= 0:
        raise FirmwareError(f"The IPSW has no unambiguous {behavior} identity for the connected device board; installation is blocked")
    raw = required_mapping(plistlib.loads(matches[0].serialized_identity), "selected build identity")
    if "Ap,ProductType" in raw and raw["Ap,ProductType"] != target.product_type:
        raise FirmwareError("The selected build identity contradicts the connected product type")
    return matches[0]


def preflight_arguments(ipsw: Path, identifier: str, mode: InstallMode, cache: Path, logfile: Path) -> tuple[str, ...]:
    if not re.fullmatch(r"(?:0x[0-9a-fA-F]{1,16}|[0-9a-fA-F-]{24,40})", identifier):
        raise FirmwareError("Select an explicit physical-device UDID or recovery ECID")
    if identifier.startswith("0x") and integer_value(identifier, "selected ECID") == 0:
        raise FirmwareError("A zero ECID cannot select an exact firmware target")
    target = ("--ecid", identifier) if identifier.startswith("0x") else ("--udid", identifier)
    erase = ("--erase",) if mode == "restore" else ()
    return ("--plain-progress", "--no-input", "--cache-path", str(cache), "--logfile", str(logfile), *target, *erase, "--no-action", str(ipsw))


def require_current_plan(plan: FirmwareInstallPlan, selected_identifier: str, mode: InstallMode, now: float) -> None:
    if selected_identifier != plan.selected_identifier or mode != plan.mode:
        raise FirmwareError("The selected device or install mode changed; run Check Before Installing again")
    if plan.firmware.path != plan.file_identity.path or matching_install_identity(plan.firmware, plan.target, plan.mode) != plan.identity:
        raise FirmwareError("The installation plan does not bind the exact firmware path, device board, and install identity")
    for timestamp in (plan.created_at, plan.signing_checked_at, plan.preflight_finished_at):
        if not math.isfinite(now) or not math.isfinite(timestamp) or now < timestamp or now - timestamp > MAX_PLAN_AGE_SECONDS:
            raise FirmwareError("The installation plan expired; check signing and the connected device again")
    if plan.signing_state != "signed":
        raise FirmwareError("Apple signing was not confirmed for this exact build identity")
    for identity in (plan.file_identity, plan.helper_bundle.manifest, *plan.helper_bundle.files):
        require_file_unchanged(identity)


def install_arguments(plan: FirmwareInstallPlan, cache: Path, logfile: Path, now: float) -> tuple[str, ...]:
    require_current_plan(plan, plan.selected_identifier, plan.mode, now)
    target = f"0x{plan.target.ecid:x}"
    arguments = preflight_arguments(plan.firmware.path, target, plan.mode, cache, logfile)
    return (*arguments[:-2], "--variant", plan.identity.variant, str(plan.firmware.path))


def firmware_library_directory(home: Path) -> Path:
    return home / "Library" / "Application Support" / "iOS Developer Toolkit" / "Firmware"


def helper_environment(directory: Path) -> dict[str, str]:
    """Give hash-checked helpers a minimal environment without loader or certificate overrides."""
    selected = directory.expanduser().absolute()
    if selected.is_symlink() or not selected.is_dir():
        raise FirmwareError("The firmware helper working directory must be a regular private directory")
    info = selected.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise FirmwareError("The firmware helper working directory must be owned by this user with mode 0700")
    return {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(Path.home()),
            "TMPDIR": str(selected), "LANG": "en_US.UTF-8", "LC_ALL": "en_US.UTF-8"}
