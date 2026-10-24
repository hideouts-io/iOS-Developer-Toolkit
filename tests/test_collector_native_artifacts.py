from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from threading import Event

from ios_developer_toolkit.apple_tools import NativeToolOperation
from ios_developer_toolkit.collector import run_native_artifact
from ios_developer_toolkit.runtime import ExecutableCommand


class NativeCollectionArtifactTests(unittest.TestCase):
    def test_real_process_result_requires_expected_bundle_type(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = Path(directory)
            trace = case / "logging.trace"
            operation = NativeToolOperation(
                "native-test", "Native artifact contract", ExecutableCommand(Path("/usr/bin/touch"), ()),
                (str(trace),), "controlled-filesystem", "host-write", 5, (trace,),
            )
            result = run_native_artifact(operation, case, Event(), "controlled-filesystem")
            self.assertEqual(result.status, "failed")
            self.assertEqual(result.exit_code, 70)

    def test_real_process_preserves_completed_bundle_and_raw_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = Path(directory)
            archive = case / "device.logarchive"
            operation = NativeToolOperation(
                "native-test", "Native artifact contract", ExecutableCommand(Path("/bin/mkdir"), ()),
                (str(archive),), "controlled-filesystem", "host-write", 5, (archive,),
            )
            result = run_native_artifact(operation, case, Event(), "controlled-filesystem")
            self.assertEqual(result.status, "completed")
            self.assertTrue(archive.is_dir())
            self.assertTrue((case / result.output_path).is_file())

    def test_successful_exit_without_expected_artifact_is_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = Path(directory)
            output = case / "missing.xml"
            operation = NativeToolOperation(
                "native-test", "Native artifact contract", ExecutableCommand(Path("/usr/bin/true"), ()),
                (), "controlled-filesystem", "host-write", 5, (output,),
            )
            result = run_native_artifact(operation, case, Event(), "controlled-filesystem")
            self.assertEqual(result.status, "failed")
            self.assertEqual(result.exit_code, 70)
