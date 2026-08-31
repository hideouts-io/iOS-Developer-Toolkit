from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Mapping, Sequence


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
    component = metadata_component(sbom)
    if component.get("name") != "ios-developer-toolkit":
        raise ReleaseMetadataError("Release SBOM metadata does not identify ios-developer-toolkit")
    if component.get("version") != release_version:
        raise ReleaseMetadataError(
            f"Release SBOM version {component.get('version')!r} does not match {release_version!r}"
        )
    if "file://" in sbom_path.read_text(encoding="utf-8"):
        raise ReleaseMetadataError("Release SBOM contains a local file URL")

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
        Path(arguments[1]).resolve(),
        Path(arguments[2]).resolve(),
        arguments[3],
    )
    print("Release metadata verification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
