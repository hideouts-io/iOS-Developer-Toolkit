from __future__ import annotations

import hashlib
import plistlib
import tempfile
import unittest
from pathlib import Path

from ios_developer_toolkit.developer_images import (
    DeveloperImageError, image_child, image_identity, inspect_developer_image,
    numeric_identifier, personalization_request, plist_record, read_image_bytes,
    select_image_payload,
)


def image_manifest(image_path: str, rules: list[dict[str, object]]) -> dict[str, object]:
    identity: dict[str, object] = {
        "ApChipID": "0x1", "ApBoardID": "0x2",
        "Manifest": {
            "PersonalizedDMG": {"Info": {"Path": image_path}, "Trusted": True, "Digest": b"synthetic-image-digest"},
            "LoadableTrustCache": {"Info": {"Path": "cache", "RestoreRequestRules": rules}, "Trusted": True, "Digest": b"synthetic-cache-digest"},
        },
    }
    return {"BuildIdentities": [identity], "SupportedProductTypes": ["iPhone18,1"], "ProductVersion": "27.0", "ProductBuildVersion": "Synthetic"}


def create_personalized_image(root: Path) -> Path:
    folder = root / "personalized"
    folder.mkdir()
    (folder / "image.dmg").write_bytes(b"synthetic image bytes")
    (folder / "cache").write_bytes(b"synthetic trust cache")
    (folder / "BuildManifest.plist").write_bytes(plistlib.dumps(image_manifest("image.dmg", [])))
    return folder


