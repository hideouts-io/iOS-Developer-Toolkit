from __future__ import annotations

import math
import time
from pathlib import Path

from PySide6.QtCore import Signal, QTimer, Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMessageBox, QScrollArea,
    QSpinBox, QVBoxLayout, QWidget,
)

from ios_developer_toolkit.action_safety import confirmation_phrase, guided_action_safety
from ios_developer_toolkit.apple_tools import AppleToolError, INSTRUMENTS_TEMPLATES, NativeToolOperation, apple_tool
from ios_developer_toolkit.apple_tools_page import NativeToolPanel, tool_button
from ios_developer_toolkit.live_logs import LiveLogError, LiveLogWindow
from ios_developer_toolkit.location_lab import validate_coordinates
from ios_developer_toolkit.operation_history import OperationContext
from ios_developer_toolkit.qt_process import OperationResult
from ios_developer_toolkit.simulator_tools import (
    SimulatorGPXRoute, SimulatorTarget, decode_simulator_apps, fixed_gpx_offsets,
    load_simulator_gpx, parse_simulators, recorded_gpx_offsets, simulator_apps,
    simulator_boot, simulator_clear_location, simulator_dark, simulator_erase,
    simulator_install, simulator_inventory, simulator_launch, simulator_light,
    simulator_log_spec, simulator_open_url, simulator_recording, simulator_screenshot,
    simulator_set_location, simulator_show, simulator_shutdown, simulator_terminate,
    simulator_uninstall,
)


