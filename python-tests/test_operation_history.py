from __future__ import annotations

import json
import shutil
import stat
import tempfile
import unittest
from pathlib import Path

from ios_developer_toolkit.operation_history import (
    OperationHistoryError,
    append_operation_record,
    operation_context,
    operation_manifest,
    operation_record,
    write_operation_manifest,
)
from ios_developer_toolkit.qt_process import OperationResult


class OperationHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = operation_context(
            "Inspect device",
            "Command Center",
            "Selected iPhone",
            "pymobiledevice3",
            ("device:selected", "trust:ready"),
            ("/tmp/output.txt",),
        )
        self.result = OperationResult(
            ("/tool/pymobiledevice3", "lockdown", "info", "--udid", "PRIVATE-DEVICE-ID"),
            "succeeded",
            "2026-09-22T12:00:00+00:00",
            "2026-09-22T12:00:01.250000+00:00",
            0,
            None,
            b"private stdout",
            b"diagnostic stderr",
        )

    def test_builds_typed_record_with_timing_and_output_digests(self) -> None:
        record = operation_record(self.context, self.result)

        self.assertEqual(record.duration_milliseconds, 1250)
        self.assertEqual(record.stdout_bytes, len(b"private stdout"))
        self.assertEqual(len(record.stdout_sha256), 64)
        self.assertEqual(record.argv[-1], "PRIVATE-DEVICE-ID")
        self.assertEqual(record.output_paths, (str(Path("/tmp/output.txt").resolve()),))

    def test_manifest_omits_raw_output_but_retains_exact_argument_vector(self) -> None:
        manifest = operation_manifest(operation_record(self.context, self.result))
        payload = json.dumps(manifest)

        self.assertNotIn("private stdout", payload)
        self.assertNotIn("diagnostic stderr", payload)
        self.assertIn("PRIVATE-DEVICE-ID", payload)
        self.assertFalse(manifest["captured_output"]["raw_output_included"])

    def test_append_is_bounded_and_rejects_duplicate_records(self) -> None:
        first = operation_record(self.context, self.result)
        second_result = OperationResult(
            self.result.argv,
            "failed",
            "2026-09-22T12:01:00+00:00",
            "2026-09-22T12:01:02+00:00",
            1,
            "failed",
            b"",
            b"failed",
        )
        second = operation_record(self.context, second_result)

        self.assertEqual(append_operation_record((first,), second, 1), (second,))
        with self.assertRaises(OperationHistoryError):
            append_operation_record((first,), first, 10)

    def test_writes_private_manifest_without_overwriting(self) -> None:
        temporary_directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, temporary_directory)
        destination = temporary_directory / "operation.json"
        record = operation_record(self.context, self.result)

        self.assertEqual(write_operation_manifest(destination, record), destination.resolve())
        self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o600)
        with self.assertRaises(OperationHistoryError):
            write_operation_manifest(destination, record)


if __name__ == "__main__":
    unittest.main()
