from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Mapping
from urllib.parse import urlparse


RiskLevel = Literal["read-only", "host-write", "device-change"]
ParameterKind = Literal[
    "bundle-id",
    "float-latitude",
    "float-longitude",
    "local-directory",
    "output-file",
    "pid",
    "remote-path",
    "url",
]


class CommandCatalogError(ValueError):
    pass


@dataclass(frozen=True)
class ParameterSpec:
    identifier: str
    label: str
    description: str
    kind: ParameterKind
    initial_value: str


@dataclass(frozen=True)
class CommandPreset:
    identifier: str
    title: str
    category: str
    summary: str
    advanced_notes: str
    argument_template: tuple[str, ...]
    parameters: tuple[ParameterSpec, ...]
    risk: RiskLevel
    requires_device: bool
    requires_developer_services: bool
    long_running: bool
    manpage_path: tuple[str, ...]


@dataclass(frozen=True)
class ManPageEntry:
    title: str
    category: str
    command_path: tuple[str, ...]

    def display_name(self) -> str:
        command = "pymobiledevice3" if not self.command_path else " ".join(self.command_path)
        return f"{self.title}  ·  {command}"


def parameter(
    identifier: str,
    label: str,
    description: str,
    kind: ParameterKind,
    initial_value: str,
) -> ParameterSpec:
    return ParameterSpec(identifier, label, description, kind, initial_value)