class DeveloperImageTests(unittest.TestCase):
    def test_exact_legacy_version_build_and_personalized_model_chip_board(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            legacy = root / "16.4 (20E247)"
            legacy.mkdir()
            (legacy / "DeveloperDiskImage.dmg").write_bytes(b"image")
            (legacy / "DeveloperDiskImage.dmg.signature").write_bytes(b"signature")
            source = inspect_developer_image(legacy)
            selected = select_image_payload(source, "iPhone14,1", "16.4.1", "20E247", 0, 0)
            self.assertEqual(selected.image.name, "DeveloperDiskImage.dmg")
            for version, build in (("16.3", "20E247"), ("16.4", "20E248"), ("17.0", "20E247")):
                with self.assertRaises(DeveloperImageError):
                    select_image_payload(source, "iPhone14,1", version, build, 0, 0)
            personalized = inspect_developer_image(create_personalized_image(root))
            payload = select_image_payload(personalized, "iPhone18,1", "27.0", "Synthetic", 1, 2)
            self.assertEqual(payload.image.read_bytes(), b"synthetic image bytes")
            for product, version, chip, board in (("iPhone17,1", "27.0", 1, 2), ("iPhone18,1", "16.4", 1, 2), ("iPhone18,1", "27.0", 3, 2), ("iPhone18,1", "27.0", 1, 3)):
                with self.assertRaises(DeveloperImageError):
                    select_image_payload(personalized, product, version, "Synthetic", chip, board)

    def test_restore_and_component_symlinks_and_traversal_are_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            folder = create_personalized_image(root)
            selected = root / "selected"
            selected.mkdir()
            (selected / "Restore").symlink_to(folder, target_is_directory=True)
            with self.assertRaisesRegex(DeveloperImageError, "symbolic link"):
                inspect_developer_image(selected)
            outside = root / "outside.dmg"
            outside.write_bytes(b"outside")
            (folder / "linked.dmg").symlink_to(outside)
            for relative in ("../outside.dmg", str(outside), "linked.dmg", "bad\\path"):
                with self.assertRaises(DeveloperImageError):
                    image_child(folder, relative)
            (folder / "nested").symlink_to(root, target_is_directory=True)
            with self.assertRaises((DeveloperImageError, OSError)):
                read_image_bytes(folder, folder / "nested" / "outside.dmg", 100)
            with self.assertRaises((DeveloperImageError, OSError)):
                read_image_bytes(folder, folder / "linked.dmg", 100)
            with self.assertRaises((DeveloperImageError, ValueError, OSError)):
                read_image_bytes(folder, folder / ".." / "outside.dmg", 100)

    def test_bounded_snapshots_remain_pinned_after_path_replacement_and_preserve_source_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            folder = create_personalized_image(root)
            image = folder / "image.dmg"
            original = image.read_bytes()
            digest = hashlib.sha256(original).hexdigest()
            with self.assertRaisesRegex(DeveloperImageError, "limit"):
                read_image_bytes(folder, image, 2)
            snapshot = read_image_bytes(folder, image, 100)
            self.assertEqual(image.read_bytes(), original)
            image.write_bytes(b"replacement")
            self.assertEqual(snapshot, original)
            self.assertEqual(hashlib.sha256(snapshot).hexdigest(), digest)
            empty = folder / "empty"
            empty.write_bytes(b"")
            with self.assertRaises(DeveloperImageError):
                read_image_bytes(folder, empty, 100)

    def test_component_rule_priority_and_optional_identifier_whitelist(self) -> None:
        fallback: list[dict[str, object]] = [{"Conditions": {"ApRawProductionMode": True}, "Actions": {"EPRO": True}}]
        own: list[dict[str, object]] = [{"Conditions": {"ApRawProductionMode": True}, "Actions": {"EPRO": False, "ESEC": 255}}]
        identity: dict[str, object] = {
            "ApChipID": "0x1", "ApBoardID": "0x2",
            "Manifest": {
                "PersonalizedDMG": {"Info": {"Path": "image.dmg", "RestoreRequestRules": own}, "Trusted": True, "Digest": b"image"},
                "LoadableTrustCache": {"Info": {"Path": "cache", "RestoreRequestRules": fallback}, "Trusted": True, "Digest": b"cache"},
            },
        }
        before = plistlib.dumps(identity)
        identifiers: dict[str, object] = {"ChipID": 1, "BoardId": 2, "Ap,ProductType": "iPhone18,1", "Ap,OSLongVersion": "27.0", "Ap,UnexpectedPrivateField": "must-not-forward"}
        request = personalization_request(identity, identifiers, 123, b"nonce")
        self.assertEqual(request["ApECID"], 123)
        self.assertEqual(request["ApNonce"], b"nonce")
        self.assertNotIn("Ap,UnexpectedPrivateField", request)
        image = request["PersonalizedDMG"]
        cache = request["LoadableTrustCache"]
        self.assertIsInstance(image, dict)
        self.assertIsInstance(cache, dict)
        if not isinstance(image, dict) or not isinstance(cache, dict):
            self.fail("SDK request did not preserve structured component tickets")
        self.assertIs(image["EPRO"], False)
        self.assertNotIn("ESEC", image)
        self.assertIs(cache["EPRO"], True)
        self.assertEqual(plistlib.dumps(identity), before)
        for bad in (42, "x" * 129, "bad\nvalue"):
            with self.assertRaises(DeveloperImageError):
                personalization_request(identity, {"ChipID": 1, "BoardId": 2, "Ap,ProductType": bad}, 123, b"nonce")

    def test_disallowed_signing_conditions_actions_and_ambiguous_board_are_refused(self) -> None:
        for rule in (
            {"Conditions": {"UnknownCondition": True}, "Actions": {"EPRO": True}},
            {"Conditions": {"ApRawProductionMode": "true"}, "Actions": {"EPRO": True}},
            {"Conditions": {"ApRawProductionMode": True}, "Actions": {"UnknownAction": True}},
            {"Conditions": {"ApRawProductionMode": True}, "Actions": {"ESEC": "255"}},
        ):
            identity: dict[str, object] = {
                "Manifest": {
                    "PersonalizedDMG": {"Trusted": True, "Info": {"RestoreRequestRules": [rule]}, "Digest": b"image"},
                    "LoadableTrustCache": {"Trusted": True, "Info": {}, "Digest": b"cache"},
                },
            }
            with self.assertRaises(DeveloperImageError):
                personalization_request(identity, {"ChipID": 1, "BoardId": 2}, 123, b"nonce")
        manifest = image_manifest("image.dmg", [])
        identities = manifest["BuildIdentities"]
        if not isinstance(identities, list):
            self.fail("Fixture identities must be a list")
        with self.assertRaises(DeveloperImageError):
            image_identity({**manifest, "BuildIdentities": identities + identities}, 1, 2)

    def test_installed_xcode_manifest_builds_exact_identity_request_without_network(self) -> None:
        path = Path("/Library/Developer/DeveloperDiskImages/iOS_DDI/Restore/BuildManifest.plist")
        if not path.is_file():
            self.skipTest("No installed personalized Xcode developer image manifest")
        original_digest = hashlib.sha256(path.read_bytes()).hexdigest()
        source = inspect_developer_image(path.parent)
        manifest = plist_record(path)
        values = manifest.get("BuildIdentities")
        if not isinstance(values, list):
            self.fail("Installed Xcode BuildIdentities must be a list")
        candidate = next((entry for entry in values if isinstance(entry, dict) and isinstance(entry.get("Manifest"), dict) and "PersonalizedDMG" in entry["Manifest"]), None)
        if not isinstance(candidate, dict):
            self.fail("Installed Xcode manifest has no PersonalizedDMG identity")
        chip = numeric_identifier(candidate.get("ApChipID"), "ApChipID")
        board = numeric_identifier(candidate.get("ApBoardID"), "ApBoardID")
        identity = image_identity(manifest, chip, board)
        payload = select_image_payload(source, source.product_types[0], "27.0", "Synthetic", chip, board)
        request = personalization_request(identity, {"ChipID": chip, "BoardId": board}, 123, bytes(range(32)))
        self.assertEqual(request["ApChipID"], chip)
        self.assertEqual(request["ApBoardID"], board)
        self.assertIs(request["@ApImg4Ticket"], True)
        self.assertIn("PersonalizedDMG", request)
        self.assertGreater(len(read_image_bytes(source.folder, payload.image, 134_217_728)), 0)
        self.assertGreater(len(read_image_bytes(source.folder, payload.signature_or_trust_cache, 16_777_216)), 0)
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), original_digest)


if __name__ == "__main__":
    unittest.main()
