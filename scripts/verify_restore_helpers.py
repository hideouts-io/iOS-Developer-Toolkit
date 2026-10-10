"""Check local bytes against the reviewed source recipe and transport patch.

The recorded hashes qualify declared build inputs. They do not authenticate an
upstream origin or prove that a native binary has safe runtime behavior.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

if __package__:
    from .restore_helper_metadata import regular_bytes, source_inventory, source_lines
else:
    from restore_helper_metadata import regular_bytes, source_inventory, source_lines


def helper_manifest(root: Path, source_script: Path) -> dict[str, object]:
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Helper output must be a regular directory")
    inventory = source_inventory(source_script)
    if regular_bytes(root / "SOURCES.txt", 64 * 1024).decode("utf-8") != source_lines(inventory):
        raise ValueError("Helper SOURCES.txt differs from the reviewed source and patch inventory; rebuild into a fresh output directory")
    if regular_bytes(root / "STAMP", 128).decode("ascii") != inventory["script_sha256"] + "\n":
        raise ValueError("Helper STAMP differs from the reviewed patched recipe; rebuild into a fresh output directory")
    hashes: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"Helper output contains a symbolic link: {path.name}")
        relative = path.relative_to(root).as_posix()
        if path.is_file() and relative != "restore-helper-manifest.json":
            hashes[relative] = hashlib.sha256(regular_bytes(path, 256 * 1024 * 1024)).hexdigest()
    if len(hashes) > 1024:
        raise ValueError("Helper output exceeds its 1024-file inventory limit")
    for name in ("idevicerestore", "irecovery"):
        binary = root / "bin" / name
        if not os.access(binary, os.X_OK):
            raise ValueError(f"Helper is missing or not executable: {name}")
        archs = subprocess.run(["/usr/bin/lipo", "-archs", str(binary)], capture_output=True, check=True, timeout=15).stdout.decode().split()
        if set(archs) != {"arm64", "x86_64"}:
            raise ValueError(f"Helper must contain Apple Silicon and Intel: {name}")
        libraries = subprocess.run(["/usr/bin/otool", "-L", str(binary)], capture_output=True, check=True, timeout=15).stdout.decode().splitlines()
        for line in libraries:
            if line.startswith("\t") and not line.strip().startswith(("/usr/lib/", "/System/Library/")):
                raise ValueError(f"Helper has a non-system dynamic dependency: {name}")
    for pin in [*inventory["git"], inventory["openssl"]]:
        if not any(key.startswith(f"licenses/{pin['name']}/") for key in hashes):
            raise ValueError(f"Helper license is missing: {pin['name']}")
    revision = next(pin["revision"] for pin in inventory["git"] if pin["name"] == "idevicerestore")
    return {"schema_version": 2, "source_revision": revision, "minimum_macos": "14.0",
            "security_patch_sha256": inventory["security_patch_sha256"],
            "recipe_sha256": inventory["script_sha256"], "files": hashes}


def write_helper_manifest(root: Path, source_script: Path) -> Path:
    manifest = helper_manifest(root, source_script)
    destination = root / "restore-helper-manifest.json"
    if destination.exists():
        if destination.is_symlink() or json.loads(regular_bytes(destination, 1_048_576)) != manifest:
            raise ValueError("Existing helper manifest differs from validated output; use a fresh build output directory")
        return destination
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        json.dump(manifest, output, indent=2, sort_keys=True)
        output.write("\n")
    return destination


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: verify_restore_helpers.py OUTPUT_DIRECTORY")
    print(write_helper_manifest(Path(sys.argv[1]).resolve(strict=True), Path(__file__).with_name("build_restore_helpers_vendor.sh")))
