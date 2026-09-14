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


Navigate = Callable[[str], None]
OpenLiveLog = Callable[[str], None]
OpenAction = Callable[[], None]


def build_home_page(preset_count: int, manpage_count: int, navigate: Navigate) -> QWidget:
    """Build the static landing workspace and delegate navigation to the main window."""
    page = QWidget()
    layout = QVBoxLayout(page)
    layout.setSpacing(16)

    hero = QFrame()
    hero.setObjectName("homeHero")
    hero_layout = QVBoxLayout(hero)
    heading = QLabel("One trusted connection. Many Apple device services.")
    heading.setObjectName("pageTitle")
    heading.setFont(QFont(heading.font().family(), 22, QFont.Weight.Bold))
    hero_layout.addWidget(heading)
    description = QLabel(
        "Use guided workflows for common work, Command Center for one-click pymobiledevice3 presets, "
        "and live Man Pages when you need the exact syntax supported by the installed version."
    )
    description.setWordWrap(True)
    hero_layout.addWidget(description)
    stats = QLabel(
        f"{preset_count} guided commands  •  {manpage_count} live help topics  •  direct execution without a shell"
    )
    stats.setObjectName("homeStats")
    hero_layout.addWidget(stats)
    layout.addWidget(hero)

    workflow_grid = QGridLayout()
    cards = (
        ("1", "Connect && prepare", "Trust the device, enable Developer Mode, and mount the correct personalized DDI.", "Device & DDI"),
        ("2", "Verify capabilities", "Test host tools, trust, Developer Mode, the DDI, tunnel, CoreDevice, DVT, and Web Inspector.", "Capability Matrix"),
        ("3", "Test location", "Set a fixed coordinate or replay a validated GPX route, then explicitly clear the simulated state.", "Location Lab"),
        ("4", "Run guided commands", "Choose a category and preset; the GUI validates any required fields and shows the exact command.", "Command Center"),
        ("5", "Collect && preserve", "Create a bounded evidence case, encrypted backup, app inventory, PCAP, logs, and crash-report set.", "Evidence Capture"),
        ("6", "Learn advanced services", "Browse current help for DVT, CoreDevice, RemoteXPC, Web Inspector, restore, profiles, and more.", "Man Pages"),
    )
    for position, (number, title, body, destination) in enumerate(cards):
        card = QGroupBox(f"{number}. {title}")
        card_layout = QVBoxLayout(card)
        body_label = QLabel(body)
        body_label.setWordWrap(True)
        card_layout.addWidget(body_label, 1)
        open_button = QPushButton(f"Open {destination.replace('&', '&&')}")
        open_button.setObjectName(f"homeOpen{destination.replace(' ', '')}Button")
        open_button.setAccessibleName(f"Open {destination} workspace")
        open_button.setAccessibleDescription(f"Navigate to the {destination} workspace.")
        open_button.clicked.connect(lambda checked=False, name=destination: navigate(name))
        card_layout.addWidget(open_button)
        workflow_grid.addWidget(card, position // 2, position % 2)
    layout.addLayout(workflow_grid)

    stack_group = QGroupBox("How the command families fit together")
    stack_layout = QVBoxLayout(stack_group)
    stack = QLabel(
        "USB / Wi-Fi pairing → usbmuxd → lockdownd → AFC, apps, backups, diagnostics, syslog\n"
        "iOS 17+ RemoteXPC / RSD → Developer Disk Image → CoreDevice and DVT instrumentation\n"
        "Correlate service views: logs + packets + processes + crash reports + backups; no single command is complete evidence."
    )
    stack.setObjectName("protocolStackSummary")
    stack.setWordWrap(True)
    stack_layout.addWidget(stack)
    layout.addWidget(stack_group)
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


def toolkit_stylesheet() -> str:
    """Return the shared application stylesheet applied by the main window."""
    return """
        QWidget { color: #1d2633; }
        QMainWindow { background: #f4f6fa; }
        QGroupBox { background: white; border: 1px solid #d9dee8; border-radius: 10px; margin-top: 12px; padding: 12px; font-weight: 600; }
        QGroupBox::title { subcontrol-origin: margin; left: 14px; padding: 0 5px; }
        QPushButton { min-height: 30px; padding: 3px 12px; border: 1px solid #c7ceda; border-radius: 7px; background: white; }
        QPushButton:hover { background: #eef4ff; border-color: #7aa7ef; }
        QPushButton:disabled { color: #9299a5; background: #eef0f4; }
        QLineEdit, QComboBox, QSpinBox, QPlainTextEdit, QTextBrowser, QTableWidget, QListWidget { border: 1px solid #cfd5df; border-radius: 7px; background: white; padding: 5px; }
        #workspaceSidebar { background: #172033; border: 1px solid #25314a; border-radius: 11px; }
        #workspaceSidebar QLabel { color: #dbe7ff; }
        #sidebarSectionLabel { color: #89a8dc; font-size: 11px; font-weight: 700; padding: 3px 7px; }
        #sidebarVersion { color: #91a2be; font-size: 11px; padding: 8px; }
        #workspaceNavigation { background: transparent; border: none; color: #dce6f7; outline: none; }
        #workspaceNavigation::item { min-height: 34px; border-radius: 7px; padding: 4px 9px; }
        #workspaceNavigation::item:hover { background: #24324b; }
        #workspaceNavigation::item:selected { background: #3567b7; color: white; }
        #homeHero { background: #e8f1ff; border: 1px solid #a9c9f6; border-radius: 12px; padding: 12px; }
        #homeStats { color: #315f9e; font-weight: 600; }
        #commandPresetBrowser, #commandPresetDetail { background: white; border: 1px solid #d9dee8; border-radius: 10px; }
        #commandPresetTitle, #manpageTitle { color: #162033; }
        #commandAdvancedNotes, #commandPrerequisites { color: #566176; }
        #commandRiskBadge { border-radius: 8px; padding: 5px 9px; font-size: 11px; font-weight: 700; }
        #commandRiskBadge[risk="read-only"] { background: #e4f6e9; color: #236b36; }
        #commandRiskBadge[risk="host-write"] { background: #fff1ce; color: #765400; }
        #commandRiskBadge[risk="device-change"] { background: #ffe2df; color: #8b2d24; }
        #protocolStackSummary { font-family: Menlo; color: #34435a; }
        #connectionBanner { background: #e9f2ff; border: 1px solid #afcff8; border-radius: 8px; padding: 10px; }
        #collectionPrivacyWarning { background: #fff5df; border: 1px solid #e7c36a; border-radius: 8px; padding: 10px; }
        #installedAppsPrivacyWarning, #backupEncryptionWarning, #locationPrivacyWarning, #capabilityMatrixBoundary { background: #fff5df; border: 1px solid #e7c36a; border-radius: 8px; padding: 10px; }
        #capabilityMatrixStatus { background: #e9f2ff; border: 1px solid #afcff8; border-radius: 8px; padding: 9px; }
        #appSubtitle { color: #596273; }
    """
