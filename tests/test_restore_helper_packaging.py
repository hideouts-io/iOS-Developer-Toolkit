"""Exercise native source packaging with real local Git trees and file artifacts."""
from __future__ import annotations

import hashlib
import io
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"

from scripts.archive_restore_helper_sources import archive_sources, git_blobs
from scripts.augment_restore_helper_sbom import augment_sbom
from scripts.embed_restore_helpers import embed_restore_helpers
from scripts.restore_helper_metadata import ARCHIVE_ROOT, SOURCE_URLS, TLS_PATCH_NAME, helper_files, packaged_helper_files, source_inventory, source_lines
from scripts.verify_release_metadata import ReleaseMetadataError, verify_native_metadata
from scripts.verify_archived_restore_sources import verify_archived_sources
from scripts.verify_macos_bundle import MacOSBundleValidationError, inspect_application_bundle, validate_application_mach_o_records
from scripts.verify_restore_helpers import write_helper_manifest


def git(root: Path, arguments: list[str]) -> str:
    result = subprocess.run(
        ["/usr/bin/git", "-c", "user.name=Packaging Fixture", "-c", "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null", "-C", str(root), *arguments],
        capture_output=True, check=True, text=True, timeout=20,
        env={"PATH": "/usr/bin:/bin", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"},
    )
    return result.stdout.strip()


def copy_package(helpers: Path, application: Path) -> Path:
    """Construct the package file contract from synthetic standalone artifacts."""
    code = application / "Contents/Helpers"
    code.mkdir(parents=True)
    metadata = application / "Contents/Resources/restore-helpers"
    metadata.mkdir(parents=True)
    for name in ("idevicerestore", "irecovery"):
        shutil.copy2(helpers / "bin" / name, code / name)
    for name in ("SOURCES.txt", "STAMP", "restore-helper-manifest.json"):
        shutil.copy2(helpers / name, metadata / name)
    shutil.copytree(helpers / "licenses", metadata / "licenses")
    return metadata


def native_tool(arguments: list[str]) -> None:
    subprocess.run(arguments, capture_output=True, check=True, timeout=60)


def fixture(root: Path) -> tuple[Path, Path, Path, Path]:
    sources = root / "source-cache"
    sources.mkdir()
    recipe = (SCRIPTS / "build_restore_helpers_vendor.sh").read_text(encoding="utf-8")
    for name in SOURCE_URLS:
        source = sources / name
        source.mkdir()
        (source / "source.c").write_text("int fixture(void) { return 0; }\n", encoding="utf-8")
        (source / "hidden.c").write_text("export ignored, but required corresponding source\n", encoding="utf-8")
        (source / ".gitattributes").write_text("hidden.c export-ignore\n", encoding="utf-8")
        (source / "COPYING").write_text("Synthetic fixture license\n", encoding="utf-8")
        (source / "include").mkdir()
        (source / "include/source.h").symlink_to("../source.c")
        git(source, ["init", "-q"])
        git(source, ["add", "."])
        git(source, ["commit", "-qm", "Fixture source"])
        revision = git(source, ["rev-parse", "HEAD"])
        recipe, count = re.subn(rf'("{re.escape(name)}\|[^|]+\|)[0-9a-f]{{40}}', lambda match: match[1] + revision, recipe)
        if count != 1:
            raise ValueError("Fixture recipe declaration was not found")
        (source / "source.c").write_text("dirty cache must never enter source archive\n", encoding="utf-8")
    openssl = b"exact synthetic OpenSSL distribution"
    (sources / "openssl-3.5.8.tar.gz").write_bytes(openssl)
    recipe = re.sub(r"(?m)^OPENSSL_SHA256=[0-9a-f]{64}$", "OPENSSL_SHA256=" + hashlib.sha256(openssl).hexdigest(), recipe)
    (root / "scripts").mkdir()
    script = root / "scripts/build_restore_helpers_vendor.sh"
    script.write_text(recipe, encoding="utf-8")
    shutil.copyfile(SCRIPTS / TLS_PATCH_NAME, script.with_name(TLS_PATCH_NAME))
    models = root / "ios_developer_toolkit"
    models.mkdir()
    (models / "firmware_models.py").write_text(
        f'PINNED_TLS_PATCH_SHA256 = "{hashlib.sha256(script.with_name(TLS_PATCH_NAME).read_bytes()).hexdigest()}"\n'
        f'PINNED_RESTORE_RECIPE_SHA256 = "{hashlib.sha256(script.read_bytes()).hexdigest()}"\n',
        encoding="ascii",
    )
    inventory = source_inventory(script)
    helpers = root / "helpers"
    (helpers / "bin").mkdir(parents=True)
    for name in ("idevicerestore", "irecovery"):
        binary = helpers / "bin" / name
        binary.write_bytes(f"synthetic executable bytes: {name}".encode("ascii"))
        binary.chmod(0o700)
    (helpers / "SOURCES.txt").write_text(source_lines(inventory), encoding="utf-8")
    (helpers / "STAMP").write_text(inventory["script_sha256"] + "\n", encoding="ascii")
    for name in [*SOURCE_URLS, "openssl"]:
        directory = helpers / "licenses" / name
        directory.mkdir(parents=True)
        (directory / "COPYING").write_text("Synthetic fixture license\n", encoding="utf-8")
    hashes = {path.relative_to(helpers).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in helpers.rglob("*") if path.is_file()}
    revision = next(pin["revision"] for pin in inventory["git"] if pin["name"] == "idevicerestore")
    (helpers / "restore-helper-manifest.json").write_text(json.dumps({"schema_version": 2, "source_revision": revision, "minimum_macos": "14.0", "security_patch_sha256": inventory["security_patch_sha256"], "recipe_sha256": inventory["script_sha256"], "files": hashes}), encoding="utf-8")
    sbom = root / "python.cdx.json"
    sbom.write_text(json.dumps({
        "bomFormat": "CycloneDX", "specVersion": "1.6", "version": 1,
        "serialNumber": "urn:uuid:12345678-1234-1234-1234-123456789012",
        "metadata": {"component": {"name": "ios-developer-toolkit", "version": "0.3.4"}},
        "components": [{"type": "library", "name": "pymobiledevice3", "version": "11.15.1", "bom-ref": "python:pymobiledevice3"}],
        "dependencies": [{"ref": "python:pymobiledevice3", "dependsOn": []}],
    }), encoding="utf-8")
    return sources, helpers, script, sbom


class RestoreHelperPackagingTests(unittest.TestCase):
    def test_complete_pinned_source_and_sbom_preserve_python_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sources, helpers, script, sbom = fixture(root)
            archive = root / "sources-macOS-arm64.tar.gz"
            archive_sources(helpers, sources, archive, script, SCRIPTS / "verify_restore_helpers.py")
            with tarfile.open(archive, "r:gz") as source:
                entries = {entry.name: entry for entry in source}
                required = f"{ARCHIVE_ROOT}/build-output/restore-helpers/src/libplist/hidden.c"
                self.assertIn(required, entries)
                content = source.extractfile(f"{ARCHIVE_ROOT}/build-output/restore-helpers/src/libplist/source.c")
                self.assertIsNotNone(content)
                if content is None:
                    self.fail("Tracked source artifact is missing")
                self.assertIn(b"return 0", content.read())
                rebuild = source.extractfile(f"{ARCHIVE_ROOT}/scripts/build_restore_helpers_from_sources.sh")
                self.assertIsNotNone(rebuild)
                if rebuild is None:
                    self.fail("Offline rebuild entrypoint is missing")
                recipe = root / "offline.sh"
                recipe.write_bytes(rebuild.read())
                result = subprocess.run(["/bin/bash", "-n", str(recipe)], capture_output=True, check=False, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(b'verify_restore_helpers.py" "$OUT"', recipe.read_bytes())
            extracted = root / "extracted"
            extracted.mkdir()
            with tarfile.open(archive, "r:gz") as source:
                for entry in source:
                    path = extracted / entry.name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    if entry.issym():
                        path.symlink_to(entry.linkname)
                    elif entry.isfile():
                        stream = source.extractfile(entry)
                        if stream is None:
                            self.fail("Generated source archive member is unreadable")
                        path.write_bytes(stream.read())
                        path.chmod(entry.mode)
                    else:
                        self.fail("Generated archive contains an unsupported test artifact")
            archive_root = extracted / ARCHIVE_ROOT
            verify_archived_sources(archive_root)
            self.assertFalse((archive_root / "build-output/restore-helpers/src/libplist/.git").exists())
            tracked = archive_root / "build-output/restore-helpers/src/libplist/source.c"
            original = tracked.read_bytes()
            tracked.write_bytes(b"changed tracked source\n")
            with self.assertRaisesRegex(ValueError, "source tree differs"):
                verify_archived_sources(archive_root)
            tracked.write_bytes(original)
            (archive_root / "build-output/restore-helpers/src/libplist/unexpected.c").write_text("untracked addition\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "source tree differs"):
                verify_archived_sources(archive_root)
            destination = root / "native.cdx.json"
            augment_sbom(sbom, helpers, archive, destination, script)
            before = json.loads(sbom.read_bytes())
            after = json.loads(destination.read_bytes())
            self.assertEqual({key: value for key, value in before.items() if key != "components"}, {key: value for key, value in after.items() if key != "components"})
            self.assertEqual(after["components"][0], before["components"][0])
            self.assertEqual(len(after["components"]), 12)
            self.assertEqual([component["type"] for component in after["components"][-2:]], ["file", "file"])
            self.assertEqual(after["components"][-1]["hashes"][0]["content"], hashlib.sha256((helpers / "bin/irecovery").read_bytes()).hexdigest())
            self.assertEqual(os.stat(archive).st_mode & 0o777, 0o600)
            self.assertEqual(os.stat(destination).st_mode & 0o777, 0o600)

    def test_refuses_wrong_pin_hash_and_existing_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sources, helpers, script, sbom = fixture(root)
            archive = root / "sources.tar.gz"
            archive.write_bytes(b"keep existing artifact")
            with self.assertRaises(FileExistsError):
                archive_sources(helpers, sources, archive, script, SCRIPTS / "verify_restore_helpers.py")
            self.assertEqual(archive.read_bytes(), b"keep existing artifact")
            git(sources / "libplist", ["commit", "--allow-empty", "-qm", "Wrong HEAD"])
            fresh = root / "fresh.tar.gz"
            with self.assertRaisesRegex(ValueError, "HEAD differs"):
                archive_sources(helpers, sources, fresh, script, SCRIPTS / "verify_restore_helpers.py")
            self.assertFalse(fresh.exists())
            (helpers / "bin/irecovery").write_bytes(b"changed binary")
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                augment_sbom(sbom, helpers, archive, root / "failed.cdx.json", script)

    def test_detects_modified_archive_source_and_manifest_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sources, helpers, script, sbom = fixture(root)
            archive = root / "sources.tar.gz"
            archive_sources(helpers, sources, archive, script, SCRIPTS / "verify_restore_helpers.py")
            tampered = root / "modified.tar.gz"
            with tarfile.open(archive, "r:gz") as source, tarfile.open(tampered, "w:gz") as target:
                for entry in source:
                    stream = source.extractfile(entry) if entry.isfile() else None
                    content = stream.read() if stream is not None else b""
                    if entry.name.endswith("/libplist/source.c"):
                        content = b"substituted source contents\n"
                        entry.size = len(content)
                    target.addfile(entry, io.BytesIO(content) if entry.isfile() else None)
            destination = root / "failed.cdx.json"
            with self.assertRaisesRegex(ValueError, "source tree differs"):
                augment_sbom(sbom, helpers, tampered, destination, script)
            self.assertFalse(destination.exists())
            (sources / "openssl-3.5.8.tar.gz").write_bytes(b"different tarball")
            with self.assertRaisesRegex(ValueError, "OpenSSL"):
                archive_sources(helpers, sources, root / "wrong.tar.gz", script, SCRIPTS / "verify_restore_helpers.py")
            self.assertFalse((root / "wrong.tar.gz").exists())

    def test_rejects_source_symlink_escape_without_following_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sources, _, script, _ = fixture(root)
            source = sources / "libplist"
            (source / "escape").symlink_to("../../private-input")
            git(source, ["add", "escape"])
            git(source, ["commit", "-qm", "Unsafe link fixture"])
            pin = next(value for value in source_inventory(script)["git"] if value["name"] == "libplist")
            revised = {**pin, "revision": git(source, ["rev-parse", "HEAD"])}
            with self.assertRaisesRegex(ValueError, "symbolic link escapes"):
                git_blobs(source, revised)

    def test_standalone_native_verifier_binds_archive_and_both_helper_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sources, helpers, script, sbom = fixture(root)
            application = root / "Toolkit.app"
            copy_package(helpers, application)
            archive = root / "iOS-Developer-Toolkit-restore-helper-sources-v0.3.4-macOS-arm64.tar.gz"
            archive_sources(helpers, sources, archive, script, SCRIPTS / "verify_restore_helpers.py")
            final_sbom = root / "native.cdx.json"
            augment_sbom(sbom, helpers, archive, final_sbom, script)
            loaded = json.loads(final_sbom.read_bytes())
            verify_native_metadata(application, final_sbom, "0.3.4", loaded, script)
            preserved_archive = root / "preserved.tar.gz"
            archive.rename(preserved_archive)
            archive.symlink_to(preserved_archive.name)
            with self.assertRaises(ReleaseMetadataError):
                verify_native_metadata(application, final_sbom, "0.3.4", loaded, script)
            archive.unlink()
            preserved_archive.rename(archive)
            loaded["components"][-1]["hashes"][0]["content"] = "0" * 64
            with self.assertRaisesRegex(ReleaseMetadataError, "binary hash differs"):
                verify_native_metadata(application, final_sbom, "0.3.4", loaded, script)

    def test_packaged_inventory_rejects_changed_files_extra_paths_links_and_special_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, helpers, script, _ = fixture(root)
            application = root / "Toolkit.app"
            metadata = copy_package(helpers, application)
            inventory = source_inventory(script)
            self.assertEqual(packaged_helper_files(application, inventory), helper_files(helpers, inventory))
            code = application / "Contents/Helpers/irecovery"
            content = code.read_bytes()
            code.write_bytes(content + b"changed")
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                packaged_helper_files(application, inventory)
            code.write_bytes(content)
            extra_code = application / "Contents/Helpers/unexpected"
            extra_code.write_bytes(b"extra code-location content")
            with self.assertRaisesRegex(ValueError, "exactly"):
                packaged_helper_files(application, inventory)
            extra_code.unlink()
            manifest = metadata / "restore-helper-manifest.json"
            original = manifest.read_bytes()
            for name in ("../outside", "bin/other-helper", "licenses/unknown/COPYING"):
                with self.subTest(name=name):
                    changed = json.loads(original)
                    changed["files"][name] = "0" * 64
                    manifest.write_text(json.dumps(changed), encoding="utf-8")
                    with self.assertRaises(ValueError):
                        packaged_helper_files(application, inventory)
            manifest.write_bytes(original)
            sources = metadata / "SOURCES.txt"
            source_content = sources.read_bytes()
            sources.unlink()
            sources.symlink_to(helpers / "SOURCES.txt")
            with self.assertRaisesRegex(ValueError, "symbolic link"):
                packaged_helper_files(application, inventory)
            sources.unlink()
            sources.write_bytes(source_content)
            extra = metadata / "extra.txt"
            extra.write_bytes(b"untracked resource")
            with self.assertRaisesRegex(ValueError, "exactly"):
                packaged_helper_files(application, inventory)
            extra.unlink()
            empty = metadata / "untracked"
            empty.mkdir()
            with self.assertRaisesRegex(ValueError, "uninventoried directory"):
                packaged_helper_files(application, inventory)
            empty.rmdir()
            os.mkfifo(metadata / "pipe")
            with self.assertRaisesRegex(ValueError, "special file"):
                packaged_helper_files(application, inventory)
            (metadata / "pipe").unlink()
            self.assertEqual(packaged_helper_files(application, inventory), helper_files(helpers, inventory))

    def test_partial_legacy_and_missing_packages_are_not_optional_success(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, helpers, script, _ = fixture(root)
            metadata_only = root / "MetadataOnly.app"
            shutil.copytree(helpers, metadata_only / "Contents/Resources/restore-helpers")
            code_only = root / "CodeOnly.app"
            (code_only / "Contents/Helpers").mkdir(parents=True)
            shutil.copy2(helpers / "bin/irecovery", code_only / "Contents/Helpers/irecovery")
            legacy = root / "Legacy.app"
            shutil.copytree(helpers, legacy / "Contents/Helpers/restore-helpers")
            declared = {"components": [{"bom-ref": "urn:ios-developer-toolkit:native:file:irecovery"}]}
            for application in (metadata_only, code_only, legacy, root / "Absent.app"):
                with self.subTest(application=application.name), self.assertRaises(ReleaseMetadataError):
                    verify_native_metadata(application, root / "sbom.json", "0.3.4", declared, script)
            complete = root / "Complete.app"
            copy_package(helpers, complete)
            (complete / "Contents/Helpers/irecovery").unlink()
            with self.assertRaisesRegex(ValueError, "exactly"):
                packaged_helper_files(complete, source_inventory(script))
            with self.assertRaises(MacOSBundleValidationError):
                validate_application_mach_o_records(complete, (), "arm64", "13.0", script)

    @unittest.skipUnless(sys.platform == "darwin", "Native macOS signing tools are required")
    def test_real_universal_package_signing_and_separate_macos_floor(self) -> None:
        """Use compiled inert code; this checks packaging, never a device command."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "main.c"
            source.write_text("int main(void) { return 0; }\n", encoding="ascii")
            helpers = root / "native-helpers"
            (helpers / "bin").mkdir(parents=True)
            script = SCRIPTS / "build_restore_helpers_vendor.sh"
            inventory = source_inventory(script)
            native_tool(["/usr/bin/xcrun", "clang", "-arch", "arm64", "-arch", "x86_64", "-mmacosx-version-min=14.0", str(source), "-o", str(helpers / "bin/idevicerestore")])
            shutil.copy2(helpers / "bin/idevicerestore", helpers / "bin/irecovery")
            (helpers / "SOURCES.txt").write_text(source_lines(inventory), encoding="utf-8")
            (helpers / "STAMP").write_text(inventory["script_sha256"] + "\n", encoding="ascii")
            for name in [*SOURCE_URLS, "openssl"]:
                directory = helpers / "licenses" / name
                directory.mkdir(parents=True)
                (directory / "COPYING").write_text("Synthetic signing fixture license\n", encoding="ascii")
            write_helper_manifest(helpers, script)
            application = root / "Native.app"
            (application / "Contents/MacOS").mkdir(parents=True)
            (application / "Contents/Resources").mkdir()
            (application / "Contents/Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": "io.hideouts.packaging.fixture", "CFBundleExecutable": "Fixture", "CFBundlePackageType": "APPL"}))
            native_tool(["/usr/bin/xcrun", "clang", "-arch", "arm64", "-arch", "x86_64", "-mmacosx-version-min=13.0", str(source), "-o", str(application / "Contents/MacOS/Fixture")])
            metadata = embed_restore_helpers(helpers, application)
            self.assertEqual(metadata, application / "Contents/Resources/restore-helpers")
            for name in ("idevicerestore", "irecovery"):
                native_tool(["/usr/bin/codesign", "--force", "--sign", "-", "--timestamp=none", str(application / "Contents/Helpers" / name)])
            native_tool(["/usr/bin/codesign", "--force", "--deep", "--sign", "-", "--timestamp=none", str(application)])
            for name in ("idevicerestore", "irecovery"):
                shutil.copy2(application / "Contents/Helpers" / name, helpers / "bin" / name)
            (helpers / "restore-helper-manifest.json").unlink()
            write_helper_manifest(helpers, script)
            shutil.copy2(helpers / "restore-helper-manifest.json", metadata / "restore-helper-manifest.json")
            expected = helper_files(helpers, inventory)
            native_tool(["/usr/bin/codesign", "--force", "--sign", "-", "--timestamp=none", str(application)])
            native_tool(["/usr/bin/codesign", "--verify", "--deep", "--strict", "--all-architectures", "--verbose=2", str(application)])
            self.assertEqual(packaged_helper_files(application, inventory), expected)
            records = inspect_application_bundle(application)
            self.assertEqual(validate_application_mach_o_records(application, records, "arm64", "13.0", script), 2)
            self.assertEqual(validate_application_mach_o_records(application, records, "x86_64", "13.0", script), 2)
            shutil.copy2(helpers / "bin/irecovery", application / "Contents/MacOS/Unexpected")
            with self.assertRaisesRegex(MacOSBundleValidationError, "newer than"):
                validate_application_mach_o_records(application, inspect_application_bundle(application), "arm64", "13.0", script)

    def test_refuses_changed_tls_patch_and_legacy_helper_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sources, helpers, script, _ = fixture(root)
            patch = script.with_name(TLS_PATCH_NAME)
            original = patch.read_bytes()
            patch.write_bytes(original + b"modified patch\n")
            with self.assertRaisesRegex(ValueError, "TLS patch SHA-256"):
                archive_sources(helpers, sources, root / "changed.tar.gz", script, SCRIPTS / "verify_restore_helpers.py")
            self.assertFalse((root / "changed.tar.gz").exists())
            patch.write_bytes(original)
            manifest = helpers / "restore-helper-manifest.json"
            legacy = json.loads(manifest.read_bytes())
            legacy["schema_version"] = 1
            del legacy["security_patch_sha256"]
            del legacy["recipe_sha256"]
            manifest.write_text(json.dumps(legacy), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "requires schema 2"):
                archive_sources(helpers, sources, root / "legacy.tar.gz", script, SCRIPTS / "verify_restore_helpers.py")
            self.assertFalse((root / "legacy.tar.gz").exists())


if __name__ == "__main__":
    unittest.main()
