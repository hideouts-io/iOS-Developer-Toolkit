"""Explicit local image selection; mounts use a fresh target and verified HTTPS TSS."""
from __future__ import annotations

import argparse
import asyncio
import os
import plistlib
import re
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal, Mapping, Sequence


class DeveloperImageError(ValueError):
    """A local image or device identity cannot be validated for mounting."""


@dataclass(frozen=True)
class DeveloperImageSource:
    folder: Path
    kind: Literal["legacy", "personalized"]
    version: str
    build: str
    product_types: tuple[str, ...]


@dataclass(frozen=True)
class DeveloperImagePayload:
    image: Path
    signature_or_trust_cache: Path
    manifest_path: Path | None


def regular_file(path: Path, limit: int) -> Path:
    if path.is_symlink():
        raise DeveloperImageError(f"Developer image file is a symbolic link: {path.name}")
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= limit:
        raise DeveloperImageError(f"Developer image file must be regular and between 1 and {limit} bytes: {path.name}")
    return path


def image_child(folder: Path, relative: str) -> Path:
    value = PurePosixPath(relative)
    if not relative or value.is_absolute() or ".." in value.parts or "\\" in relative:
        raise DeveloperImageError("Developer image manifest contains an unsafe component path")
    child = folder
    for component in value.parts:
        child = child / component
        if child.is_symlink():
            raise DeveloperImageError("Developer image component passes through a symbolic link")
    return regular_file(child, 2_147_483_648)


def plist_record(path: Path) -> dict[str, object]:
    parsed: object = plistlib.loads(read_image_bytes(path.parent, path, 8_388_608))
    if not isinstance(parsed, dict) or any(not isinstance(key, str) for key in parsed):
        raise DeveloperImageError(f"Expected a dictionary in {path.name}")
    return parsed


