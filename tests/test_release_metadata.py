from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
VERIFY_SCRIPT = REPOSITORY_ROOT / "scripts" / "verify_release_metadata.py"


def create_release_fixture(root: Path, release_version: str) -> tuple[Path, Path]:
    application_path = root / "iOS Developer Toolkit.app"
    resource_directory = application_path / "Contents" / "Resources"
    licenses_directory = resource_directory / "Licenses"
    package_directory = licenses_directory / "ThirdPartyPackages"
    package_directory.mkdir(parents=True)
    sbom = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "serialNumber": "urn:uuid:12345678-1234-1234-1234-123456789012",
        "metadata": {"component": {"name": "ios-developer-toolkit", "version": release_version}},
    }
    sbom_path = root / "release.cdx.json"
    serialized_sbom = json.dumps(sbom, sort_keys=True)
    sbom_path.write_text(serialized_sbom, encoding="utf-8")
    (resource_directory / "BOM.cdx.json").write_text(serialized_sbom, encoding="utf-8")
    (licenses_directory / "IOS_DEVELOPER_TOOLKIT_LICENSE.txt").write_text("MIT\n", encoding="utf-8")
    (licenses_directory / "THIRD_PARTY_NOTICES.md").write_text("pymobiledevice3\n", encoding="utf-8")
    (licenses_directory / "SOURCE_AVAILABILITY.md").write_text("pymobiledevice3\n", encoding="utf-8")
    (package_directory / "THIRD_PARTY_PACKAGES.md").write_text(
        "pymobiledevice3\nNuitka\n", encoding="utf-8"
    )
    return application_path, sbom_path


class ReleaseMetadataTests(unittest.TestCase):
    def run_verifier(self, application_path: Path, sbom_path: Path, release_version: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(VERIFY_SCRIPT), str(application_path), str(sbom_path), release_version],
            check=False,
            capture_output=True,
            text=True,
        )

    def test_accepts_matching_packaged_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            application_path, sbom_path = create_release_fixture(Path(temporary_directory), "0.3.1")
            result = self.run_verifier(application_path, sbom_path, "0.3.1")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Release metadata verification passed", result.stdout)

    def test_rejects_a_bundle_with_a_different_sbom(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            application_path, sbom_path = create_release_fixture(Path(temporary_directory), "0.3.1")
            (application_path / "Contents" / "Resources" / "BOM.cdx.json").write_text("{}", encoding="utf-8")
            result = self.run_verifier(application_path, sbom_path, "0.3.1")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("differs from Contents/Resources/BOM.cdx.json", result.stderr)

    def test_rejects_an_sbom_without_the_required_attestation_serial_number(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            application_path, sbom_path = create_release_fixture(Path(temporary_directory), "0.3.1")
            sbom = json.loads(sbom_path.read_text(encoding="utf-8"))
            del sbom["serialNumber"]
            serialized_sbom = json.dumps(sbom, sort_keys=True)
            sbom_path.write_text(serialized_sbom, encoding="utf-8")
            (application_path / "Contents" / "Resources" / "BOM.cdx.json").write_text(
                serialized_sbom,
                encoding="utf-8",
            )
            result = self.run_verifier(application_path, sbom_path, "0.3.1")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not define a CycloneDX serial number", result.stderr)


if __name__ == "__main__":
    unittest.main()
