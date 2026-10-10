"""Closed native-helper provenance boundaries shared by source and SBOM packaging.

Hashes bind local bytes to the declared vendor recipe; they do not authenticate
the upstream origin or establish that a native build is safe.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import stat
from pathlib import Path, PurePosixPath
from typing import TypedDict


class SourcePin(TypedDict):
    name: str
    url: str
    revision: str
    version: str


class SourceInventory(TypedDict):
    git: list[SourcePin]
    openssl: SourcePin
    script_sha256: str
    security_patch_sha256: str
    runtime_model_sha256: str


SOURCE_URLS = {
    name: f"https://github.com/libimobiledevice/{name}.git"
    for name in (
        "libplist", "libimobiledevice-glue", "libusbmuxd", "libtatsu",
        "libimobiledevice", "libirecovery", "idevicerestore",
    )
} | {"libzip": "https://github.com/nih-at/libzip.git"}
ARCHIVE_ROOT = "restore-helper-sources"
ARCHIVE_METADATA = f"{ARCHIVE_ROOT}/SOURCE-MANIFEST.json"
TLS_PATCH_NAME = "restore_helpers_verified_tls.patch"
NATIVE_PIN_NAMES = {"PINNED_TLS_PATCH_SHA256", "PINNED_RESTORE_RECIPE_SHA256"}


def native_security_values(data: bytes) -> dict[str, str]:
    """Read two reviewed literal pins without importing or executing application code."""
    try:
        parsed = ast.parse(data.decode("utf-8"))
    except SyntaxError as error:
        raise ValueError("Native security descriptor is not valid Python source") from error
    pins: dict[str, str] = {}
    for node in parsed.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name, value = node.targets[0].id, node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name, value = node.target.id, node.value
        else:
            continue
        if name not in NATIVE_PIN_NAMES:
            continue
        if name in pins or not isinstance(value, ast.Constant) or not isinstance(value.value, str) or re.fullmatch(r"[0-9a-f]{64}", value.value) is None:
            raise ValueError("Native security pin descriptor must declare unique literal SHA-256 values")
        pins[name] = value.value
    if set(pins) != NATIVE_PIN_NAMES:
        raise ValueError("Native security descriptor is missing the reviewed patch and recipe SHA-256 pins")
    return pins


def regular_bytes(path: Path, limit: int) -> bytes:
    """Snapshot one bounded regular file without following the final symlink."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        initial = os.fstat(descriptor)
        if not stat.S_ISREG(initial.st_mode) or initial.st_size > limit:
            raise ValueError(f"Packaging input must be regular and at most {limit} bytes: {path.name}")
        with os.fdopen(descriptor, "rb", closefd=False) as source:
            content = source.read(limit + 1)
        current = os.fstat(descriptor)
        identity = (initial.st_dev, initial.st_ino, initial.st_size, initial.st_mtime_ns, initial.st_ctime_ns)
        if identity != (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns, current.st_ctime_ns):
            raise ValueError(f"Packaging input changed while reading: {path.name}")
        if len(content) != initial.st_size:
            raise ValueError(f"Packaging input changed size while reading: {path.name}")
        return content
    finally:
        os.close(descriptor)