def command_presets() -> tuple[CommandPreset, ...]:
    documents = Path.home() / "Documents"
    captures = documents
    return (
        CommandPreset("devices", "Connected devices", "Device Basics", "List USB and Wi-Fi devices known to usbmuxd.", "This is the best first check. It does not prove the device is unlocked or that every service is available.", ("usbmux", "list"), (), "read-only", False, False, False, ("usbmux", "list")),
        CommandPreset("lockdown", "Lockdown overview", "Device Basics", "Read pairing-visible device identity and configuration values.", "Lockdown is the gateway used to start many other device services. Returned keys describe exposed state, not unrestricted iOS internals.", ("lockdown", "info"), (), "read-only", True, False, False, ("lockdown", "info")),
        CommandPreset("activation", "Activation state", "Device Basics", "Query the current Apple activation state.", "This preset only reads state. Activation and deactivation commands are intentionally left in Man Pages because they change device state.", ("activation", "state"), (), "read-only", True, False, False, ("activation", "state")),
        CommandPreset("developer-mode", "Developer Mode status", "Device Basics", "Query whether Developer Mode is enabled.", "Developer Mode is required for most DVT and CoreDevice commands but does not itself mount the Developer Disk Image.", ("amfi", "developer-mode-status"), (), "read-only", True, False, False, ("amfi", "developer-mode-status")),
        CommandPreset("diagnostics", "Diagnostics overview", "Device Basics", "Read the diagnostics relay overview.", "Available values vary by hardware and iOS build. A missing field is a coverage limit, not proof of absence.", ("diagnostics", "info"), (), "read-only", True, False, False, ("diagnostics", "info")),
        CommandPreset("battery", "Battery snapshot", "Device Basics", "Read a point-in-time battery diagnostics record.", "Battery current, voltage, temperature, and charging fields are device/build dependent.", ("diagnostics", "battery", "single"), (), "read-only", True, False, False, ("diagnostics", "battery", "single")),
        CommandPreset("ioregistry", "IORegistry snapshot", "Device Basics", "Read the IORegistry view exposed by diagnostics relay.", "This can be large. It is not the Mac IORegistry and does not imply kernel-level access to the phone.", ("diagnostics", "ioregistry"), (), "read-only", True, False, False, ("diagnostics", "ioregistry")),
        CommandPreset("mobilegestalt", "MobileGestalt values", "Device Basics", "Query the known MobileGestalt key set.", "Only keys supported by the pinned client and permitted by the device are returned.", ("diagnostics", "mg"), (), "read-only", True, False, False, ("diagnostics", "mg")),
        CommandPreset("processes", "Process list", "Device Basics", "List processes through diagnosticsd.", "For richer start times and metrics, compare with the DVT process presets after mounting developer support.", ("processes", "ps"), (), "read-only", True, False, False, ("processes", "ps")),
        CommandPreset("profiles", "Configuration profiles", "Device Basics", "List profiles exposed by the profile service.", "A listed profile shows configuration state, not who actively uses it. Install, removal, supervision, and erase commands are not one-click presets.", ("profile", "list"), (), "read-only", True, False, False, ("profile", "list")),
        CommandPreset("provisioning", "Provisioning profiles", "Device Basics", "List installed developer provisioning profiles.", "Provisioning metadata describes possible app authorization. It is not evidence that a provisioned app executed.", ("provision", "list"), (), "read-only", True, False, False, ("provision", "list")),
        CommandPreset("orientation", "Screen orientation", "Device Basics", "Read the current SpringBoard screen orientation.", "This uses SpringBoardServices and does not capture screen content.", ("springboard", "orientation"), (), "read-only", True, False, False, ("springboard", "orientation")),
        CommandPreset("icon-metrics", "Home Screen icon metrics", "Device Basics", "Read SpringBoard Home Screen spacing and layout metrics.", "Metrics vary by device class, display mode, and iOS version.", ("springboard", "homescreen-icon-metrics"), (), "read-only", True, False, False, ("springboard", "homescreen-icon-metrics")),
        CommandPreset("apps-list", "Installed app inventory", "Apps & Files", "List apps through Installation Proxy.", "Use the dedicated Installed Apps page for filtering, sizes, copying bundle IDs, and confirmed uninstall.", ("apps", "list"), (), "read-only", True, False, False, ("apps", "list")),
        CommandPreset("apps-query", "Query one app", "Apps & Files", "Read detailed metadata for one bundle identifier.", "The bundle must be visible to Installation Proxy.", ("apps", "query", "{bundle_id}"), (parameter("bundle_id", "Bundle identifier", "Example: com.apple.mobilesafari", "bundle-id", "com.apple.mobilesafari"),), "read-only", True, False, False, ("apps", "query")),
        CommandPreset("afc-list", "List AFC directory", "Apps & Files", "List a path under the AFC media root.", "AFC is rooted at /var/mobile/Media on a stock device. It is not unrestricted access to / or private app containers.", ("afc", "ls", "{remote_path}"), (parameter("remote_path", "AFC path", "Path relative to the AFC service root.", "remote-path", "/"),), "read-only", True, False, False, ("afc", "ls")),
        CommandPreset("dvt-list", "List DVT path", "Apps & Files", "Ask the DVT developer service to list a path.", "The service performs this operation with Apple-defined privileges. Seeing / does not mean the host has root or raw filesystem access.", ("developer", "dvt", "ls", "{remote_path}"), (parameter("remote_path", "Device path", "Absolute path in the DVT service view.", "remote-path", "/"),), "read-only", True, True, False, ("developer", "dvt", "ls")),
        CommandPreset("crash-list", "Crash report inventory", "Apps & Files", "List crash, panic, Jetsam, and diagnostic reports exposed by the crash service.", "Availability depends on retention and the service view. A missing report is not proof that an event did not occur.", ("crash", "ls"), (), "read-only", True, False, False, ("crash", "ls")),
        CommandPreset("crash-pull", "Pull crash reports", "Apps & Files", "Copy all available crash reports into a local folder.", "This writes sensitive device artifacts to the Mac. Review and sanitize before sharing.", ("crash", "pull", "{directory}"), (parameter("directory", "Destination folder", "Existing parent or new crash-report folder.", "local-directory", str(captures / "iOS Crash Reports")),), "host-write", True, False, False, ("crash", "pull")),
        CommandPreset("syslog", "Live syslog", "Logging & Capture", "Stream the standard device syslog until stopped.", "Use device-side log messages to explain client failures. Logs can contain identifiers and private content.", ("syslog", "live"), (), "read-only", True, False, True, ("syslog", "live")),
        CommandPreset("oslog", "DVT Unified Logging", "Logging & Capture", "Stream the richer DVT OS log until stopped.", "This path can expose more detail than classic syslog but is less stable and requires developer services.", ("developer", "dvt", "oslog"), (), "read-only", True, True, True, ("developer", "dvt", "oslog")),
        CommandPreset("pcap", "Network PCAP", "Logging & Capture", "Capture device packets into a local PCAP file.", "TLS, QUIC, VPN, and other encryption remain encrypted. Process metadata and endpoints are clues, not proof of purpose.", ("pcap", "--out", "{output_file}"), (parameter("output_file", "PCAP output", "Local capture file opened by Wireshark or tcpdump.", "output-file", str(captures / "ios-device-network.pcap")),), "host-write", True, False, True, ("pcap",)),
        CommandPreset("btlogger", "Bluetooth HCI capture", "Logging & Capture", "Capture Apple Bluetooth HCI logging as pcapng.", "This is device-service logging, not a generic over-the-air Bluetooth sniffer. Availability is build dependent.", ("btlogger", "--format", "pcapng", "{output_file}"), (parameter("output_file", "PCAPNG output", "Local Bluetooth capture file.", "output-file", str(captures / "ios-device-bluetooth.pcapng")),), "host-write", True, False, True, ("btlogger",)),
        CommandPreset("dvt-device", "DVT device information", "Developer & DVT", "Read the developer instrumentation device-information record.", "Compare this with Lockdown overview; each protocol exposes a different view.", ("developer", "dvt", "device-information"), (), "read-only", True, True, False, ("developer", "dvt", "device-information")),
        CommandPreset("dvt-proclist", "DVT process list", "Developer & DVT", "List processes and start times through DVT.", "This is a developer instrumentation view and may differ from diagnosticsd process output.", ("developer", "dvt", "proclist"), (), "read-only", True, True, False, ("developer", "dvt", "proclist")),
        CommandPreset("dvt-applist", "DVT application list", "Developer & DVT", "List applications through DVT instrumentation.", "Compare with Installation Proxy when investigating differences between service views.", ("developer", "dvt", "applist"), (), "read-only", True, True, False, ("developer", "dvt", "applist")),
        CommandPreset("dvt-netstat", "DVT network activity", "Developer & DVT", "Read DVT's current network-activity view.", "This is not a packet capture. Correlate with PCAP and logs for attribution.", ("developer", "dvt", "netstat"), (), "read-only", True, True, False, ("developer", "dvt", "netstat")),
        CommandPreset("dvt-pid-check", "Check process identifier", "Developer & DVT", "Ask DVT whether a specific PID is currently running.", "Process identifiers are short-lived. Correlate the result with a fresh DVT process list before attribution.", ("developer", "dvt", "is-running-pid", "{pid}"), (parameter("pid", "Process ID", "Positive numeric PID from a current process inventory.", "pid", "1"),), "read-only", True, True, False, ("developer", "dvt", "is-running-pid")),
        CommandPreset("dvt-energy", "Process energy monitor", "Developer & DVT", "Stream DVT energy telemetry for one PID.", "Energy readings are developer instrumentation estimates and should be interpreted with workload and foreground state.", ("developer", "dvt", "energy", "{pid}"), (parameter("pid", "Process ID", "Positive numeric PID from a current process inventory.", "pid", "1"),), "read-only", True, True, True, ("developer", "dvt", "energy")),
        CommandPreset("sysmon-system", "System metrics snapshot", "Developer & DVT", "Read a point-in-time DVT system metrics record.", "Useful for load context before starting a longer process monitor.", ("developer", "dvt", "sysmon", "system"), (), "read-only", True, True, False, ("developer", "dvt", "sysmon", "system")),
        CommandPreset("sysmon-process", "Process metrics snapshot", "Developer & DVT", "Read a detailed point-in-time process metrics table.", "Includes richer CPU and memory fields than the diagnostics process list.", ("developer", "dvt", "sysmon", "process", "single"), (), "read-only", True, True, False, ("developer", "dvt", "sysmon", "process", "single")),
        CommandPreset("graphics", "Graphics monitor", "Developer & DVT", "Stream graphics and frame-related instrumentation until stopped.", "Interpret changes in context of foreground activity and display state.", ("developer", "dvt", "graphics"), (), "read-only", True, True, True, ("developer", "dvt", "graphics")),
        CommandPreset("notifications", "DVT notifications", "Developer & DVT", "Stream developer memory and application notifications.", "This is distinct from the top-level Darwin notification proxy.", ("developer", "dvt", "notifications"), (), "read-only", True, True, True, ("developer", "dvt", "notifications")),
        CommandPreset("core-profile", "KDebug trace parser", "Developer & DVT", "Stream and parse CoreProfile/KDebug events.", "Advanced and high-volume. It is strace-like instrumentation, not an entitlement or security-boundary bypass.", ("developer", "dvt", "core-profile-session", "parse-live"), (), "read-only", True, True, True, ("developer", "dvt", "core-profile-session", "parse-live")),
        CommandPreset("screenshot", "Device screenshot", "Developer & DVT", "Capture the current screen to a local PNG.", "The screenshot may contain highly sensitive visible content and notifications.", ("developer", "dvt", "screenshot", "{output_file}"), (parameter("output_file", "PNG output", "Local screenshot path.", "output-file", str(captures / "ios-device-screen.png")),), "host-write", True, True, False, ("developer", "dvt", "screenshot")),
        CommandPreset("core-device-info", "CoreDevice information", "Developer & DVT", "Read device information through the modern CoreDevice service.", "CoreDevice uses the iOS 17+ RemoteXPC/RSD architecture and may expose a different record from Lockdown or DVT.", ("developer", "core-device", "get-device-info"), (), "read-only", True, True, False, ("developer", "core-device", "get-device-info")),
        CommandPreset("core-display", "CoreDevice display info", "Developer & DVT", "Read current display-service information.", "Service advertisement and fields vary by iOS build.", ("developer", "core-device", "get-display-info"), (), "read-only", True, True, False, ("developer", "core-device", "get-display-info")),
        CommandPreset("core-lock", "CoreDevice lock state", "Developer & DVT", "Read the device lock-state service.", "This reports service-visible state and does not bypass the passcode.", ("developer", "core-device", "get-lockstate"), (), "read-only", True, True, False, ("developer", "core-device", "get-lockstate")),
        CommandPreset("core-processes", "CoreDevice processes", "Developer & DVT", "List processes through CoreDevice.", "Compare this protocol view with diagnosticsd and DVT before interpreting a difference as anomalous.", ("developer", "core-device", "list-processes"), (), "read-only", True, True, False, ("developer", "core-device", "list-processes")),
        CommandPreset("core-apps", "CoreDevice applications", "Developer & DVT", "List applications through CoreDevice.", "Availability depends on the services advertised by the specific iOS build.", ("developer", "core-device", "list-apps"), (), "read-only", True, True, False, ("developer", "core-device", "list-apps")),
        CommandPreset("mounted-images", "Mounted developer images", "Developer & DVT", "List Developer Disk Images currently known to the image mounter.", "Use this to verify the result of a mount rather than repeatedly mounting.", ("mounter", "list"), (), "read-only", True, False, False, ("mounter", "list")),
        CommandPreset("personalization", "Personalization identifiers", "Developer & DVT", "Query identifiers used for personalized developer images.", "These values are device-specific and sensitive. Querying them does not itself mount an image.", ("mounter", "query-personalization-identifiers"), (), "read-only", True, False, False, ("mounter", "query-personalization-identifiers")),
        CommandPreset("bonjour-rsd", "Discover RSD devices", "Web & Discovery", "Browse for Remote Service Discovery devices over Bonjour.", "Discovery shows advertised peers; it does not prove pairing or service authorization.", ("bonjour", "rsd"), (), "read-only", False, False, True, ("bonjour", "rsd")),
        CommandPreset("remote-browse", "Browse RemoteXPC", "Web & Discovery", "Browse RemoteXPC-capable devices and tunnel endpoints.", "This is discovery only. Pairing and tunnel commands are documented separately because they create state.", ("remote", "browse"), (), "read-only", False, False, True, ("remote", "browse")),
        CommandPreset("web-tabs", "Safari and WebView tabs", "Web & Discovery", "List pages visible to iOS Web Inspector.", "Web Inspector and Remote Automation must be enabled in Safari settings. Private pages may be exposed.", ("webinspector", "opened-tabs"), (), "read-only", True, False, False, ("webinspector", "opened-tabs")),
        CommandPreset("launch-app", "Launch application", "Device Actions", "Launch an application by bundle identifier through DVT.", "The command changes foreground/process state and defaults to killing an existing instance before launch.", ("developer", "dvt", "launch", "{bundle_id}"), (parameter("bundle_id", "Bundle identifier", "Application to launch.", "bundle-id", "com.apple.mobilesafari"),), "device-change", True, True, False, ("developer", "dvt", "launch")),
        CommandPreset("open-url", "Open URL in Safari", "Device Actions", "Launch a URL through Web Inspector automation.", "Requires Web Inspector and Remote Automation. This changes device UI and can make a network request to the entered destination.", ("webinspector", "launch", "{url}"), (parameter("url", "URL", "HTTP or HTTPS URL to open on the device.", "url", "https://example.com"),), "device-change", True, False, False, ("webinspector", "launch")),
        CommandPreset("location-set", "Set simulated location", "Device Actions", "Set a DVT-simulated latitude and longitude on iOS 17+.", "Simulation changes location reported to participating software until cleared or the relevant service state ends. It does not alter GPS hardware.", ("developer", "dvt", "simulate-location", "set", "--", "{latitude}", "{longitude}"), (parameter("latitude", "Latitude", "Decimal degrees from -90 to 90.", "float-latitude", "34.0522"), parameter("longitude", "Longitude", "Decimal degrees from -180 to 180.", "float-longitude", "-118.2437")), "device-change", True, True, False, ("developer", "dvt", "simulate-location", "set")),
        CommandPreset("location-clear", "Clear simulated location", "Device Actions", "Clear the current DVT location simulation.", "Use this after location testing so later observations use normal location sources.", ("developer", "dvt", "simulate-location", "clear"), (), "device-change", True, True, False, ("developer", "dvt", "simulate-location", "clear")),
    )


