from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from ios_developer_toolkit.command_catalog import CommandPreset


CommandDriftState = Literal["verified", "route-missing", "option-mismatch", "check-failed", "not-checked"]


@dataclass(frozen=True)
class HelpRouteProbe:
    command_path: tuple[str, ...]
    exit_code: int | None
    stdout: str
    stderr: str
    error: str | None


@dataclass(frozen=True)
class CommandDriftResult:
    preset_identifier: str
    preset_title: str
    command_path: tuple[str, ...]
    expected_options: tuple[str, ...]
    state: CommandDriftState
    detail: str


def help_routes_for_presets(presets: tuple[CommandPreset, ...]) -> tuple[tuple[str, ...], ...]:
    return tuple(dict.fromkeys(preset.manpage_path for preset in presets))


def expected_option_tokens(preset: CommandPreset) -> tuple[str, ...]:
    return tuple(dict.fromkeys(token for token in preset.argument_template if token.startswith("--") and token != "--"))


def help_includes_option(help_text: str, option: str) -> bool:
    pattern = rf"(?<![A-Za-z0-9_-]){re.escape(option)}(?![A-Za-z0-9_-])"
    return re.search(pattern, help_text) is not None


def evaluate_command_drift(
    presets: tuple[CommandPreset, ...],
    probes: tuple[HelpRouteProbe, ...],
) -> tuple[CommandDriftResult, ...]:
    probes_by_path = {probe.command_path: probe for probe in probes}
    return tuple(_evaluate_preset(preset, probes_by_path.get(preset.manpage_path)) for preset in presets)


def _evaluate_preset(preset: CommandPreset, probe: HelpRouteProbe | None) -> CommandDriftResult:
    expected_options = expected_option_tokens(preset)
    if probe is None:
        return CommandDriftResult(
            preset.identifier,
            preset.title,
            preset.manpage_path,
            expected_options,
            "not-checked",
            "The live-help route was not checked.",
        )
    if probe.error is not None:
        return CommandDriftResult(
            preset.identifier,
            preset.title,
            preset.manpage_path,
            expected_options,
            "check-failed",
            probe.error,
        )
    if probe.exit_code != 0:
        output = probe.stderr or probe.stdout
        suffix = f" Output: {output.strip()}" if output.strip() else ""
        return CommandDriftResult(
            preset.identifier,
            preset.title,
            preset.manpage_path,
            expected_options,
            "route-missing",
            f"Live help exited with status {probe.exit_code}.{suffix}",
        )
    if not probe.stdout.strip():
        return CommandDriftResult(
            preset.identifier,
            preset.title,
            preset.manpage_path,
            expected_options,
            "check-failed",
            "Live help exited successfully but returned no standard output.",
        )
    missing_options = tuple(option for option in expected_options if not help_includes_option(probe.stdout, option))
    if missing_options:
        return CommandDriftResult(
            preset.identifier,
            preset.title,
            preset.manpage_path,
            expected_options,
            "option-mismatch",
            f"The installed help did not advertise: {', '.join(missing_options)}.",
        )
    return CommandDriftResult(
        preset.identifier,
        preset.title,
        preset.manpage_path,
        expected_options,
        "verified",
        "Route and expected option flags match the installed live help.",
    )


def render_command_drift_report(results: tuple[CommandDriftResult, ...]) -> str:
    counts = {state: sum(result.state == state for result in results) for state in _command_drift_states()}
    lines = (
        "Command-drift report",
        (
            f"Verified: {counts['verified']}  |  Route missing: {counts['route-missing']}  |  "
            f"Option mismatch: {counts['option-mismatch']}  |  Check failed: {counts['check-failed']}  |  "
            f"Not checked: {counts['not-checked']}"
        ),
        "",
    )
    details = tuple(
        f"[{result.state.upper()}] {result.preset_title} — pymobiledevice3 {' '.join(result.command_path)}\n"
        f"  {result.detail}"
        for result in results
        if result.state != "verified"
    )
    if not details:
        return "\n".join((*lines, "All guided preset routes and expected option flags matched live help."))
    return "\n".join((*lines, *details))


def _command_drift_states() -> tuple[CommandDriftState, ...]:
    return ("verified", "route-missing", "option-mismatch", "check-failed", "not-checked")
