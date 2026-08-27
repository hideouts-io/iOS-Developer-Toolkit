from __future__ import annotations

import json
import unittest

from ios_developer_toolkit.capability_matrix import (
    CapabilityMatrixError,
    CapabilityResult,
    CapabilityWorkerCompleted,
    CapabilityWorkerStarted,
    CommandOutcome,
    capability_definitions,
    command_succeeded,
    compact_command_detail,
    lock_state_from_payload,
    mounted_image_summary,
    parse_capability_worker_event,
    untested_capability_results,
)


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


if __name__ == "__main__":
    unittest.main()
