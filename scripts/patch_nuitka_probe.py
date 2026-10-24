"""Repair pinned Nuitka's import-probe stdin transport in a dedicated build environment.

This macOS host reports a full pipe as writable, deadlocking CPython's simultaneous
stdin write and verbose stderr read. A prewritten temporary file preserves the
exact probe bytes, verbose import discovery, parser, and failure handling. Only
the reviewed Nuitka 4.2.1 source is accepted; a changed vendor file fails the build.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import os
import stat
import sys
from pathlib import Path
from typing import Sequence


NUITKA_VERSION = "4.2.1"
ORIGINAL_SHA256 = "70060645c5949fdf477f221017b46be16c5445bf3b92937d4182a394aaeb228f"
PATCHED_SHA256 = "8da1682512ee7bc8aba6e348ff344d5c3b48e52f29ae89b03ac8170281889e6c"
MAXIMUM_SOURCE_BYTES = 256 * 1024
SOURCE_NAME = "nuitka/freezer/ImportDetection.py"
ORIGINAL_PROBE = '''    process_result = executeProcess(
        command=(
            sys.executable,
            "-s",
            "-S",
            "-v",
            "-c",
            "import sys;exec(sys.stdin.read())",
        ),
        stdin=command,
        env=dict(os.environ, PYTHONIOENCODING="utf-8"),
    )
'''
PATCHED_PROBE = '''    with tempfile.TemporaryFile() as command_input:
        command_input.write(command)
        command_input.flush()
        command_input.seek(0)
        with subprocess.Popen(
            (
                sys.executable,
                "-s",
                "-S",
                "-v",
                "-c",
                "import sys;exec(sys.stdin.read())",
            ),
            stdin=command_input,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=dict(os.environ, PYTHONIOENCODING="utf-8"),
        ) as process:
            try:
                stdout, stderr = process.communicate(timeout=60)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
                raise
        process_result = ExecuteProcessResult(
            exit_code=process.returncode, stdout=stdout, stderr=stderr, rusage=None
        )
'''


def repaired_source(original: bytes) -> bytes:
    """Validate the complete vendor source and compile the exact reviewed repair."""
    if hashlib.sha256(original).hexdigest() != ORIGINAL_SHA256:
        raise ValueError("Nuitka ImportDetection.py differs from the reviewed 4.2.1 source; use a fresh build environment")
    source = original.decode("utf-8")
    replacements = (
        ("import pkgutil\nimport sys\n", "import pkgutil\nimport subprocess\nimport sys\nimport tempfile\n"),
        ("from nuitka.utils.Execution import executeProcess", "from nuitka.utils.Execution import ExecuteProcessResult"),
        (ORIGINAL_PROBE, PATCHED_PROBE),
    )
    for before, after in replacements:
        if source.count(before) != 1:
            raise ValueError("Nuitka import-probe source shape differs from the reviewed contract")
        source = source.replace(before, after, 1)
    patched = source.encode("utf-8")
    if hashlib.sha256(patched).hexdigest() != PATCHED_SHA256:
        raise ValueError("Nuitka import-probe repair differs from the reviewed SHA-256")
    compile(patched, SOURCE_NAME, "exec")
    return patched


def patch_nuitka_probe(build_environment: Path) -> Path:
    """Patch only this interpreter's explicitly selected virtual environment."""
    environment = build_environment.resolve(strict=True)
    if sys.platform != "darwin":
        raise ValueError("The reviewed Nuitka stdin repair is restricted to macOS builds")
    if environment != Path(sys.prefix).resolve() or sys.prefix == sys.base_prefix:
        raise ValueError("Run this script with the Python executable inside the dedicated build environment")
    distribution = importlib.metadata.distribution("Nuitka")
    if distribution.version != NUITKA_VERSION:
        raise ValueError(f"The reviewed stdin repair requires Nuitka {NUITKA_VERSION}; found {distribution.version}")
    source_path = Path(str(distribution.locate_file(SOURCE_NAME)))
    if source_path.is_symlink() or not source_path.resolve(strict=True).is_relative_to(environment):
        raise ValueError("Nuitka probe source must be a regular file inside the dedicated build environment")
    descriptor = os.open(source_path, os.O_RDWR | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "r+b") as source_file:
        if not stat.S_ISREG(os.fstat(source_file.fileno()).st_mode):
            raise ValueError("Nuitka probe source is not a regular file")
        original = source_file.read(MAXIMUM_SOURCE_BYTES + 1)
        if len(original) > MAXIMUM_SOURCE_BYTES:
            raise ValueError("Nuitka probe source exceeds the reviewed size bound")
        patched = repaired_source(original)
        source_file.seek(0)
        if source_file.write(patched) != len(patched):
            raise OSError("Could not write the complete reviewed Nuitka import-probe repair")
        source_file.truncate()
        source_file.flush()
        os.fsync(source_file.fileno())
        source_file.seek(0)
        written = source_file.read(MAXIMUM_SOURCE_BYTES + 1)
        if hashlib.sha256(written).hexdigest() != PATCHED_SHA256:
            raise ValueError("Written Nuitka import-probe repair failed SHA-256 verification")
        compile(written, SOURCE_NAME, "exec")
    return source_path


def main(arguments: Sequence[str]) -> int:
    if len(arguments) != 2:
        raise ValueError("Usage: patch_nuitka_probe.py DEDICATED_BUILD_ENVIRONMENT")
    patched = patch_nuitka_probe(Path(arguments[1]))
    print(f"Nuitka {NUITKA_VERSION} import-probe stdin repaired: {patched}; verified SHA-256: {PATCHED_SHA256}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv))
    except (ValueError, OSError, importlib.metadata.PackageNotFoundError) as error:
        print(f"Nuitka build repair failed: {error}", file=sys.stderr)
        raise SystemExit(1)
