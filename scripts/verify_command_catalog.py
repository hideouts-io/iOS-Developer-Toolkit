#!/usr/bin/env python3
"""Verify that every guided preset still matches installed pymobiledevice3 help."""

from __future__ import annotations

import subprocess

from ios_developer_toolkit.command_catalog import command_presets
from ios_developer_toolkit.command_drift import (
    HelpRouteProbe,
    evaluate_command_drift,
    help_routes_for_presets,
    render_command_drift_report,
)
from ios_developer_toolkit.runtime import ExecutableCommand, command_argv, pymobiledevice3_command


HELP_TIMEOUT_SECONDS = 10


def probe_live_help(command: ExecutableCommand, command_path: tuple[str, ...]) -> HelpRouteProbe:
    """Return the result of one device-free pymobiledevice3 help probe."""
    arguments = (*command_path, "--help")
    try:
        completed = subprocess.run(
            command_argv(command, arguments),
            check=False,
            capture_output=True,
            text=True,
            timeout=HELP_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return HelpRouteProbe(
            command_path,
            None,
            "",
            "",
            f"Live help exceeded the {HELP_TIMEOUT_SECONDS}-second per-route limit.",
        )
    except OSError as error:
        return HelpRouteProbe(
            command_path,
            None,
            "",
            "",
            f"Could not start live help: {error}",
        )
    return HelpRouteProbe(
        command_path,
        completed.returncode,
        completed.stdout,
        completed.stderr,
        None,
    )


def main() -> int:
    """Print a command-drift report and return nonzero for incompatible guidance."""
    presets = command_presets()
    command = pymobiledevice3_command()
    probes = tuple(probe_live_help(command, route) for route in help_routes_for_presets(presets))
    results = evaluate_command_drift(presets, probes)
    print(render_command_drift_report(results))
    return 0 if all(result.state == "verified" for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
