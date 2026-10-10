from __future__ import annotations

import shlex
from dataclasses import replace
from pathlib import Path
from typing import Callable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QInputDialog,
    QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea,
    QSpinBox, QVBoxLayout, QWidget,
)

from ios_developer_toolkit.action_safety import ActionSafetyProfile, confirmation_phrase
from ios_developer_toolkit.apple_tools import (
    AppleToolError, CoreDeviceTarget, INSTRUMENTS_TEMPLATES, NativeToolOperation,
    coredevice_apps, coredevice_details, coredevice_install, coredevice_inventory,
    apple_tool, coredevice_launch, coredevice_open_url, coredevice_result, device_log_archive,
    device_sysdiagnose, export_trace_logs, instruments_recording, native_help,
    open_native_artifact, parse_coredevice_targets, preferred_ddi, update_host_ddis,
)
from ios_developer_toolkit.live_logs import LiveLogError, LiveLogWindow, LogStreamSpec
from ios_developer_toolkit.models import IOSDevice
from ios_developer_toolkit.operation_history import operation_context
from ios_developer_toolkit.qt_process import FiniteProcessController, OperationResult, finite_process_request


def tool_button(parent: QWidget, label: str, identifier: str, callback: Callable[[], None]) -> QPushButton:
    button = QPushButton(label, parent)
    button.setObjectName(identifier)
    button.clicked.connect(callback)
    return button