def required_record(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise DeveloperImageError(f"Developer image {field} must be a dictionary")
    return value


def numeric_identifier(value: object, field: str) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    if isinstance(value, str):
        try:
            result = int(value, 16 if value.lower().startswith("0x") else 10)
        except ValueError as error:
            raise DeveloperImageError(f"Developer image {field} is not a numeric identifier") from error
        if result >= 0:
            return result
    raise DeveloperImageError(f"Developer image {field} must be a nonnegative identifier")


def major_minor(version: str) -> str:
    match = re.match(r"^(\d+)\.(\d+)", version)
    if match is None:
        raise DeveloperImageError("An exact major.minor iOS version is required for a legacy image")
    return f"{int(match[1])}.{int(match[2])}"


def inspect_developer_image(folder: Path) -> DeveloperImageSource:
    if folder.is_symlink() or not folder.is_dir():
        raise DeveloperImageError("Choose a regular developer image folder")
    root = folder.resolve(strict=True)
    if (root / "Restore").is_symlink():
        raise DeveloperImageError("Developer image Restore folder must not be a symbolic link")
    if (root / "Restore").is_dir():
        root = root / "Restore"
    manifest_path = root / "BuildManifest.plist"
    if manifest_path.exists():
        manifest = plist_record(manifest_path)
        identities = manifest.get("BuildIdentities")
        products = manifest.get("SupportedProductTypes")
        if not isinstance(identities, list) or not 0 < len(identities) <= 1024:
            raise DeveloperImageError("Personalized image has no bounded BuildIdentities list")
        if not isinstance(products, list) or not products or any(not isinstance(item, str) for item in products):
            raise DeveloperImageError("Personalized image has no SupportedProductTypes list")
        version = manifest.get("ProductVersion")
        build = manifest.get("ProductBuildVersion")
        if not isinstance(version, str) or not isinstance(build, str):
            raise DeveloperImageError("Personalized image is missing version/build metadata")
        return DeveloperImageSource(root, "personalized", version, build, tuple(products))
    regular_file(root / "DeveloperDiskImage.dmg", 2_147_483_648)
    regular_file(root / "DeveloperDiskImage.dmg.signature", 1_048_576)
    match = re.match(r"^\d+\.\d+(?:\s*\(([^)]+)\))?", root.name)
    if match is None:
        raise DeveloperImageError("Legacy image folder must name its version, for example 16.4 (20E247)")
    return DeveloperImageSource(root, "legacy", major_minor(root.name), match[1] or "", ())


def image_identity(manifest: Mapping[str, object], chip: int, board: int) -> Mapping[str, object]:
    identities = manifest.get("BuildIdentities")
    if not isinstance(identities, list) or not 0 < len(identities) <= 1024:
        raise DeveloperImageError("Personalized image BuildIdentities is invalid")
    matches: list[Mapping[str, object]] = []
    for entry in identities:
        identity = required_record(entry, "identity")
        components = required_record(identity.get("Manifest"), "component manifest")
        if "PersonalizedDMG" not in components:
            continue
        if numeric_identifier(identity.get("ApChipID"), "ApChipID") == chip and numeric_identifier(identity.get("ApBoardID"), "ApBoardID") == board:
            matches.append(identity)
    if len(matches) != 1:
        raise DeveloperImageError(f"Developer image requires exactly one matching chip/board identity; found {len(matches)}")
    return matches[0]


def select_image_payload(source: DeveloperImageSource, product: str, version: str, build: str, chip: int, board: int) -> DeveloperImagePayload:
    if source.kind == "legacy":
        if int(major_minor(version).split(".")[0]) >= 17 or major_minor(version) != source.version:
            raise DeveloperImageError("Legacy image version does not exactly match the target's major.minor iOS version")
        if source.build and source.build != build:
            raise DeveloperImageError("Legacy image build differs from the target build")
        return DeveloperImagePayload(image_child(source.folder, "DeveloperDiskImage.dmg"), image_child(source.folder, "DeveloperDiskImage.dmg.signature"), None)
    if int(major_minor(version).split(".")[0]) < 17 or product not in source.product_types:
        raise DeveloperImageError("Personalized image does not support the selected target model/OS")
    manifest_path = source.folder / "BuildManifest.plist"
    identity = image_identity(plist_record(manifest_path), chip, board)
    components = required_record(identity.get("Manifest"), "component manifest")
    paths: list[Path] = []
    for name in ("PersonalizedDMG", "LoadableTrustCache"):
        component = required_record(components.get(name), name)
        info = required_record(component.get("Info"), f"{name}.Info")
        path = info.get("Path")
        if not isinstance(path, str):
            raise DeveloperImageError(f"Developer image {name} has no path")
        paths.append(image_child(source.folder, path))
    return DeveloperImagePayload(paths[0], paths[1], manifest_path)


def host_developer_images(applications: Path, personalized_folder: Path) -> tuple[DeveloperImageSource, ...]:
    folders: list[Path] = []
    if personalized_folder.is_dir():
        folders.append(personalized_folder)
    for xcode in sorted(applications.glob("Xcode*.app")):
        support = xcode / "Contents/Developer/Platforms/iPhoneOS.platform/DeviceSupport"
        if support.is_dir():
            folders.extend(folder for folder in sorted(support.iterdir()) if folder.is_dir())
    return tuple(inspect_developer_image(folder) for folder in folders)


def personalization_request(identity: Mapping[str, object], identifiers: Mapping[str, object], ecid: int, nonce: bytes) -> dict[str, object]:
    from pymobiledevice3.restore.tss import TSSRequest

    if not nonce or len(nonce) > 128 or ecid <= 0:
        raise DeveloperImageError("Personalization requires a valid device ECID and nonce")
    parameters: dict[str, object] = {"ApProductionMode": True, "ApSecurityDomain": 1, "ApSecurityMode": True, "ApSupportsImg4": True}
    request = TSSRequest()
    additional: dict[str, object] = {}
    for key in ("Ap,ProductType", "Ap,OSLongVersion"):
        if key in identifiers:
            value = identifiers[key]
            if not isinstance(value, str) or not 0 < len(value) <= 128 or any(not character.isprintable() for character in value):
                raise DeveloperImageError(f"Personalization identifier {key} must be bounded printable text")
            additional[key] = value
    request.update(additional)
    request.update({"@ApImg4Ticket": True, "@BBTicket": True, "ApBoardID": numeric_identifier(identifiers.get("BoardId"), "BoardId"), "ApChipID": numeric_identifier(identifiers.get("ChipID"), "ChipID"), "ApECID": ecid, "ApNonce": nonce, "SepNonce": bytes(20), "UID_MODE": False, **parameters})
    manifest = required_record(identity.get("Manifest"), "component manifest")
    trust_cache = required_record(manifest.get("LoadableTrustCache"), "trust cache")
    trust_info = required_record(trust_cache.get("Info"), "trust cache info")
    fallback_rules = trust_info.get("RestoreRequestRules", [])
    for name, raw_component in manifest.items():
        component = required_record(raw_component, name)
        if component.get("Trusted") is not True:
            continue
        info = required_record(component.get("Info"), f"{name}.Info")
        rules = info.get("RestoreRequestRules", fallback_rules)
        if not isinstance(rules, list) or len(rules) > 128:
            raise DeveloperImageError("RestoreRequestRules must be a bounded array")
        condition_values = {"ApRawProductionMode": True, "ApCurrentProductionMode": True, "ApRawSecurityMode": True, "ApRequiresImage4": True, "ApDemotionPolicyOverride": False, "ApInRomDFU": False}
        ticket = {key: value for key, value in component.items() if key in ("Digest", "Trusted", "EPRO", "ESEC")}
        for raw_rule in rules:
            rule = required_record(raw_rule, "restore request rule")
            conditions = required_record(rule.get("Conditions"), "restore request conditions")
            actions = required_record(rule.get("Actions"), "restore request actions")
            if not conditions or any(type(value) is not bool and not (key == "ApDemotionPolicyOverride" and value == "Demote") for key, value in conditions.items()):
                raise DeveloperImageError("Restore request conditions must use the supported boolean or demotion fields")
            if any(key not in condition_values for key in conditions):
                raise DeveloperImageError("Restore request contains an unsupported signing condition")
            if any(key not in ("EPRO", "ESEC") or (type(value) is not bool and not (type(value) is int and value == 255)) for key, value in actions.items()):
                raise DeveloperImageError("Restore request actions are outside the supported signing contract")
            if all(condition_values[key] == value for key, value in conditions.items()):
                ticket = {**ticket, **{key: value for key, value in actions.items() if value != 255}}
        ticket.setdefault("Digest", b"")
        request.update({name: ticket})
    return dict(request.tags())


def read_image_bytes(folder: Path, path: Path, limit: int) -> bytes:
    """Read one pinned payload through no-follow directory descriptors with a size bound."""
    relative = path.relative_to(folder)
    if not relative.parts or ".." in relative.parts or any("\\" in component for component in relative.parts):
        raise DeveloperImageError("Local image read must stay within its selected folder")
    directory = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for component in relative.parts[:-1]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        descriptor = os.open(relative.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(descriptor, "rb") as source:
            before = os.fstat(source.fileno())
            if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= limit:
                raise DeveloperImageError(f"Local image payload exceeds the {limit}-byte mounting limit")
            payload = source.read(limit + 1)
            after = os.fstat(source.fileno())
            if len(payload) != before.st_size or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
                raise DeveloperImageError("Local image payload changed while being read")
            return payload
    finally:
        os.close(directory)


async def mount_local_image(folder: Path, udid: str) -> None:
    from pymobiledevice3.lockdown import create_using_usbmux
    from pymobiledevice3.services.mobile_image_mounter import DeveloperDiskImageMounter, PersonalizedImageMounter
    from ios_developer_toolkit.firmware_transport import send_tss_request

    source = inspect_developer_image(folder)
    async with await create_using_usbmux(serial=udid, autopair=False, connection_type="USB") as lockdown:
        if lockdown.udid != udid:
            raise DeveloperImageError("Connected target identity differs from the selected device")
        if source.kind == "legacy":
            payload = select_image_payload(source, lockdown.product_type, lockdown.product_version, lockdown.build_version, 0, 0)
            async with DeveloperDiskImageMounter(lockdown) as mounter:
                await mounter.raise_if_cannot_mount()
                image_bytes = read_image_bytes(source.folder, payload.image, 134_217_728)
                signature = read_image_bytes(source.folder, payload.signature_or_trust_cache, 1_048_576)
                await mounter.upload_image("Developer", image_bytes, signature)
                await mounter.mount_image("Developer", signature)
            return
        async with PersonalizedImageMounter(lockdown) as mounter:
            await mounter.raise_if_cannot_mount()
            identifiers: Mapping[str, object] = await mounter.query_personalization_identifiers()
            chip = numeric_identifier(identifiers.get("ChipID"), "ChipID")
            board = numeric_identifier(identifiers.get("BoardId"), "BoardId")
            payload = select_image_payload(source, lockdown.product_type, lockdown.product_version, lockdown.build_version, chip, board)
            if payload.manifest_path is None:
                raise DeveloperImageError("Personalized payload has no manifest")
            identity = image_identity(plist_record(payload.manifest_path), chip, board)
            image_bytes = read_image_bytes(source.folder, payload.image, 134_217_728)
            trust_cache_bytes = read_image_bytes(source.folder, payload.signature_or_trust_cache, 16_777_216)
            nonce = await mounter.query_nonce("DeveloperDiskImage")
            request = personalization_request(identity, identifiers, lockdown.ecid, nonce)
            reply = await asyncio.to_thread(send_tss_request, request)
            ticket = reply.get("ApImg4Ticket")
            if not isinstance(ticket, bytes) or not ticket:
                raise DeveloperImageError("Apple's signing response did not contain a personalization ticket")
            await mounter.upload_image("Personalized", image_bytes, ticket)
            await mounter.mount_image("Personalized", ticket, extras={"ImageTrustCache": trust_cache_bytes})


def main(arguments: Sequence[str]) -> int:
    from pymobiledevice3.exceptions import PyMobileDevice3Exception
    from ios_developer_toolkit.firmware_models import FirmwareError

    parser = argparse.ArgumentParser(description="Mount an explicitly selected, model-checked local developer image")
    parser.add_argument("--folder", type=Path, required=True)
    parser.add_argument("--udid", required=True)
    options = parser.parse_args(arguments)
    if not re.fullmatch(r"[A-Za-z0-9-]{6,64}", options.udid):
        parser.error("Device identifier format is invalid")
    try:
        asyncio.run(asyncio.wait_for(mount_local_image(options.folder, options.udid), timeout=300))
    except PyMobileDevice3Exception as error:
        parser.exit(1, f"Local developer image device communication failed ({type(error).__name__}); confirm the selected device is connected, unlocked and trusted.\n")
    except (DeveloperImageError, FirmwareError, OSError, TimeoutError, plistlib.InvalidFileException) as error:
        parser.exit(1, f"Local developer image mounting failed: {error}\n")
    print("Local developer image mounted on the explicitly selected device.")
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(main(sys.argv[1:]))
