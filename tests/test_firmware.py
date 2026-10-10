from __future__ import annotations

import hashlib
import json
import math
import os
import plistlib
import tempfile
import time
import unittest
import zipfile
from dataclasses import replace
from pathlib import Path
from threading import Event

from ios_developer_toolkit.firmware_models import (
    PINNED_INSTALLER_REVISION, PINNED_RESTORE_RECIPE_SHA256, PINNED_TLS_PATCH_SHA256,
    FirmwareCancelled, FirmwareError, FirmwareInstallPlan,
    HelperBundle, InstallerDevice, file_identity, helper_environment, inspect_ipsw,
    install_arguments, matching_install_identity, parse_catalog, parse_installer_device,
    parse_recovery_device, require_current_plan, require_file_unchanged,
    preflight_arguments, validate_helper_bundle, validated_apple_url,
)
from ios_developer_toolkit.firmware_transport import (
    CATALOG_CACHE_MAX_AGE, MAX_CATALOG_CACHE_BYTES, import_ipsw,
    load_catalog, refresh_catalog, signing_request,
)


def fixture_identity(behavior: str, variant: str) -> dict[str, object]:
    return {"ApChipID": "0x8103", "ApBoardID": "0x01", "ApSecurityDomain": "0x01", "UniqueBuildID": b"build-id",
            "Info": {"DeviceClass": "d93ap", "Variant": variant, "RestoreBehavior": behavior},
            "Manifest": {"iBSS": {"Trusted": True, "Digest": b"digest", "Info": {"RestoreRequestRules": [
                {"Conditions": {"ApRequiresImage4": True}, "Actions": {"EPRO": True, "ESEC": True}}
            ]}}}}


def fixture_ipsw(path: Path, identities: list[dict[str, object]]) -> None:
    manifest = {"ProductVersion": "26.0", "ProductBuildVersion": "23A341", "SupportedProductTypes": ["iPhone18,1"],
                "BuildIdentities": identities}
    with zipfile.ZipFile(path, "w", allowZip64=True) as archive:
        with archive.open("BuildManifest.plist", "w", force_zip64=True) as output:
            output.write(plistlib.dumps(manifest))


def fixture_catalog() -> bytes:
    return plistlib.dumps({"MobileDeviceSoftwareVersionsByVersion": {"1": {"MobileDeviceSoftwareVersions": {
        "iPhone18,1": {"23A341": {"Restore": {"ProductVersion": "26.0", "BuildVersion": "23A341",
            "FirmwareURL": "https://updates.cdn-apple.com/firmware.ipsw", "FirmwareSHA1": "a" * 40}}}}}}})


class FirmwareTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name).resolve()
        self.ipsw = self.directory / "firmware.ipsw"
        self.update = fixture_identity("Update", "Customer Upgrade Install (IPSW)")
        self.restore = fixture_identity("Erase", "Customer Erase Install (IPSW)")
        fixture_ipsw(self.ipsw, [self.update, self.restore])

    def test_reads_real_zip64_archive_and_exact_update_erase_identities(self) -> None:
        firmware = inspect_ipsw(self.ipsw)
        target = InstallerDevice(1234, "iPhone18,1", "d93ap", "normal")
        self.assertEqual(matching_install_identity(firmware, target, "update").behavior, "Update")
        self.assertEqual(matching_install_identity(firmware, target, "restore").behavior, "Erase")
        with self.assertRaises(FirmwareError):
            matching_install_identity(firmware, replace(target, device_class="anotherboard"), "update")
        with self.assertRaises(FirmwareError):
            matching_install_identity(firmware, replace(target, mode="dfu"), "update")

    def test_never_falls_back_from_missing_update_identity_to_erase(self) -> None:
        fixture_ipsw(self.ipsw, [self.restore])
        firmware = inspect_ipsw(self.ipsw)
        target = InstallerDevice(1234, "iPhone18,1", "d93ap", "normal")
        with self.assertRaises(FirmwareError):
            matching_install_identity(firmware, target, "update")
        self.assertEqual(matching_install_identity(firmware, target, "restore").behavior, "Erase")

    def test_rejects_ambiguous_or_behavior_mismatched_install_variant(self) -> None:
        target = InstallerDevice(1234, "iPhone18,1", "d93ap", "normal")
        fixture_ipsw(self.ipsw, [self.update, self.update])
        with self.assertRaises(FirmwareError):
            matching_install_identity(inspect_ipsw(self.ipsw), target, "update")
        fixture_ipsw(self.ipsw, [fixture_identity("Erase", "Customer Upgrade Install (IPSW)")])
        with self.assertRaises(FirmwareError):
            matching_install_identity(inspect_ipsw(self.ipsw), target, "update")

    def test_rejects_duplicate_root_manifest_and_symlink_archive(self) -> None:
        link = self.directory / "link.ipsw"
        link.symlink_to(self.ipsw)
        with self.assertRaises(FirmwareError):
            inspect_ipsw(link)
        with zipfile.ZipFile(self.ipsw, "a") as archive:
            archive.writestr("BuildManifest.plist", b"duplicate")
        with self.assertRaises(FirmwareError):
            inspect_ipsw(self.ipsw)

    def test_hashes_detect_changes_and_explicit_cancellation(self) -> None:
        identity = file_identity(self.ipsw, lambda: False)
        require_file_unchanged(identity)
        self.assertEqual(identity.sha256, hashlib.sha256(self.ipsw.read_bytes()).hexdigest())
        self.ipsw.write_bytes(self.ipsw.read_bytes() + b"changed")
        with self.assertRaises(FirmwareError):
            require_file_unchanged(identity)
        with self.assertRaises(FirmwareCancelled):
            file_identity(self.ipsw, lambda: True)

    def test_nonregular_fifo_is_refused_without_waiting_for_a_writer(self) -> None:
        fifo = self.directory / "manifest-fifo"
        os.mkfifo(fifo, 0o600)
        with self.assertRaises(FirmwareError):
            file_identity(fifo, lambda: False)

    def test_parses_exact_preflight_and_recovery_device_identities(self) -> None:
        target = parse_installer_device("Found device in Normal mode\nECID: 1234\nIdentified device as d93ap, iPhone18,1\n")
        self.assertEqual(target, InstallerDevice(1234, "iPhone18,1", "d93ap", "normal"))
        with self.assertRaises(FirmwareError):
            parse_installer_device("Found device in Normal mode\nECID: 1234\nECID: 2\nIdentified device as d93ap, iPhone18,1\n")
        recovery = parse_recovery_device("ECID: 0x4d2\nMODE: Recovery\nCPID: 0x8103\nBDID: 0x01\nPRODUCT: iPhone18,1\nMODEL: d93ap\n")
        self.assertEqual(recovery.ecid, 1234)
        with self.assertRaises(FirmwareError):
            parse_recovery_device("ECID: 0x4d2\nMODE: Unknown\n")
        with self.assertRaises(FirmwareError):
            parse_recovery_device("ECID: 0x0\nMODE: Recovery\nCPID: 0x8103\nBDID: 0x01\nPRODUCT: iPhone18,1\nMODEL: d93ap\n")
        with self.assertRaises(FirmwareError):
            preflight_arguments(self.ipsw, "0x0", "update", self.directory, self.directory / "log")

    def test_catalog_keeps_apple_sha1_separate_from_local_hashes(self) -> None:
        payload = plistlib.dumps({"MobileDeviceSoftwareVersionsByVersion": {"1": {"MobileDeviceSoftwareVersions": {
            "iPhone18,1": {"23A341": {"Restore": {"ProductVersion": "26.0", "BuildVersion": "23A341",
                "FirmwareURL": "http://updates.cdn-apple.com/a/firmware.ipsw", "FirmwareSHA1": "a" * 40}}}}}}})
        release = parse_catalog(payload, "iPhone18,1")[0]
        self.assertTrue(release.url.startswith("https://"))
        self.assertEqual(release.sha1, "a" * 40)
        self.assertEqual(parse_catalog(payload, "iPad0,0"), ())
        with self.assertRaises(FirmwareError):
            validated_apple_url("https://apple.com.attacker.example/firmware.ipsw")
        with self.assertRaises(FirmwareError):
            validated_apple_url("https://secret@updates.cdn-apple.com/firmware.ipsw")

    def test_real_catalog_cache_expires_after_one_day_and_explicit_refresh_bypasses_it(self) -> None:
        cache = self.directory / "catalog" / "apple-catalog.plist"
        calls: list[str] = []
        payload = fixture_catalog()
        def fixture_fetcher(cancelled: Event) -> bytes:
            calls.append("fixture fetch")
            return payload
        now = 100_000.0
        loaded = load_catalog(cache, Event(), now, fixture_fetcher)
        self.assertEqual((loaded.source, loaded.payload, loaded.fetched_at), ("apple", payload, now))
        self.assertEqual(cache.stat().st_mode & 0o777, 0o600)
        self.assertEqual(cache.parent.stat().st_mode & 0o777, 0o700)
        fresh = load_catalog(cache, Event(), now + CATALOG_CACHE_MAX_AGE - 1, fixture_fetcher)
        self.assertEqual((fresh.source, fresh.payload), ("cache", payload))
        self.assertEqual(len(calls), 1)
        stale = load_catalog(cache, Event(), now + CATALOG_CACHE_MAX_AGE, fixture_fetcher)
        self.assertEqual((stale.source, stale.fetched_at), ("apple", now + CATALOG_CACHE_MAX_AGE))
        self.assertEqual(len(calls), 2)
        explicit = refresh_catalog(cache, Event(), now + CATALOG_CACHE_MAX_AGE + 1, fixture_fetcher)
        self.assertEqual(explicit.source, "apple")
        self.assertEqual(len(calls), 3)
        self.assertEqual(load_catalog(cache, Event(), explicit.fetched_at, fixture_fetcher).source, "cache")
        self.assertEqual(len(calls), 3)

    def test_corrupt_catalog_cache_fails_without_fetch_and_explicit_refresh_repairs_it(self) -> None:
        cache = self.directory / "catalog" / "apple-catalog.plist"
        calls: list[str] = []
        def fixture_fetcher(cancelled: Event) -> bytes:
            calls.append("fixture fetch")
            return fixture_catalog()
        refresh_catalog(cache, Event(), 100_000.0, fixture_fetcher)
        valid = cache.read_bytes()
        envelope = plistlib.loads(valid)
        calls.clear()
        corruptions = (
            dict(envelope, source_url="https://attacker.example/catalog"),
            dict(envelope, fetched_at=100_002.0),
            dict(envelope, fetched_at=math.nan),
            dict(envelope, fetched_at=True),
            dict(envelope, payload_sha256="0" * 64),
            dict(envelope, unsupported=True),
            dict(envelope, payload=b"invalid", payload_sha256=hashlib.sha256(b"invalid").hexdigest()),
        )
        for changed in corruptions:
            cache.write_bytes(plistlib.dumps(changed, fmt=plistlib.FMT_BINARY))
            with self.assertRaises(FirmwareError):
                load_catalog(cache, Event(), 100_001.0, fixture_fetcher)
            self.assertEqual(calls, [])
        cache.write_bytes(b"corrupt")
        with self.assertRaises(FirmwareError):
            load_catalog(cache, Event(), 100_001.0, fixture_fetcher)
        repaired = refresh_catalog(cache, Event(), 100_001.0, fixture_fetcher)
        self.assertEqual(repaired.source, "apple")
        self.assertEqual(len(calls), 1)
        self.assertEqual(load_catalog(cache, Event(), 100_001.0, fixture_fetcher).source, "cache")

    def test_catalog_cache_denies_unsafe_files_limits_and_cancelled_or_failed_fetch(self) -> None:
        cache = self.directory / "catalog" / "apple-catalog.plist"
        calls: list[str] = []
        def fixture_fetcher(cancelled: Event) -> bytes:
            calls.append("fixture fetch")
            return fixture_catalog()
        cancelled = Event()
        cancelled.set()
        with self.assertRaises(FirmwareCancelled):
            load_catalog(cache, cancelled, 100_000.0, fixture_fetcher)
        self.assertFalse(cache.exists())
        refresh_catalog(cache, Event(), 100_000.0, fixture_fetcher)
        valid = cache.read_bytes()
        calls.clear()
        cache.chmod(0o644)
        with self.assertRaises(FirmwareError):
            load_catalog(cache, Event(), 100_000.0, fixture_fetcher)
        self.assertEqual(cache.stat().st_mode & 0o777, 0o644)
        cache.chmod(0o600)
        with cache.open("r+b") as output:
            output.truncate(MAX_CATALOG_CACHE_BYTES + 1)
        with self.assertRaises(FirmwareError):
            load_catalog(cache, Event(), 100_000.0, fixture_fetcher)
        cache.write_bytes(valid)
        link = cache.parent / "link.plist"
        link.symlink_to(cache)
        for loader in (load_catalog, refresh_catalog):
            with self.assertRaises(FirmwareError):
                loader(link, Event(), 100_000.0, fixture_fetcher)
        fifo = cache.parent / "fifo.plist"
        os.mkfifo(fifo, 0o600)
        with self.assertRaises(FirmwareError):
            load_catalog(fifo, Event(), 100_000.0, fixture_fetcher)
        self.assertEqual(calls, [])
        def failed_fetcher(cancelled: Event) -> bytes:
            raise FirmwareError("Controlled catalog transport failure")
        with self.assertRaises(FirmwareError):
            load_catalog(cache, Event(), 100_000.0 + CATALOG_CACHE_MAX_AGE, failed_fetcher)
        self.assertEqual(cache.read_bytes(), valid)
        self.assertEqual(tuple(cache.parent.glob(".apple-catalog-*")), ())

    def test_real_import_preserves_source_and_refuses_overwriting(self) -> None:
        library = self.directory / "library"
        before = self.ipsw.read_bytes()
        imported = import_ipsw(self.ipsw, library, Event(), lambda _: None)
        self.assertEqual(imported.path.read_bytes(), before)
        self.assertEqual(self.ipsw.read_bytes(), before)
        with self.assertRaises(FileExistsError):
            import_ipsw(self.ipsw, library, Event(), lambda _: None)

    def test_helper_inventory_refuses_changed_bytes_and_closed_schema_extras(self) -> None:
        bundle = self.directory / "helpers"
        (bundle / "bin").mkdir(parents=True)
        (bundle / "licenses").mkdir()
        for relative, data in (("bin/idevicerestore", b"installer"), ("bin/irecovery", b"recovery"),
                               ("SOURCES.txt", b"pins"), ("STAMP", (PINNED_RESTORE_RECIPE_SHA256 + "\n").encode("ascii")),
                               ("licenses/COPYING", b"license")):
            path = bundle / relative
            path.write_bytes(data)
            path.chmod(0o700)
        files = {relative: hashlib.sha256((bundle / relative).read_bytes()).hexdigest() for relative in (
            "bin/idevicerestore", "bin/irecovery", "SOURCES.txt", "STAMP", "licenses/COPYING")}
        manifest = {"schema_version": 2, "source_revision": PINNED_INSTALLER_REVISION, "minimum_macos": "14.0",
                    "security_patch_sha256": PINNED_TLS_PATCH_SHA256, "recipe_sha256": PINNED_RESTORE_RECIPE_SHA256, "files": files}
        manifest_path = bundle / "restore-helper-manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        verified = validate_helper_bundle(bundle, lambda: False)
        self.assertEqual(verified.installer.path, bundle / "bin/idevicerestore")
        for changed in (
            {key: value for key, value in dict(manifest, schema_version=1).items() if key not in ("security_patch_sha256", "recipe_sha256")},
            dict(manifest, security_patch_sha256="0" * 64),
            dict(manifest, recipe_sha256="0" * 64),
        ):
            manifest_path.write_text(json.dumps(changed), encoding="utf-8")
            with self.assertRaises(FirmwareError):
                validate_helper_bundle(bundle, lambda: False)
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        (bundle / "STAMP").write_text("0" * 64 + "\n", encoding="ascii")
        changed_files = dict(files, STAMP=hashlib.sha256((bundle / "STAMP").read_bytes()).hexdigest())
        manifest_path.write_text(json.dumps(dict(manifest, files=changed_files)), encoding="utf-8")
        with self.assertRaises(FirmwareError):
            validate_helper_bundle(bundle, lambda: False)
        (bundle / "STAMP").write_text(PINNED_RESTORE_RECIPE_SHA256 + "\n", encoding="ascii")
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        manifest["untrusted_extra"] = True
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaises(FirmwareError):
            validate_helper_bundle(bundle, lambda: False)
        del manifest["untrusted_extra"]
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        (bundle / "bin/idevicerestore").write_bytes(b"changed")
        with self.assertRaises(FirmwareError):
            validate_helper_bundle(bundle, lambda: False)

    def test_sdk_builder_contains_exact_image4_identity_and_synthetic_nonce(self) -> None:
        firmware = inspect_ipsw(self.ipsw)
        identity = matching_install_identity(firmware, InstallerDevice(1234, "iPhone18,1", "d93ap", "normal"), "update")
        request = signing_request(identity)
        self.assertEqual(request["ApChipID"], identity.chip_id)
        self.assertEqual(request["ApBoardID"], identity.board_id)
        self.assertTrue(request["@ApImg4Ticket"])
        self.assertIn("iBSS", request)
        self.assertEqual(len(request["ApNonce"]), 32)
        self.assertNotIn("@BBTicket", request)

    def test_install_request_requires_fresh_exact_plan_and_pins_update_variant(self) -> None:
        firmware = inspect_ipsw(self.ipsw)
        identity = file_identity(self.ipsw, lambda: False)
        target = InstallerDevice(1234, "iPhone18,1", "d93ap", "normal")
        build = matching_install_identity(firmware, target, "update")
        bundle = HelperBundle(self.directory, identity, identity, identity, (identity,))
        timestamp = time.time()
        plan = FirmwareInstallPlan(firmware, identity, bundle, target, "0x4d2", "update", build, None,
                                   "Local SHA256 only", "signed", timestamp, timestamp, timestamp)
        arguments = install_arguments(plan, self.directory, self.directory / "install.log", timestamp)
        self.assertIn("--variant", arguments)
        self.assertIn("Customer Upgrade Install (IPSW)", arguments)
        self.assertNotIn("--erase", arguments)
        self.assertNotIn("--no-action", arguments)
        for changed in (replace(plan, created_at=timestamp - 121), replace(plan, created_at=math.nan),
                        replace(plan, identity=matching_install_identity(firmware, target, "restore")),
                        replace(plan, firmware=replace(firmware, path=self.directory / "other.ipsw"))):
            with self.assertRaises(FirmwareError):
                install_arguments(changed, self.directory, self.directory / "install.log", timestamp)
        with self.assertRaises(FirmwareError):
            require_current_plan(plan, "0x999", "update", timestamp)

    def test_helper_environment_excludes_loader_and_certificate_overrides(self) -> None:
        self.directory.chmod(0o700)
        environment = helper_environment(self.directory)
        self.assertEqual(set(environment), {"PATH", "HOME", "TMPDIR", "LANG", "LC_ALL"})
        self.assertNotIn("DYLD_INSERT_LIBRARIES", environment)
        self.assertNotIn("SSL_CERT_FILE", environment)
        self.directory.chmod(0o755)
        with self.assertRaises(FirmwareError):
            helper_environment(self.directory)


if __name__ == "__main__":
    unittest.main()