class NativeToolPanel(QWidget):
    """Qt boundary for explicit, reviewed Apple-tool operations and session records."""

    operation_completed = Signal(object, object)
    operation_started = Signal(object)

    def __init__(self, parent: QWidget, workspace: str) -> None:
        super().__init__(parent)
        self._workspace = workspace
        self._operation: NativeToolOperation | None = None
        self._success_handler: Callable[[bytes], None] | None = None
        self._controller = FiniteProcessController(self)
        self._controller.stdout_received.connect(self._append_output)
        self._controller.stderr_received.connect(self._append_output)
        self._controller.completed.connect(self._completed)
        self.output = QPlainTextEdit(self)
        self.output.setObjectName(workspace.replace(" ", "") + "Output")
        self.output.setReadOnly(True)
        self.output.setMaximumBlockCount(5000)
        self.output.setMaximumHeight(190)
        self.status = QLabel("Choose an explicit target and action.", self)
        self.status.setObjectName(workspace.replace(" ", "") + "Status")
        self.status.setWordWrap(True)
        self.stop_button = tool_button(self, "Stop", workspace.replace(" ", "") + "StopButton", self.stop)
        self.stop_button.setEnabled(False)

    def is_running(self) -> bool:
        return self._controller.is_running()

    def shutdown(self) -> None:
        self._controller.shutdown(1500, 1500)

    def stop(self) -> None:
        self._controller.cancel()

    def _append_output(self, payload: bytes) -> None:
        self.output.appendPlainText(payload.decode("utf-8", errors="replace"))

    def _run(self, operation: NativeToolOperation, success_handler: Callable[[bytes], None] | None) -> None:
        if self.is_running():
            raise AppleToolError("Stop or wait for the active native-tool operation before starting another")
        if getattr(self, "_demo_mode", False) and (operation.target != "LOCAL" or operation.risk in ("device-change", "high-impact")):
            raise AppleToolError("Native device operations are disabled during demo mode")
        if operation.risk in ("device-change", "high-impact"):
            profile = ActionSafetyProfile(operation.risk, operation.title, True, False)
            phrase = confirmation_phrase(profile, None if operation.target == "LOCAL" else operation.target)
            text, accepted = QInputDialog.getText(self, operation.title, f"Target: {operation.target}\n{shlex.join(operation.arguments)}\n\nType {phrase} to confirm:")
            if not accepted or text != phrase:
                return
        elif operation.risk == "host-write":
            response = QMessageBox.question(self, operation.title, "This writes private device data to your Mac.\n\n" + shlex.join(operation.arguments), QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            if response != QMessageBox.StandardButton.Yes:
                return
        self._execute_approved(operation, success_handler)

    def _execute_approved(self, operation: NativeToolOperation, success_handler: Callable[[bytes], None] | None) -> None:
        """Run an operation only after its caller has established the review boundary."""

        if self._controller.is_running():
            raise AppleToolError("A native-tool process is already running")
        if getattr(self, "_demo_mode", False) and (operation.target != "LOCAL" or operation.risk in ("device-change", "high-impact")):
            raise AppleToolError("Native device operations are disabled during demo mode")
        if operation.identifier in ("simulator-screenshot", "log-archive", "instruments-record", "instruments-export"):
            for path in operation.output_paths:
                if path.exists() or path.is_symlink():
                    raise AppleToolError(f"The artifact destination appeared during review; choose a new path: {path}")
        self._operation = operation
        self._success_handler = success_handler
        self.operation_started.emit(operation)
        self.output.clear()
        self.output.appendPlainText("$ " + shlex.join((str(operation.command.program), *operation.arguments)))
        self.status.setText(f"{operation.title} — target {operation.target}")
        self.stop_button.setEnabled(True)
        self._controller.start(finite_process_request(operation.command, operation.arguments, {"LC_ALL": "C"}, operation.timeout_seconds * 1000, 1500))
        self._update_controls()

    def _completed(self, result_object: object) -> None:
        if not isinstance(result_object, OperationResult) or self._operation is None:
            raise TypeError("A native-tool completion requires OperationResult and its original operation")
        operation = self._operation
        result = result_object
        if result.outcome == "succeeded":
            try:
                if operation.arguments[:1] == ("devicectl",) and "--json-output" in operation.arguments:
                    coredevice_result(result.stdout)
                for path in operation.output_paths:
                    if not path.exists() or path.is_symlink():
                        raise AppleToolError(f"The command exited successfully but its expected artifact is missing or is a symlink: {path}")
                    if path.suffix.casefold() in (".trace", ".logarchive", ".xcresult") and not path.is_dir():
                        raise AppleToolError(f"The expected Apple artifact must be a directory bundle: {path}")
                    if path.suffix.casefold() in (".png", ".xml") and not path.is_file():
                        raise AppleToolError(f"The expected Apple artifact must be a regular file: {path}")
                if self._success_handler is not None:
                    self._success_handler(result.stdout)
            except (AppleToolError, ValueError, OSError) as error:
                result = replace(result, outcome="failed", error_message=str(error))
        self.status.setText(f"{operation.title}: {result.outcome}; exit {result.exit_code}")
        if result.error_message:
            self.output.appendPlainText(result.error_message)
        context = operation_context(operation.title, self._workspace, operation.target, "Apple command-line tools", (), tuple(str(path) for path in operation.output_paths))
        self.operation_completed.emit(context, result)
        self._operation = None
        self._success_handler = None
        self.stop_button.setEnabled(False)
        self._update_controls()

    def _update_controls(self) -> None:
        self.stop_button.setEnabled(self._controller.is_running())

    def _perform(self, build: Callable[[], NativeToolOperation], success_handler: Callable[[bytes], None] | None) -> None:
        try:
            self._run(build(), success_handler)
        except (ValueError, OSError) as error:
            QMessageBox.critical(self, "Native Tool Request Failed", str(error))


class AppleToolsPage(NativeToolPanel):
    """Apple CoreDevice, saved logs, and Instruments workflows with explicit targets."""

    live_log_opened = Signal(object)

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent, "Xcode Tools")
        self.setObjectName("appleDeveloperToolsPage")
        self._device: IOSDevice | None = None
        self._demo_mode = False
        self._targets: tuple[CoreDeviceTarget, ...] = ()
        self._logs: list[LiveLogWindow] = []
        layout = QVBoxLayout(self)
        note = QLabel("Apple's Xcode tools provide CoreDevice USB and network targets, device .app installation, saved log archives, sysdiagnose, and Instruments recordings. Targets here are selected independently from the physical-device picker.")
        note.setWordWrap(True)
        layout.addWidget(note)
        row = QHBoxLayout()
        self.target_combo = QComboBox(self)
        self.target_combo.setObjectName("appleToolsTargetSelector")
        self.target_combo.addItem("Choose a CoreDevice target…", "")
        self.target_combo.currentIndexChanged.connect(self._update_controls)
        row.addWidget(self.target_combo, 1)
        self.refresh_button = tool_button(self, "Refresh CoreDevice Targets", "refreshCoreDeviceTargetsButton", self.refresh_targets)
        row.addWidget(self.refresh_button)
        layout.addLayout(row)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        content = QWidget(scroll)
        body = QVBoxLayout(content)
        device_group = QGroupBox("CoreDevice applications", content)
        form = QFormLayout(device_group)
        self.bundle_field = QLineEdit(device_group)
        self.bundle_field.setObjectName("appleToolsBundleIdentifier")
        form.addRow("Bundle identifier", self.bundle_field)
        self.url_field = QLineEdit(device_group)
        self.url_field.setObjectName("appleToolsURL")
        form.addRow("URL", self.url_field)
        buttons = QHBoxLayout()
        self.details_button = tool_button(self, "Details", "appleToolsDetailsButton", self.show_details)
        self.apps_button = tool_button(self, "App Inventory", "appleToolsAppsButton", self.show_apps)
        self.install_button = tool_button(self, "Install Device .app…", "appleToolsInstallAppButton", self.install_app)
        self.launch_button = tool_button(self, "Launch App…", "appleToolsLaunchAppButton", self.launch_app)
        self.url_button = tool_button(self, "Open URL…", "appleToolsOpenURLButton", self.open_url)
        for button in (self.details_button, self.apps_button, self.install_button, self.launch_button, self.url_button):
            buttons.addWidget(button)
        form.addRow(buttons)
        body.addWidget(device_group)
        capture_group = QGroupBox("Saved logs and diagnostics", content)
        capture_form = QFormLayout(capture_group)
        self.history_seconds = QSpinBox(capture_group)
        self.history_seconds.setObjectName("appleToolsLogHistorySeconds")
        self.history_seconds.setRange(1, 86400)
        self.history_seconds.setValue(300)
        capture_form.addRow("Saved history (seconds)", self.history_seconds)
        capture_buttons = QHBoxLayout()
        self.archive_button = tool_button(self, "Collect .logarchive…", "appleToolsLogArchiveButton", self.collect_archive)
        self.sysdiagnose_button = tool_button(self, "Collect Sysdiagnose…", "appleToolsSysdiagnoseButton", self.collect_sysdiagnose)
        capture_buttons.addWidget(self.archive_button)
        capture_buttons.addWidget(self.sysdiagnose_button)
        capture_form.addRow(capture_buttons)
        self.read_archive_button = tool_button(self, "Read Saved .logarchive…", "appleToolsReadArchiveButton", self.read_archive)
        self.open_artifact_button = tool_button(self, "Open in Apple App…", "appleToolsOpenArtifactButton", self.open_artifact)
        archive_buttons = QHBoxLayout()
        archive_buttons.addWidget(self.read_archive_button)
        archive_buttons.addWidget(self.open_artifact_button)
        capture_form.addRow(archive_buttons)
        body.addWidget(capture_group)
        instruments_group = QGroupBox("Instruments recordings", content)
        instruments_form = QFormLayout(instruments_group)
        self.template_combo = QComboBox(instruments_group)
        self.template_combo.setObjectName("appleToolsInstrumentsTemplate")
        self.template_combo.addItems(INSTRUMENTS_TEMPLATES)
        self.duration_seconds = QSpinBox(instruments_group)
        self.duration_seconds.setObjectName("appleToolsRecordingSeconds")
        self.duration_seconds.setRange(1, 3600)
        self.duration_seconds.setValue(30)
        instruments_form.addRow("Template", self.template_combo)
        instruments_form.addRow("Recording seconds", self.duration_seconds)
        instruments_buttons = QHBoxLayout()
        self.record_button = tool_button(self, "Record .trace…", "appleToolsRecordTraceButton", self.record_trace)
        self.export_button = tool_button(self, "Export Logging XML…", "appleToolsExportTraceLogsButton", self.export_logs)
        instruments_buttons.addWidget(self.record_button)
        instruments_buttons.addWidget(self.export_button)
        instruments_form.addRow(instruments_buttons)
        body.addWidget(instruments_group)
        tools_group = QGroupBox("Installed Apple tools and developer images", content)
        tools_form = QFormLayout(tools_group)
        ddi_buttons = QHBoxLayout()
        self.preferred_button = tool_button(self, "Preferred iOS DDI", "appleToolsPreferredDDIButton", self.show_preferred_ddi)
        self.update_ddis_button = tool_button(self, "Update Host DDIs…", "appleToolsUpdateHostDDIsButton", self.update_ddis)
        ddi_buttons.addWidget(self.preferred_button)
        ddi_buttons.addWidget(self.update_ddis_button)
        tools_form.addRow(ddi_buttons)
        self.help_tool = QComboBox(tools_group)
        self.help_tool.setObjectName("appleToolsHelpTool")
        self.help_tool.addItems(("devicectl", "simctl", "xctrace"))
        self.help_route = QLineEdit(tools_group)
        self.help_route.setObjectName("appleToolsHelpRoute")
        self.help_route.setPlaceholderText("e.g. device process launch")
        tools_form.addRow("Installed tool", self.help_tool)
        tools_form.addRow("Help command route", self.help_route)
        self.help_button = tool_button(self, "Read Installed Help", "appleToolsReadHelpButton", self.read_help)
        tools_form.addRow(self.help_button)
        body.addWidget(tools_group)
        body.addStretch(1)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        layout.addWidget(self.status)
        layout.addWidget(self.output)
        layout.addWidget(self.stop_button)
        self._update_controls()

    def set_device(self, device: IOSDevice | None, demo_mode: bool) -> None:
        self._device = device
        self._demo_mode = demo_mode
        self._populate_targets()
        self._update_controls()

    def _target(self) -> str:
        if self._demo_mode:
            raise AppleToolError("Apple device operations are disabled during demo mode")
        value = self.target_combo.currentData()
        if not isinstance(value, str) or not value:
            raise AppleToolError("Choose an explicit CoreDevice target after refreshing the target inventory")
        return value

    def _update_controls(self) -> None:
        super()._update_controls()
        if not hasattr(self, "target_combo") or not hasattr(self, "help_button"):
            return
        busy = self.is_running()
        selected = bool(self.target_combo.currentData()) and not self._demo_mode
        self.target_combo.setEnabled(not busy)
        self.refresh_button.setEnabled(not busy and not self._demo_mode)
        for button in (self.details_button, self.apps_button, self.install_button, self.launch_button, self.url_button, self.archive_button, self.sysdiagnose_button, self.record_button):
            button.setEnabled(selected and not busy)
        for button in (self.export_button, self.preferred_button, self.help_button, self.read_archive_button, self.open_artifact_button):
            button.setEnabled(not busy)
        self.update_ddis_button.setEnabled(not busy and not self._demo_mode)

    def refresh_targets(self) -> None:
        self._perform(coredevice_inventory, self._load_targets)

    def _load_targets(self, payload: bytes) -> None:
        self._targets = parse_coredevice_targets(payload)
        self._populate_targets()

    def _populate_targets(self) -> None:
        selected = self.target_combo.currentData()
        self.target_combo.clear()
        self.target_combo.addItem("Choose a CoreDevice target…", "")
        if self._device is not None and not self._demo_mode and not any(target.udid == self._device.identifier for target in self._targets):
            self.target_combo.addItem(f"Physical picker: {self._device.display_name()}", self._device.identifier)
        for target in self._targets:
            self.target_combo.addItem(f"{target.name} — {target.version} ({target.transport}; tunnel {target.tunnel_state})", target.identifier)
        index = self.target_combo.findData(selected)
        if index >= 0:
            self.target_combo.setCurrentIndex(index)

    def show_details(self) -> None:
        self._perform(lambda: coredevice_details(self._target()), None)

    def show_apps(self) -> None:
        self._perform(lambda: coredevice_apps(self._target()), None)

    def launch_app(self) -> None:
        self._perform(lambda: coredevice_launch(self._target(), self.bundle_field.text()), None)

    def open_url(self) -> None:
        self._perform(lambda: coredevice_open_url(self._target(), self.url_field.text()), None)

    def install_app(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Choose an iPhoneOS .app bundle", str(Path.home()), QFileDialog.Option.DontUseNativeDialog)
        if path:
            self._perform(lambda: coredevice_install(self._target(), Path(path)), None)

    def collect_archive(self) -> None:
        selected = self.target_combo.currentData()
        target = next((target for target in self._targets if target.identifier == selected), None)
        udid = target.udid if target is not None else self._device.identifier if self._device is not None and self._device.identifier == selected else None
        if udid is None:
            QMessageBox.critical(self, "UDID Unavailable", "The selected CoreDevice record must report a physical UDID before native log collection is available")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save device log archive", str(Path.home() / "Documents" / "device.logarchive"), "Log archive (*.logarchive)")
        if path:
            self._perform(lambda: device_log_archive(udid, self.history_seconds.value(), Path(path)), None)

    def collect_sysdiagnose(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Choose sysdiagnose destination")
        if path:
            self._perform(lambda: device_sysdiagnose(self._target(), Path(path)), None)

    def record_trace(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save Instruments recording", str(Path.home() / "Documents" / "recording.trace"), "Instruments trace (*.trace)")
        if path:
            self._perform(lambda: instruments_recording(self._recording_target(), self.template_combo.currentText(), self.duration_seconds.value(), Path(path)), None)

    def _recording_target(self) -> str:
        selected = self._target()
        target = next((target for target in self._targets if target.identifier == selected), None)
        if target is not None:
            if target.udid is None:
                raise AppleToolError("The selected CoreDevice record must report its physical UDID for Instruments recording")
            return target.udid
        if self._device is not None and self._device.identifier == selected:
            return self._device.identifier
        raise AppleToolError("The selected target no longer has a verified physical UDID; refresh the inventory")

    def read_archive(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Choose a saved .logarchive", str(Path.home()), QFileDialog.Option.DontUseNativeDialog)
        if not path:
            return
        try:
            archive = Path(path).resolve()
            if archive.suffix.casefold() != ".logarchive" or not archive.is_dir():
                raise AppleToolError("Choose an existing .logarchive directory")
            if QMessageBox.question(self, "Read Saved Log Archive", "The saved logs and local raw spool may contain private device and app data. Open the capture and findings viewer?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return
            specification = LogStreamSpec("archive-unified", "Saved Unified Log Archive", "Read a locally preserved Apple log archive. The new raw NDJSON spool is a derivative of the archive.", ("show", "--archive", str(archive), "--style", "ndjson", "--info", "--debug"), False, True)
            window = LiveLogWindow(apple_tool("log"), specification, "LOCAL-ARCHIVE", archive.name, {"LC_ALL": "C"}, Path(__file__).parent / "assets" / "iosdevtoolkit.png")
            self._logs.append(window)
            window.closed.connect(self._log_closed)
            self.live_log_opened.emit(window)
            window.show()
        except (ValueError, OSError, LiveLogError) as error:
            QMessageBox.critical(self, "Saved Log Archive Read Failed", str(error))

    def _log_closed(self, window_object: object) -> None:
        if not isinstance(window_object, LiveLogWindow):
            raise TypeError("Apple log-window close requires LiveLogWindow")
        self._logs.remove(window_object)

    def shutdown(self) -> None:
        super().shutdown()
        for window in tuple(self._logs):
            window.stop_capture()

    def open_artifact(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Choose a .trace, .logarchive, or .xcresult", str(Path.home()), QFileDialog.Option.DontUseNativeDialog)
        if path:
            self._perform(lambda: open_native_artifact(Path(path)), None)

    def export_logs(self) -> None:
        trace = QFileDialog.getExistingDirectory(self, "Choose a Logging .trace recording", str(Path.home()), QFileDialog.Option.DontUseNativeDialog)
        if not trace:
            return
        output, _ = QFileDialog.getSaveFileName(self, "Save recorded OSLog XML", str(Path.home() / "Documents" / "recorded-oslog.xml"), "XML (*.xml)")
        if output:
            self._perform(lambda: export_trace_logs(Path(trace), Path(output)), None)

    def show_preferred_ddi(self) -> None:
        self._perform(preferred_ddi, None)

    def update_ddis(self) -> None:
        self._perform(update_host_ddis, None)

    def read_help(self) -> None:
        self._perform(lambda: native_help(self.help_tool.currentText(), self.help_route.text()), None)
