"""Require initialized Xcode and one bounded, read-only CoreSimulator inventory.

CI establishes service readiness before the unchanged Qt help integration test.
This does not retry commands, repair Xcode, restart services, or mutate devices.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from ios_developer_toolkit.apple_tools import AppleToolError
from ios_developer_toolkit.runtime import command_argv
from ios_developer_toolkit.simulator_tools import parse_simulators, simulator_inventory


def readiness_output(arguments: tuple[str, ...], phase: str, deadline: float) -> bytes:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise AppleToolError(f"Apple-tool readiness exceeded its total deadline before {phase}")
    try:
        result = subprocess.run(arguments, capture_output=True, check=False, timeout=remaining)
    except subprocess.TimeoutExpired as error:
        raise AppleToolError(f"Apple-tool readiness exceeded its total deadline during {phase}") from error
    except OSError as error:
        raise AppleToolError(f"Could not execute {phase}: {error}") from error
    if result.returncode != 0:
        raise AppleToolError(f"{phase} exited {result.returncode}; configure the runner's selected Xcode and CoreSimulator before running native integration checks")
    return result.stdout


def verify_apple_tool_readiness() -> tuple[Path, int]:
    operation = simulator_inventory()
    deadline = time.monotonic() + operation.timeout_seconds
    selected = readiness_output(("/usr/bin/xcrun", "--find", "xcodebuild"), "selected Xcode lookup", deadline).decode("utf-8").strip()
    xcodebuild = Path(selected)
    if not selected or "\n" in selected or not xcodebuild.is_absolute() or xcodebuild.name != "xcodebuild" or not xcodebuild.is_file():
        raise AppleToolError("Selected Xcode lookup did not identify a regular absolute xcodebuild executable")
    readiness_output((str(xcodebuild), "-checkFirstLaunchStatus"), "Xcode first-launch status", deadline)
    payload = readiness_output(command_argv(operation.command, operation.arguments), "CoreSimulator inventory readiness", deadline)
    targets = parse_simulators(payload)
    if time.monotonic() > deadline:
        raise AppleToolError("Apple-tool readiness exceeded its total deadline during CoreSimulator inventory validation")
    return xcodebuild, len(targets)


def main(arguments: tuple[str, ...]) -> int:
    if len(arguments) != 1:
        raise AppleToolError("Usage: python -m scripts.verify_apple_tool_readiness")
    started = time.monotonic()
    xcodebuild, count = verify_apple_tool_readiness()
    print(f"Apple-tool readiness passed in {time.monotonic() - started:.3f}s; selected Xcode: {xcodebuild}; simulator inventory entries: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(tuple(sys.argv)))
