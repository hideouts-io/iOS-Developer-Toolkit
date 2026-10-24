"""Append exact native components to a fresh CycloneDX 1.6 SBOM output.

Existing top-level metadata, Python components and dependency relationships are
preserved. Source pins, archive source trees and final packaged binary hashes
must agree before native entries are added; no local paths enter the SBOM.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import posixpath
import re
import sys
import tarfile
import uuid
from pathlib import Path

if __package__:
    from .archive_restore_helper_sources import offline_recipe
    from .restore_helper_metadata import (
        ARCHIVE_METADATA, ARCHIVE_ROOT, TLS_PATCH_NAME, SourceInventory, discard_owned_output, exclusive_output,
        helper_files, native_security_values, object_fields, regular_bytes, safe_relative, source_inventory, verify_source_commit,
    )
else:
    from archive_restore_helper_sources import offline_recipe
    from restore_helper_metadata import (
        ARCHIVE_METADATA, ARCHIVE_ROOT, TLS_PATCH_NAME, SourceInventory, discard_owned_output, exclusive_output,
        helper_files, native_security_values, object_fields, regular_bytes, safe_relative, source_inventory, verify_source_commit,
    )


def archive_metadata(path: Path, inventory: SourceInventory, helpers: dict[str, str]) -> tuple[dict[str, object], str]:
    """Read without extraction and verify archived source content, not just labels."""
    data = regular_bytes(path, 512 * 1024 * 1024)
    digests = {pin["name"]: hashlib.sha256() for pin in inventory["git"]}
    counts = {pin["name"]: 0 for pin in inventory["git"]}
    versions = {pin["name"]: pin["version"] for pin in inventory["git"]}
    source_blobs: dict[str, list[tuple[str, str, bytes]]] = {pin["name"]: [] for pin in inventory["git"]}
    commits: dict[str, bytes] = {}
    source_prefix = f"{ARCHIVE_ROOT}/build-output/restore-helpers/src/"
    metadata: dict[str, object] | None = None
    files: dict[str, bytes] = {}
    seen: set[str] = set()
    total = 0
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        for member in archive:
            safe_relative(member.name)
            if member.name in seen or len(seen) >= 40_000 or not (member.isfile() or member.issym()):
                raise ValueError("Native source archive contains duplicate or unsupported entries")
            seen.add(member.name)
            if member.size < 0 or member.size > 128 * 1024 * 1024:
                raise ValueError("Native source archive member exceeds the size bound")
            total += member.size
            if total > 512 * 1024 * 1024:
                raise ValueError("Native source archive exceeds the expanded size bound")
            if member.issym():
                content = member.linkname.encode("utf-8")
            else:
                stream = archive.extractfile(member)
                if stream is None:
                    raise ValueError("Native source archive has an unreadable regular member")
                with stream:
                    content = stream.read(member.size + 1)
                if len(content) != member.size:
                    raise ValueError("Native source archive member is incomplete")
            if member.name.startswith(source_prefix):
                relative = member.name.removeprefix(source_prefix)
                if relative == f"openssl-{inventory['openssl']['version']}.tar.gz":
                    if not member.isfile() or hashlib.sha256(content).hexdigest() != inventory["openssl"]["revision"]:
                        raise ValueError("Archived OpenSSL distribution differs from its pinned SHA-256")
                    files["openssl"] = b"verified"
                    continue
                name, separator, relative_path = relative.partition("/")
                if not separator or name not in digests:
                    raise ValueError("Native source archive contains an undeclared source component")
                if relative_path == ".tarball-version":
                    if not member.isfile() or content != (versions[name] + "\n").encode("ascii"):
                        raise ValueError("Archived source version label differs from the vendor recipe")
                    files[f"version/{name}"] = b"verified"
                    continue
                if member.issym():
                    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(relative_path), member.linkname))
                    if not member.linkname or "\\" in member.linkname or member.linkname.startswith("/") or resolved == ".." or resolved.startswith("../"):
                        raise ValueError("Archived source symbolic link escapes its component")
                    mode = "120000"
                elif member.mode == 0o755:
                    mode = "100755"
                elif member.mode == 0o644:
                    mode = "100644"
                else:
                    raise ValueError("Archived source file mode is unsupported")
                counts[name] += 1
                source_blobs[name].append((mode, relative_path, content))
                digests[name].update(f"{mode}\0{relative_path}\0{hashlib.sha256(content).hexdigest()}\0".encode("utf-8"))
            elif member.name.startswith(f"{ARCHIVE_ROOT}/pinned-git-commits/"):
                relative = member.name.removeprefix(f"{ARCHIVE_ROOT}/pinned-git-commits/")
                name = relative.removesuffix(".commit")
                if relative != name + ".commit" or name not in versions or not member.isfile() or len(content) > 256 * 1024:
                    raise ValueError("Archived Git commit object is invalid")
                commits[name] = content
            elif member.name == ARCHIVE_METADATA:
                if not member.isfile() or len(content) > 2 * 1024 * 1024:
                    raise ValueError("Native source archive manifest is oversized or not regular")
                metadata = object_fields(json.loads(content), "Native source archive manifest")
            elif member.name.startswith(f"{ARCHIVE_ROOT}/scripts/"):
                if not member.isfile() or len(content) > 256 * 1024:
                    raise ValueError("Native source archive build recipe is oversized or not regular")
                files[member.name.rsplit("/", 1)[1]] = content
            elif member.name in {f"{ARCHIVE_ROOT}/ios_developer_toolkit/firmware_models.py", f"{ARCHIVE_ROOT}/LICENSE"}:
                if not member.isfile() or len(content) > 256 * 1024:
                    raise ValueError("Archive native descriptor/license must be bounded regular files")
                files["descriptor" if member.name.endswith("firmware_models.py") else "project-license"] = content
            elif member.name.startswith(f"{ARCHIVE_ROOT}/packaged-helper-metadata/"):
                relative = member.name.removeprefix(f"{ARCHIVE_ROOT}/packaged-helper-metadata/")
                if not member.isfile():
                    raise ValueError("Archived helper metadata must be regular files")
                if relative == "restore-helper-manifest.json":
                    archived_manifest = object_fields(json.loads(content), "Archived helper manifest")
                    source_revision = next(pin["revision"] for pin in inventory["git"] if pin["name"] == "idevicerestore")
                    expected_manifest = {"schema_version": 2, "source_revision": source_revision, "minimum_macos": "14.0", "security_patch_sha256": inventory["security_patch_sha256"], "recipe_sha256": inventory["script_sha256"], "files": helpers}
                    if type(archived_manifest.get("schema_version")) is not int or archived_manifest != expected_manifest:
                        raise ValueError("Archived helper manifest differs from the packaged helper hashes")
                elif relative not in helpers or hashlib.sha256(content).hexdigest() != helpers[relative]:
                    raise ValueError("Archived helper licenses/inventory differ from packaged hashes")
                files[f"helper/{relative}"] = content if relative == "STAMP" else b"verified"
            else:
                raise ValueError("Native source archive contains an unexpected top-level entry")
    expected_fields = {"schema_version", "git", "openssl", "security_patch_sha256", "runtime_model_sha256", "vendor_script_sha256", "offline_script_sha256", "recorded_build_script_sha256", "helper_files", "rebuild_command", "build_requirements", "provenance_scope"}
    if metadata is None or set(metadata) != expected_fields or type(metadata["schema_version"]) is not int or metadata["schema_version"] != 2:
        raise ValueError("Native source archive has an unsupported manifest schema")
    if metadata["vendor_script_sha256"] != inventory["script_sha256"] or metadata["openssl"] != inventory["openssl"] or metadata["helper_files"] != helpers or metadata["security_patch_sha256"] != inventory["security_patch_sha256"]:
        raise ValueError("Native source archive pin/recipe/helper identities differ from the selected package")
    recorded_stamp = metadata["recorded_build_script_sha256"]
    if not isinstance(recorded_stamp, str) or re.fullmatch(r"[0-9a-f]{64}", recorded_stamp) is None or files.get("helper/STAMP") != (recorded_stamp + "\n").encode("ascii"):
        raise ValueError("Archive recorded build identity differs from the packaged helper STAMP")
    source_components = metadata["git"]
    if not isinstance(source_components, list) or len(source_components) != 8:
        raise ValueError("Native source archive must identify exactly eight Git trees")
    for value, pin in zip(source_components, inventory["git"]):
        component = object_fields(value, "Archived source identity")
        if type(component.get("tracked_files")) is not int:
            raise ValueError("Archived source tracked_files must be an integer")
        commit = commits.get(pin["name"])
        if commit is None:
            raise ValueError("Native source archive is missing a pinned Git commit object")
        verify_source_commit(pin, commit, source_blobs[pin["name"]])
        expected = {**pin, "source_tree_sha256": digests[pin["name"]].hexdigest(), "tracked_files": counts[pin["name"]], "commit_sha256": hashlib.sha256(commit).hexdigest()}
        if component != expected or counts[pin["name"]] < 1:
            raise ValueError(f"Archived source tree differs from its declared pin/hash: {pin['name']}")
    vendor = files.get("build_restore_helpers_vendor.sh")
    offline = files.get("build_restore_helpers_from_sources.sh")
    if vendor is None or hashlib.sha256(vendor).hexdigest() != inventory["script_sha256"] or offline is None or offline != offline_recipe(vendor) or hashlib.sha256(offline).hexdigest() != metadata["offline_script_sha256"]:
        raise ValueError("Archived build recipes differ from their declared digests/derivation")
    if set(key for key in files if key.endswith(".py") or key.endswith(".sh")) != {"build_restore_helpers_vendor.sh", "build_restore_helpers_from_sources.sh", "verify_restore_helpers.py", "restore_helper_metadata.py", "verify_archived_restore_sources.py"}:
        raise ValueError("Native source archive is missing its rebuild verifier")
    if files["verify_restore_helpers.py"] != regular_bytes(Path(__file__).with_name("verify_restore_helpers.py"), 256 * 1024):
        raise ValueError("Archived rebuild verifier differs from the packaged verifier source")
    if files["restore_helper_metadata.py"] != regular_bytes(Path(__file__).with_name("restore_helper_metadata.py"), 256 * 1024):
        raise ValueError("Archived native metadata boundary differs from the packaged verifier source")
    if files["verify_archived_restore_sources.py"] != regular_bytes(Path(__file__).with_name("verify_archived_restore_sources.py"), 256 * 1024):
        raise ValueError("Archived source-tree verifier differs from the packaged verifier source")
    descriptor = files.get("descriptor")
    expected_pins = {"PINNED_RESTORE_RECIPE_SHA256": inventory["script_sha256"], "PINNED_TLS_PATCH_SHA256": inventory["security_patch_sha256"]}
    if descriptor is None or native_security_values(descriptor) != expected_pins or hashlib.sha256(descriptor).hexdigest() != inventory["runtime_model_sha256"] or metadata["runtime_model_sha256"] != inventory["runtime_model_sha256"] or files.get("project-license") != regular_bytes(Path(__file__).parent.parent / "LICENSE", 64 * 1024):
        raise ValueError("Archived native pin descriptor or project license differs from the selected source")
    patch = files.get(TLS_PATCH_NAME)
    if patch is None or hashlib.sha256(patch).hexdigest() != inventory["security_patch_sha256"]:
        raise ValueError("Archived native TLS security patch differs from its reviewed SHA-256")
    if set(key for key in files if "/" not in key and key != "openssl") != {"build_restore_helpers_vendor.sh", "build_restore_helpers_from_sources.sh", "verify_restore_helpers.py", "restore_helper_metadata.py", "verify_archived_restore_sources.py", TLS_PATCH_NAME, "descriptor", "project-license"}:
        raise ValueError("Native source archive contains an unexpected build script or patch")
    expected_metadata = {name for name in helpers if name in {"SOURCES.txt", "STAMP"} or name.startswith("licenses/")} | {"restore-helper-manifest.json"}
    if {key.removeprefix("helper/") for key in files if key.startswith("helper/")} != expected_metadata or "openssl" not in files or any(f"version/{name}" not in files for name in versions):
        raise ValueError("Native source archive license/version/source inventory is incomplete")
    stamp = regular_bytes(path, 512 * 1024 * 1024)
    if stamp != data:
        raise ValueError("Native source archive changed during verification")
    return metadata, hashlib.sha256(data).hexdigest()


def native_components(inventory: SourceInventory, helpers: dict[str, str], metadata: dict[str, object], archive_name: str, archive_digest: str) -> list[dict[str, object]]:
    components: list[dict[str, object]] = []
    for pin in inventory["git"] + [inventory["openssl"]]:
        components.append({
            "type": "application" if pin["name"] == "idevicerestore" else "library",
            "bom-ref": f"urn:ios-developer-toolkit:native:source:{pin['name']}:{pin['revision']}",
            "name": pin["name"], "version": pin["version"],
            "licenses": [{"license": {"name": f"See packaged-helper-metadata/licenses/{pin['name']} and source headers in corresponding source archive"}}],
            "externalReferences": [{"type": "distribution" if pin["name"] == "openssl" else "vcs", "url": pin["url"]}],
            "properties": [
                {"name": "ios-developer-toolkit:base-source-identity", "value": pin["revision"]},
                {"name": "ios-developer-toolkit:vendor-script-sha256", "value": inventory["script_sha256"]},
                {"name": "ios-developer-toolkit:security-patch-sha256", "value": inventory["security_patch_sha256"]},
                {"name": "ios-developer-toolkit:source-archive", "value": archive_name},
                {"name": "ios-developer-toolkit:source-archive-sha256", "value": archive_digest},
                {"name": "ios-developer-toolkit:linkage", "value": "static helper dependency" if pin["name"] != "idevicerestore" else "separate helper application"},
            ],
        })
    for name, source in (("idevicerestore", "idevicerestore"), ("irecovery", "libirecovery")):
        components.append({
            "type": "file", "bom-ref": f"urn:ios-developer-toolkit:native:binary:{name}:{helpers[f'bin/{name}']}",
            "name": name, "hashes": [{"alg": "SHA-256", "content": helpers[f"bin/{name}"]}],
            "properties": [
                {"name": "ios-developer-toolkit:source-component", "value": source},
                {"name": "ios-developer-toolkit:build-target-architectures", "value": "arm64,x86_64"},
                {"name": "ios-developer-toolkit:build-target-minimum-macos", "value": "14.0"},
                {"name": "ios-developer-toolkit:vendor-script-sha256", "value": inventory["script_sha256"]},
                {"name": "ios-developer-toolkit:security-patch-sha256", "value": inventory["security_patch_sha256"]},
                {"name": "ios-developer-toolkit:recorded-build-script-sha256", "value": str(metadata["recorded_build_script_sha256"])},
            ],
        })
    return components


def augment_sbom(sbom_path: Path, helper_root: Path, source_archive: Path, destination: Path, build_script: Path) -> Path:
    inventory = source_inventory(build_script)
    helpers = helper_files(helper_root, inventory)
    metadata, archive_digest = archive_metadata(source_archive, inventory, helpers)
    sbom = object_fields(json.loads(regular_bytes(sbom_path, 32 * 1024 * 1024)), "CycloneDX SBOM")
    if sbom.get("bomFormat") != "CycloneDX" or sbom.get("specVersion") != "1.6":
        raise ValueError("Native augmentation requires the existing CycloneDX 1.6 schema")
    serial = sbom.get("serialNumber")
    if not isinstance(serial, str) or not serial.startswith("urn:uuid:"):
        raise ValueError("CycloneDX SBOM must preserve a valid serial number")
    uuid.UUID(serial.removeprefix("urn:uuid:"))
    top_component = object_fields(object_fields(sbom.get("metadata"), "CycloneDX metadata").get("component"), "CycloneDX application component")
    if top_component.get("name") != "ios-developer-toolkit":
        raise ValueError("CycloneDX SBOM does not identify ios-developer-toolkit")
    existing = sbom.get("components")
    if not isinstance(existing, list):
        raise ValueError("CycloneDX SBOM must contain its generated component array")
    references: set[str] = set()
    for value in existing:
        component = object_fields(value, "Existing CycloneDX component")
        reference = component.get("bom-ref")
        if not isinstance(reference, str) or not reference or reference in references:
            raise ValueError("Existing CycloneDX components must have unique nonempty bom-ref values")
        references.add(reference)
    components = native_components(inventory, helpers, metadata, source_archive.name, archive_digest)
    for component in components:
        reference = component["bom-ref"]
        if not isinstance(reference, str) or reference in references:
            raise ValueError("Native SBOM component identity is already present; refusing duplicate augmentation")
        references.add(reference)
    result = {**sbom, "components": [*existing, *components]}
    data = (json.dumps(result, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if b"file://" in data:
        raise ValueError("CycloneDX output contains a local file URL")
    descriptor = exclusive_output(destination)
    owned = os.fstat(descriptor)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(data)
    except BaseException:
        discard_owned_output(destination, owned.st_dev, owned.st_ino)
        raise
    return destination


def main(arguments: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sbom", type=Path, required=True)
    parser.add_argument("--helper-root", type=Path, required=True)
    parser.add_argument("--source-archive", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    options = parser.parse_args(arguments)
    print(augment_sbom(options.sbom, options.helper_root, options.source_archive, options.destination, Path(__file__).with_name("build_restore_helpers_vendor.sh")))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except (ValueError, OSError, UnicodeError, tarfile.TarError) as error:
        raise SystemExit(f"Native SBOM augmentation failed: {error}") from error
