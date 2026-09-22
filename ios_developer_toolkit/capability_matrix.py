from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Literal, Mapping

from ios_developer_toolkit.command_catalog import CommandPreset
from ios_developer_toolkit.models import IOSDevice
from ios_developer_toolkit.runtime import ExecutableCommand, command_argv, command_text, device_environment
from ios_developer_toolkit.validation import output_indicates_failure


CapabilityState = Literal["ready", "attention", "unavailable", "blocked", "not-tested", "not-applicable"]
CAPABILITY_STATES: tuple[CapabilityState, ...] = (
    "ready",
    "attention",
    "unavailable",
    "blocked",
    "not-tested",
    "not-applicable",
)


class CapabilityMatrixError(ValueError):
    """Raised when a capability probe or worker response is malformed."""


@dataclass(frozen=True)
class CapabilityDefinition:
    identifier: str
    layer: str
    title: str
    remediation: str


@dataclass(frozen=True)
class CapabilityResult:
    identifier: str
    layer: str
    title: str
    state: CapabilityState
    summary: str
    evidence: str
    remediation: str

    def to_mapping(self) -> Mapping[str, object]:
        return {
            "identifier": self.identifier,
            "layer": self.layer,
            "title": self.title,
            "state": self.state,
            "summary": self.summary,
            "evidence": self.evidence,
            "remediation": self.remediation,
        }


@dataclass(frozen=True)
class CapabilityWorkerStarted:
    total: int


@dataclass(frozen=True)
class CapabilityWorkerCompleted:
    pass


CapabilityWorkerEvent = CapabilityWorkerStarted | CapabilityResult | CapabilityWorkerCompleted


PresetReadinessState = Literal["ready", "not-tested", "needs-attention"]


@dataclass(frozen=True)
class PresetReadiness:
    state: PresetReadinessState
    summary: str
    remediation: tuple[str, ...]


@dataclass(frozen=True)
class CommandOutcome:
    arguments: tuple[str, ...]
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool


def capability_definitions() -> tuple[CapabilityDefinition, ...]:
    return (
        CapabilityDefinition(
            "pymobiledevice3",
            "Host",
            "pymobiledevice3 runtime",
            "Repair the project virtual environment, then relaunch the toolkit.",
        ),
        CapabilityDefinition(
            "xcode-tools",
            "Host",
            "Apple developer tools",
            "Install Xcode and select it with xcode-select. The pymobiledevice3-only workflows remain separate.",
        ),
        CapabilityDefinition(
            "device-connection",
            "Connection",
            "Selected device",
            "Connect the intended device, unlock it, tap Trust, and refresh device discovery.",
        ),
        CapabilityDefinition(
            "pairing-trust",
            "Connection",
            "Pairing and Lockdown",
            "Unlock the device, accept the Trust prompt, reconnect USB, and refresh the matrix.",
        ),
        CapabilityDefinition(
            "developer-mode",
            "Developer readiness",
            "Developer Mode",
            "Enable Settings > Privacy & Security > Developer Mode, restart, confirm on-device, and reconnect.",
        ),
        CapabilityDefinition(
            "developer-image",
            "Developer readiness",
            "Developer Disk Image",
            "Open Device & DDI and mount the compatible personalized image before using developer services.",
        ),
        CapabilityDefinition(
            "rsd-tunnel",
            "Developer readiness",
            "iOS 17+ RSD tunnel path",
            "Start or repair the pymobiledevice3 tunnel path, then retry a CoreDevice request.",
        ),
        CapabilityDefinition(
            "coredevice",
            "Developer services",
            "CoreDevice service",
            "Confirm Developer Mode, the mounted DDI, and the iOS 17+ tunnel path.",
        ),
        CapabilityDefinition(
            "device-lockstate",
            "Developer services",
            "Lock-state service",
            "Unlock the device and keep it awake while performing developer operations.",
        ),
        CapabilityDefinition(
            "dvt",
            "Developer services",
            "DVT instrumentation",
            "Confirm Developer Mode and the mounted DDI; on iOS 17+, also confirm the tunnel path.",
        ),
        CapabilityDefinition(
            "webinspector",
            "Optional services",
            "Safari Web Inspector",
            "Enable Settings > Apps > Safari > Advanced > Web Inspector, then retry with Safari open.",
        ),
    )


