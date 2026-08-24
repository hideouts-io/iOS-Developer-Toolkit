from __future__ import annotations

import json
import os
import shlex
import sys
from pathlib import Path
from typing import Mapping

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, QUrl, Signal
from PySide6.QtGui import QCloseEvent, QDesktopServices, QFont, QTextCursor
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
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ios_developer_toolkit import APP_VERSION
from ios_developer_toolkit.catalog import is_potentially_mutating
from ios_developer_toolkit.models import DeviceDataError, IOSDevice, parse_devices_json
from ios_developer_toolkit.runtime import device_environment, pymobiledevice3_executable
from ios_developer_toolkit.validation import output_indicates_failure


XCODE_CANDIDATE_DDI = Path("/Library/Developer/CoreDevice/CandidateDDIs/iOS_DDI.dmg")
DEVELOPER_DISK_IMAGE_REPOSITORY = "https://github.com/doronz88/DeveloperDiskImage"


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
        self.setWindowTitle(f"iOS Developer Toolkit {APP_VERSION}")
        self.resize(1120, 800)
        self._pmd3 = pymobiledevice3_executable()
        self._devices: tuple[IOSDevice, ...] = ()
        self._guided_udids: set[str] = set()
        self._action_process: QProcess | None = None
        self._action_context = ""
        self._action_buffer = bytearray()
        self._collection_process: QProcess | None = None
        self._console_process: QProcess | None = None
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
        title_block = QVBoxLayout()
        title = QLabel("iOS Developer Toolkit")
        title.setObjectName("appTitle")
        title.setFont(QFont(title.font().family(), 24, QFont.Weight.Bold))
        subtitle = QLabel("Developer image mounting and evidence-oriented device collection")
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

        tabs = QTabWidget()
        tabs.setObjectName("mainTabs")
        tabs.addTab(self._build_overview_tab(), "Device & DDI")
        tabs.addTab(self._build_collection_tab(), "Collect Evidence")
        tabs.addTab(self._build_console_tab(), "pymobiledevice3 Console")
        tabs.addTab(self._build_safety_tab(), "Scope & Safety")
        root_layout.addWidget(tabs, 1)
        self.setCentralWidget(root)
        self._apply_style()

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

    def _build_console_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        explanation = QLabel(
            "Enter pymobiledevice3 arguments only. Commands are executed directly without a shell. "
            "Unknown or state-changing commands require confirmation."
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)

        preset_row = QHBoxLayout()
        self.console_presets = QComboBox()
        self.console_presets.setObjectName("consolePresets")
        presets = (
            ("Device information", "developer dvt device-information"),
            ("DVT root listing", "developer dvt ls /"),
            ("Detailed process snapshot", "developer dvt sysmon process single"),
            ("Unified Logging stream", "developer dvt oslog"),
            ("Classic syslog", "syslog live"),
            ("Installed apps", "apps list"),
            ("Crash reports", "crash ls"),
            ("Mounted images", "mounter list"),
            ("Network capture", "pcap --out device-console-capture.pcap"),
        )
        for title, command in presets:
            self.console_presets.addItem(title, command)
        preset_row.addWidget(self.console_presets, 1)
        use_preset_button = QPushButton("Use Preset")
        use_preset_button.setObjectName("useConsolePresetButton")
        use_preset_button.clicked.connect(self.use_console_preset)
        preset_row.addWidget(use_preset_button)
        layout.addLayout(preset_row)

        command_row = QHBoxLayout()
        self.console_input = QLineEdit()
        self.console_input.setObjectName("consoleCommandInput")
        self.console_input.setPlaceholderText("developer dvt device-information")
        self.console_input.returnPressed.connect(self.run_console_command)
        command_row.addWidget(self.console_input, 1)
        self.console_run_button = QPushButton("Run")
        self.console_run_button.setObjectName("runConsoleCommandButton")
        self.console_run_button.clicked.connect(self.run_console_command)
        command_row.addWidget(self.console_run_button)
        self.console_stop_button = QPushButton("Stop")
        self.console_stop_button.setObjectName("stopConsoleCommandButton")
        self.console_stop_button.setEnabled(False)
        self.console_stop_button.clicked.connect(self.stop_console_command)
        command_row.addWidget(self.console_stop_button)
        layout.addLayout(command_row)

        self.console_output = QPlainTextEdit()
        self.console_output.setObjectName("consoleOutput")
        self.console_output.setReadOnly(True)
        self.console_output.setMaximumBlockCount(10000)
        layout.addWidget(self.console_output, 1)
        return tab

    def _build_safety_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        browser = QTextBrowser()
        browser.setOpenExternalLinks(True)
        browser.setHtml(
            f"""
            <h2>What this app does</h2>
            <p>It recognizes paired USB devices, explains Developer Mode, mounts an Apple-authorized developer image,
            and orchestrates read-oriented <code>pymobiledevice3</code> collection with an auditable case manifest.</p>
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
            QLineEdit, QComboBox, QSpinBox, QPlainTextEdit, QTextBrowser { border: 1px solid #cfd5df; border-radius: 7px; background: white; padding: 5px; }
            QTabWidget::pane { border: 1px solid #d9dee8; border-radius: 8px; background: #fbfcfe; }
            QTabBar::tab { padding: 9px 16px; }
            #connectionBanner { background: #e9f2ff; border: 1px solid #afcff8; border-radius: 8px; padding: 10px; }
            #collectionPrivacyWarning { background: #fff5df; border: 1px solid #e7c36a; border-radius: 8px; padding: 10px; }
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
        enabled = device is not None
        self.mount_button.setEnabled(enabled)
        self.remove_button.setEnabled(enabled)
        self.start_collection_button.setEnabled(enabled and self._collection_process is None)
        self.console_run_button.setEnabled(enabled and self._console_process is None)
        if device is None:
            self.device_name_value.setText("No device")
            self.device_version_value.setText("—")
            self.device_model_value.setText("—")
            self.device_udid_value.setText("—")
            return
        self.device_name_value.setText(device.name)
        self.device_version_value.setText(f"{device.product_version} / {device.build_version}")
        self.device_model_value.setText(device.product_type)
        self.device_udid_value.setText(device.identifier)

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

    def use_console_preset(self) -> None:
        command = self.console_presets.currentData()
        if isinstance(command, str):
            self.console_input.setText(command)

    def run_console_command(self) -> None:
        device = self.selected_device()
        if device is None:
            self._show_no_device()
            return
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
        if is_potentially_mutating(parsed):
            warning = (
                "This command is not in the read-only allowlist and may change device or host state:\n\n"
                f"pymobiledevice3 {' '.join(parsed)}\n\nRun it anyway?"
            )
            if not self._confirm("Potentially State-Changing Command", warning):
                return
        self.console_output.appendPlainText(f"\n$ pymobiledevice3 {' '.join(parsed)}\n")
        process = QProcess(self)
        process.setProgram(str(self._pmd3))
        process.setArguments(list(parsed))
        process.setWorkingDirectory(str(Path.home()))
        process.setProcessEnvironment(qprocess_environment(device_environment(device.identifier)))
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.readyReadStandardOutput.connect(self._read_console_output)
        process.finished.connect(self._console_finished)
        process.errorOccurred.connect(self._console_error)
        self._console_process = process
        self.console_run_button.setEnabled(False)
        self.console_stop_button.setEnabled(True)
        process.start()

    def _read_console_output(self) -> None:
        if self._console_process is not None:
            text = bytes(self._console_process.readAllStandardOutput()).decode("utf-8", errors="replace")
            self.console_output.moveCursor(QTextCursor.MoveOperation.End)
            self.console_output.insertPlainText(text)

    def _console_finished(self, exit_code: int, exit_status: QProcess.ExitStatus) -> None:
        del exit_status
        self.console_output.appendPlainText(f"\n[finished: exit {exit_code}]")
        self._console_process = None
        self.console_run_button.setEnabled(self.selected_device() is not None)
        self.console_stop_button.setEnabled(False)

    def _console_error(self, process_error: QProcess.ProcessError) -> None:
        del process_error
        if self._console_process is not None:
            self.console_output.appendPlainText(f"\nProcess error: {self._console_process.errorString()}")


    def stop_console_command(self) -> None:
        if self._console_process is not None:
            self._console_process.terminate()

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
            for process in (self._action_process, self._collection_process)
            if process is not None and process.state() != QProcess.ProcessState.NotRunning
        )
        if critical_processes:
            should_close = self._confirm(
                "Stop Active Operations?",
                "A DDI or evidence operation is still running. Stop it, allow cleanup/finalization, and close the app?",
            )
            if not should_close:
                event.ignore()
                return
        for process in critical_processes:
            process.terminate()
            if not process.waitForFinished(10000):
                process.kill()
                process.waitForFinished(3000)
        for process in (self._console_process,):
            if process is not None and process.state() != QProcess.ProcessState.NotRunning:
                process.terminate()
        event.accept()


def main() -> int:
    application = QApplication(sys.argv)
    application.setApplicationName("iOS Developer Toolkit")
    application.setOrganizationName("Local Security Tools")
    window = MainWindow()
    window.show()
    window.raise_()
    window.activateWindow()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
