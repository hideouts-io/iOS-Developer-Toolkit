from __future__ import annotations

from collections.abc import Callable, Sequence

from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ios_developer_toolkit.live_logs import LogStreamSpec
from ios_developer_toolkit.workspace_profile import WORKSPACE_NAMES


Navigate = Callable[[str], None]
OpenLiveLog = Callable[[str], None]
OpenAction = Callable[[], None]


def navigation_handler(navigate: Navigate, destination: str) -> Callable[[bool], None]:
    """Bind a reviewed workspace destination without executing an operation."""
    def handle(checked: bool) -> None:
        del checked
        navigate(destination)
    return handle


def build_home_page(preset_count: int, manpage_count: int, navigate: Navigate) -> QWidget:
    """Build the landing workspace with navigation-only workflow cards."""
    page = QWidget()
    page.setObjectName("homeWorkspace")
    layout = QVBoxLayout(page)
    layout.setSpacing(16)
    hero = QFrame()
    hero.setObjectName("homeHero")
    hero_layout = QVBoxLayout(hero)
    hero_layout.setContentsMargins(22, 18, 22, 18)
    eyebrow = QLabel("YOUR DEVELOPER WORKBENCH")
    eyebrow.setObjectName("homeEyebrow")
    hero_layout.addWidget(eyebrow)
    heading = QLabel("Ready for your next device session.")
    heading.setObjectName("pageTitle")
    heading.setWordWrap(True)
    hero_layout.addWidget(heading)
    description = QLabel("Start with a trusted connection. Inspect, test, and preserve device data with clear, guided workflows.")
    description.setWordWrap(True)
    hero_layout.addWidget(description)
    stats = QLabel(f"{preset_count} guided commands   ·   {manpage_count} live help topics   ·   {len(WORKSPACE_NAMES)} workspaces")
    stats.setObjectName("homeStats")
    stats.setWordWrap(True)
    hero_layout.addWidget(stats)
    layout.addWidget(hero)
    workflow_grid = QGridLayout()
    workflow_grid.setSpacing(12)
    workflow_grid.setColumnStretch(0, 1)
    workflow_grid.setColumnStretch(1, 1)
    cards = (
        ("Connect & prepare", "Review trust, Developer Mode, and the right developer image.", "Device & DDI"),
        ("Verify readiness", "Understand which services and tools are available before you begin.", "Capability Matrix"),
        ("Test a location", "Set a coordinate or replay a route with explicit cleanup.", "Location Lab"),
        ("Run guided commands", "Find an action, review its requirements, and inspect the exact command.", "Command Center"),
        ("Capture evidence", "Keep original artifacts, coverage, and integrity records together.", "Evidence Capture"),
        ("Explore live help", "Read the installed tool's current syntax and service reference.", "Man Pages"),
    )
    for position, (title, body, destination) in enumerate(cards):
        card = QFrame()
        card.setProperty("workflowCard", True)
        card.setObjectName(f"homeCard{position}")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 13, 16, 13)
        title_label = QLabel(title)
        title_label.setProperty("cardTitle", True)
        card_layout.addWidget(title_label)
        body_label = QLabel(body)
        body_label.setProperty("cardDescription", True)
        body_label.setWordWrap(True)
        card_layout.addWidget(body_label, 1)
        open_button = QPushButton(f"Open {destination.replace('&', '&&')}")
        open_button.setObjectName(f"homeOpen{destination.replace(' ', '')}Button")
        open_button.setAccessibleName(f"Open {destination} workspace")
        open_button.setAccessibleDescription(f"Navigate to the {destination} workspace without running an action.")
        open_button.clicked.connect(navigation_handler(navigate, destination))
        card_layout.addWidget(open_button)
        workflow_grid.addWidget(card, position // 2, position % 2)
    layout.addLayout(workflow_grid)
    additional = QGroupBox("More tools for your workflow")
    additional_layout = QGridLayout(additional)
    for position, destination in enumerate(("Firmware", "Simulators", "Xcode Tools", "Security Analysis")):
        button = QPushButton(destination)
        button.setObjectName(f"homeOpen{destination.replace(' ', '')}Button")
        button.setAccessibleName(f"Open {destination} workspace")
        button.clicked.connect(navigation_handler(navigate, destination))
        additional_layout.addWidget(button, position // 2, position % 2)
    layout.addWidget(additional)
    boundary = QLabel("Choose a device before running device actions. Demo Mode is a safe visual walkthrough.")
    boundary.setObjectName("protocolStackSummary")
    boundary.setWordWrap(True)
    layout.addWidget(boundary)
    layout.addStretch()
    return page


def build_live_logs_page(
    specifications: Sequence[LogStreamSpec],
    open_live_log: OpenLiveLog,
    open_log_presets: OpenAction,
    navigate: Navigate,
) -> QWidget:
    """Build the static log-launch workspace and delegate stateful actions to the main window."""
    page = QWidget()
    layout = QVBoxLayout(page)
    layout.setSpacing(14)

    heading = QLabel("Live Logs")
    heading.setObjectName("pageTitle")
    heading.setFont(QFont(heading.font().family(), 20, QFont.Weight.Bold))
    layout.addWidget(heading)
    explanation = QLabel(
        "Open independent scrolling log windows for the selected device. Each window continuously spools the "
        "complete raw byte stream to a private local cache while its visible view can be paused, searched, or "
        "filtered. Add an optional investigation reference, select visible lines to record classified analyst "
        "findings, then export a hashed evidence bundle. Closing a window asks you to save or explicitly discard "
        "the capture."
    )
    explanation.setWordWrap(True)
    layout.addWidget(explanation)

    stream_grid = QGridLayout()
    for position, specification in enumerate(specifications):
        group = QGroupBox(specification.title)
        group_layout = QVBoxLayout(group)
        summary = QLabel(specification.summary)
        summary.setWordWrap(True)
        group_layout.addWidget(summary, 1)
        requirement = QLabel(
            "Needs Developer Mode + mounted DDI/tunnel"
            if specification.requires_developer_services
            else "Uses the trusted lockdown connection; no DDI required"
        )
        requirement.setObjectName("liveLogRequirement")
        requirement.setWordWrap(True)
        group_layout.addWidget(requirement)
        open_button = QPushButton(f"Pop Out {specification.title}")
        open_button.setObjectName(f"open{specification.identifier.replace('-', '').title()}LogButton")
        open_button.setAccessibleName(f"Open {specification.title} live log")
        open_button.setAccessibleDescription(
            "Open an independent log window for the selected trusted device."
        )
        open_button.clicked.connect(lambda checked=False, identifier=specification.identifier: open_live_log(identifier))
        group_layout.addWidget(open_button)
        stream_grid.addWidget(group, 0, position)
    layout.addLayout(stream_grid)

    integrity_group = QGroupBox("Capture integrity")
    integrity_layout = QVBoxLayout(integrity_group)
    integrity_text = QLabel(
        "Pause affects only rendering: device output continues into the raw spool. Filters affect only the current "
        "view and filtered export. Save Raw copies the complete stream and a metadata sidecar containing the exact "
        "command, target UDID, timestamps, byte/line counts, exit code, and process error. The view retains the newest "
        "50,000 decoded lines to stay responsive; the raw spool is not truncated by that limit. Mark Finding stores "
        "a selected excerpt, classification, tags, and analyst note separately from the raw stream. Review Findings "
        "keeps annotations distinct from raw output. Export Evidence Bundle creates a local folder with raw capture, "
        "metadata, findings, investigation report, and SHA-256 inventory. Findings are annotations, not proof of "
        "device activity or causality."
    )
    integrity_text.setWordWrap(True)
    integrity_layout.addWidget(integrity_text)
    layout.addWidget(integrity_group)

    archive_group = QGroupBox("Stored log archive and deeper analysis")
    archive_layout = QHBoxLayout(archive_group)
    archive_note = QLabel(
        "For retained device logs, use the Syslog → collect preset in Command Center to pull a .logarchive for "
        "Console.app or the macOS log command. Evidence Capture remains the bounded multi-source workflow."
    )
    archive_note.setWordWrap(True)
    archive_layout.addWidget(archive_note, 1)
    command_button = QPushButton("Open Log Presets")
    command_button.setObjectName("openLogPresetsButton")
    command_button.setAccessibleName("Open logging and capture presets")
    command_button.clicked.connect(open_log_presets)
    archive_layout.addWidget(command_button)
    evidence_button = QPushButton("Open Evidence Capture")
    evidence_button.setObjectName("openEvidenceCaptureButton")
    evidence_button.setAccessibleName("Open Evidence Capture workspace")
    evidence_button.clicked.connect(lambda checked=False: navigate("Evidence Capture"))
    archive_layout.addWidget(evidence_button)
    layout.addWidget(archive_group)
    layout.addStretch()
    return page


def build_safety_page(developer_disk_image_repository: str) -> QWidget:
    """Build the static scope-and-safety reference workspace."""
    tab = QWidget()
    layout = QVBoxLayout(tab)
    browser = QTextBrowser()
    browser.setOpenExternalLinks(True)
    browser.setHtml(
        f"""
        <h2>What this app does</h2>
        <p>It is a guided macOS workbench for <code>pymobiledevice3</code>: pairing-visible device inspection,
        apps and AFC, backups, diagnostics, logging, packet capture, crash reports, Web Inspector, RemoteXPC,
        Developer Disk Images, location simulation and GPX testing, CoreDevice, DVT instrumentation, and
        evidence-oriented collection.</p>
        <p>Command Center minimizes typing with validated presets. Man Pages runs the installed binary's
        <code>--help</code>, so exact syntax and service availability remain version-specific and reviewable.</p>
        <h2>What a personalized DDI is</h2>
        <p>For iOS 17 and later, the image is an APFS payload plus <code>BuildManifest.plist</code> and a trust cache.
        Apple TSS personalizes it for the device ECID and nonce. It is mounted at <code>/System/Developer</code>.</p>
        <h2>Important limits</h2>
        <ul>
          <li>This is not a jailbreak and does not bypass the passcode, Secure Enclave, sandbox, or entitlements.</li>
          <li><code>developer dvt ls /</code> is a developer-service view, not unrestricted raw filesystem acquisition.</li>
          <li>TLS remains encrypted in PCAP. A hostname, owner, or DNS answer is not proof of application purpose.</li>
          <li>A failed or empty command is a coverage gap, not proof that data or activity is absent.</li>
          <li>Mounting a DDI and enabling Developer Mode change device state and create timestamps.</li>
          <li>Simulated location is a developer-service override, not a GPS hardware change. Clear it after testing;
          some apps may ignore it or prohibit its use.</li>
          <li>Restore, erase, activation, supervision, reboot, shutdown, and nonce-roll commands can be high impact.
          They are documented in Man Pages but are not promoted as guided presets.</li>
          <li>A command existing in pymobiledevice3 does not guarantee the selected iOS build advertises its Apple service.</li>
        </ul>
        <h2>Sources</h2>
        <p><a href="{developer_disk_image_repository}">DeveloperDiskImage repository</a><br>
        <a href="https://doronz88.github.io/pymobiledevice3/">pymobiledevice3 documentation</a><br>
        <a href="https://developer.apple.com/documentation/xcode/enabling-developer-mode-on-a-device">Apple Developer Mode guidance</a></p>
        """
    )
    layout.addWidget(browser)
    return tab