def definition_by_identifier(identifier: str) -> CapabilityDefinition:
    matches = tuple(item for item in capability_definitions() if item.identifier == identifier)
    if len(matches) != 1:
        raise CapabilityMatrixError(f"Expected one capability definition for {identifier!r}, found {len(matches)}")
    return matches[0]


def capability_state_label(state: CapabilityState) -> str:
    labels: Mapping[CapabilityState, str] = {
        "ready": "Ready",
        "attention": "Needs attention",
        "unavailable": "Unavailable",
        "blocked": "Blocked",
        "not-tested": "Not tested",
        "not-applicable": "Not applicable",
    }
    return labels[state]


def untested_capability_results() -> tuple[CapabilityResult, ...]:
    return tuple(
        CapabilityResult(
            definition.identifier,
            definition.layer,
            definition.title,
            "not-tested",
            "Run the matrix to test this capability.",
            "No probe has run for the selected device.",
            definition.remediation,
        )
        for definition in capability_definitions()
    )


def capability_state_counts(results: Iterable[CapabilityResult]) -> tuple[tuple[CapabilityState, int], ...]:
    materialized = tuple(results)
    return tuple((state, sum(result.state == state for result in materialized)) for state in CAPABILITY_STATES)


def preset_capability_identifiers(preset: CommandPreset) -> tuple[str, ...]:
    identifiers: list[str] = []
    if preset.requires_device:
        identifiers.extend(("device-connection", "pairing-trust"))
    if preset.requires_developer_services:
        identifiers.extend(("developer-mode", "developer-image", "rsd-tunnel"))
        if preset.argument_template[:2] == ("developer", "core-device"):
            identifiers.append("coredevice")
        else:
            identifiers.append("dvt")
    if preset.argument_template and preset.argument_template[0] == "webinspector":
        identifiers.append("webinspector")
    return tuple(dict.fromkeys(identifiers))


def evaluate_preset_readiness(
    preset: CommandPreset,
    results: Mapping[str, CapabilityResult],
) -> PresetReadiness:
    identifiers = preset_capability_identifiers(preset)
    if not identifiers:
        return PresetReadiness("ready", "No device capability check is required for this preset.", ())
    missing = tuple(identifier for identifier in identifiers if identifier not in results)
    if missing:
        raise CapabilityMatrixError(f"Capability results are missing required identifiers: {', '.join(missing)}")
    required = tuple(results[identifier] for identifier in identifiers)
    not_tested = tuple(result for result in required if result.state == "not-tested")
    if not_tested:
        titles = ", ".join(result.title for result in not_tested)
        return PresetReadiness(
            "not-tested",
            f"Readiness not checked for: {titles}.",
            ("Run the one-click Device Readiness Check before this command.",),
        )
    attention = tuple(result for result in required if result.state in ("attention", "unavailable", "blocked"))
    if attention:
        summary = "; ".join(
            f"{result.title}: {capability_state_label(result.state)}" for result in attention
        )
        return PresetReadiness(
            "needs-attention",
            summary,
            tuple(dict.fromkeys(result.remediation for result in attention)),
        )
    return PresetReadiness(
        "ready",
        "Every tested requirement is ready or not applicable for the selected device.",
        (),
    )


def result_for(
    identifier: str,
    state: CapabilityState,
    summary: str,
    evidence: str,
) -> CapabilityResult:
    definition = definition_by_identifier(identifier)
    return CapabilityResult(
        definition.identifier,
        definition.layer,
        definition.title,
        state,
        summary,
        evidence,
        definition.remediation,
    )


