from __future__ import annotations

import json
import re
import sys
import tarfile
from pathlib import Path
from typing import Mapping, Sequence

if __package__:
    from .augment_restore_helper_sbom import archive_metadata, native_components
    from .restore_helper_metadata import helper_files, object_fields, source_inventory
else:
    from augment_restore_helper_sbom import archive_metadata, native_components
    from restore_helper_metadata import helper_files, object_fields, source_inventory


class ReleaseMetadataError(RuntimeError):
    """Raised when a release bundle lacks verifiable metadata resources."""


def read_json(path: Path) -> Mapping[str, object]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ReleaseMetadataError(f"Could not read CycloneDX SBOM {path}: {error}") from error
    if not isinstance(loaded, dict):
        raise ReleaseMetadataError(f"CycloneDX SBOM {path} must contain a JSON object")
    return loaded


def required_text_resource(path: Path) -> str:
    if not path.is_file():
        raise ReleaseMetadataError(f"Release bundle is missing required resource: {path}")
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise ReleaseMetadataError(f"Could not read required resource {path}: {error}") from error
    if not content.strip():
        raise ReleaseMetadataError(f"Release bundle resource is empty: {path}")
    return content


def metadata_component(sbom: Mapping[str, object]) -> Mapping[str, object]:
    metadata = sbom.get("metadata")
    if not isinstance(metadata, dict):
        raise ReleaseMetadataError("CycloneDX SBOM does not define metadata")
    component = metadata.get("component")
    if not isinstance(component, dict):
        raise ReleaseMetadataError("CycloneDX SBOM does not define a metadata component")
    return component


def verify_native_metadata(application_path: Path, sbom_path: Path, release_version: str, sbom: Mapping[str, object], build_script: Path) -> None:
    """Bind optional helpers, final SBOM and adjacent corresponding-source bytes."""
    helper_root = application_path / "Contents" / "Helpers" / "restore-helpers"
    if not helper_root.exists() and not helper_root.is_symlink():
        return
    try:
        if any(path.is_symlink() for path in (application_path, application_path / "Contents", helper_root.parent, helper_root, sbom_path, sbom_path.parent)):
            raise ValueError("Native helper release inputs and output directory must not be symbolic links")
        inventory = source_inventory(build_script)
        hashes = helper_files(helper_root, inventory)
        values = sbom.get("components")
        if not isinstance(values, list):
            raise ValueError("Native helper SBOM must contain its component array")
        components = [object_fields(value, "CycloneDX component") for value in values]
        source_reference = next(pin for pin in inventory["git"] if pin["name"] == "idevicerestore")
        reference = f"urn:ios-developer-toolkit:native:source:idevicerestore:{source_reference['revision']}"
        matches = [value for value in components if value.get("bom-ref") == reference]
        if len(matches) != 1:
            raise ValueError("SBOM must identify the exact pinned idevicerestore source once")
        properties = matches[0].get("properties")
        if not isinstance(properties, list):
            raise ValueError("Native source component must identify its corresponding source archive")
        archive_values: list[str] = []
        for value in properties:
            property_fields = object_fields(value, "Native source property")
            if property_fields.get("name") == "ios-developer-toolkit:source-archive":
                archive_value = property_fields.get("value")
                if not isinstance(archive_value, str):
                    raise ValueError("Native source archive basename must be text")
                archive_values.append(archive_value)
        if len(archive_values) != 1 or re.fullmatch(rf"iOS-Developer-Toolkit-restore-helper-sources-v{re.escape(release_version)}-macOS-(arm64|x86_64)\.tar\.gz", archive_values[0]) is None:
            raise ValueError("Native source archive must be a matching release basename without paths")
        source_archive = sbom_path.parent / archive_values[0]
        metadata, digest = archive_metadata(source_archive, inventory, hashes)
        expected = native_components(inventory, hashes, metadata, source_archive.name, digest)
        for component in expected:
            found = [value for value in components if value.get("bom-ref") == component["bom-ref"]]
            if len(found) != 1 or found[0] != component:
                raise ValueError(f"Native SBOM component pin, archive digest, or binary hash differs: {component['name']}")
        observed_refs = [value.get("bom-ref") for value in components if isinstance(value.get("bom-ref"), str) and str(value["bom-ref"]).startswith("urn:ios-developer-toolkit:native:")]
        if len(observed_refs) != len(expected):
            raise ValueError("SBOM contains an unsupported additional native component identity")
    except (ValueError, OSError, UnicodeError, tarfile.TarError) as error:
        raise ReleaseMetadataError(f"Native release metadata verification failed: {error}") from error


