from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QRect, QTimer, QUrl, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QCloseEvent,
    QColor,
    QDesktopServices,
    QFont,
    QIcon,
    QMouseEvent,
    QPaintEvent,
    QPainter,
    QPen,
    QPixmap,
    QKeySequence,
    QShortcut,
    QTextCursor,
)
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
    QInputDialog,
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
    QScrollArea,
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
from ios_developer_toolkit.action_safety import (
    ActionSafetyProfile,
    advanced_action_safety,
    confirmation_phrase,
    guided_action_safety,
)
from ios_developer_toolkit.backup_process import BackupProcessController
from ios_developer_toolkit.backup_protocol import BackupAction, BackupEvent, BackupRequest, BackupRequestError
from ios_developer_toolkit.case_workflow import CaseWorkflowError, create_guided_case
from ios_developer_toolkit.capability_matrix import (
    CapabilityMatrixError,
    CapabilityResult,
    CapabilityState,
    CapabilityWorkerCompleted,
    CapabilityWorkerStarted,
    capability_definitions,
    capability_state_counts,
    capability_state_label,
    evaluate_preset_readiness,
    parse_capability_worker_event,
    untested_capability_results,
)
from ios_developer_toolkit.connection_diagnostics import (
    ConnectionDiagnostic,
    devices_connection_diagnostic,
    failed_connection_diagnostic,
    initial_connection_diagnostic,
    launch_failed_connection_diagnostic,
    malformed_output_connection_diagnostic,
    process_error_connection_diagnostic,
    timed_out_connection_diagnostic,
)
from ios_developer_toolkit.device_compatibility import (
    DeviceCompatibilityError,
    DeviceCompatibilityObservation,
    append_observation,
    compatibility_history_path,
    create_observation,
    latest_observations,
    load_observations,
)
from ios_developer_toolkit.demo_mode import demo_connection_banner, demo_device
from ios_developer_toolkit.gui_pages import (
    build_home_page,
    build_live_logs_page,
    build_safety_page,
    toolkit_stylesheet,
)
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
from ios_developer_toolkit.command_drift import (
    HelpRouteProbe,
    evaluate_command_drift,
    help_routes_for_presets,
    render_command_drift_report,
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
from ios_developer_toolkit.location_lab import (
    Coordinates,
    GPXInspection,
    LocationEvidenceEvent,
    LocationLabError,
    SavedLocation,
    add_saved_location,
    append_evidence_event,
    build_route,
    clear_location_arguments,
    coordinates_to_map_fractions,
    inspect_gpx,
    load_saved_locations,
    map_fractions_to_coordinates,
    move_coordinates,
    parse_location_input,
    parse_route_waypoints,
    parse_ios_major,
    play_location_arguments,
    remove_saved_location,
    save_saved_locations,
    saved_locations_path,
    set_location_arguments,
    utc_now,
    validate_coordinates,
)
from ios_developer_toolkit.live_logs import LiveLogError, LiveLogWindow, log_stream_specs, stream_spec
from ios_developer_toolkit.models import DeviceDataError, IOSDevice, parse_devices_json
from ios_developer_toolkit.qt_process import (
    FiniteProcessController,
    OperationResult,
    finite_process_request,
)
from ios_developer_toolkit.runtime import (
    ExecutableCommand,
    command_arguments,
    command_argv,
    command_text,
    device_environment,
    is_frozen_runtime,
    pymobiledevice3_command,
    worker_command,
)
from ios_developer_toolkit.support_bundle import (
    SupportBundleContext,
    SupportBundleError,
    SupportStatus,
    create_sanitized_support_bundle,
)
from ios_developer_toolkit.ufade_connector import (
    UFADE_INSTALLATION_URL,
    UFADE_REPOSITORY_URL,
    UFADE_USAGE_URL,
    UFADEInstallation,
    UFADEValidationError,
    checkout_python_path,
    inspect_ufade_installation,
    macos_setup_commands,
)
from ios_developer_toolkit.validation import output_indicates_failure


XCODE_CANDIDATE_DDI = Path("/Library/Developer/CoreDevice/CandidateDDIs/iOS_DDI.dmg")
DEVELOPER_DISK_IMAGE_REPOSITORY = "https://github.com/doronz88/DeveloperDiskImage"
MANPAGE_HELP_TIMEOUT_MS = 15_000
MANPAGE_HELP_KILL_DELAY_MS = 1_500
COMMAND_DRIFT_HELP_TIMEOUT_MS = 5_000
RECONNECT_TIMEOUT_MS = 30_000
DEVICE_SCAN_TIMEOUT_MS = 10_000
DDI_ACTION_TIMEOUT_MS = 15 * 60_000
APPS_ACTION_TIMEOUT_MS = 10 * 60_000
IPA_INSPECTION_TIMEOUT_MS = 5 * 60_000
IPA_INSTALL_TIMEOUT_MS = 15 * 60_000
PROCESS_TERMINATE_GRACE_MS = 1_500


def application_icon_path() -> Path:
    icon_path = Path(__file__).resolve().parent / "assets" / "iosdevtoolkit.png"
    if not icon_path.is_file():
        raise FileNotFoundError(f"Application icon is missing: {icon_path}")
    return icon_path


def location_map_asset_path() -> Path:
    map_path = Path(__file__).resolve().parent / "assets" / "location-world-map.png"
    if not map_path.is_file():
        raise FileNotFoundError(f"Location Lab map asset is missing: {map_path}")
    return map_path


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
    diagnostic_changed = Signal(object)

    def __init__(self, executable: ExecutableCommand) -> None:
        super().__init__()
        self._executable = executable
        self._timer = QTimer(self)
        self._timer.setInterval(3000)
        self._timer.timeout.connect(self.scan)
        self._controller = FiniteProcessController(self)
        self._controller.completed.connect(self._completed)
        self._stopping = False

    def start(self) -> None:
        self._stopping = False
        self.scan()
        self._timer.start()

    def stop(self) -> None:
        self._stopping = True
        self._timer.stop()
        self._controller.shutdown(3000, 1000)

    def scan(self) -> None:
        if self._stopping:
            return
        if self._controller.is_running():
            return
        request = finite_process_request(
            self._executable,
            ("usbmux", "list"),
            base_environment(),
            DEVICE_SCAN_TIMEOUT_MS,
            PROCESS_TERMINATE_GRACE_MS,
        )
        self._controller.start(request)

    def _completed(self, result_object: object) -> None:
        if self._stopping:
            return
        if not isinstance(result_object, OperationResult):
            raise TypeError(f"Expected OperationResult, received {type(result_object).__name__}")
        if result_object.outcome == "launch-failed":
            diagnostic = launch_failed_connection_diagnostic()
            self.diagnostic_changed.emit(diagnostic)
            self.scan_error.emit(diagnostic.detail)
            return
        if result_object.outcome == "timed-out":
            diagnostic = timed_out_connection_diagnostic()
            self.diagnostic_changed.emit(diagnostic)
            self.scan_error.emit(diagnostic.detail)
            return
        if result_object.outcome in ("crashed", "cancelled"):
            diagnostic = process_error_connection_diagnostic()
            self.diagnostic_changed.emit(diagnostic)
            self.scan_error.emit(diagnostic.detail)
            return
        if result_object.outcome == "failed":
            if result_object.exit_code is None:
                raise RuntimeError("Failed device discovery did not provide an exit code")
            diagnostic = failed_connection_diagnostic(result_object.exit_code)
            self.diagnostic_changed.emit(diagnostic)
            self.scan_error.emit(diagnostic.detail)
            return
        if result_object.outcome != "succeeded":
            raise RuntimeError(f"Unsupported device discovery outcome: {result_object.outcome}")
        try:
            devices = parse_devices_json(result_object.stdout.decode("utf-8"))
        except (DeviceDataError, json.JSONDecodeError, UnicodeDecodeError):
            diagnostic = malformed_output_connection_diagnostic()
            self.diagnostic_changed.emit(diagnostic)
            self.scan_error.emit(diagnostic.detail)
            return
        self.diagnostic_changed.emit(devices_connection_diagnostic(len(devices)))
        self.devices_changed.emit(devices)


class LocationMapWidget(QWidget):
    coordinate_selected = Signal(float, float)

    def __init__(self, marker: Coordinates) -> None:
        super().__init__()
        self._map = QPixmap(str(location_map_asset_path()))
        if self._map.isNull():
            raise FileNotFoundError(f"Could not load Location Lab map asset: {location_map_asset_path()}")
        self._marker = marker
        self.setMinimumSize(360, 190)
        self.setAccessibleName("Offline world map coordinate picker")
        self.setToolTip("Click the offline map to fill the coordinate fields. The device is not changed until confirmation.")

    def set_marker(self, marker: Coordinates) -> None:
        self._marker = marker
        self.update()

    def _map_rectangle(self) -> QRect:
        available_width = max(1, self.width())
        available_height = max(1, self.height())
        map_width = available_width
        map_height = max(1, map_width // 2)
        if map_height > available_height:
            map_height = available_height
            map_width = max(1, map_height * 2)
        return QRect(
            (available_width - map_width) // 2,
            (available_height - map_height) // 2,
            map_width,
            map_height,
        )

    def paintEvent(self, event: QPaintEvent) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rectangle = self._map_rectangle()
        painter.drawPixmap(rectangle, self._map)
        horizontal, vertical = coordinates_to_map_fractions(self._marker)
        marker_x = rectangle.left() + horizontal * rectangle.width()
        marker_y = rectangle.top() + vertical * rectangle.height()
        painter.setPen(QPen(QColor("#ffffff"), 2.0))
        painter.setBrush(QBrush(QColor("#e43b3b")))
        painter.drawEllipse(int(marker_x) - 6, int(marker_y) - 6, 12, 12)
        painter.end()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            event.ignore()
            return
        rectangle = self._map_rectangle()
        position = event.position()
        if not rectangle.contains(int(position.x()), int(position.y())):
            event.ignore()
            return
        coordinates = map_fractions_to_coordinates(
            (position.x() - rectangle.left()) / rectangle.width(),
            (position.y() - rectangle.top()) / rectangle.height(),
        )
        self.set_marker(coordinates)
        self.coordinate_selected.emit(coordinates.latitude, coordinates.longitude)
        event.accept()


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


class UFADEGuideDialog(QDialog):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Install and Run UFADE on macOS")
        self.resize(820, 690)
        layout = QVBoxLayout(self)
        heading = QLabel("UFADE setup and acquisition walkthrough")
        heading.setObjectName("ufadeGuideHeading")
        heading.setFont(QFont(heading.font().family(), 18, QFont.Weight.DemiBold))
        layout.addWidget(heading)
        instructions = QTextBrowser()
        instructions.setObjectName("ufadeGuideContent")
        instructions.setOpenExternalLinks(True)
        instructions.setHtml(
            f"""
            <h3>1. Install UFADE in a separate Python 3.11 environment</h3>
            <p>Click <b>Copy Setup Commands</b> in the Backup workspace, paste the commands into Terminal, and wait for
            every command to finish. The recommended clone includes UFADE's developer-image submodule. Do not install
            UFADE's older pinned dependencies into the iOS Developer Toolkit environment.</p>

            <h3>2. Point the toolkit at that installation</h3>
            <ol>
              <li><b>UFADE checkout:</b> choose the cloned <code>UFADE</code> folder containing
              <code>ufade.py</code>, <code>requirements.txt</code>, and <code>LICENSE</code>.</li>
              <li><b>Python 3.11:</b> click <b>Use Checkout .venv</b>, or choose
              <code>UFADE/.venv/bin/python</code> manually.</li>
              <li><b>Working/output folder:</b> choose protected local storage with enough free space.</li>
              <li>Click <b>Validate Installation</b>. Fix every reported missing file, Python-version, or import error
              before launching.</li>
            </ol>

            <h3>3. Connect the device</h3>
            <p>Connect one authorized iPhone or iPad by USB, unlock it, tap <b>Trust</b>, and keep it unlocked while
            UFADE discovers it. The toolkit's selected device is shown for confirmation, but UFADE performs its own
            device discovery and selection.</p>

            <h3>4. Launch and operate UFADE</h3>
            <ol>
              <li>Click <b>Launch UFADE</b> and review the confirmation showing the checkout, Python, device, and
              working directory.</li>
              <li>In UFADE's window, confirm or change the output directory. The toolkit starts UFADE in the selected
              working directory, which becomes its initial default.</li>
              <li>Choose <b>Save device information</b>, a <b>Backup Option</b>, <b>Collect Unified Logs</b>,
              <b>Developer Options</b>, or <b>Advanced Options</b> inside UFADE.</li>
              <li>Answer password and acquisition prompts only in UFADE. The toolkit never receives those values.</li>
              <li>Use UFADE's own progress and stop controls. Closing this toolkit does not stop UFADE.</li>
            </ol>

            <h3>5. Developer options and completion</h3>
            <p>UFADE Developer Options may require Developer Mode, a compatible developer image, and the populated
            <code>ufade_developer</code> submodule. If the checkout was cloned without submodules, run
            <code>git submodule update --init --recursive</code> in the UFADE folder and validate again.</p>
            <p>After acquisition, wait for UFADE to report completion, review the output folder, preserve hashes and
            custody records separately, and protect the output before sharing it. A successful launch is not proof
            that every selected acquisition source completed.</p>

            <p><a href="{UFADE_INSTALLATION_URL}">Official UFADE installation instructions</a> ·
            <a href="{UFADE_USAGE_URL}">Official UFADE usage guide</a></p>
            """
        )
        layout.addWidget(instructions, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"iOS Developer Toolkit {APP_VERSION}")
        self.setAccessibleName("iOS Developer Toolkit main window")
        self.setAccessibleDescription(
            "Keyboard-first workspace for authorized iPhone and iPad development, diagnostics, backup, and evidence collection."
        )
        self.setWindowIcon(QIcon(str(application_icon_path())))
        self.resize(1280, 840)
        self._pmd3 = pymobiledevice3_command()
        self._devices: tuple[IOSDevice, ...] = ()
        self._connection_diagnostic = initial_connection_diagnostic()
        self._demo_mode = False
        self._demo_device = demo_device()
        self._active_device_identifier: str | None = None
        self._guided_udids: set[str] = set()
        self._reconnect_active = False
        self._reconnect_timeout_timer = QTimer(self)
        self._reconnect_timeout_timer.setSingleShot(True)
        self._reconnect_timeout_timer.timeout.connect(self._reconnect_timed_out)
        self._action_controller = FiniteProcessController(self)
        self._action_controller.stdout_received.connect(self._append_action_output)
        self._action_controller.stderr_received.connect(self._append_action_output)
        self._action_controller.completed.connect(self._action_completed)
        self._action_context = ""
        self._capability_process: QProcess | None = None
        self._capability_stdout_buffer = bytearray()
        self._capability_stderr = bytearray()
        self._capability_results: dict[str, CapabilityResult] = {
            result.identifier: result for result in untested_capability_results()
        }
        self._capability_completed_at: str | None = None
        self._capability_cancel_reason: str | None = None
        self._capability_worker_completed = False
        self._compatibility_history_path = compatibility_history_path(Path.home())
        self._compatibility_observations: tuple[DeviceCompatibilityObservation, ...] = ()
        self._compatibility_history_error: str | None = None
        try:
            self._compatibility_observations = load_observations(self._compatibility_history_path)
        except DeviceCompatibilityError as error:
            self._compatibility_history_error = str(error)
        self._collection_process: QProcess | None = None
        self._ipa_inspection_controller = FiniteProcessController(self)
        self._ipa_inspection_controller.completed.connect(self._ipa_inspection_completed)
        self._ipa_inspection: IPAInspection | None = None
        self._selected_ipa: Path | None = None
        self._sideload_controller = FiniteProcessController(self)
        self._sideload_controller.stdout_received.connect(self._append_sideload_output)
        self._sideload_controller.stderr_received.connect(self._append_sideload_output)
        self._sideload_controller.completed.connect(self._sideload_completed)
        self._sideload_context = ""
        self._apps_controller = FiniteProcessController(self)
        self._apps_controller.stderr_received.connect(self._append_apps_stderr)
        self._apps_controller.completed.connect(self._apps_completed)
        self._apps_context = ""
        self._installed_apps: tuple[InstalledApp, ...] = ()
        self._backup_controller = BackupProcessController(self)
        self._backup_controller.event_received.connect(self._handle_backup_event)
        self._backup_controller.stderr_received.connect(self._append_backup_stderr)
        self._backup_controller.completed.connect(self._backup_completed)
        self._backup_action: BackupAction | None = None
        self._backup_encryption_state: bool | None = None
        self._last_backup_path: Path | None = None
        self._ufade_installation: UFADEInstallation | None = None
        self._location_process: QProcess | None = None
        self._location_operation = ""
        self._location_arguments: tuple[str, ...] = ()
        self._location_buffer = bytearray()
        self._location_started_at = ""
        self._location_device_identifier: str | None = None
        self._location_device_name = ""
        self._location_device_version = ""
        self._location_coordinates: Coordinates | None = None
        self._selected_location_gpx: GPXInspection | None = None
        self._location_operation_gpx: GPXInspection | None = None
        self._location_clear_after_stop = False
        self._location_may_be_simulated = False
        self._location_log_path: Path | None = None
        self._live_log_windows: set[LiveLogWindow] = set()
        self._saved_locations_path = saved_locations_path(Path.home())
        self._saved_locations: tuple[SavedLocation, ...] = ()
        self._saved_locations_error: str | None = None
        try:
            self._saved_locations = load_saved_locations(self._saved_locations_path)
        except LocationLabError as error:
            self._saved_locations_error = str(error)
        self._console_process: QProcess | None = None
        self._presets = command_presets()
        self._current_preset: CommandPreset | None = None
        self._preset_parameter_fields: dict[str, QLineEdit] = {}
        self._manpages = manpage_entries()
        self._manpage_controller = FiniteProcessController(self)
        self._manpage_controller.completed.connect(self._manpage_completed)
        self._manpage_cache: dict[tuple[str, ...], str] = {}
        self._manpage_active_path: tuple[str, ...] | None = None
        self._command_drift_controller = FiniteProcessController(self)
        self._command_drift_controller.completed.connect(self._command_drift_probe_completed)
        self._command_drift_paths: tuple[tuple[str, ...], ...] = ()
        self._command_drift_index = 0
        self._command_drift_active_path: tuple[str, ...] | None = None
        self._command_drift_session_active = False
        self._command_drift_cancelled = False
        self._command_drift_probes: dict[tuple[str, ...], HelpRouteProbe] = {}
        self._last_case_path: Path | None = None
        self._active_case_path: Path | None = None
        self._keyboard_shortcuts: list[QShortcut] = []
        self._build_ui()
        self._configure_accessibility()
        self._configure_keyboard_shortcuts()
        self._scanner = DeviceScanner(self._pmd3)
        self._scanner.devices_changed.connect(self._devices_changed)
        self._scanner.scan_error.connect(self._scan_error)
        self._scanner.diagnostic_changed.connect(self._connection_diagnostic_changed)
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
        title = QLabel("iOS Developer Toolkit")
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
        self.demo_mode_button = QPushButton("Demo Mode")
        self.demo_mode_button.setObjectName("demoModeButton")
        self.demo_mode_button.setToolTip("Show a clearly simulated iPhone without connecting to a device")
        self.demo_mode_button.clicked.connect(self.toggle_demo_mode)
        header_layout.addWidget(self.demo_mode_button)
        self.refresh_devices_button = QPushButton("Retry Scan")
        self.refresh_devices_button.setObjectName("refreshDevicesButton")
        self.refresh_devices_button.clicked.connect(self._scanner_scan)
        header_layout.addWidget(self.refresh_devices_button)
        self.reconnect_device_button = QPushButton("Reconnect & Retry…")
        self.reconnect_device_button.setObjectName("reconnectDeviceButton")
        self.reconnect_device_button.setToolTip(
            "Guide a physical reconnection and retry USB device discovery for 30 seconds."
        )
        self.reconnect_device_button.clicked.connect(self._reconnect_device)
        header_layout.addWidget(self.reconnect_device_button)
        self.keyboard_shortcuts_button = QPushButton("Keyboard Shortcuts")
        self.keyboard_shortcuts_button.setObjectName("keyboardShortcutsButton")
        self.keyboard_shortcuts_button.setToolTip("Show keyboard shortcuts (⌘/)")
        self.keyboard_shortcuts_button.clicked.connect(self.show_keyboard_shortcuts)
        header_layout.addWidget(self.keyboard_shortcuts_button)
        self.support_bundle_button = QPushButton("Create Support Bundle…")
        self.support_bundle_button.setObjectName("createSupportBundleButton")
        self.support_bundle_button.setToolTip("Create a local sanitized ZIP for a support request")
        self.support_bundle_button.clicked.connect(self.create_support_bundle)
        header_layout.addWidget(self.support_bundle_button)
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
        version_note = QLabel(f"Toolkit {APP_VERSION}\npymobiledevice3 11.15.1")
        version_note.setObjectName("sidebarVersion")
        version_note.setWordWrap(True)
        sidebar_layout.addWidget(version_note)
        workspace.addWidget(sidebar)

        self.page_stack = QStackedWidget()
        self.page_stack.setObjectName("workspacePages")
        pages = (
            ("Home", self._build_home_page()),
            ("Device & DDI", self._build_overview_tab()),
            ("Capability Matrix", self._build_capability_matrix_page()),
            ("Location Lab", self._build_location_lab_page()),
            ("Live Logs", self._build_live_logs_page()),
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

    def _configure_accessibility(self) -> None:
        self.device_combo.setAccessibleName("Selected iPhone or iPad")
        self.device_combo.setAccessibleDescription(
            "Choose a detected, trusted device. Use Command L to focus the workspace list instead."
        )
        self.demo_mode_button.setAccessibleName("Toggle simulated device demo mode")
        self.demo_mode_button.setAccessibleDescription(
            "Show or hide a clearly labeled simulated iPhone. Demo mode never runs device-affecting actions."
        )
        self.refresh_devices_button.setAccessibleName("Retry device scan")
        self.refresh_devices_button.setAccessibleDescription("Immediately refresh the usbmux device inventory. Shortcut: Command R.")
        self.reconnect_device_button.setAccessibleName("Reconnect device and retry scan")
        self.reconnect_device_button.setAccessibleDescription(
            "Open a guided USB reconnection and trust workflow without restarting macOS services."
        )
        self.keyboard_shortcuts_button.setAccessibleName("Keyboard shortcuts")
        self.keyboard_shortcuts_button.setAccessibleDescription("Open the keyboard shortcut reference. Shortcut: Command Slash.")
        self.support_bundle_button.setAccessibleName("Create sanitized support bundle")
        self.support_bundle_button.setAccessibleDescription(
            "Create a local ZIP that excludes device content and sensitive artifacts. The application never uploads it."
        )
        self.connection_banner.setAccessibleName("Device connection status")
        self.connection_banner.setAccessibleDescription(
            "Reports whether a trusted iPhone or iPad is currently available to the toolkit."
        )
        self.navigation_list.setAccessibleName("Workspace navigation")
        self.navigation_list.setAccessibleDescription(
            "Use Up and Down Arrow to choose a workspace, then Tab to enter its controls. Shortcut: Command L."
        )
        self.navigation_list.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.page_stack.setAccessibleName("Active workspace")
        self.page_stack.setAccessibleDescription("Contains the controls for the selected workspace.")
        self.page_stack.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.command_search_field.setAccessibleName("Search guided commands")
        self.command_search_field.setAccessibleDescription("Filters guided command presets by title, category, summary, or command arguments.")
        self.command_preset_list.setAccessibleName("Guided command presets")
        self.command_preset_list.setAccessibleDescription(
            "Use Up and Down Arrow to choose a preset. Tab reaches its validated values and action buttons."
        )
        self.command_preview.setAccessibleName("Guided command preview")
        self.command_preview.setAccessibleDescription("Read-only exact pymobiledevice3 argument vector for the selected preset.")
        self.command_drift_output.setAccessibleName("Command drift report")
        self.command_drift_output.setAccessibleDescription(
            "Read-only report of installed live-help route and option compatibility for guided presets."
        )
        self.manpage_search_field.setAccessibleName("Search live help topics")
        self.manpage_search_field.setAccessibleDescription("Filters the local index of pymobiledevice3 command routes.")
        self.manpage_list.setAccessibleName("Live help topic list")
        self.manpage_list.setAccessibleDescription("Use Up and Down Arrow to choose a command route, then Tab to live-help actions.")
        self.manpage_output.setAccessibleName("Live help output")
        self.manpage_output.setAccessibleDescription("Read-only help output from the installed project-local pymobiledevice3 executable.")
        self.app_filter_field.setAccessibleName("Filter installed applications")
        self.app_filter_field.setAccessibleDescription("Filters the installed application inventory without changing the device.")
        self.installed_apps_table.setAccessibleName("Installed application inventory")
        self.installed_apps_table.setAccessibleDescription("Use Arrow keys to select an application. Actions require explicit confirmation.")
        self.capability_table.setAccessibleName("Capability Matrix results")
        self.capability_table.setAccessibleDescription("Use Arrow keys to select a capability and read its detailed evidence below.")
        self.compatibility_history_table.setAccessibleName("Real-device compatibility history")
        self.compatibility_history_table.setAccessibleDescription("Local compatibility observations with no raw device identifiers.")
        self.location_map.setAccessibleDescription(
            "Offline mouse coordinate picker. For keyboard-first location entry, use the coordinate importer, latitude, and longitude fields."
        )
        self.location_map.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        QWidget.setTabOrder(self.device_combo, self.demo_mode_button)
        QWidget.setTabOrder(self.demo_mode_button, self.refresh_devices_button)
        QWidget.setTabOrder(self.refresh_devices_button, self.reconnect_device_button)
        QWidget.setTabOrder(self.reconnect_device_button, self.keyboard_shortcuts_button)
        QWidget.setTabOrder(self.keyboard_shortcuts_button, self.support_bundle_button)
        QWidget.setTabOrder(self.support_bundle_button, self.navigation_list)

    def _configure_keyboard_shortcuts(self) -> None:
        self._add_application_shortcut("Meta+R", self._scanner_scan, "shortcutRetryDeviceScan")
        self._add_application_shortcut("Meta+L", self.focus_workspace_navigation, "shortcutFocusWorkspaceNavigation")
        self._add_application_shortcut("Meta+F", self.focus_workspace_search, "shortcutFocusWorkspaceSearch")
        self._add_application_shortcut("Meta+/", self.show_keyboard_shortcuts, "shortcutShowKeyboardReference")
        self._add_application_shortcut("Meta+Alt+Left", self.navigate_previous_workspace, "shortcutPreviousWorkspace")
        self._add_application_shortcut("Meta+Alt+Right", self.navigate_next_workspace, "shortcutNextWorkspace")
        workspace_shortcuts = (
            ("Meta+1", "Home"),
            ("Meta+2", "Device & DDI"),
            ("Meta+3", "Capability Matrix"),
            ("Meta+4", "Location Lab"),
            ("Meta+5", "Live Logs"),
            ("Meta+6", "Command Center"),
            ("Meta+7", "Installed Apps"),
            ("Meta+8", "Backup"),
            ("Meta+9", "Sideload IPA"),
            ("Meta+0", "Evidence Capture"),
            ("Meta+Shift+M", "Man Pages"),
            ("Meta+Shift+S", "Scope & Safety"),
        )
        for sequence, page_name in workspace_shortcuts:
            identifier = f"shortcutOpen{page_name.replace(' ', '').replace('&', 'And')}"
            self._add_application_shortcut(
                sequence,
                self._workspace_shortcut_handler(page_name),
                identifier,
            )

    def _add_application_shortcut(self, sequence: str, callback: Callable[[], None], identifier: str) -> None:
        shortcut = QShortcut(QKeySequence(sequence), self)
        shortcut.setObjectName(identifier)
        shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        shortcut.activated.connect(callback)
        self._keyboard_shortcuts.append(shortcut)

    def _workspace_shortcut_handler(self, page_name: str) -> Callable[[], None]:
        def navigate() -> None:
            self.navigate_to_page_and_focus(page_name)

        return navigate

    def navigate_to_page_and_focus(self, name: str) -> None:
        self.navigate_to_page(name)
        focus_targets: Mapping[str, QWidget] = {
            "Home": self.findChild(QPushButton, "homeOpenDevice&DDIButton"),
            "Device & DDI": self.refresh_devices_button,
            "Capability Matrix": self.refresh_capabilities_button,
            "Location Lab": self.location_input_field,
            "Live Logs": self.findChild(QPushButton, "openUnifiedLogButton"),
            "Command Center": self.command_search_field,
            "Installed Apps": self.app_filter_field,
            "Backup": self.backup_destination_field,
            "Sideload IPA": self.ipa_path_field,
            "Evidence Capture": self.case_title_field,
            "Man Pages": self.manpage_search_field,
            "Scope & Safety": self.navigation_list,
        }
        target = focus_targets.get(name)
        if target is None:
            raise RuntimeError(f"Workspace focus target is missing: {name}")
        target.setFocus(Qt.FocusReason.ShortcutFocusReason)
        if isinstance(target, QLineEdit):
            target.selectAll()

    def focus_workspace_navigation(self) -> None:
        self.navigation_list.setFocus(Qt.FocusReason.ShortcutFocusReason)

    def focus_workspace_search(self) -> None:
        current_item = self.navigation_list.currentItem()
        if current_item is None:
            raise RuntimeError("Cannot focus a workspace search without a selected workspace")
        focus_targets: Mapping[str, QLineEdit] = {
            "Command Center": self.command_search_field,
            "Man Pages": self.manpage_search_field,
            "Installed Apps": self.app_filter_field,
            "Location Lab": self.location_input_field,
        }
        target = focus_targets.get(current_item.text())
        if target is None:
            self.focus_workspace_navigation()
            return
        target.setFocus(Qt.FocusReason.ShortcutFocusReason)
        target.selectAll()

    def navigate_previous_workspace(self) -> None:
        count = self.navigation_list.count()
        if count == 0:
            raise RuntimeError("Workspace navigation contains no pages")
        self.navigation_list.setCurrentRow((self.navigation_list.currentRow() - 1) % count)
        self.focus_workspace_navigation()

    def navigate_next_workspace(self) -> None:
        count = self.navigation_list.count()
        if count == 0:
            raise RuntimeError("Workspace navigation contains no pages")
        self.navigation_list.setCurrentRow((self.navigation_list.currentRow() + 1) % count)
        self.focus_workspace_navigation()

    def show_keyboard_shortcuts(self) -> None:
        dialog = QDialog(self)
        dialog.setObjectName("keyboardShortcutsDialog")
        dialog.setWindowTitle("Keyboard Shortcuts")
        dialog.setAccessibleName("Keyboard shortcut reference")
        dialog.setAccessibleDescription("Lists application-wide keyboard shortcuts for workspace navigation and search.")
        dialog.setMinimumWidth(650)
        layout = QVBoxLayout(dialog)
        heading = QLabel("Keyboard-first navigation")
        heading.setFont(QFont(heading.font().family(), 18, QFont.Weight.DemiBold))
        layout.addWidget(heading)
        reference = QTextBrowser()
        reference.setAccessibleName("Keyboard shortcut list")
        reference.setHtml(
            "<table>"
            "<tr><th align='left'>Shortcut</th><th align='left'>Action</th></tr>"
            "<tr><td>⌘ R</td><td>Retry device scan</td></tr>"
            "<tr><td>⌘ L</td><td>Focus workspace navigation</td></tr>"
            "<tr><td>⌘ F</td><td>Focus search in Command Center, Man Pages, Installed Apps, or Location Lab</td></tr>"
            "<tr><td>⌘ ⌥ ← / ⌘ ⌥ →</td><td>Previous / next workspace</td></tr>"
            "<tr><td>⌘ 1–0</td><td>Home through Evidence Capture</td></tr>"
            "<tr><td>⌘ ⇧ M</td><td>Man Pages</td></tr>"
            "<tr><td>⌘ ⇧ S</td><td>Scope &amp; Safety</td></tr>"
            "<tr><td>⌘ /</td><td>Open this reference</td></tr>"
            "<tr><td>Tab / Shift Tab</td><td>Move through controls</td></tr>"
            "<tr><td>Space / Return</td><td>Activate the focused control</td></tr>"
            "<tr><td>Arrow keys</td><td>Move through lists and tables</td></tr>"
            "</table>"
            "<p>Shortcuts navigate or focus only. Commands that write files or change device state still require the "
            "existing review and typed acknowledgements.</p>"
        )
        layout.addWidget(reference)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.setAccessibleName("Close keyboard shortcut reference")
        buttons.rejected.connect(dialog.reject)
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)
        dialog.exec()

    def create_support_bundle(self) -> None:
        message = (
            "Create a local sanitized support ZIP?\n\n"
            "Included: toolkit and dependency versions, macOS/Python metadata, aggregate readiness counts, selected "
            "workspace, sanitized status summaries, and the command-drift report.\n\n"
            "Excluded: device identities, pairing records, backups, cases, captures, screenshots, raw logs, PCAPs, "
            "crash reports, IPA files, command output, passwords, and user-entered values.\n\n"
            "The application will not upload the ZIP. Review it before sharing."
        )
        if not self._confirm("Create Sanitized Support Bundle", message):
            return
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")
        suggested_path = Path.home() / f"iOSDeveloperToolkit-support-{timestamp}.zip"
        selected, _ = QFileDialog.getSaveFileName(
            self,
            "Save Sanitized Support Bundle",
            str(suggested_path),
            "ZIP archive (*.zip)",
        )
        if not selected:
            return
        destination = Path(selected)
        if destination.suffix.casefold() != ".zip":
            destination = destination.with_suffix(".zip")
        try:
            result = create_sanitized_support_bundle(destination.resolve(), self._support_bundle_context())
        except SupportBundleError as error:
            QMessageBox.critical(self, "Could Not Create Support Bundle", str(error))
            return
        QMessageBox.information(
            self,
            "Sanitized Support Bundle Created",
            f"Created locally:\n{result.path}\n\nContains {len(result.entries)} reviewed support files. Review the ZIP before sharing.",
        )

    def _support_bundle_context(self) -> SupportBundleContext:
        current_item = self.navigation_list.currentItem()
        if current_item is None:
            raise RuntimeError("Cannot create a support bundle without a selected workspace")
        capability_counts = capability_state_counts(self._capability_results.values())
        statuses = (
            SupportStatus("connection", self.connection_banner.text()),
            SupportStatus("connection_diagnostic", self._connection_diagnostic.report()),
            SupportStatus("developer_mode", self.developer_mode_status.text()),
            SupportStatus("capability_matrix", self.capability_status.text()),
            SupportStatus("command_drift", self.command_drift_status.text()),
        )
        redactions = tuple(
            value
            for device in self._devices
            for value in (device.identifier, device.name)
            if value
        )
        return SupportBundleContext(
            APP_VERSION,
            current_item.text(),
            len(self._devices),
            self.selected_device() is not None,
            capability_counts,
            self.command_drift_output.toPlainText(),
            statuses,
            redactions,
            is_frozen_runtime(),
        )

    def _build_home_page(self) -> QWidget:
        return build_home_page(len(self._presets), len(self._manpages), self.navigate_to_page)

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

    def _open_log_presets(self) -> None:
        self.command_category_combo.setCurrentText("Logging & Capture")
        self.command_search_field.clear()
        self.navigate_to_page("Command Center")

    def open_live_log_window(self, identifier: str) -> None:
        device = self.selected_device()
        if device is None:
            self._show_no_device()
            return
        try:
            specification = stream_spec(identifier)
            window = LiveLogWindow(
                self._pmd3,
                specification,
                device.identifier,
                device.name,
                dict(device_environment(device.identifier)),
                application_icon_path(),
            )
        except LiveLogError as error:
            QMessageBox.critical(self, "Could Not Open Live Log", str(error))
            return
        window.closed.connect(self._live_log_window_closed)
        self._live_log_windows.add(window)
        window.show()
        window.raise_()
        window.activateWindow()

    def _live_log_window_closed(self, window_object: object) -> None:
        if not isinstance(window_object, LiveLogWindow):
            raise TypeError(f"Expected a LiveLogWindow close signal, received {type(window_object).__name__}")
        self._live_log_windows.discard(window_object)

    def _build_overview_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(14)

        diagnostic_group = QGroupBox("Connection diagnostic")
        diagnostic_layout = QVBoxLayout(diagnostic_group)
        self.connection_diagnostic_value = QLabel(self._connection_diagnostic.report())
        self.connection_diagnostic_value.setObjectName("connectionDiagnosticValue")
        self.connection_diagnostic_value.setWordWrap(True)
        self.connection_diagnostic_value.setAccessibleName("Connection discovery diagnostic")
        self.connection_diagnostic_value.setAccessibleDescription(
            "Reports the most recent usbmux discovery result without including device identity or raw command output."
        )
        diagnostic_layout.addWidget(self.connection_diagnostic_value)
        layout.addWidget(diagnostic_group)

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

    def _build_capability_matrix_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(12)

        heading = QLabel("Device Capability Matrix")
        heading.setObjectName("pageTitle")
        heading.setFont(QFont(heading.font().family(), 20, QFont.Weight.Bold))
        layout.addWidget(heading)
        explanation = QLabel(
            "Run bounded, read-only probes against the selected device. The matrix separates host readiness, trust, "
            "Developer Mode, the mounted DDI, iOS 17+ tunneling, developer services, and optional Web Inspector access."
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)

        controls = QHBoxLayout()
        self.refresh_capabilities_button = QPushButton("Run Capability Matrix")
        self.refresh_capabilities_button.setObjectName("runCapabilityMatrixButton")
        self.refresh_capabilities_button.clicked.connect(self.refresh_capability_matrix)
        controls.addWidget(self.refresh_capabilities_button)
        self.cancel_capabilities_button = QPushButton("Cancel")
        self.cancel_capabilities_button.setObjectName("cancelCapabilityMatrixButton")
        self.cancel_capabilities_button.clicked.connect(self.cancel_capability_matrix)
        controls.addWidget(self.cancel_capabilities_button)
        self.copy_capabilities_button = QPushButton("Copy Report")
        self.copy_capabilities_button.setObjectName("copyCapabilityMatrixButton")
        self.copy_capabilities_button.clicked.connect(self.copy_capability_report)
        controls.addWidget(self.copy_capabilities_button)
        open_device_button = QPushButton("Open Device && DDI")
        open_device_button.setObjectName("capabilityOpenDeviceDDIButton")
        open_device_button.clicked.connect(self._navigation_handler("Device & DDI"))
        controls.addWidget(open_device_button)
        controls.addStretch()
        layout.addLayout(controls)

        self.capability_status = QLabel("Select a device and run the matrix. No probe runs automatically.")
        self.capability_status.setObjectName("capabilityMatrixStatus")
        self.capability_status.setWordWrap(True)
        layout.addWidget(self.capability_status)
        self.capability_progress = QProgressBar()
        self.capability_progress.setObjectName("capabilityMatrixProgress")
        self.capability_progress.setTextVisible(True)
        self.capability_progress.setRange(0, len(capability_definitions()))
        self.capability_progress.setValue(0)
        layout.addWidget(self.capability_progress)

        matrix_tabs = QTabWidget()
        matrix_tabs.setObjectName("capabilityMatrixTabs")

        current_matrix_page = QWidget()
        current_matrix_layout = QVBoxLayout(current_matrix_page)
        current_matrix_layout.setContentsMargins(0, 0, 0, 0)
        current_matrix_layout.setSpacing(10)
        self.capability_table = QTableWidget(0, 4)
        self.capability_table.setObjectName("capabilityMatrixTable")
        self.capability_table.setHorizontalHeaderLabels(("Layer", "Capability", "State", "Result"))
        self.capability_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.capability_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.capability_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.capability_table.setAlternatingRowColors(True)
        self.capability_table.verticalHeader().setVisible(False)
        self.capability_table.itemSelectionChanged.connect(self._capability_selection_changed)
        header = self.capability_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        current_matrix_layout.addWidget(self.capability_table, 1)

        detail_group = QGroupBox("Selected capability evidence and next step")
        detail_layout = QVBoxLayout(detail_group)
        self.capability_detail = QTextBrowser()
        self.capability_detail.setObjectName("capabilityMatrixDetail")
        self.capability_detail.setOpenExternalLinks(True)
        self.capability_detail.setMaximumHeight(155)
        detail_layout.addWidget(self.capability_detail)
        current_matrix_layout.addWidget(detail_group)
        matrix_tabs.addTab(current_matrix_page, "Current Device")

        compatibility_page = QWidget()
        compatibility_layout = QVBoxLayout(compatibility_page)
        compatibility_layout.setContentsMargins(0, 0, 0, 0)
        compatibility_layout.setSpacing(10)
        compatibility_explanation = QLabel(
            "This is a local comparison of completed Capability Matrix probes from physically connected devices. "
            "It displays the latest result per device fingerprint and never predicts support for an untested model or build."
        )
        compatibility_explanation.setWordWrap(True)
        compatibility_layout.addWidget(compatibility_explanation)
        compatibility_controls = QHBoxLayout()
        refresh_history_button = QPushButton("Refresh History")
        refresh_history_button.setObjectName("refreshCompatibilityHistoryButton")
        refresh_history_button.clicked.connect(self.refresh_compatibility_history)
        compatibility_controls.addWidget(refresh_history_button)
        copy_history_button = QPushButton("Copy Compatibility Matrix")
        copy_history_button.setObjectName("copyCompatibilityMatrixButton")
        copy_history_button.clicked.connect(self.copy_compatibility_matrix)
        compatibility_controls.addWidget(copy_history_button)
        compatibility_controls.addStretch()
        compatibility_layout.addLayout(compatibility_controls)
        self.compatibility_history_status = QLabel()
        self.compatibility_history_status.setObjectName("compatibilityHistoryStatus")
        self.compatibility_history_status.setWordWrap(True)
        compatibility_layout.addWidget(self.compatibility_history_status)
        self.compatibility_history_table = QTableWidget(0, 0)
        self.compatibility_history_table.setObjectName("realDeviceCompatibilityTable")
        self.compatibility_history_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.compatibility_history_table.setAlternatingRowColors(True)
        self.compatibility_history_table.verticalHeader().setVisible(False)
        compatibility_layout.addWidget(self.compatibility_history_table, 1)
        matrix_tabs.addTab(compatibility_page, "Real-Device Compatibility")
        layout.addWidget(matrix_tabs, 1)

        privacy = QLabel(
            "The probes do not mount images, change settings, start captures, or write device data. Results describe "
            "service reachability at one moment; a Ready state is not a guarantee that every downstream command will work."
        )
        privacy.setObjectName("capabilityMatrixBoundary")
        privacy.setWordWrap(True)
        layout.addWidget(privacy)
        self._populate_capability_matrix()
        self._populate_compatibility_history()
        self._update_capability_controls()
        return page

    def _build_location_lab_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(12)

        heading = QLabel("Location Lab")
        heading.setObjectName("pageTitle")
        heading.setFont(QFont(heading.font().family(), 20, QFont.Weight.Bold))
        layout.addWidget(heading)
        explanation = QLabel(
            "Use a private offline map, imported map-link coordinates, saved places, or validated GPX routes with "
            "Apple developer-service location simulation. Nothing is sent to a mapping provider, and clicking the "
            "map changes only these fields until you explicitly confirm a device action."
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)

        status_group = QGroupBox("Toolkit-known state")
        status_layout = QGridLayout(status_group)
        self.location_state_value = QLabel(
            "No simulated location is tracked by this toolkit. External changes cannot be detected automatically."
        )
        self.location_state_value.setObjectName("locationStateValue")
        self.location_state_value.setWordWrap(True)
        status_layout.addWidget(QLabel("State"), 0, 0)
        status_layout.addWidget(self.location_state_value, 0, 1, 1, 3)
        self.location_target_value = QLabel("Selected device will be used")
        self.location_target_value.setObjectName("locationTargetValue")
        self.location_target_value.setWordWrap(True)
        status_layout.addWidget(QLabel("Target"), 1, 0)
        status_layout.addWidget(self.location_target_value, 1, 1, 1, 3)
        layout.addWidget(status_group)

        workflow = QSplitter(Qt.Orientation.Horizontal)
        workflow.setObjectName("locationWorkflowSplitter")

        coordinate_group = QGroupBox("Fixed location and saved places")
        coordinate_layout = QVBoxLayout(coordinate_group)
        map_heading = QLabel("Offline click-to-select map")
        map_heading.setObjectName("locationMapHeading")
        map_heading.setFont(QFont(map_heading.font().family(), 13, QFont.Weight.DemiBold))
        coordinate_layout.addWidget(map_heading)
        self.location_map = LocationMapWidget(Coordinates(latitude=34.0522, longitude=-118.2437))
        self.location_map.setObjectName("locationOfflineMap")
        self.location_map.coordinate_selected.connect(self._map_location_selected)
        coordinate_layout.addWidget(self.location_map)
        import_row = QHBoxLayout()
        self.location_input_field = QLineEdit()
        self.location_input_field.setObjectName("locationCoordinateImporter")
        self.location_input_field.setPlaceholderText("latitude,longitude or full Apple Maps / Google Maps / geo: link")
        self.location_input_field.returnPressed.connect(self.import_location_coordinates)
        import_row.addWidget(self.location_input_field, 1)
        self.import_location_button = QPushButton("Import")
        self.import_location_button.setObjectName("importLocationCoordinatesButton")
        self.import_location_button.clicked.connect(self.import_location_coordinates)
        import_row.addWidget(self.import_location_button)
        coordinate_layout.addLayout(import_row)
        map_note = QLabel(
            "Natural Earth map data is bundled locally. Text-only or shortened map links are not resolved over the network."
        )
        map_note.setObjectName("locationMapPrivacyNote")
        map_note.setWordWrap(True)
        coordinate_layout.addWidget(map_note)
        coordinate_form = QFormLayout()
        saved_row = QHBoxLayout()
        self.saved_location_combo = QComboBox()
        self.saved_location_combo.setObjectName("savedLocationPicker")
        self.saved_location_combo.currentIndexChanged.connect(self._saved_location_selected)
        saved_row.addWidget(self.saved_location_combo, 1)
        self.remove_saved_location_button = QPushButton("Remove")
        self.remove_saved_location_button.setObjectName("removeSavedLocationButton")
        self.remove_saved_location_button.clicked.connect(self.remove_selected_saved_location)
        saved_row.addWidget(self.remove_saved_location_button)
        coordinate_form.addRow("Saved place", saved_row)
        self.location_latitude_field = QLineEdit("34.0522")
        self.location_latitude_field.setObjectName("locationLatitude")
        self.location_latitude_field.setPlaceholderText("-90 to 90")
        self.location_latitude_field.editingFinished.connect(self.sync_location_map_from_fields)
        coordinate_form.addRow("Latitude", self.location_latitude_field)
        self.location_longitude_field = QLineEdit("-118.2437")
        self.location_longitude_field.setObjectName("locationLongitude")
        self.location_longitude_field.setPlaceholderText("-180 to 180")
        self.location_longitude_field.editingFinished.connect(self.sync_location_map_from_fields)
        coordinate_form.addRow("Longitude", self.location_longitude_field)
        coordinate_layout.addLayout(coordinate_form)
        coordinate_buttons = QHBoxLayout()
        self.save_location_button = QPushButton("Save Current…")
        self.save_location_button.setObjectName("saveLocationButton")
        self.save_location_button.clicked.connect(self.save_current_location)
        coordinate_buttons.addWidget(self.save_location_button)
        self.set_location_button = QPushButton("Set Simulated Location…")
        self.set_location_button.setObjectName("setSimulatedLocationButton")
        self.set_location_button.clicked.connect(self.set_simulated_location)
        coordinate_buttons.addWidget(self.set_location_button)
        coordinate_layout.addLayout(coordinate_buttons)
        nudge_grid = QGridLayout()
        self.location_nudge_distance = QSpinBox()
        self.location_nudge_distance.setObjectName("locationNudgeDistance")
        self.location_nudge_distance.setRange(1, 100000)
        self.location_nudge_distance.setValue(10)
        self.location_nudge_distance.setSuffix(" m")
        nudge_grid.addWidget(QLabel("Nudge coordinate fields"), 0, 0, 1, 2)
        nudge_grid.addWidget(self.location_nudge_distance, 0, 2, 1, 2)
        directions = (("N", 0.0), ("NE", 45.0), ("E", 90.0), ("SE", 135.0), ("S", 180.0), ("SW", 225.0), ("W", 270.0), ("NW", 315.0))
        for position, (label, bearing) in enumerate(directions):
            button = QPushButton(label)
            button.setObjectName(f"nudgeLocation{label}Button")
            button.setToolTip(f"Move the coordinate fields {label} without changing the device")
            button.clicked.connect(lambda checked=False, selected_bearing=bearing: self.nudge_location_fields(selected_bearing))
            nudge_grid.addWidget(button, 1 + position // 4, position % 4)
        coordinate_layout.addLayout(nudge_grid)
        saved_note = QLabel(
            f"Saved places stay local in {self._saved_locations_path}. They are never synchronized by this project."
        )
        saved_note.setWordWrap(True)
        coordinate_layout.addWidget(saved_note)
        coordinate_layout.addStretch()
        workflow.addWidget(coordinate_group)

        route_group = QGroupBox("GPX route playback")
        route_layout = QVBoxLayout(route_group)
        route_row = QHBoxLayout()
        self.location_gpx_field = QLineEdit()
        self.location_gpx_field.setObjectName("locationGPXPath")
        self.location_gpx_field.setReadOnly(True)
        self.location_gpx_field.setPlaceholderText("Choose a local GPX track")
        route_row.addWidget(self.location_gpx_field, 1)
        self.choose_location_gpx_button = QPushButton("Choose GPX…")
        self.choose_location_gpx_button.setObjectName("chooseLocationGPXButton")
        self.choose_location_gpx_button.clicked.connect(self.choose_location_gpx)
        route_row.addWidget(self.choose_location_gpx_button)
        route_layout.addLayout(route_row)
        self.location_gpx_summary = QLabel(
            "The toolkit requires GPX track points, validates every coordinate, and records the file's SHA-256."
        )
        self.location_gpx_summary.setObjectName("locationGPXSummary")
        self.location_gpx_summary.setWordWrap(True)
        route_layout.addWidget(self.location_gpx_summary)
        route_options = QFormLayout()
        self.location_timing_randomness = QSpinBox()
        self.location_timing_randomness.setObjectName("locationTimingRandomness")
        self.location_timing_randomness.setRange(0, 60000)
        self.location_timing_randomness.setValue(0)
        self.location_timing_randomness.setSuffix(" ms")
        route_options.addRow("Timing randomness", self.location_timing_randomness)
        self.location_disable_sleep = QCheckBox("Ignore GPX timing delays")
        self.location_disable_sleep.setObjectName("locationDisableSleep")
        route_options.addRow("Fast playback", self.location_disable_sleep)
        route_layout.addLayout(route_options)
        self.play_location_gpx_button = QPushButton("Play Validated GPX…")
        self.play_location_gpx_button.setObjectName("playLocationGPXButton")
        self.play_location_gpx_button.clicked.connect(self.play_location_gpx)
        route_layout.addWidget(self.play_location_gpx_button)
        route_layout.addStretch()
        workflow.addWidget(route_group)
        workflow.setSizes([430, 430])
        workflow.setStretchFactor(0, 1)
        workflow.setStretchFactor(1, 1)
        layout.addWidget(workflow)

        builder_group = QGroupBox("Local QA route builder")
        builder_layout = QHBoxLayout(builder_group)
        self.location_route_waypoints = QPlainTextEdit()
        self.location_route_waypoints.setObjectName("locationRouteWaypoints")
        self.location_route_waypoints.setPlaceholderText(
            "One latitude,longitude waypoint per line\n34.052200,-118.243700\n34.053000,-118.242000"
        )
        self.location_route_waypoints.setMaximumHeight(110)
        builder_layout.addWidget(self.location_route_waypoints, 2)
        builder_options = QFormLayout()
        self.location_route_speed_preset = QComboBox()
        self.location_route_speed_preset.setObjectName("locationRouteSpeedPreset")
        for name, value in (("Walk", 5), ("Run", 10), ("Bicycle", 20), ("Urban drive", 40), ("Highway", 100)):
            self.location_route_speed_preset.addItem(f"{name} — {value} km/h", value)
        self.location_route_speed_preset.currentIndexChanged.connect(self.apply_location_speed_preset)
        builder_options.addRow("Speed preset", self.location_route_speed_preset)
        self.location_route_speed = QSpinBox()
        self.location_route_speed.setObjectName("locationRouteSpeed")
        self.location_route_speed.setRange(1, 300)
        self.location_route_speed.setValue(5)
        self.location_route_speed.setSuffix(" km/h")
        builder_options.addRow("Speed", self.location_route_speed)
        self.location_route_interval = QSpinBox()
        self.location_route_interval.setObjectName("locationRouteInterval")
        self.location_route_interval.setRange(1, 60)
        self.location_route_interval.setValue(1)
        self.location_route_interval.setSuffix(" s")
        builder_options.addRow("Point interval", self.location_route_interval)
        self.location_route_traversals = QSpinBox()
        self.location_route_traversals.setObjectName("locationRouteTraversals")
        self.location_route_traversals.setRange(1, 20)
        self.location_route_traversals.setValue(1)
        self.location_route_traversals.setToolTip("Additional traversals alternate direction instead of teleporting to the start")
        builder_options.addRow("Traversals", self.location_route_traversals)
        builder_layout.addLayout(builder_options, 1)
        builder_buttons = QVBoxLayout()
        add_waypoint = QPushButton("Add Current Coordinate")
        add_waypoint.setObjectName("addCurrentRouteWaypointButton")
        add_waypoint.clicked.connect(self.add_current_route_waypoint)
        builder_buttons.addWidget(add_waypoint)
        build_button = QPushButton("Build, Validate && Load GPX")
        build_button.setObjectName("buildLocationRouteButton")
        build_button.clicked.connect(self.build_location_route)
        builder_buttons.addWidget(build_button)
        self.location_route_summary = QLabel("Generated routes stay local and use timestamped GPX points.")
        self.location_route_summary.setObjectName("locationRouteSummary")
        self.location_route_summary.setWordWrap(True)
        builder_buttons.addWidget(self.location_route_summary)
        builder_layout.addLayout(builder_buttons, 2)
        layout.addWidget(builder_group)

        evidence_group = QGroupBox("Cleanup and evidence log")
        evidence_layout = QGridLayout(evidence_group)
        self.location_log_directory_field = QLineEdit(
            str(Path.home() / "Documents" / "iOS Developer Toolkit Location Logs")
        )
        self.location_log_directory_field.setObjectName("locationLogDirectory")
        evidence_layout.addWidget(QLabel("Log directory"), 0, 0)
        evidence_layout.addWidget(self.location_log_directory_field, 0, 1)
        choose_log = QPushButton("Choose…")
        choose_log.setObjectName("chooseLocationLogDirectoryButton")
        choose_log.clicked.connect(self.choose_location_log_directory)
        evidence_layout.addWidget(choose_log, 0, 2)
        self.open_location_log_button = QPushButton("Open Log Folder")
        self.open_location_log_button.setObjectName("openLocationLogButton")
        self.open_location_log_button.clicked.connect(self.open_location_log_directory)
        evidence_layout.addWidget(self.open_location_log_button, 0, 3)
        self.stop_clear_location_button = QPushButton("Stop Playback / Set && Clear")
        self.stop_clear_location_button.setObjectName("stopAndClearLocationButton")
        self.stop_clear_location_button.clicked.connect(self.stop_location_and_clear)
        evidence_layout.addWidget(self.stop_clear_location_button, 1, 1)
        self.clear_location_button = QPushButton("Clear Simulated Location…")
        self.clear_location_button.setObjectName("clearSimulatedLocationButton")
        self.clear_location_button.clicked.connect(self.clear_simulated_location)
        evidence_layout.addWidget(self.clear_location_button, 1, 2)
        open_help = QPushButton("Open Live Location Help")
        open_help.setObjectName("openLocationHelpButton")
        open_help.clicked.connect(self.open_location_help)
        evidence_layout.addWidget(open_help, 1, 3)
        layout.addWidget(evidence_group)

        privacy = QLabel(
            "Location simulation changes device state and may affect participating apps. Coordinates, the device UDID, "
            "GPX path/hash, commands, timestamps, and results are sensitive and are appended to location-events.jsonl. "
            "The status above reflects only actions started here; always clear the simulation when testing ends."
        )
        privacy.setObjectName("locationPrivacyWarning")
        privacy.setWordWrap(True)
        layout.addWidget(privacy)

        self.location_output = QPlainTextEdit()
        self.location_output.setObjectName("locationOutput")
        self.location_output.setReadOnly(True)
        self.location_output.setMaximumBlockCount(4000)
        self.location_output.setPlaceholderText("Location commands, service output, and evidence-log status appear here.")
        layout.addWidget(self.location_output, 1)
        self._populate_saved_locations()
        self._update_location_controls()
        page.setMinimumHeight(1050)
        scroll_area = QScrollArea()
        scroll_area.setObjectName("locationLabScrollArea")
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        scroll_area.setWidget(page)
        return scroll_area

    def _build_live_logs_page(self) -> QWidget:
        return build_live_logs_page(
            log_stream_specs(),
            self.open_live_log_window,
            self._open_log_presets,
            self.navigate_to_page,
        )

    def _build_collection_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(14)

        intake_group = QGroupBox("1. Guided case intake")
        intake_layout = QFormLayout(intake_group)
        self.case_title_field = QLineEdit()
        self.case_title_field.setObjectName("caseTitle")
        self.case_title_field.setPlaceholderText("Example: Pre-release device validation")
        intake_layout.addRow("Case title", self.case_title_field)
        self.case_purpose_field = QPlainTextEdit()
        self.case_purpose_field.setObjectName("casePurpose")
        self.case_purpose_field.setPlaceholderText("Optional local note about the authorized purpose and scope.")
        self.case_purpose_field.setMaximumHeight(72)
        intake_layout.addRow("Purpose / scope", self.case_purpose_field)
        self.case_authorization_checkbox = QCheckBox("I own this device or am authorized to examine it.")
        self.case_authorization_checkbox.setObjectName("caseAuthorizationAcknowledgement")
        intake_layout.addRow("Authorization", self.case_authorization_checkbox)
        case_actions = QHBoxLayout()
        self.case_readiness_button = QPushButton("Run Device Readiness Check")
        self.case_readiness_button.setObjectName("guidedCaseReadinessButton")
        self.case_readiness_button.clicked.connect(self.run_guided_case_readiness_check)
        self.case_readiness_button.setToolTip(
            "Run the bounded, read-only Capability Matrix for the selected device before creating or collecting a case."
        )
        case_actions.addWidget(self.case_readiness_button)
        self.create_case_button = QPushButton("Create Guided Case")
        self.create_case_button.setObjectName("createGuidedCaseButton")
        self.create_case_button.clicked.connect(self.create_guided_case)
        case_actions.addWidget(self.create_case_button)
        self.case_status = QLabel("No active case. Create one before collection to retain intake and scope metadata.")
        self.case_status.setObjectName("guidedCaseStatus")
        self.case_status.setWordWrap(True)
        case_actions.addWidget(self.case_status, 1)
        intake_layout.addRow(case_actions)
        layout.addWidget(intake_group)

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

        quick_start_group = QGroupBox("How to install and run UFADE")
        quick_start_layout = QVBoxLayout(quick_start_group)
        quick_start = QLabel(
            "1. Copy and run the macOS setup commands in Terminal.  2. Choose the cloned checkout, its .venv Python, "
            "and a protected output folder.  3. Validate the installation.  4. Connect, unlock, and trust one intended "
            "device.  5. Launch UFADE, choose the acquisition inside its window, and use UFADE's own progress and stop controls."
        )
        quick_start.setObjectName("ufadeQuickStart")
        quick_start.setWordWrap(True)
        quick_start_layout.addWidget(quick_start)
        guide_controls = QHBoxLayout()
        guide_button = QPushButton("Open Full Walkthrough")
        guide_button.setObjectName("openUFADEGuideButton")
        guide_button.clicked.connect(self.show_ufade_guide)
        guide_controls.addWidget(guide_button)
        setup_button = QPushButton("Copy Setup Commands")
        setup_button.setObjectName("copyUFADESetupButton")
        setup_button.clicked.connect(self.copy_ufade_setup_commands)
        guide_controls.addWidget(setup_button)
        official_guide_button = QPushButton("Open Official Guide")
        official_guide_button.setObjectName("openUFADEOfficialGuideButton")
        official_guide_button.clicked.connect(self.open_ufade_installation_guide)
        guide_controls.addWidget(official_guide_button)
        guide_controls.addStretch()
        quick_start_layout.addLayout(guide_controls)
        layout.addWidget(quick_start_group)

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
        use_checkout_python = QPushButton("Use Checkout .venv")
        use_checkout_python.setObjectName("useUFADECheckoutVenvButton")
        use_checkout_python.clicked.connect(self.use_checkout_ufade_python)
        python_row.addWidget(use_checkout_python)
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
        copy_launch_button = QPushButton("Copy Manual Launch")
        copy_launch_button.setObjectName("copyUFADEManualLaunchButton")
        copy_launch_button.clicked.connect(self.copy_ufade_manual_launch_command)
        controls.addWidget(copy_launch_button)
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
        self.ufade_output.setMinimumHeight(150)
        layout.addWidget(self.ufade_output, 1)
        self._update_backup_controls()
        scroll = QScrollArea()
        scroll.setObjectName("ufadeBackupScrollArea")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(page)
        return scroll

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
        browser_splitter.setMaximumHeight(470)

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

        readiness_group = QGroupBox("Selected command readiness")
        readiness_layout = QHBoxLayout(readiness_group)
        self.command_readiness_status = QLabel("Choose a preset to evaluate its device requirements.")
        self.command_readiness_status.setObjectName("commandReadinessStatus")
        self.command_readiness_status.setWordWrap(True)
        self.command_readiness_status.setAccessibleName("Selected command readiness")
        readiness_layout.addWidget(self.command_readiness_status, 1)
        self.command_readiness_button = QPushButton("Run Device Readiness Check")
        self.command_readiness_button.setObjectName("runCommandReadinessButton")
        self.command_readiness_button.setAccessibleDescription(
            "Runs the bounded read-only Capability Matrix for the selected physical device."
        )
        self.command_readiness_button.clicked.connect(self.run_selected_command_readiness_check)
        readiness_layout.addWidget(self.command_readiness_button)
        detail_layout.addWidget(readiness_group)

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

        drift_group = QGroupBox("Command-drift detection")
        drift_layout = QVBoxLayout(drift_group)
        drift_explanation = QLabel(
            "Read-only: checks each guided preset's installed pymobiledevice3 --help route and expected option flags. "
            "It never runs a preset or contacts a connected device."
        )
        drift_explanation.setWordWrap(True)
        drift_layout.addWidget(drift_explanation)
        drift_actions = QHBoxLayout()
        self.command_drift_check_button = QPushButton("Check Guided Command Drift")
        self.command_drift_check_button.setObjectName("checkCommandDriftButton")
        self.command_drift_check_button.clicked.connect(self.start_command_drift_check)
        drift_actions.addWidget(self.command_drift_check_button)
        self.command_drift_cancel_button = QPushButton("Cancel Drift Check")
        self.command_drift_cancel_button.setObjectName("cancelCommandDriftButton")
        self.command_drift_cancel_button.clicked.connect(self.cancel_command_drift_check)
        drift_actions.addWidget(self.command_drift_cancel_button)
        self.command_drift_copy_button = QPushButton("Copy Drift Report")
        self.command_drift_copy_button.setObjectName("copyCommandDriftReportButton")
        self.command_drift_copy_button.clicked.connect(self.copy_command_drift_report)
        drift_actions.addWidget(self.command_drift_copy_button)
        drift_actions.addStretch()
        drift_layout.addLayout(drift_actions)
        self.command_drift_status = QLabel("Not checked in this app session.")
        self.command_drift_status.setObjectName("commandDriftStatus")
        self.command_drift_status.setWordWrap(True)
        drift_layout.addWidget(self.command_drift_status)
        self.command_drift_output = QPlainTextEdit()
        self.command_drift_output.setObjectName("commandDriftOutput")
        self.command_drift_output.setReadOnly(True)
        self.command_drift_output.setMaximumBlockCount(250)
        self.command_drift_output.setMaximumHeight(130)
        self.command_drift_output.setPlaceholderText("The drift report will identify unavailable help routes or missing expected option flags.")
        drift_layout.addWidget(self.command_drift_output)
        layout.addWidget(drift_group)

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
        self.advanced_safety_note = QLabel(
            "Advanced commands are classified before execution. State-changing commands require a typed acknowledgement."
        )
        self.advanced_safety_note.setObjectName("advancedCommandSafetyNote")
        self.advanced_safety_note.setWordWrap(True)
        layout.addWidget(self.advanced_safety_note)

        self.console_output = QPlainTextEdit()
        self.console_output.setObjectName("consoleOutput")
        self.console_output.setReadOnly(True)
        self.console_output.setMaximumBlockCount(12000)
        self.console_output.setPlaceholderText("Command output appears here. Long-running streams continue until Stop is pressed.")
        layout.addWidget(self.console_output, 1)
        self._filter_command_presets()
        self._update_command_controls()
        self._update_command_drift_controls()
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
            "Browse the command map instantly, then request version-matched help from the installed pymobiledevice3 "
            "when needed. Live help can be cancelled and stops automatically after 15 seconds."
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
        self.cancel_manpage_button = QPushButton("Cancel Loading")
        self.cancel_manpage_button.setObjectName("cancelManpageButton")
        self.cancel_manpage_button.clicked.connect(self.cancel_manpage_load)
        manpage_actions.addWidget(self.cancel_manpage_button)
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
        return build_safety_page(DEVELOPER_DISK_IMAGE_REPOSITORY)

    def _apply_style(self) -> None:
        self.setStyleSheet(toolkit_stylesheet())

    def selected_device(self) -> IOSDevice | None:
        if self._demo_mode:
            return None
        index = self.device_combo.currentIndex()
        if index < 0 or index >= len(self._devices):
            return None
        return self._devices[index]

    def _scanner_scan(self) -> None:
        if self._demo_mode:
            return
        self._scanner.scan()

    def _reconnect_device(self) -> None:
        if self._demo_mode:
            return
        if self._reconnect_active:
            return
        confirmed = self._confirm(
            "Start a 30-second reconnect window?",
            "1. Unlock the iPhone or iPad and keep it on the Home Screen.\n"
            "2. In iOS Settings → Privacy & Security → Wired Accessories, allow the connection while unlocked.\n"
            "3. Disconnect and reconnect it directly to the Mac with a known data-capable cable. Avoid a hub.\n"
            "4. Click Allow if macOS asks to connect the accessory. Then tap Trust on the device and enter its "
            "passcode if prompted. You can also select the device in the Finder sidebar and click Trust.\n\n"
            "The toolkit will retry usbmux discovery for 30 seconds. It will not use sudo, delete pairing records, "
            "restart SIP-protected Apple agents, restart the root-owned usbmuxd service, or change the iOS device.\n\n"
            "Start retrying?",
        )
        if not confirmed:
            return
        self._reconnect_active = True
        self.reconnect_device_button.setEnabled(False)
        self.connection_banner.setText(
            "Reconnect window active. Unlock the device, reconnect its data cable directly, and tap Trust if asked; "
            "retrying usbmux discovery for up to 30 seconds…"
        )
        self._reconnect_timeout_timer.start(RECONNECT_TIMEOUT_MS)
        self._scanner.start()

    def _finish_reconnect_success(self, device_count: int) -> None:
        self._reconnect_timeout_timer.stop()
        self._reconnect_active = False
        self.refresh_devices_button.setEnabled(True)
        self.reconnect_device_button.setEnabled(True)
        self.connection_banner.setText(
            f"Reconnected: usbmux detected {device_count} trusted iOS device(s). "
            "Keep the selected device unlocked while starting developer streams."
        )

    def _reconnect_timed_out(self) -> None:
        if not self._reconnect_active:
            return
        self._reconnect_active = False
        self.refresh_devices_button.setEnabled(True)
        self.reconnect_device_button.setEnabled(True)
        self.connection_banner.setText(
            "usbmux found no device during the 30-second reconnect window. Unlock the phone, try another "
            "data-capable cable or Mac port, reconnect directly without a hub, and use Finder to complete Trust. "
            "Retry Scan afterward. The toolkit intentionally did not restart SIP-protected Apple agents or the "
            "root-owned usbmuxd service."
        )

    def _devices_changed(self, devices_object: object) -> None:
        if not isinstance(devices_object, tuple) or not all(isinstance(item, IOSDevice) for item in devices_object):
            self.connection_banner.setText("Device scanner returned an unexpected result type.")
            return
        devices = tuple(devices_object)
        previous_identifier = self.selected_device().identifier if self.selected_device() is not None else None
        changed = devices != self._devices
        self._devices = devices
        if self._demo_mode:
            return
        if changed:
            self._render_device_picker(devices, previous_identifier)
        if not devices:
            if self._reconnect_active:
                self.connection_banner.setText(
                    "Reconnect window active. Waiting for usbmux to see an unlocked device; reconnect the "
                    "data cable and tap Trust if prompted…"
                )
            else:
                self.connection_banner.setText(
                    "No usbmux device detected. Unlock the iPhone or iPad, reconnect a data-capable cable, and tap "
                    "Trust if prompted. Use Reconnect & Retry for a guided 30-second detection window."
                )
            self._update_device_fields(None)
            return
        if self._reconnect_active:
            self._finish_reconnect_success(len(devices))
        else:
            self.connection_banner.setText(
                f"Detected {len(devices)} trusted iOS device(s). Select the intended target before mounting or collecting."
            )
        self._update_device_fields(self.selected_device())
        selected = self.selected_device()
        if selected is not None and selected.identifier not in self._guided_udids:
            self._guided_udids.add(selected.identifier)
            QTimer.singleShot(350, self.show_developer_mode_guide)

    def _scan_error(self, message: str) -> None:
        if self._demo_mode:
            return
        if self._reconnect_active:
            self.connection_banner.setText(
                f"Reconnect is still retrying after a usbmux discovery error: {message} Keep the device unlocked "
                "and reconnect its data cable."
            )
            return
        self.connection_banner.setText(
            f"Device discovery error: {message} Use Retry Scan first. If it repeats, use Reconnect & Retry and "
            "complete the cable, unlock, and Finder Trust checks."
        )

    def _connection_diagnostic_changed(self, diagnostic_object: object) -> None:
        if not isinstance(diagnostic_object, ConnectionDiagnostic):
            self._connection_diagnostic = malformed_output_connection_diagnostic()
        else:
            self._connection_diagnostic = diagnostic_object
        self.connection_diagnostic_value.setText(self._connection_diagnostic.report())

    def _device_selected(self, index: int) -> None:
        del index
        self._update_device_fields(self._displayed_device())
        self.developer_mode_status.setText("Status not checked for this device")
        self._update_location_controls()

    def toggle_demo_mode(self) -> None:
        if self._demo_mode:
            self._exit_demo_mode()
            return
        self._enter_demo_mode()

    def _enter_demo_mode(self) -> None:
        if self.selected_device() is not None:
            QMessageBox.information(
                self,
                "Demo Mode Requires No Selected Device",
                "Disconnect the physical device before starting the simulated walkthrough. "
                "Demo Mode never replaces a real connected device.",
            )
            return
        self._demo_mode = True
        self._reconnect_timeout_timer.stop()
        self._reconnect_active = False
        self.device_combo.blockSignals(True)
        self.device_combo.clear()
        self.device_combo.addItem(self._demo_device.display_name(), self._demo_device.identifier)
        self.device_combo.setCurrentIndex(0)
        self.device_combo.blockSignals(False)
        self.device_combo.setEnabled(False)
        self.demo_mode_button.setText("Exit Demo")
        self.refresh_devices_button.setEnabled(False)
        self.reconnect_device_button.setEnabled(False)
        self.connection_banner.setText(demo_connection_banner())
        self._update_device_fields(self._demo_device)
        self.developer_mode_status.setText("Demo Mode: not checked; no device service was contacted")

    def _exit_demo_mode(self) -> None:
        self._demo_mode = False
        self.device_combo.setEnabled(True)
        self.demo_mode_button.setText("Demo Mode")
        self.refresh_devices_button.setEnabled(True)
        self.reconnect_device_button.setEnabled(True)
        self._render_device_picker(self._devices, None)
        self._devices_changed(self._devices)
        self._scanner.scan()

    def _render_device_picker(
        self,
        devices: tuple[IOSDevice, ...],
        previous_identifier: str | None,
    ) -> None:
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

    def _displayed_device(self) -> IOSDevice | None:
        if self._demo_mode:
            return self._demo_device
        return self.selected_device()

    def _update_device_fields(self, device: IOSDevice | None) -> None:
        identifier = device.identifier if device is not None else None
        if identifier != self._active_device_identifier:
            if self._active_case_path is not None:
                previous_case_path = self._active_case_path
                self._active_case_path = None
                self.case_status.setText(
                    f"Selected device changed. Guided case remains at {previous_case_path}; create a case for the new device."
                )
            if self._capability_process is not None:
                self._discard_capability_process_for_device_change()
            self._active_device_identifier = identifier
            self._backup_encryption_state = None
            self._reset_capability_matrix(device)
            self.backup_encryption_status.setText("Encryption state not checked for this device")
            self._installed_apps = ()
            self._populate_installed_apps(())
            self.apps_status.setText(
                "Refresh to load apps from the selected device."
                if device is not None
                else "Connect a trusted device, then refresh the inventory."
            )
        enabled = device is not None and not self._demo_mode
        action_available = enabled and not self._action_controller.is_running()
        self.mount_button.setEnabled(action_available)
        self.remove_button.setEnabled(action_available)
        self.start_collection_button.setEnabled(enabled and self._collection_process is None)
        self.create_case_button.setEnabled(enabled and self._collection_process is None and self._active_case_path is None)
        self.case_readiness_button.setEnabled(enabled and self._capability_process is None)
        self._update_live_log_controls()
        self._update_apps_controls()
        self._update_backup_controls()
        self._update_command_controls()
        self._update_location_controls()
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

    def _update_live_log_controls(self) -> None:
        for specification in log_stream_specs():
            identifier = f"open{specification.identifier.replace('-', '').title()}LogButton"
            button = self.findChild(QPushButton, identifier)
            if button is None:
                raise RuntimeError(f"Live log action is missing: {identifier}")
            button.setEnabled(not self._demo_mode)

    def _reset_capability_matrix(self, device: IOSDevice | None) -> None:
        self._capability_results = {result.identifier: result for result in untested_capability_results()}
        self._capability_completed_at = None
        self._capability_worker_completed = False
        self.capability_progress.setRange(0, len(capability_definitions()))
        self.capability_progress.setValue(0)
        self.capability_status.setText(
            "Run the matrix to test the selected device. No probe runs automatically."
            if device is not None
            else "Connect and select a trusted device before running the matrix."
        )
        self._populate_capability_matrix()
        self._update_capability_controls()
        self._update_selected_command_readiness()

    def _capability_state_brush(self, state: CapabilityState) -> QBrush:
        colors: Mapping[CapabilityState, str] = {
            "ready": "#dff3e4",
            "attention": "#fff0c7",
            "unavailable": "#ffdeda",
            "blocked": "#eceff4",
            "not-tested": "#f2f3f6",
            "not-applicable": "#e8eef7",
        }
        color = colors.get(state)
        if color is None:
            raise CapabilityMatrixError(f"No matrix color is defined for capability state: {state}")
        return QBrush(QColor(color))

    def _populate_capability_matrix(self) -> None:
        selected_identifier: str | None = None
        selected_rows = self.capability_table.selectionModel().selectedRows()
        if len(selected_rows) == 1:
            selected_item = self.capability_table.item(selected_rows[0].row(), 0)
            if selected_item is not None:
                candidate = selected_item.data(Qt.ItemDataRole.UserRole)
                if isinstance(candidate, str):
                    selected_identifier = candidate
        definitions = capability_definitions()
        self.capability_table.setRowCount(len(definitions))
        selected_row = 0
        for row, definition in enumerate(definitions):
            result = self._capability_results[definition.identifier]
            values = (
                result.layer,
                result.title,
                capability_state_label(result.state),
                result.summary,
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, result.identifier)
                if column == 2:
                    item.setBackground(self._capability_state_brush(result.state))
                    item.setFont(QFont(item.font().family(), item.font().pointSize(), QFont.Weight.DemiBold))
                self.capability_table.setItem(row, column, item)
            if result.identifier == selected_identifier:
                selected_row = row
        if definitions:
            self.capability_table.selectRow(selected_row)
        self._capability_selection_changed()

    def _populate_compatibility_history(self) -> None:
        if self._compatibility_history_error is not None:
            self.compatibility_history_status.setText(
                f"Compatibility history is unavailable: {self._compatibility_history_error}"
            )
            self.compatibility_history_table.setRowCount(0)
            self.compatibility_history_table.setColumnCount(0)
            return
        observations = latest_observations(self._compatibility_observations)
        displayed_observations = observations[-8:]
        self.compatibility_history_status.setText(
            f"{len(observations)} locally observed physical device(s); showing the latest {len(displayed_observations)}. "
            f"Raw UDIDs are not retained. History: {self._compatibility_history_path}"
        )
        definitions = capability_definitions()
        self.compatibility_history_table.setRowCount(len(definitions))
        self.compatibility_history_table.setColumnCount(len(displayed_observations) + 1)
        headers = ["Capability"]
        for observation in displayed_observations:
            headers.append(
                f"{observation.product_type}\niOS {observation.product_version} ({observation.build_version})\n"
                f"{observation.connection_type} • {observation.device_fingerprint}"
            )
        self.compatibility_history_table.setHorizontalHeaderLabels(headers)
        for row, definition in enumerate(definitions):
            title_item = QTableWidgetItem(definition.title)
            title_item.setData(Qt.ItemDataRole.UserRole, definition.identifier)
            self.compatibility_history_table.setItem(row, 0, title_item)
            for column, observation in enumerate(displayed_observations, start=1):
                results = {result.identifier: result for result in observation.results}
                result = results.get(definition.identifier)
                if result is None:
                    item = QTableWidgetItem("Not observed")
                    item.setBackground(self._capability_state_brush("not-tested"))
                else:
                    item = QTableWidgetItem(capability_state_label(result.state))
                    item.setToolTip(
                        f"Observed: {observation.recorded_at}\n\nResult: {result.summary}\n\nEvidence: {result.evidence}"
                    )
                    item.setBackground(self._capability_state_brush(result.state))
                self.compatibility_history_table.setItem(row, column, item)
        header = self.compatibility_history_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        for column in range(1, len(displayed_observations) + 1):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)

    def refresh_compatibility_history(self) -> None:
        try:
            self._compatibility_observations = load_observations(self._compatibility_history_path)
        except DeviceCompatibilityError as error:
            self._compatibility_history_error = str(error)
        else:
            self._compatibility_history_error = None
        self._populate_compatibility_history()

    def _record_compatibility_observation(self, device: IOSDevice) -> None:
        try:
            observation = create_observation(
                self._capability_completed_at or datetime.now(timezone.utc).isoformat(),
                device,
                tuple(self._capability_results.values()),
            )
            append_observation(self._compatibility_history_path, observation)
        except DeviceCompatibilityError as error:
            self._compatibility_history_error = str(error)
            self._populate_compatibility_history()
            return
        self._compatibility_history_error = None
        self._compatibility_observations = (*self._compatibility_observations, observation)
        self._populate_compatibility_history()

    def copy_compatibility_matrix(self) -> None:
        if self._compatibility_history_error is not None:
            QMessageBox.warning(self, "Compatibility History Unavailable", self._compatibility_history_error)
            return
        observations = latest_observations(self._compatibility_observations)
        if not observations:
            QMessageBox.information(
                self,
                "No Real-Device Observations",
                "Complete a Capability Matrix run against a connected device before copying compatibility results.",
            )
            return
        lines = [
            "iOS Developer Toolkit — Real-Device Compatibility Matrix",
            "Only completed local Capability Matrix observations are included. Raw UDIDs are not retained.",
            "",
        ]
        for observation in observations:
            lines.append(
                f"{observation.product_type}; iOS {observation.product_version}; build {observation.build_version}; "
                f"{observation.connection_type}; device fingerprint {observation.device_fingerprint}; observed {observation.recorded_at}"
            )
            for result in observation.results:
                lines.append(f"  [{capability_state_label(result.state)}] {result.title}: {result.summary}")
            lines.append("")
        QApplication.clipboard().setText("\n".join(lines).rstrip() + "\n")
        self.compatibility_history_status.setText("Copied local real-device compatibility observations to the clipboard.")

    def _capability_selection_changed(self) -> None:
        selected_rows = self.capability_table.selectionModel().selectedRows()
        if len(selected_rows) != 1:
            self.capability_detail.setPlainText("Select a capability to view its evidence and next step.")
            return
        item = self.capability_table.item(selected_rows[0].row(), 0)
        identifier = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if not isinstance(identifier, str) or identifier not in self._capability_results:
            raise CapabilityMatrixError("Capability matrix selection does not identify a catalog result")
        result = self._capability_results[identifier]
        self.capability_detail.setPlainText(
            f"{result.title} — {capability_state_label(result.state)}\n\n"
            f"Evidence\n{result.evidence}\n\n"
            f"Next step\n{result.remediation}"
        )

    def refresh_capability_matrix(self) -> None:
        device = self.selected_device()
        if device is None:
            self._show_no_device()
            return
        if self._capability_process is not None:
            QMessageBox.warning(self, "Capability Refresh Running", "Cancel or wait for the current matrix refresh.")
            return
        self._capability_results = {result.identifier: result for result in untested_capability_results()}
        self._capability_completed_at = None
        self._capability_cancel_reason = None
        self._capability_worker_completed = False
        self._capability_stdout_buffer.clear()
        self._capability_stderr.clear()
        self.capability_progress.setRange(0, len(capability_definitions()))
        self.capability_progress.setValue(0)
        self.capability_status.setText(
            f"Testing {device.display_name()} with bounded, read-only service probes…"
        )
        self._populate_capability_matrix()
        worker = worker_command("capability")
        process = QProcess(self)
        process.setProgram(str(worker.program))
        process.setArguments(
            list(command_arguments(worker, (
                "--identifier",
                device.identifier,
                "--name",
                device.name,
                "--product-type",
                device.product_type,
                "--product-version",
                device.product_version,
                "--build-version",
                device.build_version,
                "--connection-type",
                device.connection_type,
            )))
        )
        process.setProcessEnvironment(qprocess_environment(base_environment()))
        process.readyReadStandardOutput.connect(self._read_capability_stdout)
        process.readyReadStandardError.connect(self._read_capability_stderr)
        process.finished.connect(self._capability_finished)
        process.errorOccurred.connect(self._capability_error)
        self._capability_process = process
        self.case_readiness_button.setEnabled(False)
        self._update_capability_controls()
        process.start()

    def _read_capability_stdout(self) -> None:
        process = self._capability_process
        if process is None:
            return
        self._capability_stdout_buffer.extend(bytes(process.readAllStandardOutput()))
        while b"\n" in self._capability_stdout_buffer:
            line, remainder = self._capability_stdout_buffer.split(b"\n", 1)
            self._capability_stdout_buffer = bytearray(remainder)
            if line.strip():
                self._handle_capability_event(line.decode("utf-8"))

    def _read_capability_stderr(self) -> None:
        if self._capability_process is not None:
            self._capability_stderr.extend(bytes(self._capability_process.readAllStandardError()))

    def _handle_capability_event(self, payload: str) -> None:
        event = parse_capability_worker_event(payload)
        if isinstance(event, CapabilityWorkerStarted):
            if event.total != len(capability_definitions()):
                raise CapabilityMatrixError(
                    f"Capability worker expected {event.total} results, but the UI catalog has {len(capability_definitions())}"
                )
            self.capability_progress.setRange(0, event.total)
            return
        if isinstance(event, CapabilityResult):
            self._capability_results[event.identifier] = event
            completed = sum(result.state != "not-tested" for result in self._capability_results.values())
            self.capability_progress.setValue(completed)
            self.capability_status.setText(
                f"Completed {completed} of {len(capability_definitions())}: {event.title} — "
                f"{capability_state_label(event.state)}"
            )
            self._populate_capability_matrix()
            self._update_selected_command_readiness()
            return
        if isinstance(event, CapabilityWorkerCompleted):
            self._capability_worker_completed = True
            return
        raise CapabilityMatrixError(f"Unsupported capability event type: {type(event).__name__}")

    def _capability_finished(self, exit_code: int, exit_status: QProcess.ExitStatus) -> None:
        del exit_status
        self._read_capability_stdout()
        self._read_capability_stderr()
        if self._capability_stdout_buffer.strip():
            self._handle_capability_event(self._capability_stdout_buffer.decode("utf-8"))
            self._capability_stdout_buffer.clear()
        tested = sum(result.state != "not-tested" for result in self._capability_results.values())
        if self._capability_cancel_reason is not None:
            self.capability_status.setText(f"{self._capability_cancel_reason} Preserved {tested} completed results.")
        elif exit_code == 0 and self._capability_worker_completed:
            self._capability_completed_at = datetime.now(timezone.utc).isoformat()
            ready = sum(result.state == "ready" for result in self._capability_results.values())
            attention = sum(result.state in ("attention", "unavailable", "blocked") for result in self._capability_results.values())
            self.capability_status.setText(
                f"Capability refresh completed: {ready} ready, {attention} requiring attention, "
                f"{len(self._capability_results) - ready - attention} informational."
            )
            device = self.selected_device()
            if device is None:
                raise CapabilityMatrixError("Capability worker completed without a selected device")
            self._record_compatibility_observation(device)
        else:
            stderr = self._capability_stderr.decode("utf-8", errors="replace").strip()
            detail = stderr[-800:] if stderr else "The worker exited without a diagnostic message."
            self.capability_status.setText(f"Capability refresh failed with exit code {exit_code}: {detail}")
        self._capability_process = None
        self._capability_cancel_reason = None
        self.case_readiness_button.setEnabled(self.selected_device() is not None)
        if self._active_case_path is not None:
            ready = sum(result.state == "ready" for result in self._capability_results.values())
            attention = sum(
                result.state in ("attention", "unavailable", "blocked")
                for result in self._capability_results.values()
            )
            self.case_status.setText(
                f"Readiness check completed: {ready} ready, {attention} requiring attention. Review Capability Matrix before collection."
            )
        self._populate_capability_matrix()
        self._update_capability_controls()
        self._update_selected_command_readiness()

    def _capability_error(self, process_error: QProcess.ProcessError) -> None:
        if self._capability_process is None:
            return
        self.capability_status.setText(f"Capability worker error: {self._capability_process.errorString()}")
        if process_error == QProcess.ProcessError.FailedToStart:
            self._capability_process = None
            self.case_readiness_button.setEnabled(self.selected_device() is not None)
            self._update_capability_controls()
            self._update_selected_command_readiness()

    def cancel_capability_matrix(self) -> None:
        self._stop_capability_process("Capability refresh was cancelled.")

    def _stop_capability_process(self, reason: str) -> None:
        process = self._capability_process
        if process is None:
            return
        self._capability_cancel_reason = reason
        self.capability_status.setText(f"{reason} Stopping the active probe…")
        self._terminate_capability_children(process)
        process.terminate()
        QTimer.singleShot(1500, self._kill_capability_after_cancel)
        self._update_capability_controls()

    def _terminate_capability_children(self, process: QProcess) -> None:
        process_identifier = process.processId()
        if process_identifier <= 0 or not Path("/usr/bin/pkill").is_file():
            return
        subprocess.run(
            ["/usr/bin/pkill", "-TERM", "-P", str(process_identifier)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=2,
        )

    def _discard_capability_process_for_device_change(self) -> None:
        process = self._capability_process
        if process is None:
            return
        process.readyReadStandardOutput.disconnect(self._read_capability_stdout)
        process.readyReadStandardError.disconnect(self._read_capability_stderr)
        process.finished.disconnect(self._capability_finished)
        process.errorOccurred.disconnect(self._capability_error)
        self._terminate_capability_children(process)
        process.terminate()
        if not process.waitForFinished(2500):
            process.kill()
            process.waitForFinished(1000)
        self._capability_process = None
        self._capability_cancel_reason = None
        self._capability_stdout_buffer.clear()
        self._capability_stderr.clear()

    def _kill_capability_after_cancel(self) -> None:
        process = self._capability_process
        if process is not None and process.state() != QProcess.ProcessState.NotRunning:
            process.kill()

    def _update_capability_controls(self) -> None:
        running = self._capability_process is not None
        tested = any(result.state != "not-tested" for result in self._capability_results.values())
        self.refresh_capabilities_button.setEnabled(self.selected_device() is not None and not running)
        self.cancel_capabilities_button.setEnabled(running)
        self.copy_capabilities_button.setEnabled(tested and not running)

    def copy_capability_report(self) -> None:
        device = self.selected_device()
        if device is None:
            self._show_no_device()
            return
        tested = tuple(
            self._capability_results[definition.identifier]
            for definition in capability_definitions()
            if self._capability_results[definition.identifier].state != "not-tested"
        )
        if not tested:
            QMessageBox.information(self, "No Capability Results", "Run the capability matrix before copying a report.")
            return
        lines = [
            "iOS Developer Toolkit — Device Capability Matrix",
            f"Target: {device.name}; {device.product_type}; iOS {device.product_version}; build {device.build_version}; {device.connection_type}",
            f"Completed at: {self._capability_completed_at or 'incomplete or cancelled run'}",
            "",
        ]
        for result in tested:
            lines.extend(
                (
                    f"[{capability_state_label(result.state)}] {result.layer} / {result.title}",
                    f"Result: {result.summary}",
                    f"Evidence: {result.evidence}",
                    f"Next step: {result.remediation}",
                    "",
                )
            )
        QApplication.clipboard().setText("\n".join(lines).rstrip() + "\n")
        self.capability_status.setText("Copied the current capability report to the clipboard.")

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
        profile = guided_action_safety("device-change")
        if self.personalized_radio.isChecked():
            prompt = (
                "Mount the downloaded personalized Developer Disk Image?\n\n"
                "This downloads files from GitHub, sends personalization identifiers and a nonce to Apple TSS, "
                "uploads the image, and changes the device's mounted state."
            )
            if not self._confirm_action("Mount Personalized DDI", prompt, profile, device.identifier):
                return
            self._record_action_approval(self.action_output, "Mount Personalized DDI", profile)
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
        if not self._confirm_action("Install Local Xcode DDI", prompt, profile, device.identifier):
            return
        self._record_action_approval(self.action_output, "Install Local Xcode DDI", profile)
        self._start_action(
            worker_command("local-ddi"),
            ("--candidate", str(XCODE_CANDIDATE_DDI), "--udid", device.identifier),
            base_environment(),
            "mount-local-cryptex",
        )

    def remove_selected_ddi(self) -> None:
        device = self.selected_device()
        if device is None:
            self._show_no_device()
            return
        profile = guided_action_safety("device-change")
        if self.personalized_radio.isChecked():
            if self._confirm_action(
                "Unmount Personalized DDI",
                "Unmount the personalized image from /System/Developer?",
                profile,
                device.identifier,
            ):
                self._record_action_approval(self.action_output, "Unmount Personalized DDI", profile)
                self._run_pmd3_action(("mounter", "umount-personalized"), "unmount-personalized")
            return
        if self._confirm_action(
            "Uninstall Local DDI Cryptex",
            "Uninstall com.apple.MobileAsset.DDI from the selected device?",
            profile,
            device.identifier,
        ):
            self._record_action_approval(self.action_output, "Uninstall Local DDI Cryptex", profile)
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
        program: ExecutableCommand,
        arguments: tuple[str, ...],
        environment: Mapping[str, str],
        context: str,
    ) -> None:
        if self._action_controller.is_running():
            QMessageBox.warning(self, "Action Running", "Wait for the current DDI action to finish.")
            return
        self.action_output.appendPlainText(f"$ {command_text(program, arguments)}")
        self._action_context = context
        self.mount_button.setEnabled(False)
        self.remove_button.setEnabled(False)
        self._action_controller.start(
            finite_process_request(
                program,
                arguments,
                environment,
                DDI_ACTION_TIMEOUT_MS,
                PROCESS_TERMINATE_GRACE_MS,
            )
        )

    def _append_action_output(self, output: bytes) -> None:
        self.action_output.moveCursor(QTextCursor.MoveOperation.End)
        self.action_output.insertPlainText(output.decode("utf-8", errors="replace"))

    def _action_completed(self, result_object: object) -> None:
        if not isinstance(result_object, OperationResult):
            raise TypeError(f"Expected OperationResult, received {type(result_object).__name__}")
        context = self._action_context
        combined_output = result_object.stdout + result_object.stderr
        semantic_failure = output_indicates_failure(combined_output)
        succeeded = result_object.outcome == "succeeded" and not semantic_failure
        exit_label = "not available" if result_object.exit_code is None else str(result_object.exit_code)
        self.action_output.appendPlainText(
            f"\n[finished: {result_object.outcome}; exit {exit_label}]\n"
        )
        if result_object.error_message:
            self.action_output.appendPlainText(f"Process error: {result_object.error_message}")
        if result_object.outcome == "timed-out":
            self.action_output.appendPlainText(
                "The DDI action exceeded the 15-minute safety limit and was stopped."
            )
        if context == "developer-mode-status":
            if succeeded and b"true" in combined_output.lower():
                self.developer_mode_status.setText("Developer Mode is enabled")
            elif succeeded:
                self.developer_mode_status.setText("Developer Mode appears disabled — follow the on-device steps")
            else:
                self.developer_mode_status.setText("Could not query Developer Mode; see command output")
        elif succeeded and context.startswith("mount"):
            self.developer_mode_status.setText("Developer image operation completed successfully")
        self._action_context = ""
        self._update_device_fields(self.selected_device())

    def _apply_location_coordinates(self, coordinates: Coordinates) -> None:
        self.location_latitude_field.setText(format(coordinates.latitude, ".12g"))
        self.location_longitude_field.setText(format(coordinates.longitude, ".12g"))
        self.location_map.set_marker(coordinates)
        self.location_map.setToolTip(
            f"Selected {coordinates.latitude:.6f}, {coordinates.longitude:.6f}. "
            "The device is unchanged until Set Simulated Location is confirmed."
        )

    def _map_location_selected(self, latitude: float, longitude: float) -> None:
        coordinates = validate_coordinates(str(latitude), str(longitude))
        self._apply_location_coordinates(coordinates)

    def import_location_coordinates(self) -> None:
        try:
            coordinates = parse_location_input(self.location_input_field.text())
        except LocationLabError as error:
            QMessageBox.critical(self, "Could Not Import Location", str(error))
            return
        self._apply_location_coordinates(coordinates)
        self.location_input_field.clear()

    def sync_location_map_from_fields(self) -> None:
        try:
            coordinates = validate_coordinates(
                self.location_latitude_field.text(),
                self.location_longitude_field.text(),
            )
        except LocationLabError as error:
            self.location_map.setToolTip(f"Map marker not updated: {error}")
            return
        self.location_map.set_marker(coordinates)

    def _populate_saved_locations(self) -> None:
        self.saved_location_combo.blockSignals(True)
        self.saved_location_combo.clear()
        if self._saved_locations_error is not None:
            self.saved_location_combo.addItem(f"Unavailable: {self._saved_locations_error}")
            self.saved_location_combo.setEnabled(False)
        else:
            self.saved_location_combo.addItem("Choose a saved place…", None)
            for location in self._saved_locations:
                self.saved_location_combo.addItem(
                    f"{location.name} — {location.coordinates.latitude:.6f}, {location.coordinates.longitude:.6f}",
                    location.name,
                )
            self.saved_location_combo.setEnabled(True)
            self.saved_location_combo.setCurrentIndex(0)
        self.saved_location_combo.blockSignals(False)
        self._update_location_controls()

    def _saved_location_selected(self, index: int) -> None:
        if index <= 0 or self._saved_locations_error is not None:
            self._update_location_controls()
            return
        name = self.saved_location_combo.itemData(index)
        matching = tuple(location for location in self._saved_locations if location.name == name)
        if len(matching) != 1:
            raise LocationLabError(f"Expected one saved location for selection {name!r}, found {len(matching)}")
        location = matching[0]
        self._apply_location_coordinates(location.coordinates)
        self._update_location_controls()

    def save_current_location(self) -> None:
        if self._saved_locations_error is not None:
            QMessageBox.critical(self, "Saved Locations Unavailable", self._saved_locations_error)
            return
        try:
            coordinates = validate_coordinates(
                self.location_latitude_field.text(),
                self.location_longitude_field.text(),
            )
        except LocationLabError as error:
            QMessageBox.critical(self, "Invalid Coordinates", str(error))
            return
        name, accepted = QInputDialog.getText(self, "Save Location", "Location name")
        if not accepted:
            return
        try:
            updated = add_saved_location(self._saved_locations, name, coordinates)
            save_saved_locations(self._saved_locations_path, updated)
        except LocationLabError as error:
            QMessageBox.critical(self, "Could Not Save Location", str(error))
            return
        self._saved_locations = updated
        self._populate_saved_locations()
        matching_index = self.saved_location_combo.findData(name.strip())
        if matching_index >= 0:
            self.saved_location_combo.setCurrentIndex(matching_index)

    def remove_selected_saved_location(self) -> None:
        name = self.saved_location_combo.currentData()
        if not isinstance(name, str):
            QMessageBox.information(self, "No Saved Location", "Choose a saved location to remove.")
            return
        if not self._confirm("Remove Saved Location", f"Remove the local saved location {name!r}?"):
            return
        try:
            updated = remove_saved_location(self._saved_locations, name)
            save_saved_locations(self._saved_locations_path, updated)
        except LocationLabError as error:
            QMessageBox.critical(self, "Could Not Remove Location", str(error))
            return
        self._saved_locations = updated
        self._populate_saved_locations()

    def choose_location_gpx(self) -> None:
        selected_path, _ = QFileDialog.getOpenFileName(
            self,
            "Choose GPX track",
            str(Path.home()),
            "GPS Exchange Format (*.gpx)",
        )
        if not selected_path:
            return
        try:
            inspection = inspect_gpx(Path(selected_path))
        except LocationLabError as error:
            self._selected_location_gpx = None
            self.location_gpx_field.clear()
            self.location_gpx_summary.setText(f"GPX validation failed: {error}")
            self._update_location_controls()
            QMessageBox.critical(self, "Invalid GPX Track", str(error))
            return
        self._selected_location_gpx = inspection
        self.location_gpx_field.setText(str(inspection.path))
        self.location_gpx_summary.setText(
            f"Validated {inspection.track_point_count} track points ({inspection.timed_point_count} timed). "
            f"Start: {inspection.first_point.latitude:.6f}, {inspection.first_point.longitude:.6f} • "
            f"End: {inspection.last_point.latitude:.6f}, {inspection.last_point.longitude:.6f} • "
            f"SHA-256: {inspection.sha256}"
        )
        self._update_location_controls()

    def nudge_location_fields(self, bearing_degrees: float) -> None:
        try:
            current = validate_coordinates(
                self.location_latitude_field.text(),
                self.location_longitude_field.text(),
            )
            nudged = move_coordinates(current, bearing_degrees, float(self.location_nudge_distance.value()))
        except LocationLabError as error:
            QMessageBox.critical(self, "Could Not Nudge Coordinate", str(error))
            return
        self._apply_location_coordinates(nudged)

    def apply_location_speed_preset(self, index: int) -> None:
        speed = self.location_route_speed_preset.itemData(index)
        if not isinstance(speed, int):
            raise LocationLabError(f"Route speed preset must contain an integer, received {speed!r}")
        self.location_route_speed.setValue(speed)

    def add_current_route_waypoint(self) -> None:
        try:
            coordinates = validate_coordinates(
                self.location_latitude_field.text(),
                self.location_longitude_field.text(),
            )
        except LocationLabError as error:
            QMessageBox.critical(self, "Invalid Route Waypoint", str(error))
            return
        existing = self.location_route_waypoints.toPlainText().rstrip()
        waypoint = f"{coordinates.latitude:.9f},{coordinates.longitude:.9f}"
        self.location_route_waypoints.setPlainText(f"{existing}\n{waypoint}".lstrip())
        self.location_route_waypoints.moveCursor(QTextCursor.MoveOperation.End)

    def build_location_route(self) -> None:
        try:
            waypoints = parse_route_waypoints(self.location_route_waypoints.toPlainText())
            route = build_route(
                waypoints,
                float(self.location_route_speed.value()),
                self.location_route_interval.value(),
                self.location_route_traversals.value(),
                datetime.now(timezone.utc),
            )
            directory = self.location_log_directory() / "Generated Routes"
            directory.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
            path = directory / f"qa-route-{timestamp}.gpx"
            with path.open("x", encoding="utf-8") as output:
                output.write(route.gpx_document)
            inspection = inspect_gpx(path)
        except (LocationLabError, OSError) as error:
            QMessageBox.critical(self, "Could Not Build Route", str(error))
            return
        self._selected_location_gpx = inspection
        self.location_gpx_field.setText(str(path))
        self.location_gpx_summary.setText(
            f"Validated generated GPX: {inspection.track_point_count} timed points • SHA-256: {inspection.sha256}"
        )
        self.location_route_summary.setText(
            f"Loaded {len(route.points):,} points • {route.distance_metres / 1000.0:.3f} km • "
            f"{route.duration_seconds // 60}m {route.duration_seconds % 60}s • {route.traversal_count} traversal(s)."
        )
        self.location_output.appendPlainText(
            f"Generated and validated local QA route: {path}\n"
            f"Distance: {route.distance_metres:.1f} m; duration: {route.duration_seconds} s; "
            f"speed: {route.speed_kmh:g} km/h; SHA-256: {inspection.sha256}"
        )
        self._update_location_controls()

    def choose_location_log_directory(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self,
            "Choose Location Lab evidence directory",
            self.location_log_directory_field.text(),
        )
        if selected:
            self.location_log_directory_field.setText(selected)

    def location_log_directory(self) -> Path:
        value = self.location_log_directory_field.text().strip()
        if not value:
            raise LocationLabError("Choose a non-empty Location Lab evidence directory")
        directory = Path(value).expanduser()
        if not directory.is_absolute():
            raise LocationLabError(f"Location Lab evidence directory must be an absolute path: {directory}")
        return directory.resolve()

    def open_location_log_directory(self) -> None:
        try:
            directory = self.location_log_directory()
        except LocationLabError as error:
            QMessageBox.critical(self, "Invalid Evidence Directory", str(error))
            return
        if not directory.is_dir():
            QMessageBox.information(self, "Evidence Directory Not Found", f"The folder does not exist yet:\n{directory}")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))

    def _record_location_event(
        self,
        operation: str,
        status: str,
        device_identifier: str,
        device_name: str,
        ios_version: str,
        arguments: tuple[str, ...],
        coordinates: Coordinates | None,
        gpx: GPXInspection | None,
        exit_code: int | None,
        detail: str,
    ) -> None:
        event = LocationEvidenceEvent(
            event=operation,
            status=status,
            timestamp=utc_now(),
            device_identifier=device_identifier,
            device_name=device_name,
            ios_version=ios_version,
            command=("pymobiledevice3", *arguments),
            latitude=coordinates.latitude if coordinates is not None else None,
            longitude=coordinates.longitude if coordinates is not None else None,
            gpx_path=str(gpx.path) if gpx is not None else None,
            gpx_sha256=gpx.sha256 if gpx is not None else None,
            exit_code=exit_code,
            detail=detail,
        )
        self._location_log_path = append_evidence_event(self.location_log_directory(), event)

    def _start_location_process(
        self,
        operation: str,
        device_identifier: str,
        device_name: str,
        ios_version: str,
        arguments: tuple[str, ...],
        coordinates: Coordinates | None,
        gpx: GPXInspection | None,
    ) -> None:
        if self._location_process is not None:
            QMessageBox.warning(self, "Location Operation Running", "Stop and clear the active location operation first.")
            return
        try:
            self._record_location_event(
                operation,
                "requested",
                device_identifier,
                device_name,
                ios_version,
                arguments,
                coordinates,
                gpx,
                None,
                "User confirmed the device-state change.",
            )
        except LocationLabError as error:
            QMessageBox.critical(self, "Could Not Write Evidence Log", str(error))
            return
        self._location_operation = operation
        self._location_arguments = arguments
        self._location_buffer.clear()
        self._location_started_at = utc_now()
        self._location_device_identifier = device_identifier
        self._location_device_name = device_name
        self._location_device_version = ios_version
        self._location_coordinates = coordinates
        self._location_operation_gpx = gpx
        process = QProcess(self)
        process.setProgram(str(self._pmd3.program))
        process.setArguments(list(command_arguments(self._pmd3, arguments)))
        process.setWorkingDirectory(str(Path.home()))
        process.setProcessEnvironment(qprocess_environment(device_environment(device_identifier)))
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.started.connect(self._location_started)
        process.readyReadStandardOutput.connect(self._read_location_output)
        process.finished.connect(self._location_finished)
        process.errorOccurred.connect(self._location_error)
        self._location_process = process
        self.location_output.appendPlainText(f"\n$ pymobiledevice3 {shlex.join(arguments)}")
        self.location_state_value.setText(f"Starting {operation} for {device_name}…")
        self._update_location_controls()
        process.start()

    def _location_started(self) -> None:
        operation = self._location_operation
        if operation in ("set", "play"):
            self._location_may_be_simulated = True
        self.location_state_value.setText(
            f"{operation.title()} request is running for {self._location_device_name}. "
            "The host process started; device-side location is not independently verified."
        )
        self.location_output.appendPlainText(
            f"[started {self._location_started_at}; target {self._location_device_identifier}]"
        )
        try:
            self._record_location_event(
                operation,
                "started",
                self._location_device_identifier or "",
                self._location_device_name,
                self._location_device_version,
                self._location_arguments,
                self._location_coordinates,
                self._location_operation_gpx,
                None,
                "The host command process started; device-side effect is not independently verified.",
            )
        except LocationLabError as error:
            self.location_output.appendPlainText(f"Evidence log error: {error}")
            QMessageBox.critical(self, "Could Not Update Evidence Log", str(error))
        self._update_location_controls()

    def _read_location_output(self) -> None:
        if self._location_process is None:
            return
        data = bytes(self._location_process.readAllStandardOutput())
        self._location_buffer.extend(data)
        self.location_output.moveCursor(QTextCursor.MoveOperation.End)
        self.location_output.insertPlainText(data.decode("utf-8", errors="replace"))

    def _location_finished(self, exit_code: int, exit_status: QProcess.ExitStatus) -> None:
        del exit_status
        self._read_location_output()
        operation = self._location_operation
        user_stopped = self._location_clear_after_stop and operation in ("set", "play")
        semantic_failure = output_indicates_failure(bytes(self._location_buffer))
        succeeded = exit_code == 0 and not semantic_failure
        if user_stopped:
            status = "stopped"
            detail = "The user stopped the active process; an explicit clear follows."
        elif succeeded:
            status = "completed"
            detail = "The command completed without a detected CLI or semantic error."
        else:
            status = "failed"
            detail = "The command failed or emitted device/service error output; clearing remains recommended."
        self.location_output.appendPlainText(f"\n[location {operation} finished: exit {exit_code}; {status}]")
        try:
            self._record_location_event(
                operation,
                status,
                self._location_device_identifier or "",
                self._location_device_name,
                self._location_device_version,
                self._location_arguments,
                self._location_coordinates,
                self._location_operation_gpx,
                exit_code,
                detail,
            )
        except LocationLabError as error:
            self.location_output.appendPlainText(f"Evidence log error: {error}")
            QMessageBox.critical(self, "Could Not Finalize Evidence Log", str(error))
        self._location_process = None
        if operation == "clear" and succeeded:
            self._location_may_be_simulated = False
            self._location_device_identifier = None
            self._location_device_name = ""
            self._location_device_version = ""
            self._location_coordinates = None
            self._location_operation_gpx = None
            self.location_state_value.setText(
                "Clear completed without a detected error. External location state is not independently observable."
            )
        elif operation == "clear":
            self._location_may_be_simulated = True
            self.location_state_value.setText("Clear failed; the tracked device may still report a simulated location.")
        elif not user_stopped:
            self._location_may_be_simulated = True
            self.location_state_value.setText(
                f"{operation.title()} process ended; the tracked device may remain simulated until Clear succeeds."
            )
        if user_stopped:
            self._location_clear_after_stop = False
            QTimer.singleShot(0, self._start_tracked_location_clear)
        else:
            self._update_location_controls()

    def _location_error(self, process_error: QProcess.ProcessError) -> None:
        process = self._location_process
        if process is None:
            return
        message = process.errorString()
        self.location_output.appendPlainText(f"\nLocation process error: {message}")
        if process_error != QProcess.ProcessError.FailedToStart:
            return
        operation = self._location_operation
        try:
            self._record_location_event(
                operation,
                "failed-to-start",
                self._location_device_identifier or "",
                self._location_device_name,
                self._location_device_version,
                self._location_arguments,
                self._location_coordinates,
                self._location_operation_gpx,
                None,
                message,
            )
        except LocationLabError as error:
            self.location_output.appendPlainText(f"Evidence log error: {error}")
        if operation in ("set", "play"):
            self._location_may_be_simulated = False
            self._location_device_identifier = None
            self._location_device_name = ""
            self._location_device_version = ""
            self._location_coordinates = None
            self._location_operation_gpx = None
        self._location_process = None
        self.location_state_value.setText(f"Location command could not start: {message}")
        self._update_location_controls()

    def _update_location_controls(self) -> None:
        if not hasattr(self, "set_location_button"):
            return
        device = self.selected_device()
        running = self._location_process is not None
        available_for_new_simulation = device is not None and not running and not self._location_may_be_simulated
        self.set_location_button.setEnabled(available_for_new_simulation)
        self.play_location_gpx_button.setEnabled(
            available_for_new_simulation and self._selected_location_gpx is not None
        )
        self.clear_location_button.setEnabled(not running and (device is not None or self._location_device_identifier is not None))
        self.stop_clear_location_button.setEnabled(
            running and self._location_operation in ("set", "play") and not self._location_clear_after_stop
        )
        self.choose_location_gpx_button.setEnabled(not running)
        self.location_timing_randomness.setEnabled(not running)
        self.location_disable_sleep.setEnabled(not running)
        self.location_route_waypoints.setEnabled(not running)
        self.location_route_speed_preset.setEnabled(not running)
        self.location_route_speed.setEnabled(not running)
        self.location_route_interval.setEnabled(not running)
        self.location_route_traversals.setEnabled(not running)
        self.location_nudge_distance.setEnabled(not running)
        self.location_map.setEnabled(not running)
        self.location_input_field.setEnabled(not running)
        self.import_location_button.setEnabled(not running)
        saved_available = self._saved_locations_error is None and not running
        self.save_location_button.setEnabled(saved_available)
        self.remove_saved_location_button.setEnabled(
            saved_available and isinstance(self.saved_location_combo.currentData(), str)
        )
        if self._location_device_identifier is not None:
            selected_note = ""
            if device is not None and device.identifier != self._location_device_identifier:
                selected_note = f" Selected picker now shows {device.identifier}; cleanup will still target the tracked device."
            self.location_target_value.setText(
                f"Tracked target: {self._location_device_name} — iOS {self._location_device_version} "
                f"({self._location_device_identifier}).{selected_note}"
            )
        elif device is not None:
            self.location_target_value.setText(f"Selected target: {device.display_name()} ({device.identifier})")
        else:
            self.location_target_value.setText("No connected target is selected")

    def set_simulated_location(self) -> None:
        device = self.selected_device()
        if device is None:
            self._show_no_device()
            return
        if self._location_may_be_simulated:
            QMessageBox.warning(self, "Clear Existing Simulation", "Clear the tracked simulated location before setting another one.")
            return
        try:
            coordinates = validate_coordinates(
                self.location_latitude_field.text(),
                self.location_longitude_field.text(),
            )
            arguments = set_location_arguments(device.product_version, coordinates)
            log_directory = self.location_log_directory()
        except LocationLabError as error:
            QMessageBox.critical(self, "Invalid Location Request", str(error))
            return
        warning = (
            f"Set a simulated location on {device.display_name()}?\n\n"
            f"Latitude: {coordinates.latitude}\nLongitude: {coordinates.longitude}\n"
            f"Evidence log: {log_directory / 'location-events.jsonl'}\n\n"
            "This changes device-visible location for participating software. Keep this window open and use Stop & Clear "
            "when testing ends. The toolkit cannot independently verify what every app reports."
        )
        if not self._confirm_action(
            "Set Simulated Location",
            warning,
            guided_action_safety("device-change"),
            device.identifier,
        ):
            return
        self._start_location_process(
            "set",
            device.identifier,
            device.name,
            device.product_version,
            arguments,
            coordinates,
            None,
        )

    def play_location_gpx(self) -> None:
        device = self.selected_device()
        inspection = self._selected_location_gpx
        if device is None:
            self._show_no_device()
            return
        if inspection is None:
            QMessageBox.information(self, "No Validated GPX", "Choose and validate a GPX track first.")
            return
        if self._location_may_be_simulated:
            QMessageBox.warning(self, "Clear Existing Simulation", "Clear the tracked simulated location before replaying a route.")
            return
        try:
            arguments = play_location_arguments(
                device.product_version,
                inspection.path,
                self.location_timing_randomness.value(),
                self.location_disable_sleep.isChecked(),
            )
            log_directory = self.location_log_directory()
        except LocationLabError as error:
            QMessageBox.critical(self, "Invalid GPX Request", str(error))
            return
        warning = (
            f"Replay the validated GPX track on {device.display_name()}?\n\n"
            f"Track points: {inspection.track_point_count}\nSHA-256: {inspection.sha256}\n"
            f"Evidence log: {log_directory / 'location-events.jsonl'}\n\n"
            "GPX timestamps control pacing unless fast playback is selected. Stop & Clear restores normal location "
            "after the route or when you stop it early."
        )
        if not self._confirm_action(
            "Play GPX Route",
            warning,
            guided_action_safety("device-change"),
            device.identifier,
        ):
            return
        self._start_location_process(
            "play",
            device.identifier,
            device.name,
            device.product_version,
            arguments,
            None,
            inspection,
        )

    def clear_simulated_location(self) -> None:
        if self._location_process is not None:
            self.stop_location_and_clear()
            return
        target = self._tracked_or_selected_location_target()
        if target is None:
            self._show_no_device()
            return
        identifier, name, version = target
        try:
            arguments = clear_location_arguments(version)
            log_directory = self.location_log_directory()
        except LocationLabError as error:
            QMessageBox.critical(self, "Invalid Clear Request", str(error))
            return
        warning = (
            f"Clear simulated location on {name} — iOS {version} ({identifier})?\n\n"
            f"Evidence log: {log_directory / 'location-events.jsonl'}\n\n"
            "This requests restoration of normal location sources. A successful command is not an independent reading "
            "of every app's location state."
        )
        if not self._confirm("Clear Simulated Location", warning):
            return
        self._start_location_process("clear", identifier, name, version, arguments, None, None)

    def _tracked_or_selected_location_target(self) -> tuple[str, str, str] | None:
        if self._location_device_identifier is not None:
            return (
                self._location_device_identifier,
                self._location_device_name,
                self._location_device_version,
            )
        device = self.selected_device()
        if device is None:
            return None
        return device.identifier, device.name, device.product_version

    def stop_location_and_clear(self) -> None:
        process = self._location_process
        if process is None or self._location_operation not in ("set", "play"):
            QMessageBox.information(self, "No Active Simulation", "No active set or GPX process is available to stop.")
            return
        self._location_clear_after_stop = True
        self.location_state_value.setText("Stopping the active location process; an explicit clear will follow…")
        self.location_output.appendPlainText("\nRequesting location process stop before clear…")
        self._update_location_controls()
        process.terminate()
        QTimer.singleShot(5000, self._kill_location_after_stop_timeout)

    def _kill_location_after_stop_timeout(self) -> None:
        process = self._location_process
        if process is not None and self._location_clear_after_stop and process.state() != QProcess.ProcessState.NotRunning:
            self.location_output.appendPlainText("Location process did not terminate in 5 seconds; killing it before clear.")
            process.kill()

    def _start_tracked_location_clear(self) -> None:
        target = self._tracked_or_selected_location_target()
        if target is None:
            self.location_state_value.setText("The active process stopped, but no target remains available for clear.")
            self._location_may_be_simulated = True
            self._update_location_controls()
            return
        identifier, name, version = target
        try:
            arguments = clear_location_arguments(version)
        except LocationLabError as error:
            self.location_state_value.setText(f"Could not build the cleanup command: {error}")
            self._location_may_be_simulated = True
            self._update_location_controls()
            return
        self._start_location_process("clear", identifier, name, version, arguments, None, None)

    def open_location_help(self) -> None:
        target = self._tracked_or_selected_location_target()
        version = target[2] if target is not None else "17"
        try:
            path = (
                ("developer", "dvt", "simulate-location")
                if parse_ios_major(version) >= 17
                else ("developer", "simulate-location")
            )
        except LocationLabError as error:
            QMessageBox.critical(self, "Unknown iOS Version", str(error))
            return
        self.navigate_to_page("Man Pages")
        self.select_manpage_path(path)

    def choose_output_root(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "Choose evidence destination", self.output_root.text())
        if selected:
            self.output_root.setText(selected)

    def create_guided_case(self) -> None:
        device = self.selected_device()
        if device is None:
            self._show_no_device()
            return
        if self._collection_process is not None:
            QMessageBox.warning(self, "Collection Running", "Wait for the active collection to finish before creating another case.")
            return
        if self._active_case_path is not None:
            QMessageBox.warning(self, "Guided Case Active", "This case is ready for collection. Start it or create a new case after it is finalized.")
            return
        try:
            case_path, _ = create_guided_case(
                Path(self.output_root.text()),
                device.identifier,
                self.case_title_field.text(),
                self.case_purpose_field.toPlainText(),
                self.case_authorization_checkbox.isChecked(),
            )
        except CaseWorkflowError as error:
            QMessageBox.warning(self, "Unable to Create Guided Case", str(error))
            return
        self._active_case_path = case_path
        self._last_case_path = case_path
        self.open_case_button.setEnabled(True)
        self.create_case_button.setEnabled(False)
        self.case_status.setText(f"Active case: {case_path}. Configure coverage, then start collection.")
        self.collection_output.setPlainText(f"Guided case created:\n{case_path}\n\nCollection will attach to this case and finalize it once.")

    def run_guided_case_readiness_check(self) -> None:
        device = self.selected_device()
        if device is None:
            self._show_no_device()
            return
        if self._capability_process is not None:
            QMessageBox.warning(self, "Readiness Check Running", "Cancel or wait for the current Device Capability Matrix check.")
            return
        self.case_status.setText(
            f"Running a bounded, read-only readiness check for {device.display_name()}. Results are shown in Capability Matrix."
        )
        self.navigate_to_page("Capability Matrix")
        self.refresh_capability_matrix()

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
        arguments = ["--udid", device.identifier]
        if self._active_case_path is None:
            arguments.extend(("--output-root", self.output_root.text()))
        else:
            arguments.extend(("--case-directory", str(self._active_case_path)))
        arguments.extend(("--duration", str(self.capture_duration.value())))
        for enabled, flag in (
            (self.include_syslog.isChecked(), "--include-syslog"),
            (self.include_oslog.isChecked(), "--include-oslog"),
            (self.include_pcap.isChecked(), "--include-pcap"),
            (self.include_screenshot.isChecked(), "--include-screenshot"),
            (self.include_crash_pull.isChecked(), "--include-crash-pull"),
        ):
            if enabled:
                arguments.append(flag)
        worker = worker_command("collector")
        process = QProcess(self)
        process.setProgram(str(worker.program))
        process.setArguments(list(command_arguments(worker, arguments)))
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
        if self._active_case_path is not None:
            self.case_status.setText(
                f"Guided case finalized at {self._active_case_path}. Create a new case before another collection."
            )
            self._active_case_path = None
        self.start_collection_button.setEnabled(self.selected_device() is not None)
        self.create_case_button.setEnabled(self.selected_device() is not None)
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
        if self._ipa_inspection_controller.is_running():
            QMessageBox.warning(self, "Inspection Running", "Wait for the current IPA inspection to finish.")
            return
        self._ipa_inspection = None
        self.ipa_inspection_summary.clear()
        self.ipa_inspection_summary.setPlainText("Inspecting archive, provisioning profile, and code signature…")
        self.ipa_inspection_progress.setVisible(True)
        worker = worker_command("ipa-inspector")
        self._ipa_inspection_controller.start(
            finite_process_request(
                worker,
                (str(selected_ipa),),
                base_environment(),
                IPA_INSPECTION_TIMEOUT_MS,
                PROCESS_TERMINATE_GRACE_MS,
            )
        )
        self._update_sideload_controls()

    def _ipa_inspection_completed(self, result_object: object) -> None:
        if not isinstance(result_object, OperationResult):
            raise TypeError(f"Expected OperationResult, received {type(result_object).__name__}")
        stderr_text = result_object.stderr.decode("utf-8", errors="replace").strip()
        if result_object.outcome != "succeeded":
            exit_label = "not available" if result_object.exit_code is None else str(result_object.exit_code)
            message = stderr_text or result_object.error_message or f"IPA inspector exited with status {exit_label}"
            self.ipa_inspection_summary.setPlainText(message)
            if result_object.outcome == "timed-out":
                self.sideload_status.setText("IPA inspection exceeded the five-minute safety limit and was stopped.")
            else:
                self.sideload_status.setText("IPA inspection failed. Correct the package error before installation.")
        else:
            try:
                inspection = parse_inspection_json(result_object.stdout.decode("utf-8"))
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
        self.ipa_inspection_progress.setVisible(False)
        self._update_sideload_controls()

    def _update_sideload_controls(self) -> None:
        device_available = self.selected_device() is not None
        action_running = self._sideload_controller.is_running()
        inspection_running = self._ipa_inspection_controller.is_running()
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
        if not self._confirm_action(
            "Install IPA",
            warning,
            guided_action_safety("device-change"),
            device.identifier,
        ):
            return
        self._record_action_approval(self.sideload_output, "Install IPA", guided_action_safety("device-change"))
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
        if self._sideload_controller.is_running():
            QMessageBox.warning(self, "App Operation Running", "Stop or wait for the active app operation first.")
            return
        self._sideload_context = context
        self.sideload_output.appendPlainText(f"\n$ pymobiledevice3 {shlex.join(arguments)}\n")
        self.sideload_status.setText(f"Running {context} operation on {device.display_name()}…")
        self.sideload_activity_progress.setVisible(True)
        self._sideload_controller.start(
            finite_process_request(
                self._pmd3,
                arguments,
                device_environment(device.identifier),
                IPA_INSTALL_TIMEOUT_MS,
                PROCESS_TERMINATE_GRACE_MS,
            )
        )
        self._update_sideload_controls()

    def _append_sideload_output(self, output: bytes) -> None:
        self.sideload_output.moveCursor(QTextCursor.MoveOperation.End)
        self.sideload_output.insertPlainText(output.decode("utf-8", errors="replace"))

    def _sideload_completed(self, result_object: object) -> None:
        if not isinstance(result_object, OperationResult):
            raise TypeError(f"Expected OperationResult, received {type(result_object).__name__}")
        context = self._sideload_context
        semantic_failure = output_indicates_failure(result_object.stdout + result_object.stderr)
        succeeded = result_object.outcome == "succeeded" and not semantic_failure
        exit_label = "not available" if result_object.exit_code is None else str(result_object.exit_code)
        self.sideload_output.appendPlainText(
            f"\n[finished: {result_object.outcome}; exit {exit_label}]\n"
        )
        if result_object.error_message:
            self.sideload_output.appendPlainText(f"Process error: {result_object.error_message}")
        if succeeded:
            self.sideload_status.setText(f"{context.capitalize()} completed successfully.")
        elif result_object.outcome == "timed-out":
            self.sideload_status.setText("IPA installation exceeded the 15-minute safety limit and was stopped.")
        elif result_object.outcome == "cancelled":
            self.sideload_status.setText("IPA installation was cancelled; verify device state before retrying.")
        else:
            self.sideload_status.setText(f"{context.capitalize()} failed; review the complete command output above.")
        self._sideload_context = ""
        self.sideload_activity_progress.setVisible(False)
        self._update_sideload_controls()
        if succeeded and context == "install" and not self._apps_controller.is_running():
            QTimer.singleShot(0, self.refresh_app_inventory)

    def stop_sideload_action(self) -> None:
        if self._sideload_controller.is_running():
            self.sideload_output.appendPlainText("\nRequesting app operation stop…")
            self._sideload_controller.cancel()

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
        if self._apps_controller.is_running():
            QMessageBox.warning(self, "App Operation Running", "Stop or wait for the active app operation first.")
            return
        self._apps_context = context
        self.apps_output.appendPlainText(f"\n$ pymobiledevice3 {shlex.join(arguments)}\n")
        self.apps_status.setText(f"Running {context} operation on {device.display_name()}…")
        self._apps_controller.start(
            finite_process_request(
                self._pmd3,
                arguments,
                device_environment(device.identifier),
                APPS_ACTION_TIMEOUT_MS,
                PROCESS_TERMINATE_GRACE_MS,
            )
        )
        self._update_apps_controls()

    def _append_apps_stderr(self, output: bytes) -> None:
        self.apps_output.moveCursor(QTextCursor.MoveOperation.End)
        self.apps_output.insertPlainText(output.decode("utf-8", errors="replace"))

    def _apps_completed(self, result_object: object) -> None:
        if not isinstance(result_object, OperationResult):
            raise TypeError(f"Expected OperationResult, received {type(result_object).__name__}")
        context = self._apps_context
        combined_output = result_object.stdout + result_object.stderr
        succeeded = result_object.outcome == "succeeded" and not output_indicates_failure(combined_output)
        if succeeded and context == "inventory":
            try:
                apps = parse_installed_apps_json(result_object.stdout.decode("utf-8"))
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
            stdout_text = result_object.stdout.decode("utf-8", errors="replace").strip()
            if stdout_text:
                self.apps_output.appendPlainText(stdout_text)
            if result_object.outcome == "timed-out":
                self.apps_status.setText("App operation exceeded the 10-minute safety limit and was stopped.")
            elif result_object.outcome == "cancelled":
                self.apps_status.setText("App operation was cancelled.")
            else:
                self.apps_status.setText(f"{context.capitalize()} failed; review the output below.")
        if result_object.error_message:
            self.apps_output.appendPlainText(f"Process error: {result_object.error_message}")
        exit_label = "not available" if result_object.exit_code is None else str(result_object.exit_code)
        self.apps_output.appendPlainText(f"[finished: {result_object.outcome}; exit {exit_label}]\n")
        self._apps_context = ""
        self._update_apps_controls()
        if succeeded and context == "uninstall":
            QTimer.singleShot(0, self.refresh_app_inventory)

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
        running = self._apps_controller.is_running()
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
        if self._confirm_action(
            "Uninstall Application",
            warning,
            guided_action_safety("device-change"),
            device.identifier,
        ):
            self._record_action_approval(self.apps_output, "Uninstall Application", guided_action_safety("device-change"))
            self._start_apps_action(("apps", "uninstall", bundle_identifier), "uninstall")

    def stop_apps_action(self) -> None:
        if self._apps_controller.is_running():
            self.apps_output.appendPlainText("Requesting app operation stop…")
            self._apps_controller.cancel()

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
        request = BackupRequest(device.identifier, destination, False, "", False)
        self._start_backup_worker("status", request)

    def _backup_encryption_choice_changed(self, checked: bool) -> None:
        needs_new_password = checked and self._backup_encryption_state is not True
        controls_enabled = (
            needs_new_password
            and not self._backup_controller.is_running()
            and self.selected_device() is not None
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
        profile = (
            guided_action_safety("device-change")
            if require_encryption and self._backup_encryption_state is not True
            else guided_action_safety("host-write")
        )
        if not self._confirm_action("Start Device Backup", warning, profile, device.identifier):
            return
        self._record_action_approval(self.backup_output, "Start Device Backup", profile)
        request = BackupRequest(
            device.identifier,
            destination,
            require_encryption,
            password,
            self.full_backup_checkbox.isChecked(),
        )
        self._start_backup_worker("backup", request)
        self.backup_password_field.clear()
        self.backup_password_confirmation_field.clear()

    def _start_backup_worker(self, action: BackupAction, request: BackupRequest) -> None:
        if self._backup_controller.is_running():
            QMessageBox.warning(self, "Backup Operation Running", "Stop or wait for the active backup operation first.")
            return
        self._backup_action = action
        worker = worker_command("backup")
        self.backup_output.appendPlainText(
            "Checking backup encryption…" if action == "status" else "Starting device backup…"
        )
        if action == "backup":
            self.backup_progress.setValue(0)
        self._backup_controller.start(
            worker,
            action,
            request,
            base_environment(),
            PROCESS_TERMINATE_GRACE_MS,
        )
        self._update_backup_controls()

    def _handle_backup_event(self, event_object: object) -> None:
        if not isinstance(event_object, BackupEvent):
            raise TypeError(f"Expected BackupEvent, received {type(event_object).__name__}")
        event = event_object
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

    def _append_backup_stderr(self, output: bytes) -> None:
        self.backup_output.moveCursor(QTextCursor.MoveOperation.End)
        self.backup_output.insertPlainText(output.decode("utf-8", errors="replace"))

    def _backup_completed(self, result_object: object) -> None:
        if not isinstance(result_object, OperationResult):
            raise TypeError(f"Expected OperationResult, received {type(result_object).__name__}")
        action = self._backup_action
        if result_object.outcome == "succeeded":
            self.backup_output.appendPlainText(
                "Encryption status check completed." if action == "status" else "Backup operation completed successfully."
            )
        elif result_object.outcome == "cancelled":
            self.backup_output.appendPlainText(
                "Encryption status check stopped."
                if action == "status"
                else "Backup operation stopped. Any partial destination remains incomplete and must be reviewed before reuse."
            )
        else:
            action_label = "Backup" if action is None else action.capitalize()
            exit_label = "not available" if result_object.exit_code is None else str(result_object.exit_code)
            self.backup_output.appendPlainText(
                f"{action_label} failed ({result_object.outcome}; exit {exit_label})."
            )
            if result_object.error_message:
                self.backup_output.appendPlainText(f"Process error: {result_object.error_message}")
        self._backup_action = None
        self._update_backup_controls()
        self._backup_encryption_choice_changed(self.require_encryption_checkbox.isChecked())

    def _update_backup_controls(self) -> None:
        running = self._backup_controller.is_running()
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
        if self._backup_controller.is_running():
            if self._backup_action == "status":
                self.backup_output.appendPlainText("Stopping the encryption status check…")
            else:
                self.backup_output.appendPlainText(
                    "Stopping the backup. The partial destination may be incomplete and will not be treated as valid incremental state."
                )
            self._backup_controller.cancel()

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

    def use_checkout_ufade_python(self) -> None:
        try:
            checkout = self.ufade_checkout()
        except UFADEValidationError as error:
            QMessageBox.critical(self, "Choose UFADE Checkout First", str(error))
            return
        python = checkout_python_path(checkout)
        if not python.is_file():
            QMessageBox.critical(
                self,
                "UFADE .venv Not Found",
                f"The expected Python executable does not exist:\n{python}\n\n"
                "Click Copy Setup Commands, run every command in Terminal, then try again.",
            )
            return
        self.ufade_python_field.setText(str(python))

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
        developer_status = (
            "Developer-image submodule is populated."
            if installation.developer_images_available
            else "Developer-image submodule is not populated; logical acquisitions can still run, but UFADE Developer Options may be limited."
        )
        message = (
            f"Validated UFADE {installation.ufade_version} with Python {installation.python_version}. "
            f"{developer_status} The GPL application will remain a separate process."
        )
        self.ufade_validation_status.setText(message)
        self.ufade_output.appendPlainText(message)
        return installation

    def copy_ufade_setup_commands(self) -> None:
        commands = "\n".join(macos_setup_commands())
        QApplication.clipboard().setText(commands)
        self.ufade_output.appendPlainText(
            "Copied macOS UFADE setup commands to the clipboard. Run them in Terminal, then choose the UFADE checkout "
            "and click Use Checkout .venv."
        )

    def copy_ufade_manual_launch_command(self) -> None:
        installation = self.validate_ufade_from_ui()
        if installation is None:
            QMessageBox.critical(
                self,
                "UFADE Validation Failed",
                "Correct the UFADE checkout or Python 3.11 environment before copying a launch command.",
            )
            return
        try:
            destination = self.ufade_output_directory()
        except UFADEValidationError as error:
            QMessageBox.critical(self, "Invalid UFADE Output Directory", str(error))
            return
        command = "\n".join(
            (
                f"cd {shlex.quote(str(destination))}",
                shlex.join((str(installation.python), str(installation.script))),
            )
        )
        QApplication.clipboard().setText(command)
        self.ufade_output.appendPlainText(
            "Copied a manual UFADE launch command. It uses the validated Python and starts in the selected output folder."
        )

    def show_ufade_guide(self) -> None:
        UFADEGuideDialog().exec()

    def open_ufade_installation_guide(self) -> None:
        if not QDesktopServices.openUrl(QUrl(UFADE_INSTALLATION_URL)):
            QMessageBox.critical(
                self,
                "Could Not Open UFADE Guide",
                f"macOS could not open the official UFADE installation guide:\n{UFADE_INSTALLATION_URL}",
            )

    def open_ufade_repository(self) -> None:
        if not QDesktopServices.openUrl(QUrl(UFADE_REPOSITORY_URL)):
            QMessageBox.critical(
                self,
                "Could Not Open UFADE Repository",
                f"macOS could not open the UFADE repository:\n{UFADE_REPOSITORY_URL}",
            )

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
            f"Developer-image submodule: {'available' if installation.developer_images_available else 'not populated'}\n\n"
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
            field.setAccessibleName(spec.label)
            field.setAccessibleDescription(spec.description)
            field.textChanged.connect(self._update_command_preview)
            self._preset_parameter_fields[spec.identifier] = field
            if spec.kind in ("local-directory", "output-file"):
                row = QWidget()
                row_layout = QHBoxLayout(row)
                row_layout.setContentsMargins(0, 0, 0, 0)
                row_layout.addWidget(field, 1)
                choose_button = QPushButton("Choose…")
                choose_button.setAccessibleName(f"Choose {spec.label}")
                choose_button.setAccessibleDescription(spec.description)
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
        if preset is not None:
            self.preset_run_button.setText(
                "Run Guided Command" if preset.risk == "read-only" else "Review && Run Guided Command"
            )
        self._update_selected_command_readiness()
        self._update_advanced_safety_note()

    def _update_selected_command_readiness(self) -> None:
        preset = self._current_preset
        if preset is None:
            self.command_readiness_status.setText("Choose a preset to evaluate its device requirements.")
            self.command_readiness_button.setEnabled(False)
            return
        if preset.requires_device and self.selected_device() is None:
            self.command_readiness_status.setText(
                "Blocked — connect, unlock, trust, and select the intended physical device first."
            )
            self.command_readiness_button.setEnabled(False)
            return
        readiness = evaluate_preset_readiness(preset, self._capability_results)
        next_steps = " ".join(readiness.remediation)
        suffix = f" Next step: {next_steps}" if next_steps else ""
        labels = {
            "ready": "Ready",
            "not-tested": "Not checked",
            "needs-attention": "Needs attention",
        }
        self.command_readiness_status.setText(f"{labels[readiness.state]} — {readiness.summary}{suffix}")
        self.command_readiness_button.setEnabled(
            preset.requires_device and self.selected_device() is not None and self._capability_process is None
        )

    def run_selected_command_readiness_check(self) -> None:
        preset = self._current_preset
        if preset is None:
            raise CommandCatalogError("Cannot run command readiness without a selected preset")
        if not preset.requires_device:
            raise CommandCatalogError("The selected preset does not require a device readiness check")
        if self.selected_device() is None:
            self._show_no_device()
            return
        self.navigate_to_page("Capability Matrix")
        self.refresh_capability_matrix()

    def _update_advanced_safety_note(self) -> None:
        raw_arguments = self.console_input.text().strip()
        if not raw_arguments:
            self.advanced_safety_note.setText(
                "Advanced commands are classified before execution. State-changing commands require a typed acknowledgement."
            )
            return
        try:
            arguments = tuple(shlex.split(raw_arguments))
        except ValueError as error:
            self.advanced_safety_note.setText(f"Correct command quoting before safety classification: {error}")
            return
        if arguments and Path(arguments[0]).name == "pymobiledevice3":
            arguments = arguments[1:]
        if not arguments:
            self.advanced_safety_note.setText("Enter pymobiledevice3 arguments to classify the action.")
            return
        profile = advanced_action_safety(arguments)
        acknowledgement = "typed acknowledgement required" if profile.requires_typed_acknowledgement else "review confirmation required"
        self.advanced_safety_note.setText(
            f"Safety: {profile.level.replace('-', ' ')} — {profile.impact} {acknowledgement.capitalize()}."
        )

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
        profile = guided_action_safety(preset.risk)
        if profile.level != "read-only":
            warning = (
                f"Run {preset.title}?\n\n{profile.impact}\n\n"
                f"pymobiledevice3 {shlex.join(arguments)}\n\n"
                "Review the selected target and destination before continuing."
            )
            device = self.selected_device()
            if not self._confirm_action("Confirm Guided Command", warning, profile, device.identifier if device else None):
                return
        self._run_console_arguments(arguments, preset.title, preset.requires_device, profile)

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
        profile = advanced_action_safety(parsed)
        if profile.level != "read-only":
            warning = (
                f"{profile.impact}\n\n"
                f"pymobiledevice3 {shlex.join(parsed)}\n\n"
                "The exact command above will run without a shell. Review its target and local output path before continuing."
            )
            device = self.selected_device()
            if not self._confirm_action("Confirm Advanced Command", warning, profile, device.identifier if device else None):
                return
        self._run_console_arguments(parsed, "Advanced command", requires_device, profile)

    def _run_console_arguments(
        self,
        arguments: tuple[str, ...],
        title: str,
        requires_device: bool,
        profile: ActionSafetyProfile,
    ) -> None:
        if self._console_process is not None:
            QMessageBox.warning(self, "Command Running", "Stop the active console command first.")
            return
        device = self.selected_device()
        if requires_device and device is None:
            self._show_no_device()
            return
        approval = "" if profile.level == "read-only" else f"\n[safety approval: {profile.level}; acknowledgement accepted]"
        self.console_output.appendPlainText(f"\n[{title}]{approval}\n$ pymobiledevice3 {shlex.join(arguments)}\n")
        process = QProcess(self)
        process.setProgram(str(self._pmd3.program))
        process.setArguments(list(command_arguments(self._pmd3, arguments)))
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

    def start_command_drift_check(self) -> None:
        if self._command_drift_session_active:
            QMessageBox.information(self, "Command Drift Check", "The live-help drift check is already running.")
            return
        self._command_drift_paths = help_routes_for_presets(self._presets)
        self._command_drift_index = 0
        self._command_drift_active_path = None
        self._command_drift_session_active = True
        self._command_drift_cancelled = False
        self._command_drift_probes = {}
        self.command_drift_output.clear()
        self.command_drift_status.setText(
            f"Checking {len(self._command_drift_paths)} live-help routes without contacting a device…"
        )
        self._update_command_drift_controls()
        self._start_next_command_drift_probe()

    def _start_next_command_drift_probe(self) -> None:
        if self._command_drift_cancelled:
            self._finish_command_drift_check()
            return
        if self._command_drift_index >= len(self._command_drift_paths):
            self._finish_command_drift_check()
            return
        command_path = self._command_drift_paths[self._command_drift_index]
        self._command_drift_active_path = command_path
        self.command_drift_status.setText(
            f"Checking {self._command_drift_index + 1}/{len(self._command_drift_paths)}: "
            f"pymobiledevice3 {shlex.join(command_path)} --help"
        )
        self._command_drift_controller.start(
            finite_process_request(
                self._pmd3,
                (*command_path, "--help"),
                base_environment(),
                COMMAND_DRIFT_HELP_TIMEOUT_MS,
                PROCESS_TERMINATE_GRACE_MS,
            )
        )
        self._update_command_drift_controls()

    def _command_drift_probe_completed(self, result_object: object) -> None:
        if not isinstance(result_object, OperationResult):
            raise TypeError(f"Expected OperationResult, received {type(result_object).__name__}")
        command_path = self._command_drift_active_path
        if command_path is None:
            raise CommandCatalogError("Live-help drift probe completed without an active command path")
        if result_object.outcome == "timed-out":
            error = f"Live help exceeded the {COMMAND_DRIFT_HELP_TIMEOUT_MS // 1000}-second per-route limit."
        elif result_object.outcome == "cancelled":
            error = "Live-help drift check was cancelled by the user."
        elif result_object.outcome == "launch-failed":
            detail = result_object.error_message or "the operating system did not provide an error"
            error = f"Could not start live help: {detail}"
        elif result_object.outcome == "crashed":
            error = "Live help terminated unexpectedly before returning a complete result."
        else:
            error = None
        self._command_drift_probes[command_path] = HelpRouteProbe(
            command_path,
            result_object.exit_code,
            result_object.stdout.decode("utf-8", errors="replace"),
            result_object.stderr.decode("utf-8", errors="replace"),
            error,
        )
        self._command_drift_active_path = None
        self._command_drift_index += 1
        self._update_command_drift_controls()
        QTimer.singleShot(0, self._start_next_command_drift_probe)

    def cancel_command_drift_check(self) -> None:
        if not self._command_drift_session_active:
            return
        self._command_drift_cancelled = True
        self.command_drift_status.setText("Cancelling the current live-help check…")
        self._command_drift_controller.cancel()

    def _finish_command_drift_check(self) -> None:
        self._command_drift_session_active = False
        results = evaluate_command_drift(self._presets, tuple(self._command_drift_probes.values()))
        report = render_command_drift_report(results)
        self.command_drift_output.setPlainText(report)
        checked_count = len(self._command_drift_probes)
        if self._command_drift_cancelled:
            self.command_drift_status.setText(
                f"Cancelled after checking {checked_count}/{len(self._command_drift_paths)} live-help routes."
            )
        else:
            issue_count = sum(result.state != "verified" for result in results)
            self.command_drift_status.setText(
                f"Completed {checked_count} live-help routes; {issue_count} preset result(s) need review."
            )
        self._update_command_drift_controls()

    def _update_command_drift_controls(self) -> None:
        running = self._command_drift_session_active
        self.command_drift_check_button.setEnabled(not running)
        self.command_drift_cancel_button.setEnabled(running)
        self.command_drift_copy_button.setEnabled(bool(self.command_drift_output.toPlainText().strip()) and not running)

    def copy_command_drift_report(self) -> None:
        report = self.command_drift_output.toPlainText().strip()
        if report:
            QApplication.clipboard().setText(report)

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

    def _show_manpage_entry(self, entry: ManPageEntry) -> None:
        self.manpage_title.setText(entry.title)
        prefix = "pymobiledevice3" if not entry.command_path else f"pymobiledevice3 {shlex.join(entry.command_path)}"
        self.manpage_command.setText(prefix)
        cached_help = self._manpage_cache.get(entry.command_path)
        if cached_help is not None:
            self.manpage_output.setPlainText(cached_help)
        elif not self._manpage_controller.is_running():
            self.manpage_output.setPlainText(
                f"{entry.title}\n\nCommand prefix: {prefix}\nCategory: {entry.category}\n\n"
                "Click Refresh Live Help to query the installed pymobiledevice3 executable. Selection alone never "
                "contacts a device. Loading can be cancelled and is stopped automatically after 15 seconds."
            )

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
        if self._manpage_controller.is_running():
            QMessageBox.warning(self, "Help Loading", "Wait for the current help page to finish loading.")
            return
        self._manpage_active_path = entry.command_path
        self.manpage_output.setPlainText(
            "Loading live help from the installed pymobiledevice3…\n\n"
            "This can take several seconds on the first Python import. Use Cancel Loading to stop immediately."
        )
        self._manpage_controller.start(
            finite_process_request(
                self._pmd3,
                (*entry.command_path, "--help"),
                base_environment(),
                MANPAGE_HELP_TIMEOUT_MS,
                MANPAGE_HELP_KILL_DELAY_MS,
            )
        )
        self._update_manpage_controls()

    def _manpage_completed(self, result_object: object) -> None:
        if not isinstance(result_object, OperationResult):
            raise TypeError(f"Expected OperationResult, received {type(result_object).__name__}")
        stdout = result_object.stdout.decode("utf-8", errors="replace")
        stderr = result_object.stderr.decode("utf-8", errors="replace")
        output = stdout or stderr
        if result_object.outcome == "cancelled":
            suffix = f"\n\nPartial output:\n{output}" if output else ""
            self.manpage_output.setPlainText(f"Live help loading was cancelled by the user.{suffix}")
        elif result_object.outcome == "timed-out":
            suffix = f"\n\nPartial output:\n{output}" if output else ""
            self.manpage_output.setPlainText(
                "Live help exceeded the 15-second limit and was stopped. The installed CLI did not return "
                f"promptly; the Man Pages browser remains available.{suffix}"
            )
        elif result_object.outcome == "succeeded" and output:
            if self._manpage_active_path is None:
                raise CommandCatalogError("Live help completed without an active command path")
            self._manpage_cache[self._manpage_active_path] = output
            self.manpage_output.setPlainText(output)
        elif result_object.outcome == "launch-failed":
            error_detail = result_object.error_message or "QProcess did not provide an operating-system error"
            self.manpage_output.setPlainText(
                "Could not start live help. Verify the project runtime exists and is executable:\n"
                f"{result_object.argv[0]}\n\nSystem error: {error_detail}"
            )
        else:
            exit_detail = "unavailable" if result_object.exit_code is None else str(result_object.exit_code)
            self.manpage_output.setPlainText(
                f"Live help {result_object.outcome.replace('-', ' ')} with exit code {exit_detail}.\n\n{output}"
            )
        self._manpage_active_path = None
        self._update_manpage_controls()

    def cancel_manpage_load(self) -> None:
        if not self._manpage_controller.is_running():
            return
        self.manpage_output.setPlainText("Live help cancellation requested.\n\nStopping the help process…")
        self._manpage_controller.cancel()

    def _update_manpage_controls(self) -> None:
        running = self._manpage_controller.is_running()
        selected = self.selected_manpage_entry() is not None
        self.manpage_list.setEnabled(not running)
        self.manpage_search_field.setEnabled(not running)
        self.refresh_manpage_button.setEnabled(selected and not running)
        self.cancel_manpage_button.setEnabled(running)
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

    def _confirm_action(
        self,
        title: str,
        message: str,
        profile: ActionSafetyProfile,
        device_identifier: str | None,
    ) -> bool:
        if not profile.requires_typed_acknowledgement:
            return self._confirm(title, message)
        phrase = confirmation_phrase(profile, device_identifier)
        dialog = QDialog(self)
        dialog.setObjectName("actionSafetyConfirmationDialog")
        dialog.setWindowTitle(title)
        dialog.setMinimumWidth(560)
        layout = QVBoxLayout(dialog)
        warning = QLabel(message)
        warning.setWordWrap(True)
        layout.addWidget(warning)
        typed_instruction = QLabel(
            f"Type <b>{phrase}</b> exactly to authorize this action. The acknowledgement phrase is not retained."
        )
        typed_instruction.setWordWrap(True)
        layout.addWidget(typed_instruction)
        acknowledgement_field = QLineEdit()
        acknowledgement_field.setObjectName("actionSafetyAcknowledgement")
        acknowledgement_field.setPlaceholderText(phrase)
        layout.addWidget(acknowledgement_field)
        backup_acknowledgement: QCheckBox | None = None
        if profile.requires_backup_acknowledgement:
            backup_acknowledgement = QCheckBox(
                "I have current verified backup coverage and exact authorization for this high-impact action."
            )
            backup_acknowledgement.setObjectName("highImpactBackupAcknowledgement")
            backup_acknowledgement.setWordWrap(True)
            layout.addWidget(backup_acknowledgement)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok)
        buttons.setObjectName("actionSafetyConfirmationButtons")
        approve_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if approve_button is None:
            raise RuntimeError("Action safety confirmation dialog is missing its approval button")
        approve_button.setText("Authorize Action")
        approve_button.setEnabled(False)

        def update_approval_button(value: str) -> None:
            backup_acknowledged = backup_acknowledgement is None or backup_acknowledgement.isChecked()
            approve_button.setEnabled(value == phrase and backup_acknowledged)

        def update_backup_acknowledgement(checked: bool) -> None:
            del checked
            update_approval_button(acknowledgement_field.text())

        acknowledgement_field.textChanged.connect(update_approval_button)
        if backup_acknowledgement is not None:
            backup_acknowledgement.toggled.connect(update_backup_acknowledgement)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        return dialog.exec() == QDialog.DialogCode.Accepted

    def _record_action_approval(
        self,
        output: QPlainTextEdit,
        title: str,
        profile: ActionSafetyProfile,
    ) -> None:
        output.appendPlainText(f"[safety approval: {profile.level}; acknowledgement accepted for {title}]")

    def _show_no_device(self) -> None:
        QMessageBox.warning(self, "No Device", "Connect, unlock, and trust an iPhone or iPad first.")

    def _prepare_location_for_close(self) -> bool:
        active = self._location_process is not None
        if not active and not self._location_may_be_simulated:
            return True
        message = QMessageBox(self)
        message.setIcon(QMessageBox.Icon.Warning)
        message.setWindowTitle("Location Cleanup Before Closing")
        message.setText("Location Lab may still be changing the tracked device's reported location.")
        message.setInformativeText(
            "Stop the active process and request Clear before closing. Choose Close Without Clearing only when you "
            "intentionally want the simulated location to remain or the device is unavailable."
        )
        clear_button = message.addButton("Stop, Clear && Close", QMessageBox.ButtonRole.AcceptRole)
        close_button = message.addButton("Close Without Clearing", QMessageBox.ButtonRole.DestructiveRole)
        cancel_button = message.addButton(QMessageBox.StandardButton.Cancel)
        message.setDefaultButton(cancel_button)
        message.exec()
        clicked = message.clickedButton()
        if clicked == cancel_button:
            return False
        if clicked == close_button:
            return True
        if clicked != clear_button:
            raise RuntimeError("Location cleanup dialog returned an unknown button")
        return self._clear_location_synchronously_for_close()

    def _clear_location_synchronously_for_close(self) -> bool:
        target = self._tracked_or_selected_location_target()
        if target is None:
            QMessageBox.critical(
                self,
                "Could Not Clear Location",
                "No tracked or selected device is available for the cleanup request.",
            )
            return False
        identifier, name, version = target
        process = self._location_process
        if process is not None and process.state() != QProcess.ProcessState.NotRunning:
            self._location_clear_after_stop = False
            process.terminate()
            if not process.waitForFinished(5000):
                process.kill()
                process.waitForFinished(3000)
            self._location_process = None
        try:
            arguments = clear_location_arguments(version)
            self._record_location_event(
                "clear-on-close",
                "requested",
                identifier,
                name,
                version,
                arguments,
                None,
                None,
                None,
                "The user chose Stop, Clear & Close.",
            )
        except LocationLabError as error:
            QMessageBox.critical(self, "Could Not Prepare Location Cleanup", str(error))
            return False
        last_detail = ""
        final_exit_code: int | None = None
        for attempt in (1, 2):
            try:
                completed = subprocess.run(
                    command_argv(self._pmd3, arguments),
                    env=device_environment(identifier),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    check=False,
                    timeout=30,
                )
            except (OSError, subprocess.SubprocessError) as error:
                last_detail = f"Cleanup attempt {attempt} could not run: {error}"
                final_exit_code = None
            else:
                final_exit_code = completed.returncode
                output = completed.stdout.strip()
                if output:
                    self.location_output.appendPlainText(f"\n[close cleanup attempt {attempt}]\n{output}")
                if completed.returncode == 0 and not output_indicates_failure(completed.stdout):
                    try:
                        self._record_location_event(
                            "clear-on-close",
                            "completed",
                            identifier,
                            name,
                            version,
                            arguments,
                            None,
                            None,
                            completed.returncode,
                            f"Location clear completed on attempt {attempt} before application close.",
                        )
                    except LocationLabError as error:
                        QMessageBox.critical(self, "Could Not Finalize Location Evidence", str(error))
                        return False
                    self._location_may_be_simulated = False
                    self._location_device_identifier = None
                    self.location_state_value.setText("Location clear completed before close.")
                    return True
                last_detail = (
                    f"Cleanup attempt {attempt} exited {completed.returncode}: "
                    f"{output or 'no diagnostic output'}"
                )
            if attempt == 1:
                self.location_output.appendPlainText(f"\nWarning: {last_detail}\nRetrying location clear once…")
        try:
            self._record_location_event(
                "clear-on-close",
                "failed",
                identifier,
                name,
                version,
                arguments,
                None,
                None,
                final_exit_code,
                last_detail,
            )
        except LocationLabError as error:
            self.location_output.appendPlainText(f"Evidence log error: {error}")
        QMessageBox.critical(
            self,
            "Location Cleanup Failed",
            f"The toolkit did not confirm a successful clear after two attempts.\n\n{last_detail}\n\n"
            "The application will remain open. Reconnect the tracked device and use Clear, or explicitly choose "
            "Close Without Clearing.",
        )
        return False

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self._prepare_location_for_close():
            event.ignore()
            return
        for window in tuple(self._live_log_windows):
            window.close()
            if window.isVisible():
                event.ignore()
                return
        action_running = self._action_controller.is_running()
        apps_running = self._apps_controller.is_running()
        ipa_inspection_running = self._ipa_inspection_controller.is_running()
        sideload_running = self._sideload_controller.is_running()
        backup_running = self._backup_controller.is_running()
        critical_processes = tuple(
            process
            for process in (
                self._collection_process,
                self._console_process,
                self._location_process,
            )
            if process is not None and process.state() != QProcess.ProcessState.NotRunning
        )
        if (
            action_running
            or apps_running
            or ipa_inspection_running
            or sideload_running
            or backup_running
            or critical_processes
        ):
            should_close = self._confirm(
                "Stop Active Operations?",
                "A DDI, evidence, app, backup, Location Lab, or Command Center operation is still running. "
                "Stop it, allow cleanup/finalization, and close the app?",
            )
            if not should_close:
                event.ignore()
                return
        self._scanner.stop()
        self._reconnect_timeout_timer.stop()
        self._manpage_controller.shutdown(3000, 1000)
        self._command_drift_controller.shutdown(3000, 1000)
        self._action_controller.shutdown(10000, 3000)
        self._apps_controller.shutdown(10000, 3000)
        self._ipa_inspection_controller.shutdown(10000, 3000)
        self._sideload_controller.shutdown(10000, 3000)
        self._backup_controller.shutdown(10000, 3000)
        capability_process = self._capability_process
        if capability_process is not None and capability_process.state() != QProcess.ProcessState.NotRunning:
            self._terminate_capability_children(capability_process)
            capability_process.terminate()
            if not capability_process.waitForFinished(2500):
                capability_process.kill()
                capability_process.waitForFinished(1000)
            self._capability_process = None
        for process in critical_processes:
            process.terminate()
            if not process.waitForFinished(10000):
                process.kill()
                process.waitForFinished(3000)
        event.accept()


def main() -> int:
    application = QApplication(sys.argv)
    application.setApplicationName("iOS Developer Toolkit")
    application.setOrganizationName("hideouts.io")
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
