from __future__ import annotations

from pathlib import Path

from ios_developer_toolkit.models import CommandSpec


def snapshot_commands(include_screenshot: bool, include_crash_pull: bool) -> tuple[CommandSpec, ...]:
    commands: list[CommandSpec] = [
        CommandSpec("usbmux", "Connected device inventory", ("usbmux", "list"), Path("snapshots/usbmux.json"), True, 30),
        CommandSpec("lockdown", "Lockdown device information", ("lockdown", "info"), Path("snapshots/lockdown-info.json"), True, 45),
        CommandSpec("mounter", "Mounted developer images", ("mounter", "list"), Path("snapshots/mounter-list.json"), False, 45),
        CommandSpec("cryptex", "Installed Cryptex inventory", ("cryptex", "list"), Path("snapshots/cryptex-list.json"), False, 90),
        CommandSpec("diagnostics", "Diagnostics service information", ("diagnostics", "info"), Path("snapshots/diagnostics-info.json"), False, 45),
        CommandSpec("mobilegestalt", "Known MobileGestalt values", ("diagnostics", "mg"), Path("snapshots/mobilegestalt.json"), False, 90),
        CommandSpec("ioregistry", "Device IORegistry", ("diagnostics", "ioregistry"), Path("snapshots/ioregistry.json"), False, 90),
        CommandSpec("battery", "Battery snapshot", ("diagnostics", "battery", "single"), Path("snapshots/battery.json"), False, 45),
        CommandSpec("apps", "Installed application inventory", ("apps", "list"), Path("snapshots/apps.json"), False, 90),
        CommandSpec("processes", "Diagnostics process inventory", ("processes", "ps"), Path("snapshots/processes.txt"), False, 60),
        CommandSpec("profiles", "Installed configuration profiles", ("profile", "list"), Path("snapshots/profiles.json"), False, 60),
        CommandSpec("provisioning", "Installed provisioning profiles", ("provision", "list"), Path("snapshots/provisioning.txt"), False, 60),
        CommandSpec("crashes", "Crash report inventory", ("crash", "ls"), Path("snapshots/crash-list.txt"), False, 60),
        CommandSpec("afc", "AFC media root listing", ("afc", "ls", "/"), Path("snapshots/afc-root.txt"), False, 60),
        CommandSpec("dvt-device", "DVT extended device information", ("developer", "dvt", "device-information"), Path("snapshots/dvt-device-information.json"), False, 120),
        CommandSpec("dvt-processes", "DVT detailed process snapshot", ("developer", "dvt", "sysmon", "process", "single"), Path("snapshots/dvt-sysmon-processes.txt"), False, 120),
        CommandSpec("dvt-filesystem", "DVT root filesystem listing", ("developer", "dvt", "ls", "/"), Path("snapshots/dvt-root-listing.txt"), False, 120),
    ]
    if include_screenshot:
        commands.append(
            CommandSpec(
                "screenshot",
                "Current device screenshot",
                ("developer", "dvt", "screenshot", "artifacts/screen.png"),
                Path("snapshots/screenshot-command.txt"),
                False,
                120,
            )
        )
    if include_crash_pull:
        commands.append(
            CommandSpec(
                "crash-pull",
                "Pull crash reports",
                ("crash", "pull", "artifacts/crashes"),
                Path("snapshots/crash-pull.txt"),
                False,
                600,
            )
        )
    return tuple(commands)


def is_potentially_mutating(arguments: tuple[str, ...]) -> bool:
    if not arguments:
        return False
    safe_prefixes: tuple[tuple[str, ...], ...] = (
        ("usbmux", "list"),
        ("lockdown", "info"),
        ("mounter", "list"),
        ("mounter", "lookup"),
        ("mounter", "query-developer-mode-status"),
        ("mounter", "query-nonce"),
        ("mounter", "query-personalization-identifiers"),
        ("cryptex", "list"),
        ("cryptex", "personalization-identifiers"),
        ("cryptex", "nonce"),
        ("syslog", "live"),
        ("pcap",),
        ("diagnostics", "info"),
        ("diagnostics", "ioregistry"),
        ("diagnostics", "mg"),
        ("diagnostics", "battery"),
        ("apps", "list"),
        ("apps", "query"),
        ("crash", "ls"),
        ("crash", "pull"),
        ("crash", "watch"),
        ("processes", "ps"),
        ("processes", "pgrep"),
        ("profile", "list"),
        ("provision", "list"),
        ("provision", "dump"),
        ("afc", "ls"),
        ("afc", "pull"),
        ("developer", "dvt", "ls"),
        ("developer", "dvt", "device-information"),
        ("developer", "dvt", "sysmon"),
        ("developer", "dvt", "oslog"),
        ("developer", "dvt", "screenshot"),
    )
    return not any(arguments[: len(prefix)] == prefix for prefix in safe_prefixes)
