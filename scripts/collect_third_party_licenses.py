from __future__ import annotations

import re
import shutil
import sys
from dataclasses import dataclass
from importlib.metadata import Distribution, PackageNotFoundError, distribution
from pathlib import Path
from typing import Sequence

from packaging.requirements import InvalidRequirement, Requirement


class LicenseCollectionError(RuntimeError):
    """Raised when release dependency metadata cannot be collected safely."""


@dataclass(frozen=True)
class PackageNotice:
    name: str
    version: str
    declared_license: str
    source_url: str
    copied_files: tuple[str, ...]


def normalize_distribution_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_requirement_names(requirements_path: Path) -> tuple[str, ...]:
    names: set[str] = set()
    for line_number, raw_line in enumerate(requirements_path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            requirement = Requirement(line)
        except InvalidRequirement as error:
            raise LicenseCollectionError(
                f"Invalid frozen requirement at {requirements_path}:{line_number}: {line}"
            ) from error
        normalized_name = normalize_distribution_name(requirement.name)
        if normalized_name != "ios-developer-toolkit":
            names.add(requirement.name)
    if not names:
        raise LicenseCollectionError(f"No third-party distributions were found in {requirements_path}")
    return tuple(sorted(names, key=str.casefold))


def declared_license(package_distribution: Distribution) -> tuple[str, str | None]:
    license_value = package_distribution.metadata.get("License-Expression")
    if license_value is None:
        license_value = package_distribution.metadata.get("License")
    if license_value is None or not license_value.strip():
        return ("Not declared in installed package metadata", None)
    normalized_license = " ".join(license_value.split())
    if len(normalized_license) > 240:
        return ("Full license text stored in `METADATA-LICENSE.txt`", license_value.strip() + "\n")
    return (normalized_license, None)


def source_url(package_distribution: Distribution) -> str:
    project_urls = package_distribution.metadata.get_all("Project-URL") or []
    preferred_labels = ("source", "repository", "homepage")
    parsed_urls: dict[str, str] = {}
    for project_url in project_urls:
        label, separator, url = project_url.partition(",")
        if separator and url.strip():
            parsed_urls[label.strip().lower()] = url.strip()
    for preferred_label in preferred_labels:
        for label, url in parsed_urls.items():
            if preferred_label in label:
                return url
    homepage = package_distribution.metadata.get("Home-page")
    if homepage is not None and homepage.strip():
        return homepage.strip()
    return "Not declared in installed package metadata"


def is_license_file(path: Path) -> bool:
    filename = path.name.lower()
    return filename.startswith(("license", "copying", "notice"))


def copy_license_files(package_distribution: Distribution, destination: Path) -> tuple[str, ...]:
    package_files = package_distribution.files
    if package_files is None:
        raise LicenseCollectionError(
            f"Installed distribution {package_distribution.metadata['Name']} has no file inventory"
        )
    copied_files: list[str] = []
    for package_file in sorted(package_files, key=lambda path: str(path).casefold()):
        relative_path = Path(str(package_file))
        if not is_license_file(relative_path):
            continue
        source_path = Path(package_distribution.locate_file(package_file)).resolve()
        if not source_path.is_file():
            raise LicenseCollectionError(
                f"License file declared by {package_distribution.metadata['Name']} is missing: {source_path}"
            )
        destination.mkdir(parents=True, exist_ok=True)
        destination_path = destination / relative_path.name
        if destination_path.exists():
            destination_path = destination / f"{relative_path.parent.name}-{relative_path.name}"
        shutil.copy2(source_path, destination_path)
        copied_files.append(destination_path.name)
    return tuple(copied_files)


def collect_package_notice(distribution_name: str, output_directory: Path) -> PackageNotice:
    try:
        package_distribution = distribution(distribution_name)
    except PackageNotFoundError as error:
        raise LicenseCollectionError(
            f"Frozen release dependency is not installed: {distribution_name}"
        ) from error
    package_name = package_distribution.metadata.get("Name")
    if package_name is None or not package_name.strip():
        raise LicenseCollectionError(f"Installed distribution has no Name metadata: {distribution_name}")
    version = package_distribution.version
    if not version:
        raise LicenseCollectionError(f"Installed distribution has no version: {package_name}")
    package_directory = output_directory / normalize_distribution_name(package_name)
    copied_files = list(copy_license_files(package_distribution, package_directory))
    license_summary, complete_metadata_license = declared_license(package_distribution)
    if complete_metadata_license is not None:
        package_directory.mkdir(parents=True, exist_ok=True)
        metadata_license_path = package_directory / "METADATA-LICENSE.txt"
        metadata_license_path.write_text(complete_metadata_license, encoding="utf-8")
        copied_files.append(metadata_license_path.name)
    return PackageNotice(
        name=package_name,
        version=version,
        declared_license=license_summary,
        source_url=source_url(package_distribution),
        copied_files=tuple(copied_files),
    )


def markdown_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def render_inventory(notices: Sequence[PackageNotice]) -> str:
    lines = [
        "# Installed third-party Python packages",
        "",
        "This file was generated from the exact frozen dependency set used for this application build. License declarations come from installed package metadata. Copied files are the license or notice files supplied by each installed wheel.",
        "",
        "| Package | Version | Declared license | Source | Copied license files |",
        "|---|---:|---|---|---|",
    ]
    for notice in notices:
        copied_files = ", ".join(f"`{filename}`" for filename in notice.copied_files)
        if not copied_files:
            copied_files = "None supplied by installed wheel"
        source = markdown_cell(notice.source_url)
        if source.startswith("https://"):
            source = f"[Upstream]({source})"
        lines.append(
            "| "
            + " | ".join(
                (
                    markdown_cell(notice.name),
                    markdown_cell(notice.version),
                    markdown_cell(notice.declared_license),
                    source,
                    copied_files,
                )
            )
            + " |"
        )
    lines.extend(("", "See `THIRD_PARTY_NOTICES.md` for the release-critical dependency summary and redistribution boundary.", ""))
    return "\n".join(lines)


def collect_licenses(requirements_path: Path, output_directory: Path) -> tuple[PackageNotice, ...]:
    if not requirements_path.is_file():
        raise LicenseCollectionError(f"Frozen requirements file does not exist: {requirements_path}")
    if output_directory.exists():
        raise LicenseCollectionError(f"License output directory already exists: {output_directory}")
    output_directory.mkdir(parents=True)
    notices = tuple(
        collect_package_notice(distribution_name, output_directory)
        for distribution_name in parse_requirement_names(requirements_path)
    )
    inventory_path = output_directory / "THIRD_PARTY_PACKAGES.md"
    inventory_path.write_text(render_inventory(notices), encoding="utf-8")
    return notices


def main(arguments: Sequence[str]) -> int:
    if len(arguments) != 3:
        raise LicenseCollectionError(
            "Usage: collect_third_party_licenses.py FROZEN_REQUIREMENTS OUTPUT_DIRECTORY"
        )
    requirements_path = Path(arguments[1]).resolve()
    output_directory = Path(arguments[2]).resolve()
    notices = collect_licenses(requirements_path, output_directory)
    print(f"Collected notices for {len(notices)} third-party distributions in {output_directory}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
