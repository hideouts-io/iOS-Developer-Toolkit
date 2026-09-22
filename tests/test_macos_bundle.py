from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.verify_macos_bundle import (
    MACH_O_MAGICS,
    MacOSBundleValidationError,
    MachORecord,
    is_mach_o,
    parse_minimum_macos_versions,
    validate_mach_o_records,
    version_parts,
)


class MacOSBundleValidationTests(unittest.TestCase):
    def test_parses_modern_and_legacy_floors_for_every_architecture(self) -> None:
        output = """
Load command 9
      cmd LC_BUILD_VERSION
  cmdsize 32
 platform 1
    minos 11.0
      sdk 15.0
Load command 8
      cmd LC_VERSION_MIN_MACOSX
  cmdsize 16
  version 10.15
      sdk 14.4
"""

        self.assertEqual(parse_minimum_macos_versions(output), ("11.0", "10.15"))

    def test_rejects_newer_floor_or_missing_native_architecture(self) -> None:
        compatible = MachORecord(Path("compatible.dylib"), ("arm64", "x86_64"), ("12.0", "13.0"))
        validate_mach_o_records((compatible,), "arm64", "13.0")

        with self.assertRaisesRegex(MacOSBundleValidationError, "requires macOS 15.0"):
            validate_mach_o_records(
                (MachORecord(Path("newer.dylib"), ("arm64",), ("15.0",)),),
                "arm64",
                "13.0",
            )
        with self.assertRaisesRegex(MacOSBundleValidationError, "does not contain required architecture x86_64"):
            validate_mach_o_records((compatible,), "x86_64h", "13.0")

    def test_recognizes_mach_o_magic_without_treating_text_as_native_code(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / "binary"
            text = root / "text"
            binary.write_bytes(next(iter(MACH_O_MAGICS)) + b"payload")
            text.write_text("#!/bin/sh\n", encoding="utf-8")

            self.assertTrue(is_mach_o(binary))
            self.assertFalse(is_mach_o(text))

    def test_parses_and_compares_three_component_versions(self) -> None:
        self.assertEqual(version_parts("13"), (13, 0, 0))
        self.assertEqual(version_parts("13.0.1"), (13, 0, 1))
        with self.assertRaisesRegex(MacOSBundleValidationError, "Invalid macOS version"):
            version_parts("13.x")


if __name__ == "__main__":
    unittest.main()
