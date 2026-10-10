"""Package complete pinned native source from existing local caches, without fetching.

The archive keeps all tracked blobs, including export-ignored source, the exact
OpenSSL distribution, license inventory and build recipes. The offline recipe
uses these included trees and creates a fresh helper manifest after rebuilding.
This script does not execute any source or native build and refuses overwrites.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import posixpath
import re
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

if __package__:
    from .restore_helper_metadata import (
        ARCHIVE_METADATA, ARCHIVE_ROOT, TLS_PATCH_NAME, SourceInventory, SourcePin, discard_owned_output, exclusive_output,
        helper_files, regular_bytes, safe_relative, source_inventory, verify_source_commit,
    )
else:
    from restore_helper_metadata import (
        ARCHIVE_METADATA, ARCHIVE_ROOT, TLS_PATCH_NAME, SourceInventory, SourcePin, discard_owned_output, exclusive_output,
        helper_files, regular_bytes, safe_relative, source_inventory, verify_source_commit,
    )


def git_output(root: Path, arguments: list[str], data: bytes) -> bytes:
    """Use private files for batch I/O so developer-tool child pipes cannot stall."""
    with tempfile.TemporaryFile() as incoming, tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as diagnostics:
        incoming.write(data)
        incoming.seek(0)
        result = subprocess.run(
            ["/usr/bin/git", "--no-replace-objects", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null", "-C", str(root), *arguments],
            stdin=incoming, stdout=output, stderr=diagnostics, check=False, timeout=60,
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"},
        )
        if result.returncode:
            raise ValueError(f"Local Git source read failed for {root.name} (exit {result.returncode})")
        output.seek(0)
        content = output.read(144 * 1024 * 1024 + 1)
        if len(content) > 144 * 1024 * 1024:
            raise ValueError("Local Git read exceeds the bounded source output")
        return content


def git_blobs(root: Path, pin: SourcePin) -> list[tuple[str, str, bytes]]:
    """Read immutable objects at the exact commit, ignoring dirty working files."""
    if root.is_symlink() or not root.is_dir() or (root / ".git").is_symlink():
        raise ValueError(f"Source cache must be a regular Git checkout: {pin['name']}")
    if git_output(root, ["rev-parse", "HEAD"], b"").decode("ascii").strip() != pin["revision"]:
        raise ValueError(f"Source cache HEAD differs from pinned commit: {pin['name']}")
    git_output(root, ["fsck", "--strict", "--no-reflogs", "--no-dangling", pin["revision"]], b"")
    entries: list[tuple[str, str, str]] = []
    raw_tree = git_output(root, ["ls-tree", "-r", "--full-tree", "-z", pin["revision"]], b"")
    for raw in raw_tree.split(b"\0"):
        if not raw:
            continue
        header, raw_name = raw.split(b"\t", 1)
        mode, kind, identifier = header.decode("ascii").split(" ")
        name = raw_name.decode("utf-8")
        safe_relative(name)
        if mode not in {"100644", "100755", "120000"} or kind != "blob" or re.fullmatch(r"[0-9a-f]{40}", identifier) is None:
            raise ValueError(f"Source tree contains unsupported gitlink/object: {pin['name']}")
        entries.append((mode, name, identifier))
    if not entries or len(entries) > 30_000:
        raise ValueError(f"Source tree is empty or exceeds the file limit: {pin['name']}")
    identifiers = "".join(identifier + "\n" for _, _, identifier in entries).encode("ascii")
    sizes = git_output(root, ["cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize)"], identifiers).decode("ascii").splitlines()
    if len(sizes) != len(entries):
        raise ValueError("Git source size inventory is incomplete")
    total = 0
    for line, (_, _, identifier) in zip(sizes, entries):
        fields = line.split(" ")
        if len(fields) != 3 or fields[:2] != [identifier, "blob"] or not fields[2].isdigit():
            raise ValueError("Git source object inventory is invalid")
        size = int(fields[2])
        if size > 32 * 1024 * 1024:
            raise ValueError("Git source blob exceeds the 32 MiB bound")
        total += size
    if total > 128 * 1024 * 1024:
        raise ValueError("Git source tree exceeds the 128 MiB bound")
    stream = io.BytesIO(git_output(root, ["cat-file", "--batch"], identifiers))
    result: list[tuple[str, str, bytes]] = []
    for mode, name, identifier in entries:
        fields = stream.readline().decode("ascii").rstrip("\n").split(" ")
        if len(fields) != 3 or fields[:2] != [identifier, "blob"] or not fields[2].isdigit():
            raise ValueError("Git source batch differs from checked inventory")
        content = stream.read(int(fields[2]))
        if len(content) != int(fields[2]) or stream.read(1) != b"\n":
            raise ValueError("Git source blob read is incomplete")
        if mode == "120000":
            target = content.decode("utf-8")
            resolved = posixpath.normpath(posixpath.join(posixpath.dirname(name), target))
            if not target or "\\" in target or target.startswith("/") or resolved == ".." or resolved.startswith("../"):
                raise ValueError("Source symbolic link escapes its component tree")
        result.append((mode, name, content))
    if stream.read(1):
        raise ValueError("Git source batch has unexpected trailing bytes")
    return result


def offline_recipe(original: bytes) -> bytes:
    """Retain the build steps while replacing the sole network-fetch function."""
    source = original.decode("utf-8")
    start = source.find("fetch_sources() {\n")
    closing = source.find("\n}\n", start)
    if start < 0 or closing < 0 or source.count("fetch_sources() {\n") != 1 or source.count("\nfetch_sources\n") != 1:
        raise ValueError("Vendor recipe changed its source acquisition contract")
    end = closing + 3
    validation = '''fetch_sources() {
  python3 "$ROOT/scripts/verify_archived_restore_sources.py" "$ROOT"
  for entry in "${GIT_SOURCES[@]}"; do
    IFS='|' read -r name url commit version <<<"$entry"
    [[ -d "$SRC/$name" && ! -L "$SRC/$name" ]] || fail "included source is missing: $name"
    [[ -f "$SRC/$name/.tarball-version" ]] || fail "included source version is missing: $name"
    [[ "$(cat "$SRC/$name/.tarball-version")" == "$version" ]] || fail "included source version differs: $name"
  done
  local tarball="$SRC/openssl-$OPENSSL_VERSION.tar.gz"
  [[ -f "$tarball" && ! -L "$tarball" ]] || fail "included OpenSSL source is missing"
  echo "$OPENSSL_SHA256  $tarball" | shasum -a 256 -c - >/dev/null || fail "OpenSSL source checksum mismatch"
}
'''
    offline = source[:start] + validation + source[end:]
    offline += '\npython3 "$ROOT/scripts/verify_restore_helpers.py" "$OUT"\n'
    return offline.encode("utf-8")


def add_blob(archive: tarfile.TarFile, name: str, content: bytes, mode: int) -> None:
    safe_relative(name)
    entry = tarfile.TarInfo(name)
    entry.size, entry.mode, entry.mtime = len(content), mode, 0
    archive.addfile(entry, io.BytesIO(content))


def add_source(archive: tarfile.TarFile, pin: SourcePin, blobs: list[tuple[str, str, bytes]]) -> dict[str, object]:
    prefix = f"{ARCHIVE_ROOT}/build-output/restore-helpers/src/{pin['name']}"
    digest = hashlib.sha256()
    for mode, name, content in blobs:
        digest.update(f"{mode}\0{name}\0{hashlib.sha256(content).hexdigest()}\0".encode("utf-8"))
        if mode == "120000":
            entry = tarfile.TarInfo(f"{prefix}/{name}")
            entry.type, entry.linkname, entry.mode, entry.mtime = tarfile.SYMTYPE, content.decode("utf-8"), 0o777, 0
            archive.addfile(entry)
        else:
            add_blob(archive, f"{prefix}/{name}", content, 0o755 if mode == "100755" else 0o644)
    if any(name == ".tarball-version" for _, name, _ in blobs):
        raise ValueError("Pinned source unexpectedly tracks generated .tarball-version")
    add_blob(archive, f"{prefix}/.tarball-version", (pin["version"] + "\n").encode("ascii"), 0o644)
    return {**pin, "source_tree_sha256": digest.hexdigest(), "tracked_files": len(blobs)}


def archive_sources(helper_root: Path, source_root: Path, destination: Path, build_script: Path, verifier: Path) -> Path:
    inventory: SourceInventory = source_inventory(build_script)
    helpers = helper_files(helper_root, inventory)
    if source_root.is_symlink() or not source_root.is_dir():
        raise ValueError("Native source root must be an existing regular directory")
    tarball_name = f"openssl-{inventory['openssl']['version']}.tar.gz"
    openssl = regular_bytes(source_root / tarball_name, 128 * 1024 * 1024)
    if hashlib.sha256(openssl).hexdigest() != inventory["openssl"]["revision"]:
        raise ValueError("OpenSSL source tarball differs from the vendor's pinned SHA-256")
    recipe = regular_bytes(build_script, 256 * 1024)
    rebuild = offline_recipe(recipe)
    verifier_bytes = regular_bytes(verifier, 256 * 1024)
    patch = regular_bytes(build_script.with_name(TLS_PATCH_NAME), 256 * 1024)
    metadata_script = regular_bytes(Path(__file__).with_name("restore_helper_metadata.py"), 256 * 1024)
    source_verifier = regular_bytes(Path(__file__).with_name("verify_archived_restore_sources.py"), 256 * 1024)
    pin_descriptor = regular_bytes(build_script.parent.parent / "ios_developer_toolkit/firmware_models.py", 256 * 1024)
    project_license = regular_bytes(Path(__file__).parent.parent / "LICENSE", 64 * 1024)
    if hashlib.sha256(recipe).hexdigest() != inventory["script_sha256"] or hashlib.sha256(patch).hexdigest() != inventory["security_patch_sha256"] or hashlib.sha256(pin_descriptor).hexdigest() != inventory["runtime_model_sha256"]:
        raise ValueError("Native recipe, patch, or security descriptor changed before source packaging")
    descriptor = exclusive_output(destination)
    owned = os.fstat(descriptor)
    try:
        with os.fdopen(descriptor, "wb") as output:
            with gzip.GzipFile(filename="", mode="wb", fileobj=output, mtime=0) as compressed:
                with tarfile.open(fileobj=compressed, mode="w|") as archive:
                    components: list[dict[str, object]] = []
                    source_bytes = len(openssl)
                    source_count = 0
                    for pin in inventory["git"]:
                        source = source_root / pin["name"]
                        blobs = git_blobs(source, pin)
                        source_bytes += sum(len(content) for _, _, content in blobs)
                        source_count += len(blobs)
                        if source_bytes > 384 * 1024 * 1024 or source_count > 30_000:
                            raise ValueError("Complete native source exceeds the archive byte/file bounds")
                        commit = git_output(source, ["cat-file", "commit", pin["revision"]], b"")
                        verify_source_commit(pin, commit, blobs)
                        component = add_source(archive, pin, blobs)
                        components.append({**component, "commit_sha256": hashlib.sha256(commit).hexdigest()})
                        add_blob(archive, f"{ARCHIVE_ROOT}/pinned-git-commits/{pin['name']}.commit", commit, 0o644)
                    add_blob(archive, f"{ARCHIVE_ROOT}/build-output/restore-helpers/src/{tarball_name}", openssl, 0o644)
                    for name, content in (("build_restore_helpers_vendor.sh", recipe), ("build_restore_helpers_from_sources.sh", rebuild), ("verify_restore_helpers.py", verifier_bytes), ("restore_helper_metadata.py", metadata_script), ("verify_archived_restore_sources.py", source_verifier), (TLS_PATCH_NAME, patch)):
                        add_blob(archive, f"{ARCHIVE_ROOT}/scripts/{name}", content, 0o644 if name == TLS_PATCH_NAME else 0o755)
                    add_blob(archive, f"{ARCHIVE_ROOT}/ios_developer_toolkit/firmware_models.py", pin_descriptor, 0o644)
                    add_blob(archive, f"{ARCHIVE_ROOT}/LICENSE", project_license, 0o644)
                    metadata = {
                        "schema_version": 2, "git": components, "openssl": inventory["openssl"],
                        "security_patch_sha256": inventory["security_patch_sha256"],
                        "runtime_model_sha256": inventory["runtime_model_sha256"],
                        "vendor_script_sha256": inventory["script_sha256"],
                        "offline_script_sha256": hashlib.sha256(rebuild).hexdigest(),
                        "recorded_build_script_sha256": regular_bytes(helper_root / "STAMP", 128).decode("ascii").strip(),
                        "helper_files": helpers,
                        "rebuild_command": "bash scripts/build_restore_helpers_from_sources.sh build-output/restore-helpers/rebuilt",
                        "build_requirements": "macOS 14+, Xcode SDK, autoconf, automake, libtool, pkg-config, cmake, git, Python 3",
                        "provenance_scope": "Local exact Git object identities and byte hashes; upstream origin and build execution are not authenticated by this archive.",
                    }
                    add_blob(archive, ARCHIVE_METADATA, (json.dumps(metadata, indent=2, sort_keys=True) + "\n").encode("utf-8"), 0o644)
                    for name in sorted(helpers):
                        if name == "SOURCES.txt" or name == "STAMP" or name.startswith("licenses/"):
                            add_blob(archive, f"{ARCHIVE_ROOT}/packaged-helper-metadata/{name}", regular_bytes(helper_root / name, 4 * 1024 * 1024), 0o644)
                    add_blob(archive, f"{ARCHIVE_ROOT}/packaged-helper-metadata/restore-helper-manifest.json", regular_bytes(helper_root / "restore-helper-manifest.json", 2 * 1024 * 1024), 0o644)
        return destination
    except BaseException:
        discard_owned_output(destination, owned.st_dev, owned.st_ino)
        raise


def main(arguments: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--helper-root", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    options = parser.parse_args(arguments)
    scripts = Path(__file__).parent
    path = archive_sources(options.helper_root, options.source_root, options.destination, scripts / "build_restore_helpers_vendor.sh", scripts / "verify_restore_helpers.py")
    print(path)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except (ValueError, OSError, UnicodeError, subprocess.SubprocessError) as error:
        raise SystemExit(f"Native source archive failed: {error}") from error
