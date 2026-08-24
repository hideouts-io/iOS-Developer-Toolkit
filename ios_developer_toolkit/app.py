from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Mapping

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QCloseEvent, QDesktopServices, QFont, QIcon, QPixmap, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ios_developer_toolkit import APP_VERSION
from ios_developer_toolkit.backup_worker import BackupEvent, BackupRequestError, parse_backup_event
from ios_developer_toolkit.catalog import is_potentially_mutating
from ios_developer_toolkit.command_catalog import (
    CommandCatalogError,
    CommandPreset,
    ManPageEntry,
    ParameterSpec,
    command_presets,
    manpage_entries,
    preset_categories,
    render_preset_arguments,
    risk_title,
)
from ios_developer_toolkit.installed_apps import (
    InstalledApp,
    InstalledAppsDataError,
    format_byte_count,
    parse_installed_apps_json,
)
from ios_developer_toolkit.ipa_inspector import (
    IPAInspection,
    IPAInspectionError,
    format_inspection,
    parse_inspection_json,
    validate_bundle_identifier,
)
from ios_developer_toolkit.models import DeviceDataError, IOSDevice, parse_devices_json
from ios_developer_toolkit.runtime import device_environment, pymobiledevice3_executable
from ios_developer_toolkit.ufade_connector import (
    UFADE_REPOSITORY_URL,
    UFADEInstallation,
    UFADEValidationError,
    inspect_ufade_installation,
)
from ios_developer_toolkit.validation import output_indicates_failure


XCODE_CANDIDATE_DDI = Path("/Library/Developer/CoreDevice/CandidateDDIs/iOS_DDI.dmg")
DEVELOPER_DISK_IMAGE_REPOSITORY = "https://github.com/doronz88/DeveloperDiskImage"


def application_icon_path() -> Path:
    icon_path = Path(__file__).resolve().parent / "assets" / "iosdevtoolkit.png"
    if not icon_path.is_file():
        raise FileNotFoundError(f"Application icon is missing: {icon_path}")
    return icon_path


def qprocess_environment(values: Mapping[str, str]) -> QProcessEnvironment:
    environment = QProcessEnvironment.systemEnvironment()
    for key, value in values.items():
        environment.insert(key, value)
    return environment


def base_environment() -> Mapping[str, str]:
    environment = dict(os.environ)
    environment["PYTHONUNBUFFERED"] = "1"
    environment["NO_COLOR"] = "1"
    return environment


class DeviceScanner(QObject):
    devices_changed = Signal(object)
    scan_error = Signal(str)

    def __init__(self, executable: Path) -> None:
        super().__init__()
        self._executable = executable
        self._timer = QTimer(self)
        self._timer.setInterval(3000)
        self._timer.timeout.connect(self.scan)
        self._process: QProcess | None = None
        self._stdout = bytearray()
        self._stderr = bytearray()

    def start(self) -> None:
        self.scan()
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()
        if self._process is not None and self._process.state() != QProcess.ProcessState.NotRunning:
            self._process.terminate()

    def scan(self) -> None:
        if self._process is not None and self._process.state() != QProcess.ProcessState.NotRunning:
            return
        self._stdout.clear()
        self._stderr.clear()
        process = QProcess(self)
        process.setProgram(str(self._executable))
        process.setArguments(["usbmux", "list"])
        process.setProcessEnvironment(qprocess_environment(base_environment()))
        process.readyReadStandardOutput.connect(self._read_stdout)
        process.readyReadStandardError.connect(self._read_stderr)
        process.finished.connect(self._finished)
        self._process = process
        process.start()

    def _read_stdout(self) -> None:
        if self._process is not None:
            self._stdout.extend(bytes(self._process.readAllStandardOutput()))

    def _read_stderr(self) -> None:
        if self._process is not None:
            self._stderr.extend(bytes(self._process.readAllStandardError()))

    def _finished(self, exit_code: int, exit_status: QProcess.ExitStatus) -> None:
        del exit_status
        if exit_code != 0:
            message = self._stderr.decode("utf-8", errors="replace").strip()
            self.scan_error.emit(message or f"Device scan failed with exit code {exit_code}")
            return
        try:
            devices = parse_devices_json(self._stdout.decode("utf-8"))
        except (DeviceDataError, json.JSONDecodeError, UnicodeDecodeError) as error:
            self.scan_error.emit(f"Could not parse device discovery output: {error}")
            return
        self.devices_changed.emit(devices)