def _required_string(record: Mapping[str, object], key: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value:
        raise CapabilityMatrixError(f"Capability result is missing required string field: {key}")
    return value


def parse_capability_result(record: Mapping[str, object]) -> CapabilityResult:
    state_value = _required_string(record, "state")
    if state_value not in CAPABILITY_STATES:
        raise CapabilityMatrixError(f"Capability result has unsupported state: {state_value}")
    state: CapabilityState = state_value
    result = CapabilityResult(
        _required_string(record, "identifier"),
        _required_string(record, "layer"),
        _required_string(record, "title"),
        state,
        _required_string(record, "summary"),
        _required_string(record, "evidence"),
        _required_string(record, "remediation"),
    )
    definition = definition_by_identifier(result.identifier)
    if result.layer != definition.layer or result.title != definition.title:
        raise CapabilityMatrixError(f"Capability result metadata does not match the catalog: {result.identifier}")
    return result


def parse_capability_worker_event(payload: str) -> CapabilityWorkerEvent:
    try:
        parsed: object = json.loads(payload)
    except json.JSONDecodeError as error:
        raise CapabilityMatrixError(f"Capability worker emitted invalid JSON: {error}") from error
    if not isinstance(parsed, dict):
        raise CapabilityMatrixError("Capability worker event must be a JSON object")
    event = parsed.get("event")
    if event == "started":
        total = parsed.get("total")
        if not isinstance(total, int) or isinstance(total, bool) or total <= 0:
            raise CapabilityMatrixError("Capability worker started event has an invalid total")
        return CapabilityWorkerStarted(total)
    if event == "result":
        result = parsed.get("result")
        if not isinstance(result, dict):
            raise CapabilityMatrixError("Capability worker result event is missing a result object")
        return parse_capability_result(result)
    if event == "completed":
        return CapabilityWorkerCompleted()
    raise CapabilityMatrixError(f"Capability worker emitted an unsupported event: {event!r}")


def _decode_timeout_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def run_command(
    program: ExecutableCommand,
    arguments: tuple[str, ...],
    environment: Mapping[str, str],
    timeout_seconds: int,
) -> CommandOutcome:
    try:
        completed = subprocess.run(
            command_argv(program, arguments),
            env=dict(environment),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as error:
        return CommandOutcome(
            arguments,
            None,
            _decode_timeout_output(error.stdout),
            _decode_timeout_output(error.stderr),
            True,
        )
    except OSError as error:
        raise CapabilityMatrixError(f"Could not execute {program}: {error}") from error
    return CommandOutcome(arguments, completed.returncode, completed.stdout, completed.stderr, False)


def command_succeeded(outcome: CommandOutcome) -> bool:
    combined = f"{outcome.stdout}\n{outcome.stderr}".encode("utf-8", errors="replace")
    return outcome.exit_code == 0 and not outcome.timed_out and not output_indicates_failure(combined)


def compact_command_detail(outcome: CommandOutcome, device_identifier: str) -> str:
    if outcome.timed_out:
        return f"Timed out while running: pymobiledevice3 {' '.join(outcome.arguments)}"
    combined = " ".join(f"{outcome.stdout}\n{outcome.stderr}".split())
    redacted = combined.replace(device_identifier, "<selected-device>")
    if not redacted:
        redacted = f"Command exited with code {outcome.exit_code} without diagnostic output."
    return redacted[:500]


def _json_output(outcome: CommandOutcome) -> object:
    try:
        return json.loads(outcome.stdout)
    except json.JSONDecodeError as error:
        raise CapabilityMatrixError(f"Command returned malformed JSON: {error}") from error


def parse_ios_major(product_version: str) -> int | None:
    match = re.match(r"\s*(\d+)", product_version)
    return int(match.group(1)) if match is not None else None


def mounted_image_summary(payload: object) -> tuple[bool, str]:
    if not isinstance(payload, list):
        raise CapabilityMatrixError("Mounted image response must be a JSON array")
    if not payload:
        return False, "The image mounter returned an empty mounted-image list."
    image_types: list[str] = []
    developer_image_found = False
    for item in payload:
        if not isinstance(item, dict):
            raise CapabilityMatrixError("Mounted image entries must be JSON objects")
        candidate = item.get("PersonalizedImageType", item.get("ImageType", "Developer image"))
        image_type = candidate if isinstance(candidate, str) and candidate else "Unknown image type"
        image_types.append(image_type)
        normalized = image_type.casefold()
        if "developer" in normalized or normalized == "ddi" or normalized.endswith(".ddi"):
            developer_image_found = True
    unique_types = tuple(dict.fromkeys(image_types))
    evidence = f"Mounted image records: {', '.join(unique_types)}."
    if not developer_image_found:
        evidence += " No record was identifiable as a Developer Disk Image."
    return developer_image_found, evidence


def lock_state_from_payload(payload: object) -> str | None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            normalized_key = str(key).replace("_", "").replace("-", "").casefold()
            if normalized_key in ("locked", "islocked", "deviceislocked") and isinstance(value, bool):
                return "locked" if value else "unlocked"
            if "lockstate" in normalized_key and isinstance(value, str):
                normalized_value = value.casefold()
                if normalized_value in ("locked", "unlocked"):
                    return normalized_value
        for value in payload.values():
            nested = lock_state_from_payload(value)
            if nested is not None:
                return nested
    if isinstance(payload, list):
        for value in payload:
            nested = lock_state_from_payload(value)
            if nested is not None:
                return nested
    return None


def unavailable_from_outcome(identifier: str, outcome: CommandOutcome, device_identifier: str) -> CapabilityResult:
    return result_for(
        identifier,
        "attention",
        "The capability probe did not complete successfully.",
        compact_command_detail(outcome, device_identifier),
    )


def _probe_xcode_tools() -> CapabilityResult:
    xcrun = shutil.which("xcrun")
    if xcrun is None:
        return result_for(
            "xcode-tools",
            "unavailable",
            "Xcode command-line tools were not found.",
            "xcrun is not available on PATH.",
        )
    environment = dict(os.environ)
    resolved: list[str] = []
    missing: list[str] = []
    for tool in ("devicectl", "xctrace"):
        outcome = run_command(ExecutableCommand(Path(xcrun), ()), ("--find", tool), environment, 5)
        if command_succeeded(outcome) and outcome.stdout.strip():
            resolved.append(tool)
        else:
            missing.append(tool)
    if missing:
        return result_for(
            "xcode-tools",
            "attention",
            "Some Apple developer tools are unavailable.",
            f"Found: {', '.join(resolved) or 'none'}; missing: {', '.join(missing)}.",
        )
    return result_for(
        "xcode-tools",
        "ready",
        "Apple's device and Instruments command-line tools are available.",
        "xcrun resolved devicectl and xctrace.",
    )


def probe_capabilities(pymobiledevice3: ExecutableCommand, device: IOSDevice) -> Iterable[CapabilityResult]:
    environment = device_environment(device.identifier)
    version_outcome = run_command(pymobiledevice3, ("version",), dict(os.environ), 8)
    if command_succeeded(version_outcome) and version_outcome.stdout.strip():
        yield result_for(
            "pymobiledevice3",
            "ready",
            f"pymobiledevice3 {version_outcome.stdout.strip()} is executable.",
            command_text(pymobiledevice3, ()),
        )
    else:
        yield unavailable_from_outcome("pymobiledevice3", version_outcome, device.identifier)

    yield _probe_xcode_tools()
    yield result_for(
        "device-connection",
        "ready",
        f"{device.name} is selected over {device.connection_type}.",
        f"{device.product_type}; iOS {device.product_version}; build {device.build_version}.",
    )

    lockdown_outcome = run_command(pymobiledevice3, ("lockdown", "info"), environment, 10)
    if command_succeeded(lockdown_outcome):
        yield result_for(
            "pairing-trust",
            "ready",
            "Lockdown returned the selected device's information record.",
            "The current pairing record and trusted connection were accepted.",
        )
    else:
        yield unavailable_from_outcome("pairing-trust", lockdown_outcome, device.identifier)

    developer_mode_outcome = run_command(
        pymobiledevice3,
        ("mounter", "query-developer-mode-status"),
        environment,
        10,
    )
    if command_succeeded(developer_mode_outcome):
        try:
            developer_mode = _json_output(developer_mode_outcome)
        except CapabilityMatrixError as error:
            yield result_for("developer-mode", "attention", "Developer Mode returned an unreadable response.", str(error))
        else:
            if developer_mode is True:
                yield result_for("developer-mode", "ready", "Developer Mode is enabled.", "The image mounter returned true.")
            elif developer_mode is False:
                yield result_for("developer-mode", "attention", "Developer Mode is disabled.", "The image mounter returned false.")
            else:
                yield result_for(
                    "developer-mode",
                    "attention",
                    "Developer Mode returned an unexpected value.",
                    json.dumps(developer_mode, ensure_ascii=False)[:500],
                )
    else:
        yield unavailable_from_outcome("developer-mode", developer_mode_outcome, device.identifier)

    mounted_outcome = run_command(pymobiledevice3, ("mounter", "list"), environment, 10)
    image_is_mounted = False
    if command_succeeded(mounted_outcome):
        try:
            image_is_mounted, image_evidence = mounted_image_summary(_json_output(mounted_outcome))
        except CapabilityMatrixError as error:
            yield result_for("developer-image", "attention", "Mounted-image state was unreadable.", str(error))
        else:
            yield result_for(
                "developer-image",
                "ready" if image_is_mounted else "attention",
                "A developer image is mounted." if image_is_mounted else "No mounted developer image was reported.",
                image_evidence,
            )
    else:
        yield unavailable_from_outcome("developer-image", mounted_outcome, device.identifier)

    ios_major = parse_ios_major(device.product_version)
    coredevice_ready = False
    if ios_major is not None and ios_major < 17:
        yield result_for(
            "rsd-tunnel",
            "not-applicable",
            "The RSD tunnel path is not required for this iOS version.",
            f"Selected device reports iOS {device.product_version}.",
        )
        yield result_for(
            "coredevice",
            "not-applicable",
            "CoreDevice is an iOS 17+ developer-service path.",
            f"Selected device reports iOS {device.product_version}.",
        )
    elif not image_is_mounted:
        yield result_for(
            "rsd-tunnel",
            "blocked",
            "Tunnel readiness was not inferred because the DDI is not ready.",
            "CoreDevice was not contacted after the DDI prerequisite failed.",
        )
        yield result_for(
            "coredevice",
            "blocked",
            "CoreDevice was not tested because its DDI prerequisite is not ready.",
            "Mount a compatible developer image, then refresh the matrix.",
        )
    else:
        coredevice_outcome = run_command(
            pymobiledevice3,
            ("developer", "core-device", "get-device-info"),
            environment,
            15,
        )
        coredevice_ready = command_succeeded(coredevice_outcome)
        if coredevice_ready:
            yield result_for(
                "rsd-tunnel",
                "ready",
                "The iOS 17+ RSD/tunnel route reached CoreDevice.",
                "A CoreDevice information request completed through the selected device path.",
            )
            yield result_for(
                "coredevice",
                "ready",
                "CoreDevice information is available.",
                "com.apple.coredevice.deviceinfo responded successfully.",
            )
        else:
            failure_detail = compact_command_detail(coredevice_outcome, device.identifier)
            yield result_for(
                "rsd-tunnel",
                "attention",
                "The RSD/tunnel route did not complete a CoreDevice request.",
                failure_detail,
            )
            yield result_for(
                "coredevice",
                "attention",
                "CoreDevice did not respond successfully.",
                failure_detail,
            )

    if coredevice_ready:
        lock_outcome = run_command(
            pymobiledevice3,
            ("developer", "core-device", "get-lockstate"),
            environment,
            10,
        )
        if command_succeeded(lock_outcome):
            try:
                lock_payload = _json_output(lock_outcome)
            except CapabilityMatrixError as error:
                yield result_for("device-lockstate", "attention", "The lock-state response was unreadable.", str(error))
            else:
                lock_state = lock_state_from_payload(lock_payload)
                if lock_state == "locked":
                    yield result_for(
                        "device-lockstate",
                        "attention",
                        "The device reports a locked state.",
                        "The CoreDevice lock-state service responded successfully.",
                    )
                elif lock_state == "unlocked":
                    yield result_for(
                        "device-lockstate",
                        "ready",
                        "The device reports an unlocked state.",
                        "The CoreDevice lock-state service responded successfully.",
                    )
                else:
                    yield result_for(
                        "device-lockstate",
                        "ready",
                        "The CoreDevice lock-state service is reachable.",
                        json.dumps(lock_payload, ensure_ascii=False)[:500],
                    )
        else:
            yield unavailable_from_outcome("device-lockstate", lock_outcome, device.identifier)
    else:
        yield result_for(
            "device-lockstate",
            "blocked" if ios_major is None or ios_major >= 17 else "not-applicable",
            "Lock-state service was not tested through CoreDevice.",
            "CoreDevice must be reachable before this service can be queried.",
        )

    if not image_is_mounted:
        yield result_for(
            "dvt",
            "blocked",
            "DVT was not tested because its DDI prerequisite is not ready.",
            "Mount a compatible developer image, then refresh the matrix.",
        )
    else:
        dvt_outcome = run_command(
            pymobiledevice3,
            ("developer", "dvt", "device-information"),
            environment,
            15,
        )
        if command_succeeded(dvt_outcome):
            yield result_for(
                "dvt",
                "ready",
                "DVT instrumentation is reachable.",
                "The device-information channel responded successfully.",
            )
        else:
            yield unavailable_from_outcome("dvt", dvt_outcome, device.identifier)

    webinspector_outcome = run_command(
        pymobiledevice3,
        ("webinspector", "opened-tabs", "--timeout", "3"),
        environment,
        8,
    )
    if command_succeeded(webinspector_outcome):
        yield result_for(
            "webinspector",
            "ready",
            "Safari Web Inspector responded.",
            "The opened-tabs request completed; an empty list simply means no inspectable page was open.",
        )
    else:
        yield unavailable_from_outcome("webinspector", webinspector_outcome, device.identifier)