def verify_release_metadata(application_path: Path, sbom_path: Path, release_version: str) -> None:
    resource_directory = application_path / "Contents" / "Resources"
    bundle_sbom_path = resource_directory / "BOM.cdx.json"
    if not sbom_path.is_file():
        raise ReleaseMetadataError(f"Release SBOM does not exist: {sbom_path}")
    if not bundle_sbom_path.is_file():
        raise ReleaseMetadataError(f"Release bundle is missing its SBOM: {bundle_sbom_path}")
    if sbom_path.read_bytes() != bundle_sbom_path.read_bytes():
        raise ReleaseMetadataError("Release SBOM differs from Contents/Resources/BOM.cdx.json")

    sbom = read_json(sbom_path)
    if sbom.get("bomFormat") != "CycloneDX":
        raise ReleaseMetadataError("Release SBOM does not declare CycloneDX format")
    if sbom.get("specVersion") != "1.6":
        raise ReleaseMetadataError("Release SBOM does not declare CycloneDX 1.6")
    serial_number = sbom.get("serialNumber")
    if not isinstance(serial_number, str) or not serial_number:
        raise ReleaseMetadataError("Release SBOM does not define a CycloneDX serial number for attestation")
    component = metadata_component(sbom)
    if component.get("name") != "ios-developer-toolkit":
        raise ReleaseMetadataError("Release SBOM metadata does not identify ios-developer-toolkit")
    if component.get("version") != release_version:
        raise ReleaseMetadataError(
            f"Release SBOM version {component.get('version')!r} does not match {release_version!r}"
        )
    if "file://" in sbom_path.read_text(encoding="utf-8"):
        raise ReleaseMetadataError("Release SBOM contains a local file URL")
    verify_native_metadata(application_path, sbom_path, release_version, sbom, Path(__file__).with_name("build_restore_helpers_vendor.sh"))

    licenses_directory = resource_directory / "Licenses"
    required_text_resource(licenses_directory / "IOS_DEVELOPER_TOOLKIT_LICENSE.txt")
    notices = required_text_resource(licenses_directory / "THIRD_PARTY_NOTICES.md")
    source_availability = required_text_resource(licenses_directory / "SOURCE_AVAILABILITY.md")
    inventory = required_text_resource(licenses_directory / "ThirdPartyPackages" / "THIRD_PARTY_PACKAGES.md")
    if "pymobiledevice3" not in notices:
        raise ReleaseMetadataError("Third-party notices do not identify pymobiledevice3")
    if "pymobiledevice3" not in inventory or "Nuitka" not in inventory:
        raise ReleaseMetadataError("Generated package inventory is missing release-critical dependencies")
    if "pymobiledevice3" not in source_availability:
        raise ReleaseMetadataError("Source-availability statement does not identify pymobiledevice3")


def main(arguments: Sequence[str]) -> int:
    if len(arguments) != 4:
        raise ReleaseMetadataError(
            "Usage: verify_release_metadata.py APPLICATION_PATH SBOM_PATH RELEASE_VERSION"
        )
    verify_release_metadata(
        Path(arguments[1]).absolute(),
        Path(arguments[2]).absolute(),
        arguments[3],
    )
    print("Release metadata verification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