class SimulatorToolsPage(NativeToolPanel):
    """Explicit simulator control, using the shared process and log-viewer boundaries."""

    live_log_opened = Signal(object)
    close_requested = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent, "Simulators")
        self.setObjectName("simulatorToolsPage")
        self._demo_mode = False
        self._targets: tuple[SimulatorTarget, ...] = ()
        self._logs: list[LiveLogWindow] = []
        self._route: SimulatorGPXRoute | None = None
        self._gpx_offsets: tuple[float, ...] = ()
        self._gpx_target = ""
        self._gpx_index = 0
        self._gpx_started = 0.0
        self._gpx_last_sent = -1.0
        self._gpx_running = False
        self._clear_after_stop = False
        self._location_target = ""
        self._close_after_clear = False
        self._gpx_timer = QTimer(self)
        self._gpx_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._gpx_timer.setSingleShot(True)
        self._gpx_timer.timeout.connect(self._play_point)
        self.operation_completed.connect(self._step_completed)
        self.operation_started.connect(self._operation_started)
        layout = QVBoxLayout(self)
        note = QLabel("Select a simulator UUID explicitly. Simulators use Apple's simctl tools and remain separate from connected physical devices. GPX playback preserves recorded timing or uses a selected fixed interval; always clear location after testing.")
        note.setWordWrap(True)
        layout.addWidget(note)
        target_row = QHBoxLayout()
        self.target_combo = QComboBox(self)
        self.target_combo.setObjectName("simulatorTargetSelector")
        self.target_combo.addItem("Choose a simulator…", "")
        self.target_combo.currentIndexChanged.connect(self._target_changed)
        target_row.addWidget(self.target_combo, 1)
        self.refresh_button = tool_button(self, "Refresh Simulators", "refreshSimulatorsButton", self.refresh_targets)
        target_row.addWidget(self.refresh_button)
        layout.addLayout(target_row)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        content = QWidget(scroll)
        body = QVBoxLayout(content)
        lifecycle = QHBoxLayout()
        self.boot_button = tool_button(self, "Boot…", "simulatorBootButton", self.boot)
        self.shutdown_button = tool_button(self, "Shut Down…", "simulatorShutdownButton", self.shut_down)
        self.show_button = tool_button(self, "Show Simulator…", "simulatorShowButton", self.show_simulator)
        self.erase_button = tool_button(self, "Erase Contents…", "simulatorEraseButton", self.erase)
        self.dark_button = tool_button(self, "Dark Appearance…", "simulatorDarkButton", self.dark)
        self.light_button = tool_button(self, "Light Appearance…", "simulatorLightButton", self.light)
        for button in (self.boot_button, self.shutdown_button, self.show_button, self.erase_button, self.dark_button, self.light_button):
            lifecycle.addWidget(button)
        body.addLayout(lifecycle)
        apps = QGroupBox("Simulator applications", content)
        apps_form = QFormLayout(apps)
        self.app_combo = QComboBox(apps)
        self.app_combo.setObjectName("simulatorInstalledApps")
        self.app_combo.setEditable(True)
        apps_form.addRow("Bundle identifier", self.app_combo)
        app_buttons = QHBoxLayout()
        self.apps_button = tool_button(self, "Load Apps", "simulatorLoadAppsButton", self.load_apps)
        self.install_button = tool_button(self, "Install Simulator .app…", "simulatorInstallAppButton", self.install_app)
        self.launch_button = tool_button(self, "Launch…", "simulatorLaunchAppButton", self.launch)
        self.terminate_button = tool_button(self, "Terminate…", "simulatorTerminateAppButton", self.terminate)
        self.remove_button = tool_button(self, "Remove App and Data…", "simulatorRemoveAppButton", self.remove_app)
        for button in (self.apps_button, self.install_button, self.launch_button, self.terminate_button, self.remove_button):
            app_buttons.addWidget(button)
        apps_form.addRow(app_buttons)
        self.url_field = QLineEdit(apps)
        self.url_field.setObjectName("simulatorURL")
        apps_form.addRow("URL", self.url_field)
        self.url_button = tool_button(self, "Open URL…", "simulatorOpenURLButton", self.open_url)
        apps_form.addRow(self.url_button)
        body.addWidget(apps)
        location = QGroupBox("Simulator location and GPX playback", content)
        location_form = QFormLayout(location)
        self.latitude = QLineEdit(location)
        self.latitude.setObjectName("simulatorLatitude")
        self.longitude = QLineEdit(location)
        self.longitude.setObjectName("simulatorLongitude")
        location_form.addRow("Latitude", self.latitude)
        location_form.addRow("Longitude", self.longitude)
        self.set_location_button = tool_button(self, "Set Location…", "simulatorSetLocationButton", self.set_location)
        self.clear_location_button = tool_button(self, "Clear Tracked Location…", "simulatorClearLocationButton", self.clear_location)
        location_buttons = QHBoxLayout()
        location_buttons.addWidget(self.set_location_button)
        location_buttons.addWidget(self.clear_location_button)
        location_form.addRow(location_buttons)
        self.route_label = QLabel("Choose a local GPX track; every coordinate and timestamp is validated.", location)
        self.route_label.setObjectName("simulatorGPXSummary")
        self.route_label.setWordWrap(True)
        location_form.addRow(self.route_label)
        self.choose_gpx_button = tool_button(self, "Choose GPX…", "simulatorChooseGPXButton", self.choose_gpx)
        location_form.addRow(self.choose_gpx_button)
        self.recorded_timing = QCheckBox("Use recorded timestamps", location)
        self.recorded_timing.setObjectName("simulatorGPXRecordedTiming")
        self.recorded_timing.setChecked(True)
        self.interval_seconds = QDoubleSpinBox(location)
        self.interval_seconds.setObjectName("simulatorGPXIntervalSeconds")
        self.interval_seconds.setRange(0.5, 3600)
        self.interval_seconds.setValue(1)
        location_form.addRow(self.recorded_timing)
        location_form.addRow("Fixed interval seconds", self.interval_seconds)
        self.play_gpx_button = tool_button(self, "Play Validated GPX…", "simulatorPlayGPXButton", self.play_gpx)
        self.stop_clear_button = tool_button(self, "Stop and Clear Location", "simulatorStopAndClearButton", self.stop_and_clear)
        route_buttons = QHBoxLayout()
        route_buttons.addWidget(self.play_gpx_button)
        route_buttons.addWidget(self.stop_clear_button)
        location_form.addRow(route_buttons)
        self.location_status = QLabel("No simulator location is tracked. External changes cannot be detected.", location)
        self.location_status.setObjectName("simulatorLocationState")
        self.location_status.setWordWrap(True)
        location_form.addRow(self.location_status)
        body.addWidget(location)
        capture = QGroupBox("Capture and Instruments", content)
        capture_form = QFormLayout(capture)
        self.screenshot_button = tool_button(self, "Capture PNG…", "simulatorScreenshotButton", self.screenshot)
        self.log_button = tool_button(self, "Open Unified Log Viewer…", "simulatorLogViewerButton", self.open_logs)
        capture_row = QHBoxLayout()
        capture_row.addWidget(self.screenshot_button)
        capture_row.addWidget(self.log_button)
        capture_form.addRow(capture_row)
        self.template_combo = QComboBox(capture)
        self.template_combo.setObjectName("simulatorInstrumentsTemplate")
        self.template_combo.addItems(INSTRUMENTS_TEMPLATES)
        self.duration_seconds = QSpinBox(capture)
        self.duration_seconds.setObjectName("simulatorRecordingSeconds")
        self.duration_seconds.setRange(1, 3600)
        self.duration_seconds.setValue(30)
        capture_form.addRow("Template", self.template_combo)
        capture_form.addRow("Recording seconds", self.duration_seconds)
        self.record_button = tool_button(self, "Record .trace…", "simulatorRecordTraceButton", self.record_trace)
        capture_form.addRow(self.record_button)
        body.addWidget(capture)
        body.addStretch(1)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        layout.addWidget(self.status)
        layout.addWidget(self.output)
        layout.addWidget(self.stop_button)
        self._update_controls()

    def set_demo_mode(self, demo_mode: bool) -> None:
        self._demo_mode = demo_mode
        self._update_controls()

    def is_running(self) -> bool:
        return self._controller.is_running() or getattr(self, "_gpx_running", False)

    def shutdown(self) -> None:
        self._gpx_timer.stop()
        self._gpx_running = False
        self._clear_after_stop = False
        super().shutdown()
        for window in tuple(self._logs):
            window.stop_capture()

    def prepare_close(self) -> bool:
        """Address tracked simulator location explicitly before the host closes this page."""

        if not self._location_target:
            return True
        if self._close_after_clear:
            return False
        message = QMessageBox(self)
        message.setObjectName("simulatorLocationCloseReview")
        message.setWindowTitle("Simulator Location May Remain Active")
        message.setIcon(QMessageBox.Icon.Warning)
        message.setText(f"A simulated location may remain active on {self._location_target}.")
        message.setInformativeText("Clear it before closing, explicitly close leaving the simulation, or keep the toolkit open.")
        clear = message.addButton("Clear Location and Close", QMessageBox.ButtonRole.AcceptRole)
        leave = message.addButton("Close Leaving Simulation", QMessageBox.ButtonRole.DestructiveRole)
        cancel = message.addButton(QMessageBox.StandardButton.Cancel)
        clear.setObjectName("simulatorClearAndCloseButton")
        leave.setObjectName("simulatorLeaveLocationAndCloseButton")
        cancel.setObjectName("simulatorCancelCloseButton")
        message.setDefaultButton(cancel)
        message.exec()
        clicked = message.clickedButton()
        if clicked == cancel or clicked is None:
            return False
        if clicked == leave:
            return True
        if clicked != clear:
            raise AppleToolError("Simulator location close review returned an unrecognized button")
        self._close_after_clear = True
        if self._gpx_running:
            self.stop_and_clear()
        else:
            self.clear_location()
        if not self._controller.is_running() and not self._clear_after_stop:
            self._close_after_clear = False
        return False

    def stop(self) -> None:
        if self._gpx_running:
            self._gpx_timer.stop()
            self._gpx_running = False
            self.location_status.setText("GPX stopped. The simulated location remains; explicitly clear it.")
        super().stop()
        self._update_controls()

    def _target(self) -> str:
        if self._demo_mode:
            raise AppleToolError("Simulator actions are disabled during demo mode")
        selected = self.target_combo.currentData()
        target = next((target for target in self._targets if target.identifier == selected), None)
        if target is None or not target.available:
            raise AppleToolError("Choose an available simulator after refreshing the inventory")
        return target.identifier

    def _target_changed(self) -> None:
        if hasattr(self, "app_combo"):
            self.app_combo.clear()
        self._update_controls()

    def _update_controls(self) -> None:
        super()._update_controls()
        if not hasattr(self, "record_button"):
            return
        busy = self.is_running()
        selected = self.target_combo.currentData()
        target = next((target for target in self._targets if target.identifier == selected), None)
        available = target is not None and target.available and not self._demo_mode
        self.target_combo.setEnabled(not busy)
        self.refresh_button.setEnabled(not busy and not self._demo_mode)
        for button in (self.boot_button, self.shutdown_button, self.show_button, self.erase_button, self.dark_button, self.light_button, self.apps_button, self.install_button, self.launch_button, self.terminate_button, self.remove_button, self.url_button, self.set_location_button, self.screenshot_button, self.log_button, self.record_button):
            button.setEnabled(available and not busy)
        self.clear_location_button.setEnabled(bool(self._location_target or available) and not busy and not self._demo_mode)
        self.choose_gpx_button.setEnabled(not busy)
        self.play_gpx_button.setEnabled(available and not busy and self._route is not None)
        self.stop_clear_button.setEnabled(self._gpx_running and not self._clear_after_stop)
        self.stop_button.setEnabled(busy)

    def refresh_targets(self) -> None:
        self._perform(simulator_inventory, self._load_targets)

    def _load_targets(self, payload: bytes) -> None:
        targets = parse_simulators(payload)
        selected = self.target_combo.currentData()
        self._targets = targets
        self.target_combo.clear()
        self.target_combo.addItem("Choose a simulator…", "")
        for target in targets:
            suffix = "" if target.available else "; unavailable"
            self.target_combo.addItem(f"{target.name} — {target.runtime_name} ({target.state}{suffix})", target.identifier)
        index = self.target_combo.findData(selected)
        if index >= 0:
            self.target_combo.setCurrentIndex(index)

    def boot(self) -> None:
        self._perform(lambda: simulator_boot(self._target()), None)

    def shut_down(self) -> None:
        self._perform(lambda: simulator_shutdown(self._target()), None)

    def show_simulator(self) -> None:
        self._perform(lambda: simulator_show(self._target()), None)

    def erase(self) -> None:
        self._perform(lambda: simulator_erase(self._target()), None)

    def dark(self) -> None:
        self._perform(lambda: simulator_dark(self._target()), None)

    def light(self) -> None:
        self._perform(lambda: simulator_light(self._target()), None)

    def load_apps(self) -> None:
        self._perform(lambda: simulator_apps(self._target()), self._load_apps)

    def _load_apps(self, payload: bytes) -> None:
        apps = decode_simulator_apps(payload)
        self.app_combo.clear()
        for app in apps:
            self.app_combo.addItem(f"{app.name} ({app.bundle_identifier})", app.bundle_identifier)

    def _bundle(self) -> str:
        selected = self.app_combo.currentData()
        if isinstance(selected, str) and self.app_combo.currentText() == self.app_combo.itemText(self.app_combo.currentIndex()):
            return selected
        return self.app_combo.currentText()

    def launch(self) -> None:
        self._perform(lambda: simulator_launch(self._target(), self._bundle()), None)

    def terminate(self) -> None:
        self._perform(lambda: simulator_terminate(self._target(), self._bundle()), None)

    def remove_app(self) -> None:
        self._perform(lambda: simulator_uninstall(self._target(), self._bundle()), None)

    def install_app(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Choose an iPhoneSimulator .app bundle", str(Path.home()), QFileDialog.Option.DontUseNativeDialog)
        if path:
            self._perform(lambda: simulator_install(self._target(), Path(path)), None)

    def open_url(self) -> None:
        self._perform(lambda: simulator_open_url(self._target(), self.url_field.text()), None)

    def screenshot(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save simulator screenshot", str(Path.home() / "Documents" / "simulator.png"), "PNG (*.png)")
        if path:
            self._perform(lambda: simulator_screenshot(self._target(), Path(path)), None)

    def record_trace(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save simulator Instruments recording", str(Path.home() / "Documents" / "simulator.trace"), "Instruments trace (*.trace)")
        if path:
            self._perform(lambda: simulator_recording(self._target(), self.template_combo.currentText(), self.duration_seconds.value(), Path(path)), None)

    def set_location(self) -> None:
        self._perform(self._set_location_operation, self._location_set)

    def _set_location_operation(self) -> NativeToolOperation:
        target = self._target()
        if self._location_target and self._location_target != target:
            raise AppleToolError("Clear the tracked location on the original simulator before setting a location on another simulator")
        return simulator_set_location(target, validate_coordinates(self.latitude.text(), self.longitude.text()))

    def _location_set(self, payload: bytes) -> None:
        del payload
        if self._operation is None:
            raise AppleToolError("Simulator location completed without its original explicit target")
        self._location_target = self._operation.target
        self.location_status.setText(f"A simulated location may be active on {self._location_target}. Clear it after testing.")

    def _operation_started(self, operation_object: object) -> None:
        if not isinstance(operation_object, NativeToolOperation):
            raise TypeError("Simulator operation start requires NativeToolOperation")
        if operation_object.identifier == "simulator-set-location":
            self._location_target = operation_object.target
            self.location_status.setText(f"Location set requested on {self._location_target}. Clear it after testing, including if this operation fails or is stopped.")

    def clear_location(self) -> None:
        self._perform(lambda: simulator_clear_location(self._location_target or self._target()), self._location_cleared)

    def _location_cleared(self, payload: bytes) -> None:
        del payload
        self._location_target = ""
        self.location_status.setText("The simulator accepted location clear. Individual app location state is not independently verified.")
        if self._close_after_clear:
            self._close_after_clear = False
            QTimer.singleShot(0, self.close_requested.emit)

    def choose_gpx(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose simulator GPX route", str(Path.home()), "GPX (*.gpx)")
        if not path:
            return
        try:
            route = load_simulator_gpx(Path(path))
        except (ValueError, OSError) as error:
            QMessageBox.critical(self, "Invalid Simulator GPX", str(error))
            return
        self._route = route
        self.route_label.setText(f"{route.path.name}: {len(route.points)} points; SHA-256 {route.sha256}")
        self._update_controls()

    def play_gpx(self) -> None:
        try:
            target = self._target()
            route = self._route
            if route is None or self.is_running():
                raise AppleToolError("Choose a validated GPX route and finish the active operation first")
            if self._location_target:
                raise AppleToolError("Clear the tracked simulated location before starting another GPX route")
            offsets = recorded_gpx_offsets(route.points) if self.recorded_timing.isChecked() else fixed_gpx_offsets(route.points, self.interval_seconds.value())
            phrase = confirmation_phrase(guided_action_safety("device-change"), target)
            text, accepted = QInputDialog.getText(self, "Play Simulator GPX", f"Target: {target}\n{len(route.points)} points; duration {offsets[-1]:.1f}s\nSHA-256: {route.sha256}\n\nType {phrase} to confirm playback. Stop and Clear will restore normal location.")
            if not accepted or text != phrase:
                return
            self._gpx_target = target
            self._gpx_offsets = offsets
            self._gpx_index = 0
            self._gpx_started = time.monotonic()
            self._gpx_last_sent = self._gpx_started - 0.5
            self._gpx_running = True
            self._clear_after_stop = False
            self._location_target = target
            self._update_controls()
            self._schedule_point()
        except (ValueError, OSError) as error:
            QMessageBox.critical(self, "Simulator GPX Playback Failed", str(error))

    def _schedule_point(self) -> None:
        due = max(self._gpx_started + self._gpx_offsets[self._gpx_index], self._gpx_last_sent + 0.5)
        delay = max(0, math.ceil((due - time.monotonic()) * 1000))
        self.location_status.setText(f"GPX point {self._gpx_index + 1}/{len(self._gpx_offsets)} scheduled on {self._gpx_target}.")
        self._gpx_timer.start(delay)

    def _play_point(self) -> None:
        if not self._gpx_running or self._route is None:
            return
        try:
            operation = simulator_set_location(self._gpx_target, self._route.points[self._gpx_index].coordinates)
            self._gpx_last_sent = time.monotonic()
            self._execute_approved(operation, self._location_set)
        except (ValueError, OSError) as error:
            self._gpx_running = False
            self.location_status.setText(f"GPX playback could not continue: {error}. A simulated location may remain; explicitly clear it.")
            self._update_controls()

    def _step_completed(self, context_object: object, result_object: object) -> None:
        if not isinstance(context_object, OperationContext) or not isinstance(result_object, OperationResult):
            raise TypeError("Simulator completion requires OperationContext and OperationResult")
        if self._close_after_clear and result_object.outcome != "succeeded" and not self._clear_after_stop:
            self._close_after_clear = False
        if self._clear_after_stop:
            self._clear_after_stop = False
            QTimer.singleShot(0, self._clear_after_playback)
            return
        if not self._gpx_running:
            return
        if result_object.outcome != "succeeded":
            self._gpx_running = False
            self.location_status.setText("GPX playback failed. A simulated location may remain; explicitly clear it.")
            QTimer.singleShot(0, self._update_controls)
            return
        self._gpx_index += 1
        if self._gpx_index == len(self._gpx_offsets):
            self._gpx_running = False
            self.location_status.setText("GPX playback finished. The last coordinate remains; explicitly clear location.")
            QTimer.singleShot(0, self._update_controls)
        else:
            QTimer.singleShot(0, self._schedule_point)

    def stop_and_clear(self) -> None:
        if not self._gpx_running:
            return
        self._gpx_timer.stop()
        self._gpx_running = False
        if self._controller.is_running():
            self._clear_after_stop = True
            self._controller.cancel()
        else:
            self._clear_after_playback()
        self._update_controls()

    def _clear_after_playback(self) -> None:
        try:
            operation = simulator_clear_location(self._gpx_target)
            self._execute_approved(operation, self._location_cleared)
        except (ValueError, OSError) as error:
            self._close_after_clear = False
            self.location_status.setText(f"Simulator location clear could not start: {error}. The simulated location may remain.")
            self._update_controls()

    def open_logs(self) -> None:
        try:
            target = self._target()
            if QMessageBox.question(self, "Open Simulator Unified Logs", "The raw log spool may contain private app and simulator data. Open a local capture viewer?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return
            record = next(record for record in self._targets if record.identifier == target)
            window = LiveLogWindow(apple_tool("xcrun"), simulator_log_spec(target), target, record.name, {"LC_ALL": "C"}, Path(__file__).parent / "assets" / "iosdevtoolkit.png")
            self._logs.append(window)
            window.closed.connect(self._log_closed)
            self.live_log_opened.emit(window)
            window.show()
        except (ValueError, OSError, LiveLogError) as error:
            QMessageBox.critical(self, "Simulator Log Viewer Failed", str(error))

    def _log_closed(self, window_object: object) -> None:
        if not isinstance(window_object, LiveLogWindow):
            raise TypeError("Simulator log-window close requires LiveLogWindow")
        self._logs.remove(window_object)
