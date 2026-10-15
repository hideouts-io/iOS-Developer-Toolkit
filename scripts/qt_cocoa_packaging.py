"""Install a verified Cocoa repair and bind its final bundle bytes to corresponding source.

Only a dedicated virtual environment may receive the repaired plugin. The bundle
keeps the original build manifest and a separate hash for the final signed copy,
because Nuitka relocation and code signing change that file after compilation.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import sys
from typing import Mapping, Sequence

if __package__:
    from .build_qt_cocoa_plugin import validated_manifest, validated_output, validated_source_archive
    from .restore_helper_metadata import exclusive_output, object_fields, regular_bytes, safe_relative
else:
    from build_qt_cocoa_plugin import validated_manifest, validated_output, validated_source_archive
    from restore_helper_metadata import exclusive_output, object_fields, regular_bytes, safe_relative


PLUGIN_RELATIVE = Path("Contents/MacOS/PySide6/qt-plugins/platforms/libqcocoa.dylib")
RESOURCE_RELATIVE = Path("Contents/Resources/qt-cocoa-repair")
PACKAGE_FIELDS = {"schema_version", "build_manifest_sha256", "packaged_plugin_sha256", "source_archive"}
COMPONENT_PREFIX = "urn:ios-developer-toolkit:qt-cocoa:"


def digest_file(path: Path, limit: int) -> str:
    return hashlib.sha256(regular_bytes(path, limit)).hexdigest()


def text_field(values: dict[str, object], name: str) -> str:
    value = values.get(name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Cocoa repair metadata requires a nonempty {name}")
    return value


def digest_field(values: dict[str, object], name: str) -> str:
    value = text_field(values, name)
    if re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError(f"Cocoa repair metadata requires a SHA-256 value for {name}")
    return value


def install_qt_cocoa_plugin(repair_output: Path, environment: Path) -> Path:
    """Replace only the exact stock plugin in this interpreter's selected build venv."""
    target_environment = environment.resolve(strict=True)
    if sys.prefix == sys.base_prefix or Path(sys.prefix).resolve() != target_environment:
        raise ValueError("Install the Cocoa repair using Python inside the dedicated build environment")
    manifest = validated_output(repair_output)
    distribution = importlib.metadata.distribution("PySide6-Essentials")
    if distribution.version != "6.9.3":
        raise ValueError("The verified Cocoa repair requires the exact PySide6 6.9.3 environment")
    qt_root = Path(str(distribution.locate_file("PySide6/Qt")))
    plugin = qt_root / "plugins/platforms/libqcocoa.dylib"
    if any(path.is_symlink() for path in (qt_root, plugin.parent, plugin)) or not plugin.resolve(strict=True).is_relative_to(target_environment):
        raise ValueError("The stock Cocoa plugin must be a regular file inside the dedicated build environment")
    wheel = object_fields(manifest.get("wheel"), "Cocoa repair wheel identity")
    for path, field in (
        (plugin, "original_plugin_sha256"),
        (qt_root / "lib/QtCore.framework/Versions/A/QtCore", "qtcore_sha256"),
        (qt_root / "lib/QtGui.framework/Versions/A/QtGui", "qtgui_sha256"),
    ):
        if digest_file(path, 64 * 1024 * 1024) != digest_field(wheel, field):
            raise ValueError(f"Cocoa repair does not match the installed Qt dependency: {path.name}")
    backup = plugin.with_name("libqcocoa.stock.dylib.backup")
    temporary = plugin.with_name("libqcocoa.repair.dylib.pending")
    backup_destination = target_environment / "stock-qt-cocoa-plugin.dylib"
    if any(path.exists() or path.is_symlink() for path in (backup, temporary, backup_destination)):
        raise ValueError("Use a fresh dedicated build environment; Cocoa repair staging already exists")
    shutil.copy2(plugin, backup)
    with os.fdopen(exclusive_output(temporary), "wb") as output:
        output.write(regular_bytes(repair_output / "libqcocoa.dylib", 16 * 1024 * 1024))
    temporary.chmod(plugin.stat().st_mode & 0o777)
    os.replace(temporary, plugin)
    expected = digest_field(object_fields(manifest.get("plugin"), "Cocoa plugin identity"), "sha256")
    if digest_file(plugin, 16 * 1024 * 1024) != expected:
        raise ValueError("Installed Cocoa repair bytes differ from the verified native build")
    # Keep the stock backup outside the plugin search directory and frozen bundle.
    backup.rename(backup_destination)
    return plugin


