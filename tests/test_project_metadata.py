from __future__ import annotations

import plistlib
import tomllib
import unittest
from pathlib import Path

from ios_developer_toolkit import APP_VERSION


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class ProjectMetadataTests(unittest.TestCase):
    def test_source_and_packaging_metadata_match_application_version(self) -> None:
        pyproject = tomllib.loads((REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        project = pyproject["project"]
        self.assertIsInstance(project, dict)
        self.assertEqual(project["version"], APP_VERSION)

        with (REPOSITORY_ROOT / "macos" / "Info.plist").open("rb") as stream:
            plist = plistlib.load(stream)
        self.assertEqual(plist["CFBundleShortVersionString"], APP_VERSION)
        self.assertEqual(plist["CFBundleDisplayName"], "iOS Developer Toolkit")
        self.assertEqual(plist["CFBundleIdentifier"], "io.hideouts.ios-developer-toolkit")
        self.assertEqual(plist["CFBundleVersion"], "6")
        self.assertEqual(plist["LSMinimumSystemVersion"], "13.0")

        deployment_specification = (REPOSITORY_ROOT / "packaging" / "pysidedeploy.spec").read_text(encoding="utf-8")
        self.assertIn(f"--macos-app-version={APP_VERSION}", deployment_specification)
        self.assertIn("--macos-app-macos-min-version=13.0", deployment_specification)
        release_builder = (REPOSITORY_ROOT / "scripts" / "build_macos_release.sh").read_text(encoding="utf-8")
        self.assertIn("--macos-app-version=$release_version", release_builder)
        self.assertIn("CFBundleVersion string 6", release_builder)
        self.assertIn('compiled_minimum_macos_version', release_builder)
        self.assertIn('MACOSX_DEPLOYMENT_TARGET', release_builder)

    def test_dependency_notices_match_the_pinned_pymobiledevice3_release(self) -> None:
        pyproject = tomllib.loads((REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        project = pyproject["project"]
        self.assertIsInstance(project, dict)
        dependencies = project["dependencies"]
        self.assertIsInstance(dependencies, list)
        pinned_dependency = next(
            dependency for dependency in dependencies if isinstance(dependency, str) and dependency.startswith("pymobiledevice3==")
        )
        pinned_version = pinned_dependency.removeprefix("pymobiledevice3==")

        notices = (REPOSITORY_ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
        source_availability = (REPOSITORY_ROOT / "SOURCE_AVAILABILITY.md").read_text(encoding="utf-8")
        self.assertIn(f"| {pinned_version} |", notices)
        self.assertIn(f"tree/v{pinned_version}", notices)
        self.assertIn(f"tree/v{pinned_version}", source_availability)


if __name__ == "__main__":
    unittest.main()