class DeveloperModeDialog(QDialog):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Enable Developer Mode on the iPhone or iPad")
        self.setMinimumWidth(600)
        layout = QVBoxLayout(self)
        heading = QLabel("Complete these steps on the connected device")
        heading.setObjectName("developerModeHeading")
        heading.setFont(QFont(heading.font().family(), 17, QFont.Weight.DemiBold))
        layout.addWidget(heading)
        instructions = QTextBrowser()
        instructions.setOpenExternalLinks(True)
        instructions.setHtml(
            """
            <ol>
              <li>Unlock the iPhone or iPad, connect it by USB, and tap <b>Trust</b> if prompted.</li>
              <li>Open <b>Settings → Privacy &amp; Security → Developer Mode</b>.</li>
              <li>Turn Developer Mode on and tap <b>Restart</b>.</li>
              <li>After restart, unlock the device, tap <b>Turn On</b> or <b>Enable</b>, and enter the device passcode.</li>
              <li>Reconnect and trust the Mac again if iOS asks.</li>
            </ol>
            <p>If Developer Mode is missing, first pair the device in Xcode using
            <b>Window → Devices and Simulators</b>, then return to Settings.</p>
            <p><b>Security note:</b> Developer Mode deliberately exposes development services.
            Turn it off and restart the device when the investigation is finished if you no longer need it.</p>
            """
        )
        instructions.setMinimumHeight(310)
        layout.addWidget(instructions)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"iOS Device Workbench {APP_VERSION}")
        self.setWindowIcon(QIcon(str(application_icon_path())))
        self.resize(1280, 840)
        self._pmd3 = pymobiledevice3_executable()
        self._devices: tuple[IOSDevice, ...] = ()
        self._active_device_identifier: str | None = None
        self._guided_udids: set[str] = set()
        self._action_process: QProcess | None = None
        self._action_context = ""
        self._action_buffer = bytearray()
        self._collection_process: QProcess | None = None
        self._ipa_inspection_process: QProcess | None = None
        self._ipa_inspection_stdout = bytearray()
        self._ipa_inspection_stderr = bytearray()
        self._ipa_inspection: IPAInspection | None = None
        self._selected_ipa: Path | None = None
        self._sideload_process: QProcess | None = None
        self._sideload_context = ""
        self._sideload_buffer = bytearray()
        self._apps_process: QProcess | None = None
        self._apps_context = ""
        self._apps_stdout = bytearray()
        self._apps_stderr = bytearray()
        self._installed_apps: tuple[InstalledApp, ...] = ()
        self._backup_process: QProcess | None = None
        self._backup_action = ""
        self._backup_stdout = bytearray()
        self._backup_stderr = bytearray()
        self._backup_encryption_state: bool | None = None
        self._last_backup_path: Path | None = None
        self._ufade_installation: UFADEInstallation | None = None
        self._console_process: QProcess | None = None
        self._presets = command_presets()
        self._current_preset: CommandPreset | None = None
        self._preset_parameter_fields: dict[str, QLineEdit] = {}
        self._manpages = manpage_entries()
        self._manpage_process: QProcess | None = None
        self._manpage_stdout = bytearray()
        self._manpage_stderr = bytearray()
        self._last_case_path: Path | None = None
        self._build_ui()
        self._scanner = DeviceScanner(self._pmd3)
        self._scanner.devices_changed.connect(self._devices_changed)
        self._scanner.scan_error.connect(self._scan_error)
        self._scanner.start()

    def _build_ui(self) -> None:
        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(22, 18, 22, 18)
        root_layout.setSpacing(14)

        header_layout = QHBoxLayout()
        logo = QLabel()
        logo.setObjectName("appLogo")
        logo_pixmap = QPixmap(str(application_icon_path()))
        if logo_pixmap.isNull():
            raise RuntimeError(f"Could not load application logo: {application_icon_path()}")
        logo.setPixmap(
            logo_pixmap.scaled(
                66,
                66,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        logo.setFixedSize(66, 66)
        logo.setAccessibleName("iOS Developer Toolkit logo")
        header_layout.addWidget(logo)
        title_block = QVBoxLayout()
        title = QLabel("iOS Device Workbench")
        title.setObjectName("appTitle")
        title.setFont(QFont(title.font().family(), 24, QFont.Weight.Bold))
        subtitle = QLabel("pymobiledevice3 Swiss-army GUI • Developer images • diagnostics • evidence")
        subtitle.setObjectName("appSubtitle")
        title_block.addWidget(title)
        title_block.addWidget(subtitle)
        header_layout.addLayout(title_block)
        header_layout.addStretch()
        self.device_combo = QComboBox()
        self.device_combo.setObjectName("devicePicker")
        self.device_combo.setMinimumWidth(390)
        self.device_combo.currentIndexChanged.connect(self._device_selected)
        header_layout.addWidget(self.device_combo)
        refresh_button = QPushButton("Refresh")
        refresh_button.setObjectName("refreshDevicesButton")
        refresh_button.clicked.connect(self._scanner_scan)
        header_layout.addWidget(refresh_button)
        root_layout.addLayout(header_layout)

        self.connection_banner = QLabel("Waiting for an unlocked and trusted iPhone or iPad over USB…")
        self.connection_banner.setObjectName("connectionBanner")
        self.connection_banner.setWordWrap(True)
        root_layout.addWidget(self.connection_banner)

        workspace = QSplitter(Qt.Orientation.Horizontal)
        workspace.setObjectName("workspaceSplitter")
        sidebar = QFrame()
        sidebar.setObjectName("workspaceSidebar")
        sidebar.setMinimumWidth(205)
        sidebar.setMaximumWidth(250)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(10, 12, 10, 12)
        section_label = QLabel("WORKSPACE")
        section_label.setObjectName("sidebarSectionLabel")
        sidebar_layout.addWidget(section_label)
        self.navigation_list = QListWidget()
        self.navigation_list.setObjectName("workspaceNavigation")
        self.navigation_list.setSpacing(2)
        sidebar_layout.addWidget(self.navigation_list, 1)
        version_note = QLabel(f"Toolkit {APP_VERSION}\npymobiledevice3 10.11.0")
        version_note.setObjectName("sidebarVersion")
        version_note.setWordWrap(True)
        sidebar_layout.addWidget(version_note)
        workspace.addWidget(sidebar)

        self.page_stack = QStackedWidget()
        self.page_stack.setObjectName("workspacePages")
        pages = (
            ("Home", self._build_home_page()),
            ("Device & DDI", self._build_overview_tab()),
            ("Command Center", self._build_command_center_page()),
            ("Installed Apps", self._build_installed_apps_tab()),
            ("Backup", self._build_backup_tab()),
            ("Sideload IPA", self._build_sideload_tab()),
            ("Evidence Capture", self._build_collection_tab()),
            ("Man Pages", self._build_manpages_page()),
            ("Scope & Safety", self._build_safety_tab()),
        )
        self._page_indices: dict[str, int] = {}
        for name, page in pages:
            self._page_indices[name] = self.page_stack.addWidget(page)
            self.navigation_list.addItem(QListWidgetItem(name))
        self.navigation_list.currentRowChanged.connect(self.page_stack.setCurrentIndex)
        self.navigation_list.setCurrentRow(0)
        workspace.addWidget(self.page_stack)
        workspace.setStretchFactor(0, 0)
        workspace.setStretchFactor(1, 1)
        root_layout.addWidget(workspace, 1)
        self.setCentralWidget(root)
        self._apply_style()

    def _build_home_page(self) -> QWidget:
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
            f"{len(self._presets)} guided commands  •  {len(self._manpages)} live help topics  •  direct execution without a shell"
        )
        stats.setObjectName("homeStats")
        hero_layout.addWidget(stats)
        layout.addWidget(hero)

        workflow_grid = QGridLayout()
        cards = (
            ("1", "Connect && prepare", "Trust the device, enable Developer Mode, and mount the correct personalized DDI.", "Device & DDI"),
            ("2", "Run guided commands", "Choose a category and preset; the GUI validates any required fields and shows the exact command.", "Command Center"),
            ("3", "Collect && preserve", "Create a bounded evidence case, encrypted backup, app inventory, PCAP, logs, and crash-report set.", "Evidence Capture"),
            ("4", "Learn advanced services", "Browse current help for DVT, CoreDevice, RemoteXPC, Web Inspector, restore, profiles, and more.", "Man Pages"),
        )
        for position, (number, title, body, destination) in enumerate(cards):
            card = QGroupBox(f"{number}. {title}")
            card_layout = QVBoxLayout(card)
            body_label = QLabel(body)
            body_label.setWordWrap(True)
            card_layout.addWidget(body_label, 1)
            open_button = QPushButton(f"Open {destination.replace('&', '&&')}")
            open_button.setObjectName(f"homeOpen{destination.replace(' ', '')}Button")
            open_button.clicked.connect(self._navigation_handler(destination))
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

    def navigate_to_page(self, name: str) -> None:
        index = self._page_indices.get(name)
        if index is None:
            raise KeyError(f"Unknown workspace page: {name}")
        self.navigation_list.setCurrentRow(index)

    def _navigation_handler(self, name: str) -> Callable[[bool], None]:
        def navigate(checked: bool) -> None:
            del checked
            self.navigate_to_page(name)

        return navigate

    def _build_overview_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(14)

        device_group = QGroupBox("Connected device")
        device_layout = QGridLayout(device_group)
        self.device_name_value = QLabel("No device")
        self.device_version_value = QLabel("—")
        self.device_model_value = QLabel("—")
        self.device_udid_value = QLabel("—")
        self.device_udid_value.setTextInteractionFlags(self.device_udid_value.textInteractionFlags())
        device_layout.addWidget(QLabel("Name"), 0, 0)
        device_layout.addWidget(self.device_name_value, 0, 1)
        device_layout.addWidget(QLabel("iOS / build"), 0, 2)
        device_layout.addWidget(self.device_version_value, 0, 3)
        device_layout.addWidget(QLabel("Model"), 1, 0)
        device_layout.addWidget(self.device_model_value, 1, 1)
        device_layout.addWidget(QLabel("UDID"), 1, 2)
        device_layout.addWidget(self.device_udid_value, 1, 3)
        layout.addWidget(device_group)

        developer_group = QGroupBox("1. Developer Mode")
        developer_layout = QHBoxLayout(developer_group)
        self.developer_mode_status = QLabel("Status not checked")
        self.developer_mode_status.setObjectName("developerModeStatus")
        developer_layout.addWidget(self.developer_mode_status, 1)
        guide_button = QPushButton("Show Steps")
        guide_button.setObjectName("developerModeGuideButton")
        guide_button.clicked.connect(self.show_developer_mode_guide)
        developer_layout.addWidget(guide_button)
        check_button = QPushButton("Check Status")
        check_button.setObjectName("developerModeCheckButton")
        check_button.clicked.connect(self.check_developer_mode)
        developer_layout.addWidget(check_button)
        layout.addWidget(developer_group)

        ddi_group = QGroupBox("2. Choose a Developer Disk Image source")
        ddi_layout = QVBoxLayout(ddi_group)
        self.personalized_radio = QRadioButton("Downloaded personalized DDI — recommended for iOS 17+")
        self.personalized_radio.setObjectName("personalizedDDIRadio")
        self.personalized_radio.setChecked(True)
        self.local_radio = QRadioButton(f"Local Apple/Xcode DDI — {XCODE_CANDIDATE_DDI}")
        self.local_radio.setObjectName("localDDIRadio")
        self.personalized_radio.toggled.connect(self._ddi_source_changed)
        ddi_layout.addWidget(self.personalized_radio)
        ddi_layout.addWidget(self.local_radio)
        self.ddi_description = QTextBrowser()
        self.ddi_description.setObjectName("ddiDescription")
        self.ddi_description.setOpenExternalLinks(True)
        self.ddi_description.setMaximumHeight(145)
        ddi_layout.addWidget(self.ddi_description)
        button_layout = QHBoxLayout()
        self.mount_button = QPushButton("Mount Personalized DDI")
        self.mount_button.setObjectName("mountDDIButton")
        self.mount_button.clicked.connect(self.mount_selected_ddi)
        button_layout.addWidget(self.mount_button)
        status_button = QPushButton("List Mounted Images")
        status_button.setObjectName("listMountedImagesButton")
        status_button.clicked.connect(self.list_mounted_images)
        button_layout.addWidget(status_button)
        self.remove_button = QPushButton("Unmount Personalized DDI")
        self.remove_button.setObjectName("removeDDIButton")
        self.remove_button.clicked.connect(self.remove_selected_ddi)
        button_layout.addWidget(self.remove_button)
        button_layout.addStretch()
        ddi_layout.addLayout(button_layout)
        layout.addWidget(ddi_group)

        self.action_output = QPlainTextEdit()
        self.action_output.setObjectName("ddiActionOutput")
        self.action_output.setReadOnly(True)
        self.action_output.setMaximumBlockCount(3000)
        self.action_output.setPlaceholderText("DDI and Developer Mode command output appears here.")
        layout.addWidget(self.action_output, 1)
        self._ddi_source_changed()
        return tab

    def _build_collection_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(14)

        destination_group = QGroupBox("Evidence case")
        destination_layout = QFormLayout(destination_group)
        output_row = QHBoxLayout()
        self.output_root = QLineEdit(str(Path.home() / "Documents" / "iOS Developer Toolkit Cases"))
        self.output_root.setObjectName("evidenceOutputRoot")
        output_row.addWidget(self.output_root, 1)
        browse_button = QPushButton("Choose…")
        browse_button.setObjectName("chooseEvidenceFolderButton")
        browse_button.clicked.connect(self.choose_output_root)
        output_row.addWidget(browse_button)
        destination_layout.addRow("Store case folders in", output_row)
        self.capture_duration = QSpinBox()
        self.capture_duration.setObjectName("captureDurationSeconds")
        self.capture_duration.setRange(10, 3600)
        self.capture_duration.setValue(300)
        self.capture_duration.setSuffix(" seconds")
        destination_layout.addRow("Live capture duration", self.capture_duration)
        layout.addWidget(destination_group)

        options_group = QGroupBox("Collection coverage")
        options_layout = QGridLayout(options_group)
        self.include_syslog = QCheckBox("Classic syslog stream")
        self.include_syslog.setChecked(True)
        self.include_oslog = QCheckBox("DVT structured Unified Logging stream")
        self.include_oslog.setChecked(True)
        self.include_pcap = QCheckBox("Network PCAP with process metadata")
        self.include_pcap.setChecked(True)
        self.include_screenshot = QCheckBox("Capture current screen")
        self.include_crash_pull = QCheckBox("Pull all crash reports")
        for checkbox, name in (
            (self.include_syslog, "includeSyslog"),
            (self.include_oslog, "includeDVTOSLog"),
            (self.include_pcap, "includePCAP"),
            (self.include_screenshot, "includeScreenshot"),
            (self.include_crash_pull, "includeCrashPull"),
        ):
            checkbox.setObjectName(name)
        options_layout.addWidget(self.include_syslog, 0, 0)
        options_layout.addWidget(self.include_oslog, 0, 1)
        options_layout.addWidget(self.include_pcap, 1, 0)
        options_layout.addWidget(self.include_screenshot, 1, 1)
        options_layout.addWidget(self.include_crash_pull, 2, 0)
        coverage_note = QLabel(
            "Every run also inventories lockdown, mounted images, diagnostics, MobileGestalt, IORegistry, battery, apps, "
            "processes, profiles, provisioning, AFC, crash names, DVT device data, DVT sysmon, and the DVT root listing."
        )
        coverage_note.setWordWrap(True)
        options_layout.addWidget(coverage_note, 3, 0, 1, 2)
        layout.addWidget(options_group)

        privacy = QLabel(
            "PCAP, logs, screenshots, UDIDs, app lists, and crash reports can contain private information. "
            "The output stays in the selected local folder; review and sanitize it before sharing."
        )
        privacy.setObjectName("collectionPrivacyWarning")
        privacy.setWordWrap(True)
        layout.addWidget(privacy)

        controls = QHBoxLayout()
        self.start_collection_button = QPushButton("Start Evidence Collection")
        self.start_collection_button.setObjectName("startCollectionButton")
        self.start_collection_button.clicked.connect(self.start_collection)
        controls.addWidget(self.start_collection_button)
        self.stop_collection_button = QPushButton("Stop & Finalize")
        self.stop_collection_button.setObjectName("stopCollectionButton")
        self.stop_collection_button.setEnabled(False)
        self.stop_collection_button.clicked.connect(self.stop_collection)
        controls.addWidget(self.stop_collection_button)
        self.open_case_button = QPushButton("Open Last Case")
        self.open_case_button.setObjectName("openLastCaseButton")
        self.open_case_button.setEnabled(False)
        self.open_case_button.clicked.connect(self.open_last_case)
        controls.addWidget(self.open_case_button)
        controls.addStretch()
        layout.addLayout(controls)

        self.collection_output = QPlainTextEdit()
        self.collection_output.setObjectName("collectionOutput")
        self.collection_output.setReadOnly(True)
        self.collection_output.setMaximumBlockCount(7000)
        layout.addWidget(self.collection_output, 1)
        return tab

    def _build_sideload_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(12)

        package_group = QGroupBox("IPA package and local verification")
        package_layout = QVBoxLayout(package_group)
        package_row = QHBoxLayout()
        self.ipa_path_field = QLineEdit()
        self.ipa_path_field.setObjectName("ipaPathField")
        self.ipa_path_field.setReadOnly(True)
        self.ipa_path_field.setPlaceholderText("Choose a locally stored .ipa package")
        package_row.addWidget(self.ipa_path_field, 1)
        self.choose_ipa_button = QPushButton("Choose IPA…")
        self.choose_ipa_button.setObjectName("chooseIPAButton")
        self.choose_ipa_button.clicked.connect(self.choose_ipa)
        package_row.addWidget(self.choose_ipa_button)
        package_layout.addLayout(package_row)

        self.ipa_inspection_progress = QProgressBar()
        self.ipa_inspection_progress.setObjectName("ipaInspectionProgress")
        self.ipa_inspection_progress.setRange(0, 0)
        self.ipa_inspection_progress.setVisible(False)
        package_layout.addWidget(self.ipa_inspection_progress)

        self.ipa_inspection_summary = QPlainTextEdit()
        self.ipa_inspection_summary.setObjectName("ipaInspectionSummary")
        self.ipa_inspection_summary.setReadOnly(True)
        self.ipa_inspection_summary.setMaximumBlockCount(500)
        self.ipa_inspection_summary.setMaximumHeight(245)
        self.ipa_inspection_summary.setPlaceholderText(
            "The toolkit will validate the IPA archive, decode embedded provisioning metadata, "
            "extract it into a temporary directory, and ask macOS codesign to verify the app bundle."
        )
        package_layout.addWidget(self.ipa_inspection_summary)
        layout.addWidget(package_group)

        install_group = QGroupBox("Install on the selected device")
        install_layout = QHBoxLayout(install_group)
        self.developer_package_checkbox = QCheckBox("Install as developer package")
        self.developer_package_checkbox.setObjectName("developerPackageCheckbox")
        install_layout.addWidget(self.developer_package_checkbox)
        install_layout.addStretch()
        self.install_ipa_button = QPushButton("Install Verified IPA")
        self.install_ipa_button.setObjectName("installIPAButton")
        self.install_ipa_button.setEnabled(False)
        self.install_ipa_button.clicked.connect(self.install_selected_ipa)
        install_layout.addWidget(self.install_ipa_button)
        self.stop_sideload_button = QPushButton("Stop")
        self.stop_sideload_button.setObjectName("stopSideloadButton")
        self.stop_sideload_button.setEnabled(False)
        self.stop_sideload_button.clicked.connect(self.stop_sideload_action)
        install_layout.addWidget(self.stop_sideload_button)
        layout.addWidget(install_group)

        self.sideload_status = QLabel(
            "Only correctly signed and provisioned packages can run on stock iOS. "
            "The DDI does not sign an IPA or bypass Apple installation policy."
        )
        self.sideload_status.setObjectName("sideloadStatus")
        self.sideload_status.setWordWrap(True)
        layout.addWidget(self.sideload_status)

        self.sideload_activity_progress = QProgressBar()
        self.sideload_activity_progress.setObjectName("sideloadActivityProgress")
        self.sideload_activity_progress.setRange(0, 0)
        self.sideload_activity_progress.setVisible(False)
        layout.addWidget(self.sideload_activity_progress)

        self.sideload_output = QPlainTextEdit()
        self.sideload_output.setObjectName("sideloadOutput")
        self.sideload_output.setReadOnly(True)
        self.sideload_output.setMaximumBlockCount(7000)
        layout.addWidget(self.sideload_output, 1)
        return tab

    def _build_installed_apps_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(12)

        controls = QHBoxLayout()
        self.app_filter_field = QLineEdit()
        self.app_filter_field.setObjectName("installedAppsFilter")
        self.app_filter_field.setPlaceholderText("Filter by app name, bundle ID, version, or type")
        self.app_filter_field.textChanged.connect(self._filter_installed_apps)
        controls.addWidget(self.app_filter_field, 1)
        self.calculate_app_sizes_checkbox = QCheckBox("Calculate sizes")
        self.calculate_app_sizes_checkbox.setObjectName("calculateInstalledAppSizes")
        controls.addWidget(self.calculate_app_sizes_checkbox)
        self.refresh_apps_button = QPushButton("Refresh")
        self.refresh_apps_button.setObjectName("refreshInstalledAppsButton")
        self.refresh_apps_button.clicked.connect(self.refresh_app_inventory)
        controls.addWidget(self.refresh_apps_button)
        layout.addLayout(controls)

        self.installed_apps_table = QTableWidget(0, 6)
        self.installed_apps_table.setObjectName("installedAppsTable")
        self.installed_apps_table.setHorizontalHeaderLabels(
            ("Name", "Bundle ID", "Version", "Build", "Type", "Size")
        )
        self.installed_apps_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.installed_apps_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.installed_apps_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.installed_apps_table.setAlternatingRowColors(True)
        self.installed_apps_table.setSortingEnabled(True)
        self.installed_apps_table.itemSelectionChanged.connect(self._installed_app_selection_changed)
        header = self.installed_apps_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for column in range(2, 6):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.installed_apps_table, 1)

        action_row = QHBoxLayout()
        self.apps_status = QLabel("Connect a trusted device, then refresh the inventory.")
        self.apps_status.setObjectName("installedAppsStatus")
        self.apps_status.setWordWrap(True)
        action_row.addWidget(self.apps_status, 1)
        self.copy_bundle_id_button = QPushButton("Copy Bundle ID")
        self.copy_bundle_id_button.setObjectName("copyInstalledAppBundleID")
        self.copy_bundle_id_button.clicked.connect(self.copy_selected_bundle_identifier)
        action_row.addWidget(self.copy_bundle_id_button)
        self.uninstall_app_button = QPushButton("Uninstall Selected…")
        self.uninstall_app_button.setObjectName("uninstallSelectedAppButton")
        self.uninstall_app_button.clicked.connect(self.uninstall_selected_application)
        action_row.addWidget(self.uninstall_app_button)
        self.stop_apps_button = QPushButton("Stop")
        self.stop_apps_button.setObjectName("stopInstalledAppsOperationButton")
        self.stop_apps_button.clicked.connect(self.stop_apps_action)
        action_row.addWidget(self.stop_apps_button)
        layout.addLayout(action_row)

        privacy = QLabel(
            "The inventory can reveal sensitive app usage. It stays in memory unless you include app inventory in an evidence collection."
        )
        privacy.setObjectName("installedAppsPrivacyWarning")
        privacy.setWordWrap(True)
        layout.addWidget(privacy)

        self.apps_output = QPlainTextEdit()
        self.apps_output.setObjectName("installedAppsOutput")
        self.apps_output.setReadOnly(True)
        self.apps_output.setMaximumBlockCount(3000)
        self.apps_output.setMaximumHeight(145)
        layout.addWidget(self.apps_output)
        self._update_apps_controls()
        return tab

    def _build_backup_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(12)

        heading = QLabel("Backup providers")
        heading.setObjectName("pageTitle")
        heading.setFont(QFont(heading.font().family(), 20, QFont.Weight.Bold))
        layout.addWidget(heading)
        explanation = QLabel(
            "Choose the built-in MobileBackup2 workflow or launch a separately installed UFADE forensic acquisition. "
            "The providers use isolated runtimes and do not share passwords or dependencies."
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)

        provider_tabs = QTabWidget()
        provider_tabs.setObjectName("backupProviderTabs")
        provider_tabs.addTab(self._build_mobilebackup_page(), "MobileBackup2")
        provider_tabs.addTab(self._build_ufade_backup_page(), "UFADE External")
        provider_tabs.setTabToolTip(0, "Toolkit-managed full or incremental iTunes-style backup")
        provider_tabs.setTabToolTip(1, "Launch an independently installed UFADE acquisition environment")
        layout.addWidget(provider_tabs, 1)
        return page

    def _build_mobilebackup_page(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(12)

        destination_group = QGroupBox("Local backup destination")
        destination_layout = QFormLayout(destination_group)
        destination_row = QHBoxLayout()
        self.backup_destination_field = QLineEdit(str(Path.home() / "Documents" / "iOS Developer Toolkit Backups"))
        self.backup_destination_field.setObjectName("backupDestination")
        destination_row.addWidget(self.backup_destination_field, 1)
        choose_destination_button = QPushButton("Choose…")
        choose_destination_button.setObjectName("chooseBackupDestinationButton")
        choose_destination_button.clicked.connect(self.choose_backup_destination)
        destination_row.addWidget(choose_destination_button)
        self.open_backup_button = QPushButton("Open Folder")
        self.open_backup_button.setObjectName("openBackupFolderButton")
        self.open_backup_button.clicked.connect(self.open_backup_folder)
        destination_row.addWidget(self.open_backup_button)
        destination_layout.addRow("Store backups in", destination_row)
        self.full_backup_checkbox = QCheckBox("Force a full backup instead of reusing valid incremental state")
        self.full_backup_checkbox.setObjectName("forceFullBackup")
        destination_layout.addRow("Backup mode", self.full_backup_checkbox)
        layout.addWidget(destination_group)

        encryption_group = QGroupBox("Backup encryption")
        encryption_layout = QFormLayout(encryption_group)
        encryption_status_row = QHBoxLayout()
        self.backup_encryption_status = QLabel("Encryption state not checked for this device")
        self.backup_encryption_status.setObjectName("backupEncryptionStatus")
        encryption_status_row.addWidget(self.backup_encryption_status, 1)
        self.check_encryption_button = QPushButton("Check Status")
        self.check_encryption_button.setObjectName("checkBackupEncryptionButton")
        self.check_encryption_button.clicked.connect(self.check_backup_encryption)
        encryption_status_row.addWidget(self.check_encryption_button)
        encryption_layout.addRow("Device setting", encryption_status_row)
        self.require_encryption_checkbox = QCheckBox("Require encrypted backup (enable persistent encryption if needed)")
        self.require_encryption_checkbox.setObjectName("requireEncryptedBackup")
        self.require_encryption_checkbox.setChecked(True)
        self.require_encryption_checkbox.toggled.connect(self._backup_encryption_choice_changed)
        encryption_layout.addRow("Policy", self.require_encryption_checkbox)
        self.backup_password_field = QLineEdit()
        self.backup_password_field.setObjectName("newBackupEncryptionPassword")
        self.backup_password_field.setEchoMode(QLineEdit.EchoMode.Password)
        self.backup_password_field.setPlaceholderText("New password, only if encryption is currently off")
        encryption_layout.addRow("New password", self.backup_password_field)
        self.backup_password_confirmation_field = QLineEdit()
        self.backup_password_confirmation_field.setObjectName("confirmBackupEncryptionPassword")
        self.backup_password_confirmation_field.setEchoMode(QLineEdit.EchoMode.Password)
        self.backup_password_confirmation_field.setPlaceholderText("Enter the new password again")
        encryption_layout.addRow("Confirm password", self.backup_password_confirmation_field)
        warning = QLabel(
            "Encryption is a persistent device backup setting. The password is sent only through a private helper input stream and is never logged or saved. "
            "Store it safely: previous encrypted backups cannot be restored without their password. This toolkit never disables encryption automatically."
        )
        warning.setObjectName("backupEncryptionWarning")
        warning.setWordWrap(True)
        encryption_layout.addRow(warning)
        layout.addWidget(encryption_group)

        controls = QHBoxLayout()
        self.start_backup_button = QPushButton("Start Backup…")
        self.start_backup_button.setObjectName("startDeviceBackupButton")
        self.start_backup_button.clicked.connect(self.start_backup)
        controls.addWidget(self.start_backup_button)
        self.stop_backup_button = QPushButton("Stop")
        self.stop_backup_button.setObjectName("stopDeviceBackupButton")
        self.stop_backup_button.clicked.connect(self.stop_backup)
        controls.addWidget(self.stop_backup_button)
        controls.addStretch()
        layout.addLayout(controls)

        self.backup_progress = QProgressBar()
        self.backup_progress.setObjectName("deviceBackupProgress")
        self.backup_progress.setRange(0, 100)
        self.backup_progress.setValue(0)
        layout.addWidget(self.backup_progress)

        self.backup_output = QPlainTextEdit()
        self.backup_output.setObjectName("deviceBackupOutput")
        self.backup_output.setReadOnly(True)
        self.backup_output.setMaximumBlockCount(7000)
        layout.addWidget(self.backup_output, 1)
        self._backup_encryption_choice_changed(self.require_encryption_checkbox.isChecked())
        self._update_backup_controls()
        return tab

    def _build_ufade_backup_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(12)

        overview = QLabel(
            "UFADE (Universal Forensic Apple Device Extractor) is an independent GPL-3.0 application. "
            "This toolkit validates and launches a user-managed UFADE checkout; it does not vendor, import, modify, "
            "or redistribute UFADE and does not read UFADE passwords or acquisition output."
        )
        overview.setObjectName("ufadeProviderExplanation")
        overview.setWordWrap(True)
        layout.addWidget(overview)

        types_group = QGroupBox("Acquisition types selected inside UFADE")
        types_layout = QVBoxLayout(types_group)
        types = QLabel(
            "• Logical — iTunes-style MobileBackup2 acquisition.\n"
            "• Logical+ — backup plus AFC media, shared app folders, crash reports, and optional Unified Logs.\n"
            "• Logical+ UFD — advanced logical ZIP with a UFD descriptor for compatible forensic tooling.\n"
            "• PRFS — decrypted, filesystem-shaped logical archive assembled from service-visible data.\n"
            "• Full filesystem — only for a device that is already jailbroken; UFADE does not provide a bypass."
        )
        types.setWordWrap(True)
        types_layout.addWidget(types)
        layout.addWidget(types_group)

        setup_group = QGroupBox("Separate UFADE installation")
        setup_layout = QFormLayout(setup_group)

        checkout_row = QHBoxLayout()
        self.ufade_checkout_field = QLineEdit()
        self.ufade_checkout_field.setObjectName("ufadeCheckout")
        self.ufade_checkout_field.setPlaceholderText("Absolute path to a cloned prosch88/UFADE checkout")
        self.ufade_checkout_field.textChanged.connect(self._invalidate_ufade_validation)
        checkout_row.addWidget(self.ufade_checkout_field, 1)
        choose_checkout = QPushButton("Choose…")
        choose_checkout.setObjectName("chooseUFADECheckoutButton")
        choose_checkout.clicked.connect(self.choose_ufade_checkout)
        checkout_row.addWidget(choose_checkout)
        setup_layout.addRow("UFADE checkout", checkout_row)

        python_row = QHBoxLayout()
        self.ufade_python_field = QLineEdit()
        self.ufade_python_field.setObjectName("ufadePythonExecutable")
        self.ufade_python_field.setPlaceholderText("UFADE's separate Python 3.11 virtual-environment executable")
        self.ufade_python_field.textChanged.connect(self._invalidate_ufade_validation)
        python_row.addWidget(self.ufade_python_field, 1)
        choose_python = QPushButton("Choose…")
        choose_python.setObjectName("chooseUFADEPythonButton")
        choose_python.clicked.connect(self.choose_ufade_python)
        python_row.addWidget(choose_python)
        setup_layout.addRow("Python 3.11", python_row)

        output_row = QHBoxLayout()
        self.ufade_output_field = QLineEdit(str(Path.home() / "Documents" / "UFADE Acquisitions"))
        self.ufade_output_field.setObjectName("ufadeOutputDirectory")
        output_row.addWidget(self.ufade_output_field, 1)
        choose_output = QPushButton("Choose…")
        choose_output.setObjectName("chooseUFADEOutputButton")
        choose_output.clicked.connect(self.choose_ufade_output_directory)
        output_row.addWidget(choose_output)
        self.open_ufade_output_button = QPushButton("Open Folder")
        self.open_ufade_output_button.setObjectName("openUFADEOutputButton")
        self.open_ufade_output_button.clicked.connect(self.open_ufade_output_directory)
        output_row.addWidget(self.open_ufade_output_button)
        setup_layout.addRow("Working/output folder", output_row)

        self.ufade_validation_status = QLabel("UFADE installation has not been validated")
        self.ufade_validation_status.setObjectName("ufadeValidationStatus")
        self.ufade_validation_status.setWordWrap(True)
        setup_layout.addRow("Status", self.ufade_validation_status)
        layout.addWidget(setup_group)

        controls = QHBoxLayout()
        validate_button = QPushButton("Validate Installation")
        validate_button.setObjectName("validateUFADEButton")
        validate_button.clicked.connect(self.validate_ufade_from_ui)
        controls.addWidget(validate_button)
        setup_button = QPushButton("Copy Setup Commands")
        setup_button.setObjectName("copyUFADESetupButton")
        setup_button.clicked.connect(self.copy_ufade_setup_commands)
        controls.addWidget(setup_button)
        repository_button = QPushButton("Open UFADE Repository")
        repository_button.setObjectName("openUFADERepositoryButton")
        repository_button.clicked.connect(self.open_ufade_repository)
        controls.addWidget(repository_button)
        self.launch_ufade_button = QPushButton("Launch UFADE…")
        self.launch_ufade_button.setObjectName("launchUFADEButton")
        self.launch_ufade_button.clicked.connect(self.launch_ufade)
        controls.addWidget(self.launch_ufade_button)
        controls.addStretch()
        layout.addLayout(controls)

        warning = QLabel(
            "UFADE runs as a separate process with its own UI, dependency versions, device selection, password handling, "
            "stop controls, and output formats. Keep only the intended device connected and review UFADE's own prompts."
        )
        warning.setObjectName("ufadeBoundaryWarning")
        warning.setWordWrap(True)
        layout.addWidget(warning)

        self.ufade_output = QPlainTextEdit()
        self.ufade_output.setObjectName("ufadeProviderOutput")
        self.ufade_output.setReadOnly(True)
        self.ufade_output.setMaximumBlockCount(1000)
        layout.addWidget(self.ufade_output, 1)
        self._update_backup_controls()
        return page

    def _build_command_center_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(12)

        heading = QLabel("Command Center")
        heading.setObjectName("pageTitle")
        heading.setFont(QFont(heading.font().family(), 20, QFont.Weight.Bold))
        layout.addWidget(heading)
        explanation = QLabel(
            "Choose a guided preset for the pinned pymobiledevice3 syntax. Parameters are validated and the exact "
            "argument vector is shown before execution. Advanced mode remains available without invoking a shell."
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)

        browser_splitter = QSplitter(Qt.Orientation.Horizontal)
        browser_splitter.setObjectName("commandBrowserSplitter")
        browser_splitter.setMaximumHeight(390)

        preset_browser = QFrame()
        preset_browser.setObjectName("commandPresetBrowser")
        preset_browser.setMinimumWidth(300)
        preset_browser_layout = QVBoxLayout(preset_browser)
        self.command_category_combo = QComboBox()
        self.command_category_combo.setObjectName("commandCategory")
        self.command_category_combo.addItem("All categories")
        self.command_category_combo.addItems(preset_categories())
        self.command_category_combo.currentIndexChanged.connect(self._filter_command_presets)
        preset_browser_layout.addWidget(self.command_category_combo)
        self.command_search_field = QLineEdit()
        self.command_search_field.setObjectName("commandPresetSearch")
        self.command_search_field.setPlaceholderText("Search guided commands")
        self.command_search_field.textChanged.connect(self._filter_command_presets)
        preset_browser_layout.addWidget(self.command_search_field)
        self.command_preset_list = QListWidget()
        self.command_preset_list.setObjectName("commandPresetList")
        self.command_preset_list.currentItemChanged.connect(self._command_preset_selected)
        preset_browser_layout.addWidget(self.command_preset_list, 1)
        browser_splitter.addWidget(preset_browser)

        detail_frame = QFrame()
        detail_frame.setObjectName("commandPresetDetail")
        detail_layout = QVBoxLayout(detail_frame)
        title_row = QHBoxLayout()
        self.command_preset_title = QLabel("Choose a preset")
        self.command_preset_title.setObjectName("commandPresetTitle")
        self.command_preset_title.setFont(QFont(self.command_preset_title.font().family(), 16, QFont.Weight.DemiBold))
        title_row.addWidget(self.command_preset_title, 1)
        self.command_risk_badge = QLabel("—")
        self.command_risk_badge.setObjectName("commandRiskBadge")
        title_row.addWidget(self.command_risk_badge)
        detail_layout.addLayout(title_row)
        self.command_summary = QLabel("Select a command to see capability, prerequisites, and interpretation limits.")
        self.command_summary.setWordWrap(True)
        detail_layout.addWidget(self.command_summary)
        self.command_advanced_notes = QLabel("")
        self.command_advanced_notes.setObjectName("commandAdvancedNotes")
        self.command_advanced_notes.setWordWrap(True)
        detail_layout.addWidget(self.command_advanced_notes)
        self.command_prerequisites = QLabel("")
        self.command_prerequisites.setObjectName("commandPrerequisites")
        self.command_prerequisites.setWordWrap(True)
        detail_layout.addWidget(self.command_prerequisites)

        self.preset_parameters_group = QGroupBox("Required values")
        self.preset_parameters_layout = QFormLayout(self.preset_parameters_group)
        detail_layout.addWidget(self.preset_parameters_group)
        self.command_preview = QLineEdit()
        self.command_preview.setObjectName("guidedCommandPreview")
        self.command_preview.setReadOnly(True)
        detail_layout.addWidget(self.command_preview)
        button_row = QHBoxLayout()
        self.preset_run_button = QPushButton("Run Guided Command")
        self.preset_run_button.setObjectName("runGuidedCommandButton")
        self.preset_run_button.clicked.connect(self.run_selected_preset)
        button_row.addWidget(self.preset_run_button)
        self.console_stop_button = QPushButton("Stop")
        self.console_stop_button.setObjectName("stopConsoleCommandButton")
        self.console_stop_button.clicked.connect(self.stop_console_command)
        button_row.addWidget(self.console_stop_button)
        self.preset_help_button = QPushButton("Open Live Help")
        self.preset_help_button.setObjectName("openPresetManPageButton")
        self.preset_help_button.clicked.connect(self.open_selected_preset_help)
        button_row.addWidget(self.preset_help_button)
        button_row.addStretch()
        detail_layout.addLayout(button_row)
        browser_splitter.addWidget(detail_frame)
        browser_splitter.setSizes([330, 800])
        browser_splitter.setStretchFactor(0, 1)
        browser_splitter.setStretchFactor(1, 2)
        layout.addWidget(browser_splitter)

        advanced_group = QGroupBox("Advanced arguments")
        advanced_layout = QHBoxLayout(advanced_group)
        self.console_input = QLineEdit()
        self.console_input.setObjectName("consoleCommandInput")
        self.console_input.setPlaceholderText("Example: developer dvt device-information --userspace")
        self.console_input.returnPressed.connect(self.run_console_command)
        self.console_input.textChanged.connect(self._update_command_controls)
        advanced_layout.addWidget(self.console_input, 1)
        self.console_run_button = QPushButton("Run Advanced")
        self.console_run_button.setObjectName("runConsoleCommandButton")
        self.console_run_button.clicked.connect(self.run_console_command)
        advanced_layout.addWidget(self.console_run_button)
        layout.addWidget(advanced_group)

        self.console_output = QPlainTextEdit()
        self.console_output.setObjectName("consoleOutput")
        self.console_output.setReadOnly(True)
        self.console_output.setMaximumBlockCount(12000)
        self.console_output.setPlaceholderText("Command output appears here. Long-running streams continue until Stop is pressed.")
        layout.addWidget(self.console_output, 1)
        self._filter_command_presets()
        self._update_command_controls()
        return page

    def _build_manpages_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(12)

        heading = QLabel("Man Pages & Possibilities")
        heading.setObjectName("pageTitle")
        heading.setFont(QFont(heading.font().family(), 20, QFont.Weight.Bold))
        layout.addWidget(heading)
        explanation = QLabel(
            "This browser runs the installed pymobiledevice3 with --help and displays its output verbatim. "
            "It is the authoritative syntax for this environment; the attachment is used as the command-family map."
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setObjectName("manpageSplitter")
        index_frame = QFrame()
        index_frame.setMinimumWidth(300)
        index_layout = QVBoxLayout(index_frame)
        self.manpage_search_field = QLineEdit()
        self.manpage_search_field.setObjectName("manpageSearch")
        self.manpage_search_field.setPlaceholderText("Search services and command paths")
        self.manpage_search_field.textChanged.connect(self._filter_manpages)
        index_layout.addWidget(self.manpage_search_field)
        self.manpage_list = QListWidget()
        self.manpage_list.setObjectName("manpageList")
        self.manpage_list.currentItemChanged.connect(self._manpage_selected)
        index_layout.addWidget(self.manpage_list, 1)
        splitter.addWidget(index_frame)

        content_frame = QFrame()
        content_layout = QVBoxLayout(content_frame)
        self.manpage_title = QLabel("Select a help topic")
        self.manpage_title.setObjectName("manpageTitle")
        self.manpage_title.setFont(QFont(self.manpage_title.font().family(), 16, QFont.Weight.DemiBold))
        content_layout.addWidget(self.manpage_title)
        self.manpage_command = QLineEdit()
        self.manpage_command.setObjectName("manpageCommand")
        self.manpage_command.setReadOnly(True)
        content_layout.addWidget(self.manpage_command)
        manpage_actions = QHBoxLayout()
        self.refresh_manpage_button = QPushButton("Refresh Live Help")
        self.refresh_manpage_button.setObjectName("refreshManpageButton")
        self.refresh_manpage_button.clicked.connect(self.refresh_selected_manpage)
        manpage_actions.addWidget(self.refresh_manpage_button)
        self.copy_manpage_command_button = QPushButton("Copy Command Prefix")
        self.copy_manpage_command_button.setObjectName("copyManpageCommandButton")
        self.copy_manpage_command_button.clicked.connect(self.copy_selected_manpage_command)
        manpage_actions.addWidget(self.copy_manpage_command_button)
        self.use_manpage_command_button = QPushButton("Use in Advanced Mode")
        self.use_manpage_command_button.setObjectName("useManpageCommandButton")
        self.use_manpage_command_button.clicked.connect(self.use_selected_manpage_command)
        manpage_actions.addWidget(self.use_manpage_command_button)
        manpage_actions.addStretch()
        content_layout.addLayout(manpage_actions)
        self.manpage_output = QPlainTextEdit()
        self.manpage_output.setObjectName("manpageOutput")
        self.manpage_output.setReadOnly(True)
        self.manpage_output.setMaximumBlockCount(12000)
        content_layout.addWidget(self.manpage_output, 1)
        splitter.addWidget(content_frame)
        splitter.setSizes([330, 850])
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        layout.addWidget(splitter, 1)
        self._filter_manpages()
        self._update_manpage_controls()
        return page

    def _build_safety_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        browser = QTextBrowser()
        browser.setOpenExternalLinks(True)
        browser.setHtml(
            f"""
            <h2>What this app does</h2>
            <p>It is a guided macOS workbench for <code>pymobiledevice3</code>: pairing-visible device inspection,
            apps and AFC, backups, diagnostics, logging, packet capture, crash reports, Web Inspector, RemoteXPC,
            Developer Disk Images, CoreDevice, DVT instrumentation, and evidence-oriented collection.</p>
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
              <li>Restore, erase, activation, supervision, reboot, shutdown, and nonce-roll commands can be high impact.
              They are documented in Man Pages but are not promoted as guided presets.</li>
              <li>A command existing in pymobiledevice3 does not guarantee the selected iOS build advertises its Apple service.</li>
            </ul>
            <h2>Sources</h2>
            <p><a href="{DEVELOPER_DISK_IMAGE_REPOSITORY}">DeveloperDiskImage repository</a><br>
            <a href="https://doronz88.github.io/pymobiledevice3/">pymobiledevice3 documentation</a><br>
            <a href="https://developer.apple.com/documentation/xcode/enabling-developer-mode-on-a-device">Apple Developer Mode guidance</a></p>
            """
        )
        layout.addWidget(browser)
        return tab

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
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
            #installedAppsPrivacyWarning, #backupEncryptionWarning { background: #fff5df; border: 1px solid #e7c36a; border-radius: 8px; padding: 10px; }
            #appSubtitle { color: #596273; }
            """
        )

    def selected_device(self) -> IOSDevice | None:
        index = self.device_combo.currentIndex()
        if index < 0 or index >= len(self._devices):
            return None
        return self._devices[index]

    def _scanner_scan(self) -> None:
        self._scanner.scan()

    def _devices_changed(self, devices_object: object) -> None:
        if not isinstance(devices_object, tuple) or not all(isinstance(item, IOSDevice) for item in devices_object):
            self.connection_banner.setText("Device scanner returned an unexpected result type.")
            return
        devices = tuple(devices_object)
        previous_identifier = self.selected_device().identifier if self.selected_device() is not None else None
        changed = devices != self._devices
        self._devices = devices
        if changed:
            self.device_combo.blockSignals(True)
            self.device_combo.clear()
            for device in devices:
                self.device_combo.addItem(device.display_name(), device.identifier)
            if previous_identifier is not None:
                matching_index = next(
                    (index for index, device in enumerate(devices) if device.identifier == previous_identifier),
                    0,
                )
                self.device_combo.setCurrentIndex(matching_index)
            self.device_combo.blockSignals(False)
        if not devices:
            self.connection_banner.setText("No device detected. Connect by USB, unlock it, and tap Trust.")
            self._update_device_fields(None)
            return
        self.connection_banner.setText(f"Detected {len(devices)} trusted iOS device(s). Select the intended target before mounting or collecting.")
        self._update_device_fields(self.selected_device())
        selected = self.selected_device()
        if selected is not None and selected.identifier not in self._guided_udids:
            self._guided_udids.add(selected.identifier)
            QTimer.singleShot(350, self.show_developer_mode_guide)

    def _scan_error(self, message: str) -> None:
        self.connection_banner.setText(f"Device discovery error: {message}")

    def _device_selected(self, index: int) -> None:
        del index
        self._update_device_fields(self.selected_device())
        self.developer_mode_status.setText("Status not checked for this device")

    def _update_device_fields(self, device: IOSDevice | None) -> None:
        identifier = device.identifier if device is not None else None
        if identifier != self._active_device_identifier:
            self._active_device_identifier = identifier
            self._backup_encryption_state = None
            self.backup_encryption_status.setText("Encryption state not checked for this device")
            self._installed_apps = ()
            self._populate_installed_apps(())
            self.apps_status.setText(
                "Refresh to load apps from the selected device."
                if device is not None
                else "Connect a trusted device, then refresh the inventory."
            )
        enabled = device is not None
        self.mount_button.setEnabled(enabled)
        self.remove_button.setEnabled(enabled)
        self.start_collection_button.setEnabled(enabled and self._collection_process is None)
        self._update_apps_controls()
        self._update_backup_controls()
        self._update_command_controls()
        if device is None:
            self.device_name_value.setText("No device")
            self.device_version_value.setText("—")
            self.device_model_value.setText("—")
            self.device_udid_value.setText("—")
            self._update_sideload_controls()
            return
        self.device_name_value.setText(device.name)
        self.device_version_value.setText(f"{device.product_version} / {device.build_version}")
        self.device_model_value.setText(device.product_type)
        self.device_udid_value.setText(device.identifier)
        self._update_sideload_controls()

    def show_developer_mode_guide(self) -> None:
        DeveloperModeDialog().exec()

    def check_developer_mode(self) -> None:
        self._run_pmd3_action(("mounter", "query-developer-mode-status"), "developer-mode-status")

    def _ddi_source_changed(self) -> None:
        if self.personalized_radio.isChecked():
            self.mount_button.setText("Mount Personalized DDI")
            self.remove_button.setText("Unmount Personalized DDI")
            self.ddi_description.setHtml(
                f"<b>Downloaded personalized image:</b> fetches the APFS image, BuildManifest, and trust cache from "
                f"<a href='{DEVELOPER_DISK_IMAGE_REPOSITORY}'>DeveloperDiskImage</a>, caches them under "
                "<code>~/.pymobiledevice3/Xcode_iOS_DDI_Personalized</code>, requests an Apple TSS ticket, and mounts "
                "the result at <code>/System/Developer</code>. This is the simplest current path."
            )
        else:
            exists_text = "available" if XCODE_CANDIDATE_DDI.is_file() else "not found"
            self.mount_button.setText("Install Local Xcode DDI Cryptex")
            self.remove_button.setText("Uninstall Local DDI Cryptex")
            self.ddi_description.setHtml(
                f"<b>Local Apple/Xcode image ({exists_text}):</b> read-only attaches the outer candidate at "
                f"<code>{XCODE_CANDIDATE_DDI}</code>, uses its <code>Restore</code> payload, personalizes it through "
                "Apple TSS, installs it as <code>com.apple.MobileAsset.DDI</code>, then detaches the Mac-side image. "
                "The outer DMG itself is never sent directly to iOS."
            )

    def mount_selected_ddi(self) -> None:
        device = self.selected_device()
        if device is None:
            self._show_no_device()
            return
        if self.personalized_radio.isChecked():
            prompt = (
                "Mount the downloaded personalized Developer Disk Image?\n\n"
                "This downloads files from GitHub, sends personalization identifiers and a nonce to Apple TSS, "
                "uploads the image, and changes the device's mounted state."
            )
            if not self._confirm("Mount Personalized DDI", prompt):
                return
            self._run_pmd3_action(("mounter", "auto-mount"), "mount-personalized")
            return
        if not XCODE_CANDIDATE_DDI.is_file():
            QMessageBox.critical(self, "Local DDI Missing", f"The Xcode candidate DDI was not found:\n{XCODE_CANDIDATE_DDI}")
            return
        prompt = (
            "Install the local Xcode DDI as a personalized Cryptex?\n\n"
            "The Apple DMG is attached read-only on this Mac, its Restore payload is personalized through Apple TSS, "
            "and com.apple.MobileAsset.DDI is installed on the selected device."
        )
        if not self._confirm("Install Local Xcode DDI", prompt):
            return
        self._start_action(
            Path(sys.executable),
            ("-m", "ios_developer_toolkit.local_ddi", "--candidate", str(XCODE_CANDIDATE_DDI), "--udid", device.identifier),
            base_environment(),
            "mount-local-cryptex",
        )

    def remove_selected_ddi(self) -> None:
        if self.personalized_radio.isChecked():
            if self._confirm("Unmount Personalized DDI", "Unmount the personalized image from /System/Developer?"):
                self._run_pmd3_action(("mounter", "umount-personalized"), "unmount-personalized")
            return
        if self._confirm(
            "Uninstall Local DDI Cryptex",
            "Uninstall com.apple.MobileAsset.DDI from the selected device?",
        ):
            self._run_pmd3_action(("cryptex", "uninstall", "com.apple.MobileAsset.DDI"), "uninstall-local-cryptex")

    def list_mounted_images(self) -> None:
        arguments = ("mounter", "list") if self.personalized_radio.isChecked() else ("cryptex", "list")
        self._run_pmd3_action(arguments, "list-images")

    def _run_pmd3_action(self, arguments: tuple[str, ...], context: str) -> None:
        device = self.selected_device()
        if device is None:
            self._show_no_device()
            return
        self._start_action(self._pmd3, arguments, device_environment(device.identifier), context)

    def _start_action(
        self,
        program: Path,
        arguments: tuple[str, ...],
        environment: Mapping[str, str],
        context: str,
    ) -> None:
        if self._action_process is not None and self._action_process.state() != QProcess.ProcessState.NotRunning:
            QMessageBox.warning(self, "Action Running", "Wait for the current DDI action to finish.")
            return
        self.action_output.appendPlainText(f"$ {program} {' '.join(arguments)}")
        self._action_buffer.clear()
        process = QProcess(self)
        process.setProgram(str(program))
        process.setArguments(list(arguments))
        process.setProcessEnvironment(qprocess_environment(environment))
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.readyReadStandardOutput.connect(self._read_action_output)
        process.finished.connect(self._action_finished)
        process.errorOccurred.connect(self._action_error)
        self._action_process = process
        self._action_context = context
        self.mount_button.setEnabled(False)
        self.remove_button.setEnabled(False)
        process.start()

    def _read_action_output(self) -> None:
        if self._action_process is not None:
            text = bytes(self._action_process.readAllStandardOutput()).decode("utf-8", errors="replace")
            self._action_buffer.extend(text.encode("utf-8"))
            self.action_output.moveCursor(QTextCursor.MoveOperation.End)
            self.action_output.insertPlainText(text)

    def _action_finished(self, exit_code: int, exit_status: QProcess.ExitStatus) -> None:
        del exit_status
        context = self._action_context
        self.action_output.appendPlainText(f"\n[finished: exit {exit_code}]\n")
        semantic_failure = output_indicates_failure(bytes(self._action_buffer))
        if context == "developer-mode-status":
            recent_text = self.action_output.toPlainText().lower()
            if exit_code == 0 and not semantic_failure and "true" in recent_text.split("$ ")[-1]:
                self.developer_mode_status.setText("Developer Mode is enabled")
            elif exit_code == 0 and not semantic_failure:
                self.developer_mode_status.setText("Developer Mode appears disabled — follow the on-device steps")
            else:
                self.developer_mode_status.setText("Could not query Developer Mode; see command output")
        elif exit_code == 0 and not semantic_failure and context.startswith("mount"):
            self.developer_mode_status.setText("Developer image operation completed successfully")
        self._action_process = None
        self._update_device_fields(self.selected_device())

    def _action_error(self, process_error: QProcess.ProcessError) -> None:
        del process_error
        if self._action_process is not None:
            self.action_output.appendPlainText(f"\nProcess error: {self._action_process.errorString()}")

    def choose_output_root(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "Choose evidence destination", self.output_root.text())
        if selected:
            self.output_root.setText(selected)

    def start_collection(self) -> None:
        device = self.selected_device()
        if device is None:
            self._show_no_device()
            return
        if self._collection_process is not None:
            QMessageBox.warning(self, "Collection Running", "A collection is already running.")
            return
        selected_streams = self.include_syslog.isChecked() or self.include_oslog.isChecked() or self.include_pcap.isChecked()
        if not selected_streams:
            QMessageBox.information(self, "Snapshot Only", "No live streams are selected; the app will collect snapshots only.")
        warning = (
            f"Collect evidence from {device.display_name()}?\n\n"
            "The case will contain identifiers and potentially sensitive device data. "
            "PCAP does not decrypt TLS, but unencrypted payloads may be recorded."
        )
        if not self._confirm("Start Evidence Collection", warning):
            return
        arguments = [
            "-m",
            "ios_developer_toolkit.collector",
            "--udid",
            device.identifier,
            "--output-root",
            self.output_root.text(),
            "--duration",
            str(self.capture_duration.value()),
        ]
        for enabled, flag in (
            (self.include_syslog.isChecked(), "--include-syslog"),
            (self.include_oslog.isChecked(), "--include-oslog"),
            (self.include_pcap.isChecked(), "--include-pcap"),
            (self.include_screenshot.isChecked(), "--include-screenshot"),
            (self.include_crash_pull.isChecked(), "--include-crash-pull"),
        ):
            if enabled:
                arguments.append(flag)
        process = QProcess(self)
        process.setProgram(sys.executable)
        process.setArguments(arguments)
        process.setProcessEnvironment(qprocess_environment(base_environment()))
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.readyReadStandardOutput.connect(self._read_collection_output)
        process.finished.connect(self._collection_finished)
        process.errorOccurred.connect(self._collection_error)
        self._collection_process = process
        self.collection_output.clear()
        self.start_collection_button.setEnabled(False)
        self.stop_collection_button.setEnabled(True)
        process.start()

    def _read_collection_output(self) -> None:
        if self._collection_process is None:
            return
        text = bytes(self._collection_process.readAllStandardOutput()).decode("utf-8", errors="replace")
        self.collection_output.moveCursor(QTextCursor.MoveOperation.End)
        self.collection_output.insertPlainText(text)
        for line in text.splitlines():
            try:
                record: object = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict) and record.get("event") == "case-created" and isinstance(record.get("path"), str):
                self._last_case_path = Path(record["path"])
                self.open_case_button.setEnabled(True)

    def _collection_finished(self, exit_code: int, exit_status: QProcess.ExitStatus) -> None:
        del exit_status
        self.collection_output.appendPlainText(f"\nCollection process finished with exit code {exit_code}.")
        self._collection_process = None
        self.start_collection_button.setEnabled(self.selected_device() is not None)
        self.stop_collection_button.setEnabled(False)

    def _collection_error(self, process_error: QProcess.ProcessError) -> None:
        del process_error
        if self._collection_process is not None:
            self.collection_output.appendPlainText(f"\nProcess error: {self._collection_process.errorString()}")

    def stop_collection(self) -> None:
        if self._collection_process is not None:
            self.collection_output.appendPlainText("\nRequesting a clean stop and evidence finalization…")
            self._collection_process.terminate()

    def open_last_case(self) -> None:
        if self._last_case_path is None or not self._last_case_path.is_dir():
            QMessageBox.warning(self, "Case Not Available", "The last case directory is not available.")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._last_case_path)))

    def choose_ipa(self) -> None:
        selected_path, _ = QFileDialog.getOpenFileName(
            self,
            "Choose an iOS IPA package",
            str(Path.home()),
            "iOS application packages (*.ipa)",
        )
        if not selected_path:
            return
        self._selected_ipa = Path(selected_path).expanduser().resolve()
        self.ipa_path_field.setText(str(self._selected_ipa))
        self._start_ipa_inspection()

    def _start_ipa_inspection(self) -> None:
        selected_ipa = self._selected_ipa
        if selected_ipa is None:
            raise IPAInspectionError("No IPA path was selected for inspection")
        if self._ipa_inspection_process is not None:
            QMessageBox.warning(self, "Inspection Running", "Wait for the current IPA inspection to finish.")
            return
        self._ipa_inspection = None
        self._ipa_inspection_stdout.clear()
        self._ipa_inspection_stderr.clear()
        self.ipa_inspection_summary.clear()
        self.ipa_inspection_summary.setPlainText("Inspecting archive, provisioning profile, and code signature…")
        self.ipa_inspection_progress.setVisible(True)
        process = QProcess(self)
        process.setProgram(sys.executable)
        process.setArguments(["-m", "ios_developer_toolkit.ipa_inspector", str(selected_ipa)])
        process.setProcessEnvironment(qprocess_environment(base_environment()))
        process.readyReadStandardOutput.connect(self._read_ipa_inspection_stdout)
        process.readyReadStandardError.connect(self._read_ipa_inspection_stderr)
        process.finished.connect(self._ipa_inspection_finished)
        process.errorOccurred.connect(self._ipa_inspection_error)
        self._ipa_inspection_process = process
        self._update_sideload_controls()
        process.start()

    def _read_ipa_inspection_stdout(self) -> None:
        if self._ipa_inspection_process is not None:
            self._ipa_inspection_stdout.extend(bytes(self._ipa_inspection_process.readAllStandardOutput()))

    def _read_ipa_inspection_stderr(self) -> None:
        if self._ipa_inspection_process is not None:
            self._ipa_inspection_stderr.extend(bytes(self._ipa_inspection_process.readAllStandardError()))

    def _ipa_inspection_finished(self, exit_code: int, exit_status: QProcess.ExitStatus) -> None:
        del exit_status
        self._read_ipa_inspection_stdout()
        self._read_ipa_inspection_stderr()
        stderr_text = self._ipa_inspection_stderr.decode("utf-8", errors="replace").strip()
        if exit_code != 0:
            message = stderr_text or f"IPA inspector exited with status {exit_code}"
            self.ipa_inspection_summary.setPlainText(message)
            self.sideload_status.setText("IPA inspection failed. Correct the package error before installation.")
        else:
            try:
                inspection = parse_inspection_json(self._ipa_inspection_stdout.decode("utf-8"))
            except (IPAInspectionError, UnicodeDecodeError) as error:
                self.ipa_inspection_summary.setPlainText(f"IPA inspection output validation failed: {error}")
                self.sideload_status.setText("IPA inspection failed. The inspector returned malformed data.")
            else:
                self._ipa_inspection = inspection
                self.ipa_inspection_summary.setPlainText(format_inspection(inspection))
                if inspection.signature.status == "valid":
                    self.sideload_status.setText(
                        "macOS codesign verified the extracted bundle. Confirm that its provisioning method permits the selected device."
                    )
                else:
                    self.sideload_status.setText(
                        f"Installation is disabled because the extracted bundle signature is {inspection.signature.status}."
                    )
        self._ipa_inspection_process = None
        self.ipa_inspection_progress.setVisible(False)
        self._update_sideload_controls()

    def _ipa_inspection_error(self, process_error: QProcess.ProcessError) -> None:
        del process_error
        if self._ipa_inspection_process is not None:
            self.ipa_inspection_summary.setPlainText(
                f"Could not start IPA inspection: {self._ipa_inspection_process.errorString()}"
            )

    def _update_sideload_controls(self) -> None:
        device_available = self.selected_device() is not None
        action_running = self._sideload_process is not None
        inspection_running = self._ipa_inspection_process is not None
        signature_valid = self._ipa_inspection is not None and self._ipa_inspection.signature.status == "valid"
        self.choose_ipa_button.setEnabled(not inspection_running and not action_running)
        self.install_ipa_button.setEnabled(device_available and signature_valid and not action_running and not inspection_running)
        self.stop_sideload_button.setEnabled(action_running)

    def install_selected_ipa(self) -> None:
        device = self.selected_device()
        inspection = self._ipa_inspection
        selected_ipa = self._selected_ipa
        if device is None:
            self._show_no_device()
            return
        if inspection is None or selected_ipa is None:
            QMessageBox.warning(self, "IPA Not Inspected", "Choose and successfully inspect an IPA first.")
            return
        if inspection.signature.status != "valid":
            QMessageBox.critical(
                self,
                "Invalid IPA Signature",
                f"macOS codesign reported signature status: {inspection.signature.status}. Installation is blocked.",
            )
            return
        developer_install = self.developer_package_checkbox.isChecked()
        install_mode = "developer package" if developer_install else "standard package"
        warning = (
            f"Install {inspection.app_name} on {device.display_name()}?\n\n"
            f"IPA: {selected_ipa}\n"
            f"Bundle ID: {inspection.bundle_identifier}\n"
            f"Version: {inspection.version} ({inspection.build})\n"
            f"Mode: {install_mode}\n\n"
            "This changes device state. iOS will still enforce provisioning, signing, Developer Mode, and trust policy."
        )
        if not self._confirm("Install IPA", warning):
            return
        arguments = ["apps", "install"]
        if developer_install:
            arguments.append("--developer")
        arguments.append(str(selected_ipa))
        self._start_sideload_action(tuple(arguments), "install")

    def _start_sideload_action(self, arguments: tuple[str, ...], context: str) -> None:
        device = self.selected_device()
        if device is None:
            self._show_no_device()
            return
        if self._sideload_process is not None:
            QMessageBox.warning(self, "App Operation Running", "Stop or wait for the active app operation first.")
            return
        self._sideload_buffer.clear()
        self._sideload_context = context
        self.sideload_output.appendPlainText(f"\n$ pymobiledevice3 {shlex.join(arguments)}\n")
        process = QProcess(self)
        process.setProgram(str(self._pmd3))
        process.setArguments(list(arguments))
        process.setWorkingDirectory(str(Path.home()))
        process.setProcessEnvironment(qprocess_environment(device_environment(device.identifier)))
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.readyReadStandardOutput.connect(self._read_sideload_output)
        process.finished.connect(self._sideload_finished)
        process.errorOccurred.connect(self._sideload_error)
        self._sideload_process = process
        self.sideload_status.setText(f"Running {context} operation on {device.display_name()}…")
        self.sideload_activity_progress.setVisible(True)
        self._update_sideload_controls()
        process.start()

    def _read_sideload_output(self) -> None:
        if self._sideload_process is None:
            return
        output = bytes(self._sideload_process.readAllStandardOutput())
        self._sideload_buffer.extend(output)
        self.sideload_output.moveCursor(QTextCursor.MoveOperation.End)
        self.sideload_output.insertPlainText(output.decode("utf-8", errors="replace"))

    def _sideload_finished(self, exit_code: int, exit_status: QProcess.ExitStatus) -> None:
        del exit_status
        self._read_sideload_output()
        context = self._sideload_context
        semantic_failure = output_indicates_failure(bytes(self._sideload_buffer))
        succeeded = exit_code == 0 and not semantic_failure
        self.sideload_output.appendPlainText(f"\n[finished: exit {exit_code}]\n")
        self.sideload_status.setText(
            f"{context.capitalize()} completed successfully."
            if succeeded
            else f"{context.capitalize()} failed; review the complete command output above."
        )
        self._sideload_process = None
        self._sideload_context = ""
        self.sideload_activity_progress.setVisible(False)
        self._update_sideload_controls()
        if succeeded and context == "install" and self._apps_process is None:
            QTimer.singleShot(0, self.refresh_app_inventory)

    def _sideload_error(self, process_error: QProcess.ProcessError) -> None:
        del process_error
        if self._sideload_process is not None:
            self.sideload_output.appendPlainText(f"\nProcess error: {self._sideload_process.errorString()}")

    def stop_sideload_action(self) -> None:
        if self._sideload_process is not None:
            self.sideload_output.appendPlainText("\nRequesting app operation stop…")
            self._sideload_process.terminate()

    def refresh_app_inventory(self) -> None:
        device = self.selected_device()
        if device is None:
            self._show_no_device()
            return
        arguments = ["apps", "list", "--type", "Any"]
        if self.calculate_app_sizes_checkbox.isChecked():
            arguments.append("--calculate-sizes")
        self._start_apps_action(tuple(arguments), "inventory")

    def _start_apps_action(self, arguments: tuple[str, ...], context: str) -> None:
        device = self.selected_device()
        if device is None:
            self._show_no_device()
            return
        if self._apps_process is not None:
            QMessageBox.warning(self, "App Operation Running", "Stop or wait for the active app operation first.")
            return
        self._apps_context = context
        self._apps_stdout.clear()
        self._apps_stderr.clear()
        self.apps_output.appendPlainText(f"\n$ pymobiledevice3 {shlex.join(arguments)}\n")
        process = QProcess(self)
        process.setProgram(str(self._pmd3))
        process.setArguments(list(arguments))
        process.setWorkingDirectory(str(Path.home()))
        process.setProcessEnvironment(qprocess_environment(device_environment(device.identifier)))
        process.readyReadStandardOutput.connect(self._read_apps_stdout)
        process.readyReadStandardError.connect(self._read_apps_stderr)
        process.finished.connect(self._apps_finished)
        process.errorOccurred.connect(self._apps_error)
        self._apps_process = process
        self.apps_status.setText(f"Running {context} operation on {device.display_name()}…")
        self._update_apps_controls()
        process.start()

    def _read_apps_stdout(self) -> None:
        if self._apps_process is not None:
            self._apps_stdout.extend(bytes(self._apps_process.readAllStandardOutput()))

    def _read_apps_stderr(self) -> None:
        if self._apps_process is None:
            return
        output = bytes(self._apps_process.readAllStandardError())
        self._apps_stderr.extend(output)
        self.apps_output.moveCursor(QTextCursor.MoveOperation.End)
        self.apps_output.insertPlainText(output.decode("utf-8", errors="replace"))

    def _apps_finished(self, exit_code: int, exit_status: QProcess.ExitStatus) -> None:
        del exit_status
        self._read_apps_stdout()
        self._read_apps_stderr()
        context = self._apps_context
        combined_output = bytes(self._apps_stdout + self._apps_stderr)
        succeeded = exit_code == 0 and not output_indicates_failure(combined_output)
        if succeeded and context == "inventory":
            try:
                apps = parse_installed_apps_json(self._apps_stdout.decode("utf-8"))
            except (InstalledAppsDataError, json.JSONDecodeError, UnicodeDecodeError) as error:
                succeeded = False
                self.apps_output.appendPlainText(f"Inventory validation failed: {error}")
            else:
                self._installed_apps = apps
                self._populate_installed_apps(apps)
                self.apps_status.setText(f"Loaded {len(apps)} installed applications.")
        elif succeeded and context == "uninstall":
            self.apps_status.setText("Application uninstalled successfully. Refreshing inventory…")
        if not succeeded:
            stdout_text = self._apps_stdout.decode("utf-8", errors="replace").strip()
            if stdout_text:
                self.apps_output.appendPlainText(stdout_text)
            self.apps_status.setText(f"{context.capitalize()} failed; review the output below.")
        self.apps_output.appendPlainText(f"[finished: exit {exit_code}]\n")
        self._apps_process = None
        self._apps_context = ""
        self._update_apps_controls()
        if succeeded and context == "uninstall":
            QTimer.singleShot(0, self.refresh_app_inventory)

    def _apps_error(self, process_error: QProcess.ProcessError) -> None:
        del process_error
        if self._apps_process is not None:
            self.apps_output.appendPlainText(f"Process error: {self._apps_process.errorString()}")

    def _populate_installed_apps(self, apps: tuple[InstalledApp, ...]) -> None:
        self.installed_apps_table.setSortingEnabled(False)
        self.installed_apps_table.setRowCount(len(apps))
        for row, app in enumerate(apps):
            values = (
                app.name,
                app.bundle_identifier,
                app.version or "—",
                app.build or "—",
                app.application_type,
                format_byte_count(app.total_bytes),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 5 and app.total_bytes is not None:
                    item.setData(Qt.ItemDataRole.UserRole, app.total_bytes)
                self.installed_apps_table.setItem(row, column, item)
        self.installed_apps_table.setSortingEnabled(True)
        self._filter_installed_apps(self.app_filter_field.text())
        self._update_apps_controls()

    def _filter_installed_apps(self, value: str) -> None:
        needle = value.strip().casefold()
        for row in range(self.installed_apps_table.rowCount()):
            searchable = " ".join(
                self.installed_apps_table.item(row, column).text()
                for column in range(self.installed_apps_table.columnCount())
                if self.installed_apps_table.item(row, column) is not None
            ).casefold()
            self.installed_apps_table.setRowHidden(row, bool(needle) and needle not in searchable)

    def selected_installed_bundle_identifier(self) -> str | None:
        selected_rows = self.installed_apps_table.selectionModel().selectedRows()
        if len(selected_rows) != 1:
            return None
        item = self.installed_apps_table.item(selected_rows[0].row(), 1)
        return item.text() if item is not None else None

    def _installed_app_selection_changed(self) -> None:
        self._update_apps_controls()

    def _update_apps_controls(self) -> None:
        running = self._apps_process is not None
        device_available = self.selected_device() is not None
        selected = self.selected_installed_bundle_identifier() is not None
        self.refresh_apps_button.setEnabled(device_available and not running)
        self.calculate_app_sizes_checkbox.setEnabled(not running)
        self.copy_bundle_id_button.setEnabled(selected and not running)
        self.uninstall_app_button.setEnabled(device_available and selected and not running)
        self.stop_apps_button.setEnabled(running)

    def copy_selected_bundle_identifier(self) -> None:
        bundle_identifier = self.selected_installed_bundle_identifier()
        if bundle_identifier is None:
            QMessageBox.information(self, "No App Selected", "Select one application row first.")
            return
        QApplication.clipboard().setText(bundle_identifier)
        self.apps_status.setText(f"Copied {bundle_identifier} to the clipboard.")

    def uninstall_selected_application(self) -> None:
        device = self.selected_device()
        if device is None:
            self._show_no_device()
            return
        selected_identifier = self.selected_installed_bundle_identifier()
        if selected_identifier is None:
            QMessageBox.information(self, "No App Selected", "Select one application row first.")
            return
        try:
            bundle_identifier = validate_bundle_identifier(selected_identifier)
        except IPAInspectionError as error:
            QMessageBox.critical(self, "Invalid Bundle Identifier", str(error))
            return
        warning = (
            f"Uninstall {bundle_identifier} from {device.display_name()}?\n\n"
            "This removes the application and may remove its local app data. This action cannot be undone by the toolkit."
        )
        if self._confirm("Uninstall Application", warning):
            self._start_apps_action(("apps", "uninstall", bundle_identifier), "uninstall")

    def stop_apps_action(self) -> None:
        if self._apps_process is not None:
            self.apps_output.appendPlainText("Requesting app operation stop…")
            self._apps_process.terminate()

    def choose_backup_destination(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self,
            "Choose local backup destination",
            self.backup_destination_field.text(),
        )
        if selected:
            self.backup_destination_field.setText(selected)

    def backup_destination(self) -> Path:
        raw_destination = self.backup_destination_field.text().strip()
        if not raw_destination:
            raise BackupRequestError("Choose a non-empty local backup destination")
        return Path(raw_destination).expanduser().resolve()

    def check_backup_encryption(self) -> None:
        device = self.selected_device()
        if device is None:
            self._show_no_device()
            return
        try:
            destination = self.backup_destination()
        except BackupRequestError as error:
            QMessageBox.critical(self, "Invalid Backup Destination", str(error))
            return
        request: dict[str, str | bool] = {
            "udid": device.identifier,
            "destination": str(destination),
            "require_encryption": False,
            "new_password": "",
            "full": False,
        }
        self._start_backup_worker("status", request)

    def _backup_encryption_choice_changed(self, checked: bool) -> None:
        needs_new_password = checked and self._backup_encryption_state is not True
        controls_enabled = (
            needs_new_password and self._backup_process is None and self.selected_device() is not None
        )
        self.backup_password_field.setEnabled(controls_enabled)
        self.backup_password_confirmation_field.setEnabled(controls_enabled)

    def start_backup(self) -> None:
        device = self.selected_device()
        if device is None:
            self._show_no_device()
            return
        try:
            destination = self.backup_destination()
        except BackupRequestError as error:
            QMessageBox.critical(self, "Invalid Backup Destination", str(error))
            return
        require_encryption = self.require_encryption_checkbox.isChecked()
        password = self.backup_password_field.text()
        confirmation = self.backup_password_confirmation_field.text()
        if require_encryption and self._backup_encryption_state is not True:
            if not password:
                QMessageBox.critical(
                    self,
                    "Encryption Password Required",
                    "Enter a new backup password because encryption is off or has not yet been checked.",
                )
                return
            if password != confirmation:
                QMessageBox.critical(self, "Passwords Do Not Match", "Enter the same new backup password twice.")
                return
        else:
            password = ""
        if require_encryption and self._backup_encryption_state is False:
            encryption_summary = "Persistent local-backup encryption will be enabled before this backup."
        elif require_encryption and self._backup_encryption_state is True:
            encryption_summary = "The existing encrypted-backup setting and password will be preserved."
        elif require_encryption:
            encryption_summary = "The helper will verify encryption and enable it with the new password only if needed."
        else:
            encryption_summary = "The device's current encryption setting will be preserved; encryption will not be disabled."
        backup_mode = "Full" if self.full_backup_checkbox.isChecked() else "Incremental when valid local state exists"
        warning = (
            f"Back up {device.display_name()}?\n\n"
            f"Destination: {destination / device.identifier}\n"
            f"Mode: {backup_mode}\n"
            f"Encryption: {encryption_summary}\n\n"
            "Backups can contain messages, account data, Health data when encrypted, identifiers, and other private information. "
            "Keep the destination protected."
        )
        if not self._confirm("Start Device Backup", warning):
            return
        request: dict[str, str | bool] = {
            "udid": device.identifier,
            "destination": str(destination),
            "require_encryption": require_encryption,
            "new_password": password,
            "full": self.full_backup_checkbox.isChecked(),
        }
        self._start_backup_worker("backup", request)
        self.backup_password_field.clear()
        self.backup_password_confirmation_field.clear()

    def _start_backup_worker(self, action: str, request: dict[str, str | bool]) -> None:
        if self._backup_process is not None:
            QMessageBox.warning(self, "Backup Operation Running", "Stop or wait for the active backup operation first.")
            return
        self._backup_action = action
        self._backup_stdout.clear()
        self._backup_stderr.clear()
        process = QProcess(self)
        process.setProgram(sys.executable)
        process.setArguments(["-m", "ios_developer_toolkit.backup_worker", action])
        process.setProcessEnvironment(qprocess_environment(base_environment()))
        process.readyReadStandardOutput.connect(self._read_backup_stdout)
        process.readyReadStandardError.connect(self._read_backup_stderr)
        process.finished.connect(self._backup_finished)
        process.errorOccurred.connect(self._backup_error)
        self._backup_process = process
        self.backup_output.appendPlainText(
            "Checking backup encryption…" if action == "status" else "Starting device backup…"
        )
        if action == "backup":
            self.backup_progress.setValue(0)
        self._update_backup_controls()
        process.start()
        if not process.waitForStarted(3000):
            self.backup_output.appendPlainText(f"Could not start backup helper: {process.errorString()}")
            self._backup_process = None
            self._backup_action = ""
            self._update_backup_controls()
            return
        request_payload = json.dumps(request).encode("utf-8")
        accepted_bytes = process.write(request_payload)
        if accepted_bytes != len(request_payload):
            process.kill()
            process.waitForFinished(3000)
            self._backup_process = None
            self._backup_action = ""
            self._update_backup_controls()
            message = f"Backup helper accepted {accepted_bytes} of {len(request_payload)} request bytes."
            self.backup_output.appendPlainText(message)
            QMessageBox.critical(
                self,
                "Backup Request Failed",
                f"{message}\nNo backup operation was started.",
            )
            return
        process.closeWriteChannel()

    def _read_backup_stdout(self) -> None:
        if self._backup_process is None:
            return
        self._backup_stdout.extend(bytes(self._backup_process.readAllStandardOutput()))
        while b"\n" in self._backup_stdout:
            line, _, remainder = self._backup_stdout.partition(b"\n")
            self._backup_stdout = bytearray(remainder)
            if line.strip():
                self._handle_backup_event_line(line)

    def _handle_backup_event_line(self, line: bytes) -> None:
        try:
            event = parse_backup_event(line.decode("utf-8"))
        except (BackupRequestError, json.JSONDecodeError, UnicodeDecodeError) as error:
            self.backup_output.appendPlainText(f"Invalid backup helper event: {error}")
            return
        self._handle_backup_event(event)

    def _handle_backup_event(self, event: BackupEvent) -> None:
        self.backup_output.appendPlainText(event.message)
        if event.percent is not None:
            self.backup_progress.setValue(max(0, min(100, event.percent)))
        if event.encrypted is not None:
            self._backup_encryption_state = event.encrypted
            state = "enabled" if event.encrypted else "disabled"
            self.backup_encryption_status.setText(f"Backup encryption is {state} on this device")
            self._backup_encryption_choice_changed(self.require_encryption_checkbox.isChecked())
        if event.path is not None:
            self._last_backup_path = event.path

    def _read_backup_stderr(self) -> None:
        if self._backup_process is None:
            return
        output = bytes(self._backup_process.readAllStandardError())
        self._backup_stderr.extend(output)
        self.backup_output.moveCursor(QTextCursor.MoveOperation.End)
        self.backup_output.insertPlainText(output.decode("utf-8", errors="replace"))

    def _backup_finished(self, exit_code: int, exit_status: QProcess.ExitStatus) -> None:
        del exit_status
        self._read_backup_stdout()
        self._read_backup_stderr()
        if self._backup_stdout.strip():
            self._handle_backup_event_line(bytes(self._backup_stdout))
            self._backup_stdout.clear()
        action = self._backup_action
        if exit_code == 0:
            self.backup_output.appendPlainText(
                "Encryption status check completed." if action == "status" else "Backup operation completed successfully."
            )
        else:
            self.backup_output.appendPlainText(f"{action.capitalize()} failed with exit code {exit_code}.")
        self._backup_process = None
        self._backup_action = ""
        self._update_backup_controls()
        self._backup_encryption_choice_changed(self.require_encryption_checkbox.isChecked())

    def _backup_error(self, process_error: QProcess.ProcessError) -> None:
        del process_error
        if self._backup_process is not None:
            self.backup_output.appendPlainText(f"Process error: {self._backup_process.errorString()}")

    def _update_backup_controls(self) -> None:
        running = self._backup_process is not None
        device_available = self.selected_device() is not None
        self.start_backup_button.setEnabled(device_available and not running)
        self.check_encryption_button.setEnabled(device_available and not running)
        self.stop_backup_button.setEnabled(running)
        self.full_backup_checkbox.setEnabled(not running)
        self.require_encryption_checkbox.setEnabled(not running)
        self.backup_destination_field.setEnabled(not running)
        self.open_backup_button.setEnabled(not running)
        if hasattr(self, "launch_ufade_button"):
            self.launch_ufade_button.setEnabled(device_available and not running)
        self._backup_encryption_choice_changed(self.require_encryption_checkbox.isChecked())

    def stop_backup(self) -> None:
        if self._backup_process is not None:
            self.backup_output.appendPlainText(
                "Stopping the backup. The partial destination may be incomplete and will not be treated as valid incremental state."
            )
            self._backup_process.terminate()

    def open_backup_folder(self) -> None:
        try:
            destination = self.backup_destination()
        except BackupRequestError as error:
            QMessageBox.critical(self, "Invalid Backup Destination", str(error))
            return
        target = self._last_backup_path if self._last_backup_path is not None else destination
        if not target.is_dir():
            QMessageBox.information(self, "Backup Folder Not Found", f"The folder does not exist yet:\n{target}")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

    def _invalidate_ufade_validation(self, value: str) -> None:
        del value
        self._ufade_installation = None
        self.ufade_validation_status.setText("UFADE installation has not been validated")

    def choose_ufade_checkout(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self,
            "Choose cloned UFADE checkout",
            self.ufade_checkout_field.text(),
        )
        if selected:
            self.ufade_checkout_field.setText(selected)

    def choose_ufade_python(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "Choose UFADE Python 3.11 executable",
            self.ufade_python_field.text(),
            "Executable (*)",
        )
        if selected:
            self.ufade_python_field.setText(selected)

    def choose_ufade_output_directory(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self,
            "Choose UFADE working and output directory",
            self.ufade_output_field.text(),
        )
        if selected:
            self.ufade_output_field.setText(selected)

    def ufade_checkout(self) -> Path:
        value = self.ufade_checkout_field.text().strip()
        if not value:
            raise UFADEValidationError("Choose the root of a cloned UFADE checkout")
        return Path(value).expanduser()

    def ufade_python(self) -> Path:
        value = self.ufade_python_field.text().strip()
        if not value:
            raise UFADEValidationError("Choose the Python executable from UFADE's separate Python 3.11 environment")
        return Path(value).expanduser()

    def ufade_output_directory(self) -> Path:
        value = self.ufade_output_field.text().strip()
        if not value:
            raise UFADEValidationError("Choose a non-empty UFADE working and output directory")
        destination = Path(value).expanduser()
        if not destination.is_absolute():
            raise UFADEValidationError(f"UFADE output directory must be an absolute path: {destination}")
        return destination.resolve()

    def validate_ufade_from_ui(self) -> UFADEInstallation | None:
        self.ufade_validation_status.setText("Validating UFADE checkout, Python 3.11, and runtime imports…")
        QApplication.processEvents()
        try:
            installation = inspect_ufade_installation(self.ufade_checkout(), self.ufade_python())
            self.ufade_output_directory()
        except (UFADEValidationError, OSError, subprocess.SubprocessError) as error:
            self._ufade_installation = None
            self.ufade_validation_status.setText(f"Validation failed: {error}")
            self.ufade_output.appendPlainText(f"UFADE validation failed: {error}")
            return None
        self._ufade_installation = installation
        message = (
            f"Validated UFADE {installation.ufade_version} with Python {installation.python_version}. "
            "The GPL application will remain a separate process."
        )
        self.ufade_validation_status.setText(message)
        self.ufade_output.appendPlainText(message)
        return installation

    def copy_ufade_setup_commands(self) -> None:
        commands = "\n".join(
            (
                "brew install python@3.11 python-tk@3.11",
                "git clone https://github.com/prosch88/UFADE.git",
                "cd UFADE",
                "python3.11 -m venv venv",
                "venv/bin/python -m pip install -r requirements.txt",
            )
        )
        QApplication.clipboard().setText(commands)
        self.ufade_output.appendPlainText("Copied macOS UFADE setup commands to the clipboard.")

    def open_ufade_repository(self) -> None:
        QDesktopServices.openUrl(QUrl(UFADE_REPOSITORY_URL))

    def launch_ufade(self) -> None:
        device = self.selected_device()
        if device is None:
            self._show_no_device()
            return
        installation = self.validate_ufade_from_ui()
        if installation is None:
            QMessageBox.critical(
                self,
                "UFADE Validation Failed",
                "Correct the UFADE checkout or Python 3.11 environment before launching it.",
            )
            return
        try:
            destination = self.ufade_output_directory()
        except UFADEValidationError as error:
            QMessageBox.critical(self, "Invalid UFADE Output Directory", str(error))
            return
        warning = (
            f"Launch UFADE {installation.ufade_version} for an independent forensic acquisition?\n\n"
            f"Toolkit-selected device: {device.display_name()} ({device.identifier})\n"
            f"Working/output folder: {destination}\n"
            f"Python: {installation.python}\n\n"
            "UFADE performs its own device discovery and will ask you to choose Logical, Logical+, UFD, PRFS, or other "
            "operations in its own window. Keep only the intended device connected. UFADE may create decrypted copies, "
            "archives, logs, or reports containing highly sensitive data. Use UFADE's own stop controls; closing this "
            "toolkit will not stop the separate UFADE process."
        )
        if not self._confirm("Launch External UFADE", warning):
            return
        try:
            destination.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            QMessageBox.critical(
                self,
                "Could Not Create UFADE Output Directory",
                f"Could not create {destination}: {error}",
            )
            return
        started, process_identifier = QProcess.startDetached(
            str(installation.python),
            [str(installation.script)],
            str(destination),
        )
        if not started:
            QMessageBox.critical(
                self,
                "Could Not Launch UFADE",
                f"The external process did not start with {installation.python}",
            )
            return
        self.ufade_output.appendPlainText(
            f"Launched external UFADE {installation.ufade_version} as process {process_identifier}. "
            f"Working directory: {destination}"
        )

    def open_ufade_output_directory(self) -> None:
        try:
            destination = self.ufade_output_directory()
        except UFADEValidationError as error:
            QMessageBox.critical(self, "Invalid UFADE Output Directory", str(error))
            return
        if not destination.is_dir():
            QMessageBox.information(
                self,
                "UFADE Output Folder Not Found",
                f"The folder does not exist yet:\n{destination}",
            )
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(destination)))

    def _filter_command_presets(self) -> None:
        selected_identifier = self._current_preset.identifier if self._current_preset is not None else None
        category = self.command_category_combo.currentText()
        query = self.command_search_field.text().strip().casefold()
        matching = tuple(
            preset
            for preset in self._presets
            if (category == "All categories" or preset.category == category)
            and (
                not query
                or query in preset.title.casefold()
                or query in preset.summary.casefold()
                or query in preset.category.casefold()
                or query in " ".join(preset.argument_template).casefold()
            )
        )
        self.command_preset_list.blockSignals(True)
        self.command_preset_list.clear()
        selected_row = 0
        for row, preset in enumerate(matching):
            item = QListWidgetItem(preset.title)
            item.setToolTip(preset.summary)
            item.setData(Qt.ItemDataRole.UserRole, preset.identifier)
            self.command_preset_list.addItem(item)
            if preset.identifier == selected_identifier:
                selected_row = row
        if matching:
            self.command_preset_list.setCurrentRow(selected_row)
        self.command_preset_list.blockSignals(False)
        if matching:
            self._show_command_preset(matching[selected_row])
        else:
            self._current_preset = None
            self.command_preset_title.setText("No matching presets")
            self.command_summary.setText("Change the category or search text.")
            self.command_advanced_notes.clear()
            self.command_preview.clear()
            self._clear_preset_parameters()
            self._update_command_controls()

    def _command_preset_selected(
        self,
        current: QListWidgetItem | None,
        previous: QListWidgetItem | None,
    ) -> None:
        del previous
        if current is None:
            return
        identifier = current.data(Qt.ItemDataRole.UserRole)
        if not isinstance(identifier, str):
            raise CommandCatalogError("selected preset is missing its string identifier")
        matching = tuple(preset for preset in self._presets if preset.identifier == identifier)
        if len(matching) != 1:
            raise CommandCatalogError(f"expected one selected preset for {identifier!r}, found {len(matching)}")
        self._show_command_preset(matching[0])

    def _show_command_preset(self, preset: CommandPreset) -> None:
        self._current_preset = preset
        self.command_preset_title.setText(preset.title)
        self.command_risk_badge.setText(risk_title(preset.risk))
        self.command_risk_badge.setProperty("risk", preset.risk)
        self.command_risk_badge.style().unpolish(self.command_risk_badge)
        self.command_risk_badge.style().polish(self.command_risk_badge)
        self.command_summary.setText(preset.summary)
        self.command_advanced_notes.setText(preset.advanced_notes)
        prerequisites = []
        prerequisites.append("Connected and trusted device" if preset.requires_device else "No selected USB device required")
        if preset.requires_developer_services:
            prerequisites.append("Developer Mode + mounted DDI + iOS 17+ userspace/RSD tunnel when needed")
        if preset.long_running:
            prerequisites.append("Runs until Stop or service completion")
        self.command_prerequisites.setText("Prerequisites: " + " • ".join(prerequisites))
        self._clear_preset_parameters()
        for spec in preset.parameters:
            field = QLineEdit(spec.initial_value)
            field.setObjectName(f"presetParameter_{spec.identifier}")
            field.setPlaceholderText(spec.description)
            field.textChanged.connect(self._update_command_preview)
            self._preset_parameter_fields[spec.identifier] = field
            if spec.kind in ("local-directory", "output-file"):
                row = QWidget()
                row_layout = QHBoxLayout(row)
                row_layout.setContentsMargins(0, 0, 0, 0)
                row_layout.addWidget(field, 1)
                choose_button = QPushButton("Choose…")
                choose_button.clicked.connect(self._preset_path_handler(spec))
                row_layout.addWidget(choose_button)
                self.preset_parameters_layout.addRow(spec.label, row)
            else:
                self.preset_parameters_layout.addRow(spec.label, field)
        self.preset_parameters_group.setVisible(bool(preset.parameters))
        self._update_command_preview()

    def _clear_preset_parameters(self) -> None:
        while self.preset_parameters_layout.rowCount() > 0:
            self.preset_parameters_layout.removeRow(0)
        self._preset_parameter_fields.clear()

    def _preset_path_handler(self, spec: ParameterSpec) -> Callable[[bool], None]:
        def choose(checked: bool) -> None:
            del checked
            field = self._preset_parameter_fields.get(spec.identifier)
            if field is None:
                raise CommandCatalogError(f"missing field for preset parameter {spec.identifier}")
            if spec.kind == "local-directory":
                selected = QFileDialog.getExistingDirectory(self, spec.label, field.text())
            else:
                selected, _ = QFileDialog.getSaveFileName(self, spec.label, field.text(), "All files (*)")
            if selected:
                field.setText(selected)

        return choose

    def _preset_values(self) -> Mapping[str, str]:
        return {identifier: field.text() for identifier, field in self._preset_parameter_fields.items()}

    def _update_command_preview(self) -> None:
        preset = self._current_preset
        if preset is None:
            self.command_preview.clear()
            self._update_command_controls()
            return
        try:
            arguments = render_preset_arguments(preset, self._preset_values())
        except CommandCatalogError as error:
            self.command_preview.setText(f"Incomplete: {error}")
        else:
            self.command_preview.setText(f"pymobiledevice3 {shlex.join(arguments)}")
        self._update_command_controls()

    def _update_command_controls(self) -> None:
        running = self._console_process is not None
        preset = self._current_preset
        preset_valid = False
        if preset is not None:
            try:
                render_preset_arguments(preset, self._preset_values())
            except CommandCatalogError:
                preset_valid = False
            else:
                preset_valid = not preset.requires_device or self.selected_device() is not None
        self.preset_run_button.setEnabled(preset_valid and not running)
        self.preset_help_button.setEnabled(preset is not None and not running)
        self.console_run_button.setEnabled(bool(self.console_input.text().strip()) and not running)
        self.console_stop_button.setEnabled(running)
        self.command_preset_list.setEnabled(not running)
        self.command_category_combo.setEnabled(not running)
        self.command_search_field.setEnabled(not running)
        self.preset_parameters_group.setEnabled(not running)
        self.console_input.setEnabled(not running)

    def run_selected_preset(self) -> None:
        preset = self._current_preset
        if preset is None:
            QMessageBox.information(self, "No Preset", "Choose a guided command first.")
            return
        try:
            arguments = render_preset_arguments(preset, self._preset_values())
        except CommandCatalogError as error:
            QMessageBox.critical(self, "Invalid Preset Value", str(error))
            return
        if preset.requires_device and self.selected_device() is None:
            self._show_no_device()
            return
        if preset.risk != "read-only":
            impact = (
                "This command writes device-derived data to the Mac."
                if preset.risk == "host-write"
                else "This command changes device application, UI, location, or process state."
            )
            warning = (
                f"Run {preset.title}?\n\n{impact}\n\n"
                f"pymobiledevice3 {shlex.join(arguments)}\n\n"
                "Review the selected target and destination before continuing."
            )
            if not self._confirm("Confirm Guided Command", warning):
                return
        self._run_console_arguments(arguments, preset.title, preset.requires_device)

    def open_selected_preset_help(self) -> None:
        preset = self._current_preset
        if preset is None:
            return
        self.navigate_to_page("Man Pages")
        self.select_manpage_path(preset.manpage_path)

    def run_console_command(self) -> None:
        if self._console_process is not None:
            QMessageBox.warning(self, "Command Running", "Stop the active console command first.")
            return
        try:
            parsed = tuple(shlex.split(self.console_input.text()))
        except ValueError as error:
            QMessageBox.critical(self, "Invalid Command", str(error))
            return
        if parsed and Path(parsed[0]).name == "pymobiledevice3":
            parsed = parsed[1:]
        if not parsed:
            QMessageBox.information(self, "No Command", "Enter pymobiledevice3 arguments to run.")
            return
        requires_device = not self._advanced_command_can_run_without_device(parsed)
        if requires_device and self.selected_device() is None:
            self._show_no_device()
            return
        high_impact = self._is_high_impact_command(parsed)
        if high_impact:
            warning = (
                "This advanced command can erase data, restore firmware, alter activation, reboot, shut down, "
                "or make another high-impact device change:\n\n"
                f"pymobiledevice3 {shlex.join(parsed)}\n\n"
                "The toolkit cannot undo the result. Run it only with a current verified backup and exact authorization."
            )
            if not self._confirm("High-Impact Command", warning):
                return
        elif is_potentially_mutating(parsed):
            warning = (
                "This command is not in the read-only allowlist and may change device or host state:\n\n"
                f"pymobiledevice3 {shlex.join(parsed)}\n\nRun it anyway?"
            )
            if not self._confirm("Potentially State-Changing Command", warning):
                return
        self._run_console_arguments(parsed, "Advanced command", requires_device)

    def _run_console_arguments(self, arguments: tuple[str, ...], title: str, requires_device: bool) -> None:
        if self._console_process is not None:
            QMessageBox.warning(self, "Command Running", "Stop the active console command first.")
            return
        device = self.selected_device()
        if requires_device and device is None:
            self._show_no_device()
            return
        self.console_output.appendPlainText(f"\n[{title}]\n$ pymobiledevice3 {shlex.join(arguments)}\n")
        process = QProcess(self)
        process.setProgram(str(self._pmd3))
        process.setArguments(list(arguments))
        process.setWorkingDirectory(str(Path.home()))
        environment = base_environment() if device is None else device_environment(device.identifier)
        process.setProcessEnvironment(qprocess_environment(environment))
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.readyReadStandardOutput.connect(self._read_console_output)
        process.finished.connect(self._console_finished)
        process.errorOccurred.connect(self._console_error)
        self._console_process = process
        self._update_command_controls()
        process.start()

    def _advanced_command_can_run_without_device(self, arguments: tuple[str, ...]) -> bool:
        prefixes = (
            ("usbmux", "list"),
            ("bonjour",),
            ("remote", "browse"),
            ("version",),
        )
        return any(arguments[: len(prefix)] == prefix for prefix in prefixes)

    def _is_high_impact_command(self, arguments: tuple[str, ...]) -> bool:
        prefixes = (
            ("restore",),
            ("profile", "erase-device"),
            ("profile", "supervise"),
            ("backup2", "erase-device"),
            ("backup2", "restore"),
            ("diagnostics", "restart"),
            ("diagnostics", "shutdown"),
            ("activation", "activate"),
            ("activation", "deactivate"),
            ("mounter", "roll-personalization-nonce"),
            ("mounter", "roll-cryptex-nonce"),
        )
        return any(arguments[: len(prefix)] == prefix for prefix in prefixes)

    def _read_console_output(self) -> None:
        if self._console_process is not None:
            text = bytes(self._console_process.readAllStandardOutput()).decode("utf-8", errors="replace")
            self.console_output.moveCursor(QTextCursor.MoveOperation.End)
            self.console_output.insertPlainText(text)

    def _console_finished(self, exit_code: int, exit_status: QProcess.ExitStatus) -> None:
        del exit_status
        self.console_output.appendPlainText(f"\n[finished: exit {exit_code}]")
        self._console_process = None
        self._update_command_controls()

    def _console_error(self, process_error: QProcess.ProcessError) -> None:
        if self._console_process is not None:
            self.console_output.appendPlainText(f"\nProcess error: {self._console_process.errorString()}")
            if process_error == QProcess.ProcessError.FailedToStart:
                self._console_process = None
                self._update_command_controls()


    def stop_console_command(self) -> None:
        if self._console_process is not None:
            self.console_output.appendPlainText("\nRequesting command stop…")
            self._console_process.terminate()

    def _filter_manpages(self) -> None:
        selected_path = self.selected_manpage_entry().command_path if self.selected_manpage_entry() is not None else None
        query = self.manpage_search_field.text().strip().casefold()
        matching = tuple(
            (index, entry)
            for index, entry in enumerate(self._manpages)
            if not query
            or query in entry.title.casefold()
            or query in entry.category.casefold()
            or query in " ".join(entry.command_path).casefold()
        )
        self.manpage_list.blockSignals(True)
        self.manpage_list.clear()
        selected_row = 0
        for row, (source_index, entry) in enumerate(matching):
            item = QListWidgetItem(entry.display_name())
            item.setData(Qt.ItemDataRole.UserRole, source_index)
            item.setToolTip(entry.category)
            self.manpage_list.addItem(item)
            if entry.command_path == selected_path:
                selected_row = row
        if matching:
            self.manpage_list.setCurrentRow(selected_row)
        self.manpage_list.blockSignals(False)
        if matching:
            self._show_manpage_entry(matching[selected_row][1])

    def selected_manpage_entry(self) -> ManPageEntry | None:
        item = self.manpage_list.currentItem()
        if item is None:
            return None
        index = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(index, int) or index < 0 or index >= len(self._manpages):
            raise CommandCatalogError("selected man page has an invalid source index")
        return self._manpages[index]

    def _manpage_selected(
        self,
        current: QListWidgetItem | None,
        previous: QListWidgetItem | None,
    ) -> None:
        del current, previous
        entry = self.selected_manpage_entry()
        if entry is not None:
            self._show_manpage_entry(entry)
            self.refresh_selected_manpage()

    def _show_manpage_entry(self, entry: ManPageEntry) -> None:
        self.manpage_title.setText(entry.title)
        prefix = "pymobiledevice3" if not entry.command_path else f"pymobiledevice3 {shlex.join(entry.command_path)}"
        self.manpage_command.setText(prefix)

    def select_manpage_path(self, command_path: tuple[str, ...]) -> None:
        matching_index = next(
            (index for index, entry in enumerate(self._manpages) if entry.command_path == command_path),
            None,
        )
        if matching_index is None:
            broader_paths = tuple(
                entry.command_path
                for entry in self._manpages
                if command_path[: len(entry.command_path)] == entry.command_path
            )
            if not broader_paths:
                raise CommandCatalogError(f"No man page entry covers command path: {command_path}")
            best_path = max(broader_paths, key=len)
            matching_index = next(
                index for index, entry in enumerate(self._manpages) if entry.command_path == best_path
            )
        self.manpage_search_field.clear()
        for row in range(self.manpage_list.count()):
            item = self.manpage_list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == matching_index:
                self.manpage_list.setCurrentRow(row)
                return
        raise CommandCatalogError(f"Man page path was indexed but not visible: {command_path}")

    def refresh_selected_manpage(self) -> None:
        entry = self.selected_manpage_entry()
        if entry is None:
            QMessageBox.information(self, "No Help Topic", "Select a command family first.")
            return
        if self._manpage_process is not None:
            QMessageBox.warning(self, "Help Loading", "Wait for the current help page to finish loading.")
            return
        self._manpage_stdout.clear()
        self._manpage_stderr.clear()
        self.manpage_output.setPlainText("Loading live help from the installed pymobiledevice3…")
        process = QProcess(self)
        process.setProgram(str(self._pmd3))
        process.setArguments([*entry.command_path, "--help"])
        process.setProcessEnvironment(qprocess_environment(base_environment()))
        process.readyReadStandardOutput.connect(self._read_manpage_stdout)
        process.readyReadStandardError.connect(self._read_manpage_stderr)
        process.finished.connect(self._manpage_finished)
        process.errorOccurred.connect(self._manpage_error)
        self._manpage_process = process
        self._update_manpage_controls()
        process.start()

    def _read_manpage_stdout(self) -> None:
        if self._manpage_process is not None:
            self._manpage_stdout.extend(bytes(self._manpage_process.readAllStandardOutput()))

    def _read_manpage_stderr(self) -> None:
        if self._manpage_process is not None:
            self._manpage_stderr.extend(bytes(self._manpage_process.readAllStandardError()))

    def _manpage_finished(self, exit_code: int, exit_status: QProcess.ExitStatus) -> None:
        del exit_status
        self._read_manpage_stdout()
        self._read_manpage_stderr()
        stdout = self._manpage_stdout.decode("utf-8", errors="replace")
        stderr = self._manpage_stderr.decode("utf-8", errors="replace")
        if exit_code == 0:
            self.manpage_output.setPlainText(stdout)
        else:
            self.manpage_output.setPlainText(
                f"Live help failed with exit code {exit_code}.\n\n{stderr or stdout}"
            )
        self._manpage_process = None
        self._update_manpage_controls()

    def _manpage_error(self, process_error: QProcess.ProcessError) -> None:
        if self._manpage_process is not None:
            self.manpage_output.setPlainText(f"Could not load help: {self._manpage_process.errorString()}")
            if process_error == QProcess.ProcessError.FailedToStart:
                self._manpage_process = None
                self._update_manpage_controls()

    def _update_manpage_controls(self) -> None:
        running = self._manpage_process is not None
        selected = self.selected_manpage_entry() is not None
        self.manpage_list.setEnabled(not running)
        self.manpage_search_field.setEnabled(not running)
        self.refresh_manpage_button.setEnabled(selected and not running)
        self.copy_manpage_command_button.setEnabled(selected and not running)
        self.use_manpage_command_button.setEnabled(selected and not running)

    def copy_selected_manpage_command(self) -> None:
        if not self.manpage_command.text():
            return
        QApplication.clipboard().setText(self.manpage_command.text())

    def use_selected_manpage_command(self) -> None:
        entry = self.selected_manpage_entry()
        if entry is None:
            return
        self.console_input.setText(shlex.join(entry.command_path))
        self.navigate_to_page("Command Center")

    def _confirm(self, title: str, message: str) -> bool:
        answer = QMessageBox.question(
            self,
            title,
            message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def _show_no_device(self) -> None:
        QMessageBox.warning(self, "No Device", "Connect, unlock, and trust an iPhone or iPad first.")

    def closeEvent(self, event: QCloseEvent) -> None:
        self._scanner.stop()
        critical_processes = tuple(
            process
            for process in (
                self._action_process,
                self._collection_process,
                self._sideload_process,
                self._apps_process,
                self._backup_process,
                self._console_process,
            )
            if process is not None and process.state() != QProcess.ProcessState.NotRunning
        )
        if critical_processes:
            should_close = self._confirm(
                "Stop Active Operations?",
                "A DDI, evidence, app, backup, or Command Center operation is still running. "
                "Stop it, allow cleanup/finalization, and close the app?",
            )
            if not should_close:
                event.ignore()
                return
        for process in critical_processes:
            process.terminate()
            if not process.waitForFinished(10000):
                process.kill()
                process.waitForFinished(3000)
        for process in (self._ipa_inspection_process, self._manpage_process):
            if process is not None and process.state() != QProcess.ProcessState.NotRunning:
                process.terminate()
        event.accept()


def main() -> int:
    application = QApplication(sys.argv)
    application.setApplicationName("iOS Device Workbench")
    application.setOrganizationName("Local Security Tools")
    application_icon = QIcon(str(application_icon_path()))
    if application_icon.isNull():
        raise RuntimeError(f"Could not load application icon: {application_icon_path()}")
    application.setWindowIcon(application_icon)
    window = MainWindow()
    window.show()
    window.raise_()
    window.activateWindow()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