def qt_cocoa_components(manifest: dict[str, object], plugin_digest: str, archive_name: str) -> list[dict[str, object]]:
    """Describe the changed plugin without relabeling the untouched Python wheels."""
    source = object_fields(manifest.get("source"), "Cocoa source identity")
    sources = object_fields(manifest.get("sources"), "Cocoa corresponding-source identity")
    patch = object_fields(manifest.get("patch"), "Cocoa ownership patch")
    return [{
        "type": "library", "bom-ref": COMPONENT_PREFIX + plugin_digest,
        "name": "Qt Cocoa platform plugin with accessibility ownership repair", "version": "6.9.3+toolkit.1",
        "hashes": [{"alg": "SHA-256", "content": plugin_digest}],
        "licenses": [{"license": {"name": "See Contents/Resources/qt-cocoa-repair/licenses and corresponding Qtbase source headers"}}],
        "externalReferences": [{"type": "distribution", "url": text_field(source, "url")}],
        "properties": [
            {"name": "ios-developer-toolkit:base-source-sha256", "value": digest_field(source, "sha256")},
            {"name": "ios-developer-toolkit:accessibility-patch-sha256", "value": digest_field(patch, "sha256")},
            {"name": "ios-developer-toolkit:source-archive", "value": archive_name},
            {"name": "ios-developer-toolkit:source-archive-sha256", "value": digest_field(sources, "sha256")},
            {"name": "ios-developer-toolkit:build-target-architectures", "value": "arm64,x86_64"},
            {"name": "ios-developer-toolkit:build-target-minimum-macos", "value": "13.0"},
        ],
    }]


def source_archive_name(value: str, release_version: str) -> str:
    if re.fullmatch(rf"iOS-Developer-Toolkit-qt-cocoa-sources-v{re.escape(release_version)}-macOS-(arm64|x86_64)\.tar\.gz", value) is None:
        raise ValueError("Cocoa corresponding source must use the matching architecture-labeled release basename")
    return value


