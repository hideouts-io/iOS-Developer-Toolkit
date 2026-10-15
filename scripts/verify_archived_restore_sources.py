"""Verify extracted native corresponding source without fetching or Git metadata.

The complete source trees reconstruct the Git tree objects named by the included
raw pinned commits. Only the generated .tarball-version files are excluded.
This runs before the offline recipe copies or patches any build source.
"""
from __future__ import annotations

import hashlib
import json
import os
import posixpath
import stat
import sys
from pathlib import Path

if __package__:
    from .restore_helper_metadata import object_fields, regular_bytes, safe_relative, source_inventory, verify_source_commit
else:
    from restore_helper_metadata import object_fields, regular_bytes, safe_relative, source_inventory, verify_source_commit


def source_blobs(root: Path) -> list[tuple[str, str, bytes]]:
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Archived native source component must be a regular directory")
    result: list[tuple[str, str, bytes]] = []
    total = 0
    entries = 0
    directories: set[str] = set()
    for path in root.rglob("*"):
        entries += 1
        if entries > 60_000:
            raise ValueError("Archived source component exceeds the 60000-entry bound")
        relative = path.relative_to(root).as_posix()
        safe_relative(relative)
        info = path.lstat()
        if stat.S_ISDIR(info.st_mode):
            directories.add(relative)
            continue
        if relative == ".tarball-version":
            continue
        if stat.S_ISLNK(info.st_mode):
            target = os.readlink(path)
            resolved = posixpath.normpath(posixpath.join(posixpath.dirname(relative), target))
            if not target or "\\" in target or target.startswith("/") or resolved == ".." or resolved.startswith("../"):
                raise ValueError("Archived source symbolic link escapes its component")
            mode, content = "120000", target.encode("utf-8")
        elif stat.S_ISREG(info.st_mode):
            mode = "100755" if info.st_mode & 0o111 else "100644"
            content = regular_bytes(path, 32 * 1024 * 1024)
        else:
            raise ValueError("Archived source contains an unsupported special file")
        total += len(content)
        if total > 128 * 1024 * 1024 or len(result) >= 30_000:
            raise ValueError("Archived source component exceeds its byte/file bounds")
        result.append((mode, relative, content))
    expected_directories: set[str] = set()
    for _, relative, _ in result:
        parts = relative.split("/")
        expected_directories.update("/".join(parts[:index]) for index in range(1, len(parts)))
    if directories != expected_directories:
        raise ValueError("Archived source contains an unexpected empty/index/generated directory")
    return result


def verify_archived_sources(root: Path) -> None:
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Choose a regular extracted native source archive directory")
    for name in ("scripts", "ios_developer_toolkit", "pinned-git-commits", "build-output", "build-output/restore-helpers", "build-output/restore-helpers/src"):
        directory = root / name
        if directory.is_symlink() or not directory.is_dir():
            raise ValueError("Extracted source archive contains an unsafe or missing required directory")
    inventory = source_inventory(root / "scripts/build_restore_helpers_vendor.sh")
    manifest = object_fields(json.loads(regular_bytes(root / "SOURCE-MANIFEST.json", 2 * 1024 * 1024)), "Source archive manifest")
    if type(manifest.get("schema_version")) is not int or manifest.get("schema_version") != 2 or manifest.get("vendor_script_sha256") != inventory["script_sha256"] or manifest.get("security_patch_sha256") != inventory["security_patch_sha256"] or manifest.get("runtime_model_sha256") != inventory["runtime_model_sha256"] or manifest.get("openssl") != inventory["openssl"]:
        raise ValueError("Extracted source archive identity differs from the reviewed recipe and TLS patch")
    metadata = manifest.get("git")
    if not isinstance(metadata, list) or len(metadata) != 8:
        raise ValueError("Source archive must identify exactly eight Git source trees")
    source_root = root / "build-output/restore-helpers/src"
    if source_root.is_symlink() or not source_root.is_dir():
        raise ValueError("Extracted source archive is missing its regular source directory")
    expected_entries = {pin["name"] for pin in inventory["git"]} | {f"openssl-{inventory['openssl']['version']}.tar.gz"}
    if {path.name for path in source_root.iterdir()} != expected_entries:
        raise ValueError("Extracted source directory contains unexpected extra inputs")
    for value, pin in zip(metadata, inventory["git"]):
        declared = object_fields(value, "Archived source identity")
        source = source_root / pin["name"]
        version = regular_bytes(source / ".tarball-version", 256)
        if version != (pin["version"] + "\n").encode("ascii"):
            raise ValueError("Extracted source version label differs from the pinned recipe")
        blobs = source_blobs(source)
        commit = regular_bytes(root / f"pinned-git-commits/{pin['name']}.commit", 256 * 1024)
        verify_source_commit(pin, commit, blobs)
        if any(declared.get(key) != item for key, item in pin.items()) or type(declared.get("tracked_files")) is not int or declared.get("tracked_files") != len(blobs) or declared.get("commit_sha256") != hashlib.sha256(commit).hexdigest():
            raise ValueError("Extracted source manifest differs from its complete pinned tree")
    openssl = regular_bytes(source_root / f"openssl-{inventory['openssl']['version']}.tar.gz", 128 * 1024 * 1024)
    if hashlib.sha256(openssl).hexdigest() != inventory["openssl"]["revision"]:
        raise ValueError("Extracted OpenSSL tarball differs from its pinned SHA-256")


if __name__ == "__main__":
    try:
        if len(sys.argv) != 2:
            raise ValueError("Usage: verify_archived_restore_sources.py EXTRACTED_SOURCE_ARCHIVE_ROOT")
        verify_archived_sources(Path(sys.argv[1]).absolute())
        print("Verified all archived native source trees and OpenSSL without Git metadata")
    except (ValueError, OSError, UnicodeError) as error:
        raise SystemExit(f"Offline native source verification failed: {error}") from error