def object_fields(value: object, description: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{description} must be a JSON object with string keys")
    return {key: item for key, item in value.items()}


def safe_relative(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if not value or len(value) > 1024 or len(path.parts) > 32 or "\0" in value or "\\" in value or path.is_absolute() or any(part in ("", ".", "..") for part in value.split("/")):
        raise ValueError("Packaging inventory contains an unsafe relative path")
    return path


def git_object_identity(kind: str, content: bytes) -> str:
    """Use Git's existing SHA-1 object format, not an origin authentication scheme."""
    return hashlib.sha1(f"{kind} {len(content)}\0".encode("ascii") + content, usedforsecurity=False).hexdigest()


def git_tree_identity(blobs: list[tuple[str, str, bytes]]) -> str:
    """Reconstruct a complete Git tree from tracked paths, modes and blob bytes."""
    entries: dict[str, tuple[str, bytes]] = {}
    directories: dict[str, list[tuple[str, str, bytes]]] = {}
    for mode, path, content in blobs:
        name, separator, suffix = path.partition("/")
        if separator:
            directories.setdefault(name, []).append((mode, suffix, content))
        else:
            if name in entries:
                raise ValueError("Source tree contains a duplicate tracked path")
            entries[name] = (mode, bytes.fromhex(git_object_identity("blob", content)))
    if set(entries) & set(directories):
        raise ValueError("Source tree has a file/directory path collision")
    for name, children in directories.items():
        entries[name] = ("40000", bytes.fromhex(git_tree_identity(children)))
    content = b"".join(
        mode.encode("ascii") + b" " + name.encode("utf-8") + b"\0" + identifier
        for name in sorted(entries, key=lambda name: (name + ("/" if entries[name][0] == "40000" else "")).encode("utf-8"))
        for mode, identifier in [entries[name]]
    )
    return git_object_identity("tree", content)


def verify_source_commit(pin: SourcePin, commit: bytes, blobs: list[tuple[str, str, bytes]]) -> None:
    if git_object_identity("commit", commit) != pin["revision"]:
        raise ValueError(f"Archived Git commit bytes differ from the pinned identity: {pin['name']}")
    tree_line = commit.split(b"\n", 1)[0]
    if tree_line != b"tree " + git_tree_identity(blobs).encode("ascii"):
        raise ValueError(f"Complete source tree differs from the pinned Git commit: {pin['name']}")


def source_inventory(script: Path) -> SourceInventory:
    data = regular_bytes(script, 256 * 1024)
    text = data.decode("utf-8")
    if text.splitlines().count("MIN_MACOS=14.0") != 1:
        raise ValueError("Vendor recipe must preserve the native helper macOS 14.0 build target")
    declarations = re.findall(r'^  "([a-z0-9-]+)\|(https://[^|\n]+)\|([0-9a-f]{40})\|([a-zA-Z0-9.-]+)"$', text, re.MULTILINE)
    pins: list[SourcePin] = [
        {"name": name, "url": url, "revision": revision, "version": version}
        for name, url, revision, version in declarations
    ]
    if len(pins) != 8 or {pin["name"]: pin["url"] for pin in pins} != SOURCE_URLS:
        raise ValueError("Vendor recipe must pin exactly the eight approved native source repositories")
    versions = re.findall(r"^OPENSSL_VERSION=([0-9]+\.[0-9]+\.[0-9]+)$", text, re.MULTILINE)
    hashes = re.findall(r"^OPENSSL_SHA256=([0-9a-f]{64})$", text, re.MULTILINE)
    url_line = 'OPENSSL_URL="https://github.com/openssl/openssl/releases/download/openssl-$OPENSSL_VERSION/openssl-$OPENSSL_VERSION.tar.gz"'
    if len(versions) != 1 or len(hashes) != 1 or text.splitlines().count(url_line) != 1:
        raise ValueError("Vendor recipe must pin the approved OpenSSL release URL, version, and SHA-256")
    version = versions[0]
    openssl: SourcePin = {
        "name": "openssl", "version": version, "revision": hashes[0],
        "url": f"https://github.com/openssl/openssl/releases/download/openssl-{version}/openssl-{version}.tar.gz",
    }
    patch_hashes = re.findall(r"^TLS_PATCH_SHA256=([0-9a-f]{64})$", text, re.MULTILINE)
    patch = regular_bytes(script.with_name(TLS_PATCH_NAME), 256 * 1024)
    patch_digest = hashlib.sha256(patch).hexdigest()
    if len(patch_hashes) != 1 or patch_hashes[0] != patch_digest:
        raise ValueError("Vendor recipe must pin the exact reviewed native TLS patch SHA-256")
    recipe_digest = hashlib.sha256(data).hexdigest()
    runtime_source = regular_bytes(script.parent.parent / "ios_developer_toolkit/firmware_models.py", 256 * 1024)
    reviewed = native_security_values(runtime_source)
    if reviewed["PINNED_TLS_PATCH_SHA256"] != patch_digest or reviewed["PINNED_RESTORE_RECIPE_SHA256"] != recipe_digest:
        raise ValueError("Native recipe or TLS patch differs from the reviewed application security descriptor")
    return {"git": pins, "openssl": openssl, "script_sha256": recipe_digest, "security_patch_sha256": patch_digest, "runtime_model_sha256": hashlib.sha256(runtime_source).hexdigest()}


def source_lines(inventory: SourceInventory) -> str:
    lines = [f"{pin['name']} {pin['version']} {pin['url']} @ {pin['revision']}" for pin in inventory["git"]]
    openssl = inventory["openssl"]
    lines.append(f"openssl {openssl['version']} {openssl['url']} sha256 {openssl['revision']}")
    return "\n".join(lines) + "\n"


def helper_hash_inventory(payload: bytes, inventory: SourceInventory) -> dict[str, str]:
    """Parse the shared closed schema-2 logical inventory without choosing file paths."""
    manifest = object_fields(json.loads(payload), "Helper manifest")
    if type(manifest.get("schema_version")) is not int or manifest.get("schema_version") != 2:
        raise ValueError("Helper output requires schema 2; rebuild with the current reviewed TLS recipe")
    if set(manifest) != {"schema_version", "source_revision", "minimum_macos", "security_patch_sha256", "recipe_sha256", "files"}:
        raise ValueError("Helper manifest contains unsupported fields")
    revision = next(pin["revision"] for pin in inventory["git"] if pin["name"] == "idevicerestore")
    if type(manifest["schema_version"]) is not int or manifest["schema_version"] != 2 or manifest["source_revision"] != revision or manifest["minimum_macos"] != "14.0" or manifest["recipe_sha256"] != inventory["script_sha256"] or manifest["security_patch_sha256"] != inventory["security_patch_sha256"]:
        raise ValueError("Helper manifest identity does not match the pinned vendor recipe")
    declared = object_fields(manifest["files"], "Helper hash inventory")
    if not declared or len(declared) > 4096:
        raise ValueError("Helper file inventory is empty or exceeds the 4096-file bound")
    hashes: dict[str, str] = {}
    for name, digest in declared.items():
        safe_relative(name)
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None or name == "restore-helper-manifest.json":
            raise ValueError("Helper manifest contains an invalid SHA-256 entry")
        hashes[name] = digest
    return hashes


def helper_files(root: Path, inventory: SourceInventory) -> dict[str, str]:
    """Validate closed manifest identity and every byte after final helper signing."""
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Helper root must be a regular directory")
    hashes = helper_hash_inventory(regular_bytes(root / "restore-helper-manifest.json", 2 * 1024 * 1024), inventory)
    actual: set[str] = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError("Helper output contains a symbolic link")
        if not path.is_file() and not path.is_dir():
            raise ValueError("Helper output contains an unsupported special file")
        relative = path.relative_to(root).as_posix()
        if path.is_file() and relative != "restore-helper-manifest.json":
            actual.add(relative)
            if len(actual) > 4096:
                raise ValueError("Helper output exceeds the 4096-file bound")
    if actual != set(hashes):
        raise ValueError("Helper manifest does not inventory exactly the packaged regular files")
    total = 0
    for name, digest in hashes.items():
        content = regular_bytes(root / name, 256 * 1024 * 1024)
        total += len(content)
        if total > 512 * 1024 * 1024:
            raise ValueError("Helper output exceeds the 512 MiB aggregate bound")
        if hashlib.sha256(content).hexdigest() != digest:
            raise ValueError(f"Helper output differs from manifest SHA-256: {name}")
    for binary in ("bin/idevicerestore", "bin/irecovery"):
        if binary not in hashes or not os.access(root / binary, os.X_OK):
            raise ValueError(f"Helper executable is missing: {binary}")
    if regular_bytes(root / "SOURCES.txt", 64 * 1024).decode("utf-8") != source_lines(inventory):
        raise ValueError("Helper SOURCES.txt does not exactly match all nine pinned source identities")
    if regular_bytes(root / "STAMP", 128).decode("ascii") != inventory["script_sha256"] + "\n":
        raise ValueError("Helper STAMP differs from the current TLS-qualified vendor build recipe")
    for name in [pin["name"] for pin in inventory["git"]] + ["openssl"]:
        if not any(path.startswith(f"licenses/{name}/") for path in hashes):
            raise ValueError(f"Helper license inventory is missing {name}")
    return hashes


PACKAGED_HELPER_NAMES: dict[str, str] = {
    "bin/idevicerestore": "idevicerestore",
    "bin/irecovery": "irecovery",
}


def packaged_helpers_present(application: Path) -> bool:
    """Detect both split-layout and unsupported legacy helper-package markers."""
    contents = application / "Contents"
    markers = (
        contents / "Resources/restore-helpers",
        contents / "Helpers/restore-helpers",
        *(contents / "Helpers" / name for name in PACKAGED_HELPER_NAMES.values()),
    )
    return any(path.exists() or path.is_symlink() for path in markers)


def packaged_metadata_names(hashes: dict[str, str], inventory: SourceInventory) -> set[str]:
    """Limit logical package names to fixed tools and approved component metadata."""
    required = {*PACKAGED_HELPER_NAMES, "SOURCES.txt", "STAMP"}
    if not required.issubset(hashes) or len(hashes) > 1024:
        raise ValueError("Packaged helper inventory must include both executables, source pins and stamp within its 1024-file bound")
    names = {pin["name"] for pin in inventory["git"]} | {"openssl"}
    metadata_names: set[str] = set()
    for name in hashes:
        if name in PACKAGED_HELPER_NAMES:
            continue
        parts = safe_relative(name).parts
        if name not in {"SOURCES.txt", "STAMP"} and (len(parts) < 3 or parts[0] != "licenses" or parts[1] not in names):
            raise ValueError("Packaged manifest contains a path outside the fixed helper/resource mapping")
        metadata_names.add(name)
    for name in names:
        if not any(path.startswith(f"licenses/{name}/") for path in hashes):
            raise ValueError(f"Packaged helper license inventory is missing {name}")
    return metadata_names


def packaged_helper_files(application: Path, inventory: SourceInventory) -> dict[str, str]:
    """Bind schema-2 logical names to two flat code files and sealed resource metadata.

    No manifest-controlled path can select another executable. Resources contain
    only the exact source, stamp and license inventory; standalone layout and
    its hash keys remain unchanged for corresponding-source/SBOM generation.
    """
    contents = application / "Contents"
    code = contents / "Helpers"
    resources = contents / "Resources"
    metadata = resources / "restore-helpers"
    for directory in (application, contents, code, resources, metadata):
        if directory.is_symlink() or not directory.is_dir():
            raise ValueError("Packaged helpers require regular code and resource directories")
    code_names: set[str] = set()
    for path in code.iterdir():
        if path.is_symlink() or not path.is_file():
            raise ValueError("Packaged helper code files must be regular and must not be symbolic links")
        code_names.add(path.name)
        if len(code_names) > 2:
            raise ValueError("Contents/Helpers must contain exactly the two flat native helper code files")
    if code_names != set(PACKAGED_HELPER_NAMES.values()):
        raise ValueError("Contents/Helpers must contain exactly the two flat native helper code files")
    hashes = helper_hash_inventory(regular_bytes(metadata / "restore-helper-manifest.json", 1024 * 1024), inventory)
    metadata_names = packaged_metadata_names(hashes, inventory)
    allowed_directories = {
        parent.as_posix()
        for name in metadata_names
        for parent in PurePosixPath(name).parents
        if parent != PurePosixPath(".")
    }
    actual: set[str] = set()
    for path in metadata.rglob("*"):
        if path.is_symlink():
            raise ValueError("Packaged helper metadata contains a symbolic link")
        relative = path.relative_to(metadata).as_posix()
        if path.is_dir():
            if relative not in allowed_directories:
                raise ValueError("Packaged helper metadata contains an uninventoried directory")
        elif path.is_file():
            if relative != "restore-helper-manifest.json":
                actual.add(relative)
                if len(actual) > 1024:
                    raise ValueError("Packaged helper metadata exceeds its 1024-file bound")
        else:
            raise ValueError("Packaged helper metadata contains an unsupported special file")
    if actual != metadata_names:
        raise ValueError("Packaged manifest does not inventory exactly the regular resource metadata files")
    total = 0
    for name, digest in hashes.items():
        path = code / PACKAGED_HELPER_NAMES[name] if name in PACKAGED_HELPER_NAMES else metadata / name
        content = regular_bytes(path, 256 * 1024 * 1024)
        total += len(content)
        if total > 512 * 1024 * 1024:
            raise ValueError("Packaged helper files exceed the 512 MiB aggregate bound")
        if hashlib.sha256(content).hexdigest() != digest:
            raise ValueError(f"Packaged helper differs from manifest SHA-256: {name}")
    for name in PACKAGED_HELPER_NAMES.values():
        if not os.access(code / name, os.X_OK):
            raise ValueError(f"Packaged helper is not executable: {name}")
    if regular_bytes(metadata / "SOURCES.txt", 64 * 1024).decode("utf-8") != source_lines(inventory):
        raise ValueError("Packaged SOURCES.txt differs from all nine pinned source identities")
    if regular_bytes(metadata / "STAMP", 128).decode("ascii") != inventory["script_sha256"] + "\n":
        raise ValueError("Packaged STAMP differs from the current TLS-qualified vendor recipe")
    return hashes


def exclusive_output(path: Path) -> int:
    """Create a task-owned private output; callers remove only their failed output."""
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise ValueError("Output parent must be an existing regular directory")
    return os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)


def discard_owned_output(path: Path, device: int, inode: int) -> None:
    current = path.lstat()
    if (current.st_dev, current.st_ino) != (device, inode):
        raise ValueError("Failed packaging output was replaced; preserving the replacement file")
    path.unlink()
