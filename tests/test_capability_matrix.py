from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from ios_developer_toolkit.capability_matrix import (
    CapabilityMatrixError,
    CapabilityResult,
    CapabilityWorkerCompleted,
    CapabilityWorkerStarted,
    CommandOutcome,
    _probe_xcode_tools,
    capability_definitions,
    command_succeeded,
    compact_command_detail,
    lock_state_from_payload,
    mounted_image_summary,
    parse_capability_worker_event,
    untested_capability_results,
)
from ios_developer_toolkit.device_compatibility import (
    DeviceCompatibilityError,
    append_observation,
    compatibility_history_path,
    create_observation,
    latest_observations,
    load_observations,
)
from ios_developer_toolkit.models import IOSDevice


class CapabilityMatrixTests(unittest.TestCase):
    def test_catalog_and_initial_results_are_complete_and_unique(self) -> None:
        definitions = capability_definitions()
        results = untested_capability_results()
        self.assertEqual(len(definitions), len(results))
        self.assertEqual(len({item.identifier for item in definitions}), len(definitions))
        self.assertEqual(
            {item.identifier for item in definitions},
            {item.identifier for item in results},
        )
        self.assertTrue(all(item.state == "not-tested" for item in results))

    def test_parses_strict_worker_events(self) -> None:
        started = parse_capability_worker_event('{"event":"started","total":11}')
        self.assertEqual(started, CapabilityWorkerStarted(total=11))
        result = untested_capability_results()[0]
        result_event = parse_capability_worker_event(
            json.dumps({"event": "result", "result": result.to_mapping()})
        )
        self.assertIsInstance(result_event, CapabilityResult)
        self.assertEqual(result_event, result)
        self.assertEqual(parse_capability_worker_event('{"event":"completed"}'), CapabilityWorkerCompleted())
        with self.assertRaises(CapabilityMatrixError):
            parse_capability_worker_event('{"event":"started","total":true}')
        with self.assertRaises(CapabilityMatrixError):
            parse_capability_worker_event('{"event":"unknown"}')

    def test_interprets_mounted_images_and_lock_state_without_mutating_payloads(self) -> None:
        mounted_payload = [{"PersonalizedImageType": "DeveloperDiskImage"}]
        mounted, evidence = mounted_image_summary(mounted_payload)
        self.assertTrue(mounted)
        self.assertIn("DeveloperDiskImage", evidence)
        self.assertEqual(mounted_payload, [{"PersonalizedImageType": "DeveloperDiskImage"}])
        self.assertEqual(mounted_image_summary([]), (False, "The image mounter returned an empty mounted-image list."))
        unrelated, unrelated_evidence = mounted_image_summary([{"ImageType": "Cryptex1"}])
        self.assertFalse(unrelated)
        self.assertIn("No record was identifiable", unrelated_evidence)
        self.assertEqual(lock_state_from_payload({"result": {"lockState": "unlocked"}}), "unlocked")
        self.assertEqual(lock_state_from_payload({"deviceIsLocked": True}), "locked")
        self.assertIsNone(lock_state_from_payload({"state": "unknown"}))

    def test_command_outcome_detects_semantic_failure_and_redacts_identifier(self) -> None:
        success = CommandOutcome(("lockdown", "info"), 0, '{"DeviceName":"Test"}', "", False)
        semantic_failure = CommandOutcome(
            ("developer", "dvt", "device-information"),
            0,
            "",
            "2026-08-27 worker[123] ERROR Device not found: PRIVATE-UDID",
            False,
        )
        self.assertTrue(command_succeeded(success))
        self.assertFalse(command_succeeded(semantic_failure))
        detail = compact_command_detail(semantic_failure, "PRIVATE-UDID")
        self.assertNotIn("PRIVATE-UDID", detail)
        self.assertIn("<selected-device>", detail)

    def test_real_device_observations_are_local_and_keep_only_a_fingerprint(self) -> None:
        temporary_directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, temporary_directory)
        history_path = compatibility_history_path(temporary_directory)
        device = IOSDevice("PRIVATE-UDID", "Private iPhone", "iPhone14,5", "26.3.1", "23D123", "USB")
        result = CapabilityResult(
            "device-connection",
            "Connection",
            "Selected device",
            "ready",
            "Connected PRIVATE-UDID",
            "Observed PRIVATE-UDID over USB",
            "No action required",
        )
        observation = create_observation("2026-09-14T12:00:00+00:00", device, (result,))
        append_observation(history_path, observation)
        persisted = history_path.read_text(encoding="utf-8")
        self.assertNotIn("PRIVATE-UDID", persisted)
        self.assertNotIn("Private iPhone", persisted)
        self.assertEqual(load_observations(history_path), (observation,))

    def test_latest_real_device_observation_wins_without_mixing_devices(self) -> None:
        device_one = IOSDevice("DEVICE-ONE", "One", "iPhone14,5", "26.3.1", "23D123", "USB")
        device_two = IOSDevice("DEVICE-TWO", "Two", "iPad14,3", "26.3.1", "23D123", "USB")
        first = create_observation("2026-09-14T10:00:00+00:00", device_one, untested_capability_results())
        newer = create_observation("2026-09-14T11:00:00+00:00", device_one, untested_capability_results())
        other = create_observation("2026-09-14T09:00:00+00:00", device_two, untested_capability_results())
        self.assertEqual(latest_observations((first, newer, other)), (other, newer))

    def test_real_device_observation_rejects_duplicate_capabilities(self) -> None:
        device = IOSDevice("DEVICE", "One", "iPhone14,5", "26.3.1", "23D123", "USB")
        with self.assertRaises(DeviceCompatibilityError):
            create_observation("2026-09-14T10:00:00+00:00", device, (untested_capability_results()[0],) * 2)

    @unittest.skipIf(shutil.which("xcrun") is None, "xcrun is unavailable")
    def test_xcode_tool_probe_uses_the_executable_command_wrapper(self) -> None:
        result = _probe_xcode_tools()

        self.assertEqual(result.identifier, "xcode-tools")
        self.assertIn(result.state, ("ready", "attention"))


if __name__ == "__main__":
    unittest.main()