def package_qt_cocoa_repair(repair_output: Path, app: Path, source_archive: Path, sbom_input: Path, sbom_output: Path) -> Path:
    """Embed repair notices and bind the signed plugin plus source to a fresh SBOM."""
    manifest = validated_output(repair_output)
    if any(path.is_symlink() or not path.is_dir() for path in (app, app / "Contents", app / "Contents/Resources")):
        raise ValueError("Choose a newly built regular application bundle for Cocoa repair metadata")
    plugin = app / PLUGIN_RELATIVE
    if not plugin.is_file() or plugin.is_symlink():
        raise ValueError("The newly built application is missing its relocated Cocoa plugin")
    plist = plistlib.loads(regular_bytes(app / "Contents/Info.plist", 1024 * 1024))
    version = text_field(object_fields(plist, "Application Info.plist"), "CFBundleShortVersionString")
    archive_name = source_archive_name(source_archive.name, version)
    if source_archive.exists() or source_archive.is_symlink():
        raise ValueError("Refusing to overwrite the Cocoa corresponding-source archive")
    shutil.copy2(repair_output / "sources.tar.gz", source_archive)
    sources = object_fields(manifest.get("sources"), "Cocoa corresponding-source identity")
    if digest_file(source_archive, 128 * 1024 * 1024) != digest_field(sources, "sha256"):
        raise ValueError("Copied Cocoa corresponding-source archive differs from its verified bytes")
    resources = app / RESOURCE_RELATIVE
    resources.mkdir(exist_ok=False)
    manifest_bytes = regular_bytes(repair_output / "manifest.json", 1024 * 1024)
    (resources / "build-manifest.json").write_bytes(manifest_bytes)
    patch = object_fields(manifest.get("patch"), "Cocoa ownership patch")
    shutil.copy2(repair_output / text_field(patch, "file"), resources / "accessibility.patch")
    license_values = manifest.get("licenses")
    if not isinstance(license_values, list):
        raise ValueError("Cocoa repair is missing its license inventory")
    for value in license_values:
        record = object_fields(value, "Cocoa license")
        relative = safe_relative(text_field(record, "file"))
        destination = resources / str(relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(repair_output / str(relative), destination)
    plugin_digest = digest_file(plugin, 16 * 1024 * 1024)
    packaged = {
        "schema_version": 1, "build_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "packaged_plugin_sha256": plugin_digest, "source_archive": archive_name,
    }
    (resources / "packaged-manifest.json").write_text(json.dumps(packaged, indent=2) + "\n", encoding="utf-8")
    sbom = object_fields(json.loads(regular_bytes(sbom_input, 32 * 1024 * 1024)), "CycloneDX SBOM")
    components = sbom.get("components")
    if sbom.get("bomFormat") != "CycloneDX" or sbom.get("specVersion") != "1.6" or not isinstance(components, list):
        raise ValueError("Cocoa packaging requires the generated CycloneDX 1.6 component array")
    if any(isinstance(value, dict) and str(value.get("bom-ref", "")).startswith(COMPONENT_PREFIX) for value in components):
        raise ValueError("The source SBOM already contains Cocoa repair components")
    sbom["components"] = [*components, *qt_cocoa_components(manifest, plugin_digest, archive_name)]
    with os.fdopen(exclusive_output(sbom_output), "wb") as output:
        output.write((json.dumps(sbom, indent=2) + "\n").encode("utf-8"))
    return resources


def verify_qt_cocoa_bundle(app: Path, sbom_path: Path, version: str, sbom: Mapping[str, object]) -> None:
    """Require provenance for a real Cocoa plugin and verify final code/source hashes."""
    plugin = app / PLUGIN_RELATIVE
    resources = app / RESOURCE_RELATIVE
    values = sbom.get("components")
    observed = [value for value in values if isinstance(value, dict) and str(value.get("bom-ref", "")).startswith(COMPONENT_PREFIX)] if isinstance(values, list) else []
    qt_wheel_present = isinstance(values, list) and any(
        isinstance(value, dict) and isinstance(value.get("name"), str)
        and value["name"].lower().replace("_", "-") == "pyside6-essentials"
        for value in values
    )
    if not plugin.exists() and not resources.exists() and not observed and not qt_wheel_present:
        return
    if any(path.is_symlink() for path in (app, app / "Contents", app / "Contents/Resources", resources, sbom_path, sbom_path.parent)):
        raise ValueError("Cocoa bundle and metadata inputs must not be symbolic links")
    if not isinstance(values, list):
        raise ValueError("Cocoa bundle verification requires the SBOM component array")
    manifest_bytes = regular_bytes(resources / "build-manifest.json", 1024 * 1024)
    manifest = validated_manifest(manifest_bytes)
    packaged = object_fields(json.loads(regular_bytes(resources / "packaged-manifest.json", 1024 * 1024)), "Packaged Cocoa repair")
    if set(packaged) != PACKAGE_FIELDS or type(packaged.get("schema_version")) is not int or packaged["schema_version"] != 1:
        raise ValueError("Packaged Cocoa repair has an unsupported closed schema")
    if digest_field(packaged, "build_manifest_sha256") != hashlib.sha256(manifest_bytes).hexdigest():
        raise ValueError("Packaged Cocoa build manifest differs from its recorded bytes")
    plugin_digest = digest_file(plugin, 16 * 1024 * 1024)
    if plugin_digest != digest_field(packaged, "packaged_plugin_sha256"):
        raise ValueError("Final signed Cocoa plugin differs from its provenance hash")
    archive_name = source_archive_name(text_field(packaged, "source_archive"), version)
    sources = object_fields(manifest.get("sources"), "Cocoa corresponding-source identity")
    archive = sbom_path.parent / archive_name
    if digest_file(archive, 128 * 1024 * 1024) != digest_field(sources, "sha256"):
        raise ValueError("Cocoa corresponding-source bytes differ from the packaged manifest")
    validated_source_archive(archive, manifest)
    patch = object_fields(manifest.get("patch"), "Cocoa ownership patch")
    if digest_file(resources / "accessibility.patch", 1024 * 1024) != digest_field(patch, "sha256"):
        raise ValueError("Packaged Cocoa ownership patch differs from its source recipe")
    license_values = manifest.get("licenses")
    if not isinstance(license_values, list):
        raise ValueError("Cocoa repair is missing its license inventory")
    for value in license_values:
        record = object_fields(value, "Cocoa license")
        path = resources / str(safe_relative(text_field(record, "file")))
        if digest_file(path, 1024 * 1024) != digest_field(record, "sha256"):
            raise ValueError("Packaged Cocoa license differs from its source recipe")
    if observed != qt_cocoa_components(manifest, plugin_digest, archive_name):
        raise ValueError("CycloneDX Cocoa component differs from the final code/source package")


def main(arguments: Sequence[str]) -> int:
    if len(arguments) != 6:
        raise ValueError("Usage: python -m scripts.qt_cocoa_packaging REPAIR_OUTPUT NEW_APP SOURCE_ARCHIVE INPUT_SBOM OUTPUT_SBOM")
    result = package_qt_cocoa_repair(*(Path(value).absolute() for value in arguments[1:]))
    print(f"Verified Cocoa repair provenance embedded: {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
