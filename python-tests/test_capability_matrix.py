from __future__ import annotations

import json
import os
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
    capability_state_counts,
    command_succeeded,
    compact_command_detail,
    evaluate_preset_readiness,
    lock_state_from_payload,
    mounted_image_summary,
    parse_capability_worker_event,
    preset_capability_identifiers,
    result_for,
    untested_capability_results,
)
from ios_developer_toolkit.command_catalog import command_presets
from ios_developer_toolkit.device_compatibility import (
    CompatibilityReportEnvironment,
    DeviceCompatibilityError,
    append_observation,
    compatibility_report_mapping,
    compatibility_history_path,
    create_compatibility_report,
    create_observation,
    latest_observations,
    load_observations,
    render_compatibility_markdown,
    write_compatibility_json_report,
    write_compatibility_markdown_report,
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

    def test_counts_every_supported_capability_state_including_attention(self) -> None:
        results = (
            result_for("pymobiledevice3", "ready", "ready", "tested"),
            result_for("xcode-tools", "attention", "attention", "tested"),
        )

        counts = dict(capability_state_counts(results))

        self.assertEqual(counts["ready"], 1)
        self.assertEqual(counts["attention"], 1)
        self.assertEqual(set(counts), {"ready", "attention", "unavailable", "blocked", "not-tested", "not-applicable"})

    def test_evaluates_command_specific_readiness_without_mutating_matrix_results(self) -> None:
        dvt = next(preset for preset in command_presets() if preset.identifier == "dvt-device")
        initial = {result.identifier: result for result in untested_capability_results()}
        self.assertEqual(
            preset_capability_identifiers(dvt),
            ("device-connection", "pairing-trust", "developer-mode", "developer-image", "rsd-tunnel", "dvt"),
        )
        self.assertEqual(evaluate_preset_readiness(dvt, initial).state, "not-tested")

        ready = dict(initial)
        for identifier in preset_capability_identifiers(dvt):
            state = "not-applicable" if identifier == "rsd-tunnel" else "ready"
            ready[identifier] = result_for(identifier, state, "tested", "evidence")
        self.assertEqual(evaluate_preset_readiness(dvt, ready).state, "ready")

        attention = dict(ready)
        attention["developer-mode"] = result_for("developer-mode", "attention", "disabled", "tested")
        evaluation = evaluate_preset_readiness(dvt, attention)
        self.assertEqual(evaluation.state, "needs-attention")
        self.assertIn("Enable Settings", evaluation.remediation[0])
        self.assertTrue(all(result.state == "not-tested" for result in initial.values()))

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

    def test_sanitized_compatibility_report_omits_stable_identity_and_private_paths(self) -> None:
        device = IOSDevice("PRIVATE-UDID", "Private iPhone", "iPhone14,5", "26.3.1", "23D123", "USB")
        result = CapabilityResult(
            "developer-image",
            "Developer",
            "Developer image",
            "ready",
            "Ready for PRIVATE-UDID",
            "Mounted from /Users/julian/Private/DDI for analyst@example.com",
            "No action required",
        )
        observation = create_observation("2026-09-14T12:00:00+00:00", device, (result,))
        environment = CompatibilityReportEnvironment(
            "0.3.4",
            "15.6.1",
            "arm64",
            "3.13.7",
            "source-python",
            "11.15.1",
            "6.9.3",
        )
        report = create_compatibility_report(
            "2026-09-22T12:00:00+00:00",
            environment,
            (observation,),
        )

        payload = json.dumps(compatibility_report_mapping(report), sort_keys=True)
        markdown = render_compatibility_markdown(report)

        self.assertNotIn("PRIVATE-UDID", payload)
        self.assertNotIn("Private iPhone", payload)
        self.assertNotIn(observation.device_fingerprint, payload)
        self.assertNotIn("/Users/julian", payload)
        self.assertNotIn("analyst@example.com", payload)
        self.assertIn("<local-path>", payload)
        self.assertIn("<email-address>", payload)
        self.assertIn("iPhone14,5", markdown)
        self.assertIn("pymobiledevice3", markdown)

    def test_compatibility_reports_are_owner_only_and_refuse_overwrite(self) -> None:
        temporary_directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, temporary_directory)
        device = IOSDevice("DEVICE", "One", "iPad14,3", "26.3.1", "23D123", "USB")
        observation = create_observation(
            "2026-09-14T12:00:00+00:00",
            device,
            untested_capability_results(),
        )
        environment = CompatibilityReportEnvironment(
            "0.3.4",
            "15.6.1",
            "arm64",
            "3.13.7",
            "frozen-app",
            "11.15.1",
            "6.9.3",
        )
        report = create_compatibility_report(
            "2026-09-22T12:00:00+00:00",
            environment,
            (observation,),
        )
        json_path = write_compatibility_json_report(temporary_directory / "compatibility.json", report)
        markdown_path = write_compatibility_markdown_report(temporary_directory / "compatibility.md", report)

        self.assertEqual(os.stat(json_path).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(markdown_path).st_mode & 0o777, 0o600)
        self.assertEqual(json.loads(json_path.read_text(encoding="utf-8"))["schema_version"], 1)
        self.assertIn("## Observed device 1", markdown_path.read_text(encoding="utf-8"))
        with self.assertRaises(DeviceCompatibilityError):
            write_compatibility_json_report(json_path, report)

    def test_compatibility_report_requires_completed_observations(self) -> None:
        environment = CompatibilityReportEnvironment(
            "0.3.4",
            "15.6.1",
            "arm64",
            "3.13.7",
            "source-python",
            "11.15.1",
            "6.9.3",
        )
        with self.assertRaises(DeviceCompatibilityError):
            create_compatibility_report("2026-09-22T12:00:00+00:00", environment, ())

    @unittest.skipIf(shutil.which("xcrun") is None, "xcrun is unavailable")
    def test_xcode_tool_probe_uses_the_executable_command_wrapper(self) -> None:
        result = _probe_xcode_tools()

        self.assertEqual(result.identifier, "xcode-tools")
        self.assertIn(result.state, ("ready", "attention"))


if __name__ == "__main__":
    unittest.main()