def preset_categories() -> tuple[str, ...]:
    return tuple(dict.fromkeys(preset.category for preset in command_presets()))


def preset_by_identifier(identifier: str) -> CommandPreset:
    matching = tuple(preset for preset in command_presets() if preset.identifier == identifier)
    if len(matching) != 1:
        raise CommandCatalogError(f"expected one preset for {identifier!r}, found {len(matching)}")
    return matching[0]


def validate_parameter(spec: ParameterSpec, raw_value: str) -> str:
    value = raw_value.strip()
    if not value:
        raise CommandCatalogError(f"{spec.label} is required")
    if spec.kind == "bundle-id":
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,254}", value) is None or "." not in value:
            raise CommandCatalogError(f"{spec.label} must be a valid bundle identifier")
        return value
    if spec.kind == "pid":
        if not value.isdecimal() or int(value) <= 0:
            raise CommandCatalogError(f"{spec.label} must be a positive process identifier")
        return value
    if spec.kind == "remote-path":
        if not value.startswith("/") or "\x00" in value:
            raise CommandCatalogError(f"{spec.label} must be an absolute device-service path")
        return value
    if spec.kind == "url":
        parsed = urlparse(value)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise CommandCatalogError(f"{spec.label} must be an HTTP or HTTPS URL with a host")
        return value
    if spec.kind == "float-latitude":
        return validate_coordinate(value, spec.label, -90.0, 90.0)
    if spec.kind == "float-longitude":
        return validate_coordinate(value, spec.label, -180.0, 180.0)
    path = Path(value).expanduser()
    if spec.kind == "local-directory":
        parent = path if path.exists() else path.parent
        if not parent.is_dir():
            raise CommandCatalogError(f"{spec.label} parent directory does not exist: {parent}")
        return str(path.resolve())
    if spec.kind == "output-file":
        if not path.parent.is_dir():
            raise CommandCatalogError(f"{spec.label} parent directory does not exist: {path.parent}")
        return str(path.resolve())
    raise CommandCatalogError(f"unsupported parameter kind: {spec.kind}")


def validate_coordinate(value: str, label: str, minimum: float, maximum: float) -> str:
    try:
        coordinate = float(value)
    except ValueError as error:
        raise CommandCatalogError(f"{label} must be a decimal number") from error
    if coordinate < minimum or coordinate > maximum:
        raise CommandCatalogError(f"{label} must be between {minimum:g} and {maximum:g}")
    return value


def render_preset_arguments(preset: CommandPreset, raw_values: Mapping[str, str]) -> tuple[str, ...]:
    expected_keys = {spec.identifier for spec in preset.parameters}
    received_keys = set(raw_values)
    if received_keys != expected_keys:
        missing = sorted(expected_keys - received_keys)
        unexpected = sorted(received_keys - expected_keys)
        raise CommandCatalogError(f"parameter mismatch; missing={missing}, unexpected={unexpected}")
    values = {spec.identifier: validate_parameter(spec, raw_values[spec.identifier]) for spec in preset.parameters}
    arguments: list[str] = []
    for token in preset.argument_template:
        placeholder = re.fullmatch(r"\{([a-z_]+)\}", token)
        arguments.append(values[placeholder.group(1)] if placeholder is not None else token)
    return tuple(arguments)


def risk_title(risk: RiskLevel) -> str:
    if risk == "read-only":
        return "READ-ORIENTED"
    if risk == "host-write":
        return "WRITES LOCAL FILES"
    return "CHANGES DEVICE STATE"


def command_part_title(value: str) -> str:
    abbreviations = {
        "afc": "AFC",
        "amfi": "AMFI",
        "dvt": "DVT",
        "hid": "HID",
        "idam": "IDAM",
        "pcap": "PCAP",
        "rsd": "RSD",
        "wda": "WDA",
    }
    return abbreviations.get(value, value.replace("-", " ").title())


def manpage_entries() -> tuple[ManPageEntry, ...]:
    top_level = (
        "activation", "afc", "amfi", "apps", "backup2", "btlogger", "bonjour", "companion",
        "crash", "cryptex", "developer", "diagnostics", "idam", "lockdown", "mounter",
        "notification", "pcap", "power-assertion", "processes", "profile", "provision", "remote",
        "restore", "springboard", "syslog", "usbmux", "webinspector", "version",
    )
    entries: list[ManPageEntry] = [ManPageEntry("All commands", "Overview", ())]
    entries.extend(ManPageEntry(command_part_title(group), "Top-level groups", (group,)) for group in top_level)
    developer_paths = (
        ("developer", "dvt"),
        ("developer", "dvt", "sysmon"),
        ("developer", "dvt", "sysmon", "process"),
        ("developer", "dvt", "sysmon", "process", "monitor"),
        ("developer", "dvt", "core-profile-session"),
        ("developer", "dvt", "simulate-location"),
        ("developer", "dvt", "condition"),
        ("developer", "core-device"),
        ("developer", "core-device", "display"),
        ("developer", "core-device", "hid"),
        ("developer", "core-device", "location"),
        ("developer", "debugserver"),
        ("developer", "accessibility"),
        ("developer", "wda"),
    )
    entries.extend(
        ManPageEntry(" › ".join(command_part_title(part) for part in path), "Developer services", path)
        for path in developer_paths
    )
    workflow_paths = (
        ("apps", "list"), ("apps", "query"), ("apps", "install"), ("apps", "uninstall"),
        ("backup2", "backup"), ("backup2", "restore"), ("backup2", "encryption"),
        ("crash", "pull"), ("diagnostics", "battery"), ("mounter", "auto-mount"),
        ("pcap",), ("syslog", "live"), ("syslog", "collect"),
        ("webinspector", "launch"), ("webinspector", "opened-tabs"),
    )
    entries.extend(
        ManPageEntry(" › ".join(command_part_title(part) for part in path), "Common leaf commands", path)
        for path in workflow_paths
    )
    return tuple(entries)
