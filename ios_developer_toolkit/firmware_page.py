from __future__ import annotations

import platform
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Callable

from PySide6.QtCore import QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
    QVBoxLayout, QWidget,
)

from ios_developer_toolkit.firmware_models import (
    BuildIdentity, FirmwareCancelled, FirmwareError, FirmwareFile, FirmwareInstallPlan,
    FirmwareRelease, HelperBundle, RecoveryDevice, file_identity, firmware_library_directory,
    helper_environment, inspect_ipsw, integer_value, matching_install_identity, parse_catalog,
    parse_installer_device, parse_recovery_device, preflight_arguments,
    require_current_plan, validate_helper_bundle, validate_packaged_helper_bundle,
)
from ios_developer_toolkit.firmware_process import FirmwareInstallController
from ios_developer_toolkit.firmware_transport import (
    CatalogSource, check_signing, download_ipsw, fetch_catalog, import_ipsw, inspect_remote_ipsw,
    load_catalog, private_library, refresh_catalog,
)
from ios_developer_toolkit.models import IOSDevice
from ios_developer_toolkit.runtime import (
    active_frozen_executable, command_argv, device_environment,
    is_frozen_runtime, pymobiledevice3_command,
)


@dataclass(frozen=True)
class FirmwareCatalogResult:
    releases: tuple[FirmwareRelease, ...]
    source: CatalogSource


@dataclass(frozen=True)
class FirmwareLibraryResult:
    files: tuple[FirmwareFile, ...]
    failures: tuple[str, ...]


@dataclass(frozen=True)
class FirmwareMessage:
    message: str


@dataclass(frozen=True)
class FirmwarePreview:
    plan: FirmwareInstallPlan


@dataclass(frozen=True)
class FirmwareInstallReady:
    plan: FirmwareInstallPlan


@dataclass(frozen=True)
class FirmwareRemoteManifest:
    release: FirmwareRelease
    firmware: FirmwareFile


FirmwareTaskResult = FirmwareCatalogResult | FirmwareLibraryResult | FirmwareMessage | FirmwarePreview | FirmwareInstallReady | RecoveryDevice | FirmwareRemoteManifest
FirmwareOperation = Callable[[Event, Callable[[str], None]], FirmwareTaskResult]


class FirmwareTask(QThread):
    """Run bounded read, validation, or network I/O outside the GUI thread."""

    result_ready = Signal(object)
    failed = Signal(str)
    progress = Signal(str)

    def __init__(self, parent: QWidget, operation: FirmwareOperation) -> None:
        super().__init__(parent)
        self._operation = operation
        self.cancelled = Event()

    def run(self) -> None:
        try:
            result = self._operation(self.cancelled, self.progress.emit)
            if self.cancelled.is_set():
                raise FirmwareCancelled("Firmware operation was stopped")
            self.result_ready.emit(result)
        except (FirmwareError, OSError, ValueError, subprocess.SubprocessError) as error:
            self.failed.emit(str(error))


def default_helper_directory() -> Path:
    if is_frozen_runtime():
        return active_frozen_executable().parent.parent / "Resources" / "restore-helpers"
    return Path(__file__).resolve().parent.parent / "build-output" / "restore-helpers" / "out"


def selected_helper_bundle(directory: Path, cancelled: Callable[[], bool]) -> HelperBundle:
    """Use the packaged validator only for this frozen app's exact default folder."""
    selected = directory.expanduser().absolute()
    if is_frozen_runtime():
        contents = active_frozen_executable().parent.parent
        if selected == contents / "Resources" / "restore-helpers":
            return validate_packaged_helper_bundle(contents, cancelled)
    return validate_helper_bundle(selected, cancelled)


def require_helper_host() -> None:
    version = platform.mac_ver()[0]
    if platform.system() != "Darwin" or not version or int(version.split(".")[0]) < 14:
        raise FirmwareError("The pinned firmware helpers require macOS 14 or later; other Python toolkit features remain available")


def run_read_command(program: Path, arguments: tuple[str, ...], environment: dict[str, str], timeout: int) -> str:
    result = subprocess.run((str(program), *arguments), env=environment, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, timeout=timeout, check=False)
    if result.returncode != 0:
        raise FirmwareError(f"Firmware read operation failed with exit code {result.returncode}; reconnect the selected device and check again")
    if len(result.stdout) + len(result.stderr) > 8 * 1024 * 1024:
        raise FirmwareError("Firmware read operation exceeded its 8 MiB output limit")
    return result.stdout.decode("utf-8", errors="strict")


def build_install_plan(path: Path, helpers: Path, identifier: str, product_type: str,
                       mode: str, catalog_sha1: str | None, directory: Path,
                       cancelled: Event, progress: Callable[[str], None]) -> FirmwareInstallPlan:
    """Bind the real target to fresh board/build signing using synthetic ECID/nonces.

    This preflight does not obtain the selected device's ticket. The installer
    must request device-specific personalization during the installation.
    """
    require_helper_host()
    if mode not in ("update", "restore"):
        raise FirmwareError("Choose Update or Restore")
    selected_mode = "update" if mode == "update" else "restore"
    private_library(directory)
    progress("Validating the helper bundle, firmware hashes, and manifest…")
    bundle = selected_helper_bundle(helpers, cancelled.is_set)
    identity = file_identity(path, cancelled.is_set)
    firmware = inspect_ipsw(path)
    if catalog_sha1 is not None and identity.sha1 != catalog_sha1:
        raise FirmwareError("This IPSW does not match Apple's catalog SHA-1; installation is blocked")
    if cancelled.is_set():
        raise FirmwareCancelled("Firmware validation was stopped")
    progress("Reading the selected device identity without installing firmware…")
    logfile = directory / f"preflight-{time.time_ns()}.log"
    output = run_read_command(bundle.installer.path,
                              preflight_arguments(path, identifier, selected_mode, directory, logfile), helper_environment(directory), 60)
    preflight_time = time.time()
    target = parse_installer_device(output)
    if identifier.startswith("0x") and target.ecid != integer_value(identifier, "selected ECID"):
        raise FirmwareError("The installer detected a different device ECID; installation is blocked")
    if product_type and target.product_type != product_type:
        raise FirmwareError("The installer detected a different model than the selected device; installation is blocked")
    build_identity = matching_install_identity(firmware, target, selected_mode)
    if cancelled.is_set():
        raise FirmwareCancelled("Firmware validation was stopped")
    progress("Checking board/build/variant signing with synthetic ECID/nonces; the installer obtains the device-specific ticket…")
    signing_time = check_signing(build_identity)
    if cancelled.is_set():
        raise FirmwareCancelled("Firmware validation was stopped")
    provenance = "Apple catalog SHA-1 matched" if catalog_sha1 else "Apple catalog SHA-1 unavailable; SHA-256 establishes local byte identity only"
    plan = FirmwareInstallPlan(firmware, identity, bundle, target, identifier, selected_mode,
                               build_identity, catalog_sha1, provenance, "signed", signing_time, preflight_time, time.time())
    require_current_plan(plan, identifier, selected_mode, time.time())
    return plan


class FirmwarePage(QWidget):
    """IPSW library, Apple catalog, exact-device preflight, recovery, and protected install UI."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("firmwarePage")
        self._device: IOSDevice | None = None
        self._demo_mode = False
        self._task: FirmwareTask | None = None
        self._plan: FirmwareInstallPlan | None = None
        self._recovery: RecoveryDevice | None = None
        self._releases: tuple[FirmwareRelease, ...] = ()
        self._catalog_digests: dict[Path, str | None] = {}
        self._catalog_cache_path = firmware_library_directory(Path.home()) / "Catalog" / "apple-catalog.plist"
        self._pending_remote: FirmwareRemoteManifest | None = None
        self._remote_picker_active = False
        self._watch_timer = QTimer(self)
        self._watch_timer.setInterval(3000)
        self._watch_timer.timeout.connect(self._watch_recovery)
        self._installer = FirmwareInstallController(self)
        self._installer.output_received.connect(self._append_output)
        self._installer.progress_received.connect(self._installation_progress)
        self._installer.completed.connect(self._installation_finished)
        layout = QVBoxLayout(self)
        heading = QLabel("Firmware — Update and Restore")
        heading.setObjectName("firmwareHeading")
        layout.addWidget(heading)
        self.target_label = QLabel("Select a physical device or query a recovery device.")
        self.target_label.setObjectName("firmwareTarget")
        self.target_label.setWordWrap(True)
        layout.addWidget(self.target_label)
        library = QGroupBox("IPSW library and Apple firmware catalog")
        form = QFormLayout(library)
        self.library_field = QLineEdit(str(firmware_library_directory(Path.home())))
        self.library_field.setObjectName("firmwareLibraryPath")
        self.library_field.textChanged.connect(self._invalidate_plan)
        form.addRow("Library folder", self.library_field)
        self.model_field = QLineEdit()
        self.model_field.setObjectName("firmwareModel")
        self.model_field.textChanged.connect(self._invalidate_plan)
        form.addRow("Device model", self.model_field)
        catalog_row = QHBoxLayout()
        catalog_controls = QHBoxLayout()
        self.catalog_button = self._button("Load Apple Catalog", "firmwareFetchCatalog", self.fetch_apple_catalog)
        self.catalog_button.setToolTip("Reuse the private Apple catalog cache for less than one day; stale or missing data is fetched from Apple.")
        catalog_controls.addWidget(self.catalog_button)
        self.refresh_catalog_button = self._button("Refresh from Apple", "firmwareRefreshCatalog", self.refresh_apple_catalog)
        self.refresh_catalog_button.setToolTip("Fetch a fresh catalog over verified HTTPS and replace the local cache. This does not check signing.")
        catalog_controls.addWidget(self.refresh_catalog_button)
        form.addRow(catalog_controls)
        self.release_combo = QComboBox()
        self.release_combo.setObjectName("firmwareRelease")
        catalog_row.addWidget(self.release_combo, 1)
        self.download_button = self._button("Download / Resume", "firmwareDownload", self.download_selected_release)
        catalog_row.addWidget(self.download_button)
        self.release_signing_button = self._button("Check Apple Signing", "firmwareCheckReleaseSigning", self.check_release_signing)
        catalog_row.addWidget(self.release_signing_button)
        form.addRow(catalog_row)
        local_row = QHBoxLayout()
        self.import_button = self._button("Import IPSW…", "firmwareImport", self.import_firmware)
        local_row.addWidget(self.import_button)
        self.scan_button = self._button("Refresh Library", "firmwareRefreshLibrary", self.refresh_library)
        local_row.addWidget(self.scan_button)
        self.local_combo = QComboBox()
        self.local_combo.setObjectName("firmwareLocalIPSW")
        self.local_combo.currentIndexChanged.connect(self._invalidate_plan)
        local_row.addWidget(self.local_combo, 1)
        self.integrity_button = self._button("Check Local Integrity", "firmwareCheckIntegrity", self.check_local_integrity)
        local_row.addWidget(self.integrity_button)
        form.addRow(local_row)
        layout.addWidget(library)
        helpers = QGroupBox("Manifest-checked local firmware helpers")
        helpers_layout = QHBoxLayout(helpers)
        self.helper_field = QLineEdit(str(default_helper_directory()))
        self.helper_field.setObjectName("firmwareHelperPath")
        self.helper_field.textChanged.connect(self._invalidate_plan)
        helpers_layout.addWidget(self.helper_field, 1)
        self.choose_helpers_button = self._button("Choose Helpers…", "firmwareChooseHelpers", self.choose_helpers)
        helpers_layout.addWidget(self.choose_helpers_button)
        layout.addWidget(helpers)
        recovery_row = QHBoxLayout()
        self.query_recovery_button = self._button("Query Recovery / DFU", "firmwareQueryRecovery", self.query_recovery)
        self.enter_recovery_button = self._button("Enter Recovery…", "firmwareEnterRecovery", self.enter_recovery)
        self.exit_recovery_button = self._button("Exit Recovery…", "firmwareExitRecovery", self.exit_recovery)
        for button in (self.query_recovery_button, self.enter_recovery_button, self.exit_recovery_button):
            recovery_row.addWidget(button)
        self.watch_recovery_checkbox = QCheckBox("Watch Recovery / DFU")
        self.watch_recovery_checkbox.setObjectName("firmwareWatchRecovery")
        self.watch_recovery_checkbox.toggled.connect(self._set_recovery_watch)
        recovery_row.addWidget(self.watch_recovery_checkbox)
        layout.addLayout(recovery_row)
        install = QGroupBox("Check before installing")
        install_layout = QVBoxLayout(install)
        self.mode_combo = QComboBox()
        self.mode_combo.setObjectName("firmwareInstallMode")
        self.mode_combo.addItem("Update — retain device data", "update")
        self.mode_combo.addItem("Restore — erase all device data", "restore")
        self.mode_combo.currentIndexChanged.connect(self._invalidate_plan)
        install_layout.addWidget(self.mode_combo)
        self.backup_acknowledgement = QCheckBox("I have a current backup and understand that any firmware installation can lose data.")
        self.backup_acknowledgement.setObjectName("firmwareBackupAcknowledgement")
        install_layout.addWidget(self.backup_acknowledgement)
        controls = QHBoxLayout()
        self.check_button = self._button("Check Before Installing", "firmwareCheck", self.check_before_installing)
        controls.addWidget(self.check_button)
        self.install_button = self._button("Install…", "firmwareInstall", self.confirm_installation)
        controls.addWidget(self.install_button)
        self.stop_button = self._button("Stop Read / Download", "firmwareStop", self.stop_read_operation)
        controls.addWidget(self.stop_button)
        install_layout.addLayout(controls)
        warning = QLabel("The board/build signing check uses synthetic ECID/nonces for the exact Update or Erase variant; the installer must obtain the device-specific ticket. Update never falls back to erasing. During installation, Stop and normal Quit are disabled until the helper exits. Keep the Mac powered and the device connected. Activation Lock remains enforced.")
        warning.setObjectName("firmwareSafetyWarning")
        warning.setWordWrap(True)
        install_layout.addWidget(warning)
        layout.addWidget(install)
        self.status_label = QLabel("No firmware checked. Network access occurs only after Fetch, Download, or Check.")
        self.status_label.setObjectName("firmwareStatus")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        self.install_progress = QProgressBar()
        self.install_progress.setObjectName("firmwareInstallProgress")
        self.install_progress.setVisible(False)
        layout.addWidget(self.install_progress)
        self.output = QPlainTextEdit()
        self.output.setObjectName("firmwareOutput")
        self.output.setReadOnly(True)
        self.output.setMaximumBlockCount(6000)
        layout.addWidget(self.output, 1)
        self._update_controls()

    def _button(self, title: str, identifier: str, action: Callable[[], None]) -> QPushButton:
        button = QPushButton(title)
        button.setObjectName(identifier)
        button.clicked.connect(action)
        return button

    def set_device(self, device: IOSDevice | None, demo_mode: bool) -> None:
        changed = self._device != device or self._demo_mode != demo_mode
        if changed:
            self._plan = None
            if device is not None:
                self._recovery = None
        self._device, self._demo_mode = device, demo_mode
        if self._recovery is not None and not demo_mode:
            target = self._recovery
            self.target_label.setText(f"Recovery target: {target.product_type} · {target.device_class} · {target.mode} · ECID 0x{target.ecid:x}")
        elif device is not None and not demo_mode:
            self.target_label.setText(f"Selected physical device: {device.name} · {device.product_type}")
            if changed:
                self.model_field.setText(device.product_type)
        elif demo_mode:
            self.target_label.setText("Demo Mode: firmware device operations and installation are disabled.")
        else:
            self.target_label.setText("Select a physical device, or Query Recovery / DFU to identify an exact recovery target.")
        self._update_controls()

    def is_running(self) -> bool:
        return self._remote_picker_active or self._task is not None or self._installer.is_running()

    def can_close(self) -> bool:
        return not self.is_running()

    def shutdown(self) -> None:
        self._installer.shutdown()
        if self._task is not None or self._remote_picker_active:
            raise FirmwareError("A firmware read or download is still running; stop it and wait for completion before quitting")
        self._watch_timer.stop()

    def _start_task(self, operation: FirmwareOperation) -> None:
        if self.is_running():
            self._show_error("Another firmware operation is running")
            return
        task = FirmwareTask(self, operation)
        self._task = task
        task.result_ready.connect(self._task_result)
        task.failed.connect(self._task_failed)
        task.progress.connect(self.status_label.setText)
        task.finished.connect(self._task_finished)
        self._update_controls()
        task.start()

    def _task_finished(self) -> None:
        task = self._task
        self._task = None
        if task is not None:
            task.deleteLater()
        self._update_controls()
        pending = self._pending_remote
        self._pending_remote = None
        if pending is not None:
            self._choose_remote_identity(pending)

    def _task_failed(self, message: str) -> None:
        self._plan = None
        self.status_label.setText(message)
        self._append_output(message)

    def _task_result(self, result: object) -> None:
        if isinstance(result, FirmwareCatalogResult):
            self._releases = result.releases
            self.release_combo.clear()
            for release in result.releases:
                self.release_combo.addItem(f"{release.version} ({release.build})", release)
            source = "Cached Apple catalog (under 24 hours old)" if result.source == "cache" else "Catalog refreshed from Apple"
            self.status_label.setText(f"{source} lists {len(result.releases)} firmware build(s) for this model. Catalog presence is not a signing check.")
        elif isinstance(result, FirmwareLibraryResult):
            self.local_combo.clear()
            for firmware in result.files:
                self.local_combo.addItem(f"{firmware.version} ({firmware.build}) · {firmware.path.name}", firmware)
            for failure in result.failures:
                self._append_output(failure)
            self.status_label.setText(f"Loaded {len(result.files)} IPSW(s); {len(result.failures)} invalid file(s) reported.")
        elif isinstance(result, FirmwareMessage):
            self.status_label.setText(result.message)
            self._append_output(result.message)
        elif isinstance(result, RecoveryDevice):
            self._recovery = result
            self._plan = None
            self.model_field.setText(result.product_type)
            self.target_label.setText(f"Recovery target: {result.product_type} · {result.device_class} · {result.mode} · ECID 0x{result.ecid:x}")
            self.status_label.setText("Recovery identity read. Installation targets this exact ECID.")
        elif isinstance(result, FirmwarePreview):
            self._plan = result.plan
            self.status_label.setText("Target preflight and board/build signing passed. Checks run again after confirmation; the installer must obtain the device-specific ticket.")
            self._append_output(self._plan_summary(result.plan))
        elif isinstance(result, FirmwareInstallReady):
            try:
                identifier, _ = self._selection()
                mode = self.mode_combo.currentData()
                if not isinstance(mode, str):
                    raise FirmwareError("Firmware install mode is invalid")
                require_current_plan(result.plan, identifier, result.plan.mode if mode == result.plan.mode else "restore" if mode == "restore" else "update", time.time())
                if self._demo_mode:
                    raise FirmwareError("Firmware installation is disabled in Demo Mode")
                directory = Path(self.library_field.text()).expanduser().absolute()
                logfile = directory / f"install-{time.time_ns()}.log"
                self._installer.start(result.plan, directory, logfile)
                self.install_progress.setRange(0, 0)
                self.install_progress.setVisible(True)
                self._plan = None
                self.status_label.setText("Firmware installation is running. Stop and normal Quit are blocked until the helper exits.")
                self._append_output(f"Private installer log: {logfile}")
            except (FirmwareError, OSError) as error:
                self._show_error(str(error))
        elif isinstance(result, FirmwareRemoteManifest):
            self._pending_remote = result
        else:
            self._show_error("Firmware task returned an unsupported result")
        self._update_controls()

    def _plan_summary(self, plan: FirmwareInstallPlan) -> str:
        return (f"{plan.mode.upper()}: iOS {plan.firmware.version} ({plan.firmware.build})\n"
                f"Target: {plan.target.product_type} / {plan.target.device_class}; ECID 0x{plan.target.ecid:x}; {plan.target.mode}\n"
                f"Exact variant: {plan.identity.variant}\nIPSW SHA-256: {plan.file_identity.sha256}\n"
                f"Installer SHA-256: {plan.helper_bundle.installer.sha256}\n{plan.provenance}\n"
                "Apple accepted board/build/variant signing with synthetic ECID/nonces. The installer must obtain the device-specific ticket; this check does not establish that the actual device can be personalized.")

    def _append_output(self, text: str) -> None:
        self.output.appendPlainText(text.rstrip())

    def _show_error(self, message: str) -> None:
        self.status_label.setText(message)
        QMessageBox.critical(self, "Firmware Operation Blocked", message)

    def _invalidate_plan(self) -> None:
        self._plan = None
        self._update_controls()

    def _update_controls(self) -> None:
        if not hasattr(self, "check_button"):
            return
        running = self.is_running()
        for widget in (self.catalog_button, self.refresh_catalog_button, self.download_button, self.release_signing_button, self.import_button, self.scan_button, self.integrity_button,
                       self.choose_helpers_button, self.query_recovery_button, self.model_field, self.helper_field,
                       self.library_field, self.release_combo, self.local_combo, self.mode_combo, self.backup_acknowledgement,
                       self.watch_recovery_checkbox):
            widget.setEnabled(not running)
        self.enter_recovery_button.setEnabled(not running and self._device is not None and not self._demo_mode)
        self.exit_recovery_button.setEnabled(not running and self._recovery is not None and not self._demo_mode)
        self.check_button.setEnabled(not running and not self._demo_mode and self.local_combo.count() > 0 and (self._device is not None or self._recovery is not None))
        self.install_button.setEnabled(not running and not self._demo_mode and self._plan is not None)
        self.stop_button.setEnabled(self._task is not None and not self._installer.is_running())

    def choose_helpers(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "Choose manifest-checked local firmware helpers", self.helper_field.text())
        if selected:
            self.helper_field.setText(selected)

    def fetch_apple_catalog(self) -> None:
        model = self.model_field.text().strip()
        if not model:
            self._show_error("Enter the exact device product type, such as iPhone18,1")
            return
        def operation(cancelled: Event, progress: Callable[[str], None]) -> FirmwareTaskResult:
            progress("Loading Apple's catalog; cache entries expire after one day…")
            snapshot = load_catalog(self._catalog_cache_path, cancelled, time.time(), fetch_catalog)
            return FirmwareCatalogResult(parse_catalog(snapshot.payload, model), snapshot.source)
        self._start_task(operation)

    def refresh_apple_catalog(self) -> None:
        model = self.model_field.text().strip()
        if not model:
            self._show_error("Enter the exact device product type, such as iPhone18,1")
            return
        def operation(cancelled: Event, progress: Callable[[str], None]) -> FirmwareTaskResult:
            progress("Refreshing Apple's firmware catalog over verified HTTPS…")
            snapshot = refresh_catalog(self._catalog_cache_path, cancelled, time.time(), fetch_catalog)
            return FirmwareCatalogResult(parse_catalog(snapshot.payload, model), snapshot.source)
        self._start_task(operation)

    def refresh_library(self) -> None:
        directory = Path(self.library_field.text()).expanduser().absolute()
        def operation(cancelled: Event, progress: Callable[[str], None]) -> FirmwareTaskResult:
            private_library(directory)
            files: list[FirmwareFile] = []
            failures: list[str] = []
            for path in sorted(directory.glob("*.ipsw")):
                if cancelled.is_set():
                    raise FirmwareCancelled("Firmware library loading was stopped")
                progress(f"Reading {path.name}…")
                try:
                    files.append(inspect_ipsw(path))
                except (FirmwareError, OSError) as error:
                    failures.append(f"{path.name}: {error}")
            return FirmwareLibraryResult(tuple(files), tuple(failures))
        self._start_task(operation)

    def import_firmware(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(self, "Import local IPSW", "", "Firmware (*.ipsw)")
        if not selected:
            return
        directory = Path(self.library_field.text()).expanduser().absolute()
        def operation(cancelled: Event, progress: Callable[[str], None]) -> FirmwareTaskResult:
            firmware = import_ipsw(Path(selected), directory, cancelled, progress)
            return FirmwareLibraryResult((firmware,), ())
        self._start_task(operation)

    def download_selected_release(self) -> None:
        release = self.release_combo.currentData()
        if not isinstance(release, FirmwareRelease):
            self._show_error("Fetch Apple's catalog and select a firmware release first")
            return
        directory = Path(self.library_field.text()).expanduser().absolute()
        self._catalog_digests[directory / release.filename] = release.sha1
        def operation(cancelled: Event, progress: Callable[[str], None]) -> FirmwareTaskResult:
            identity = download_ipsw(release, directory, cancelled, progress)
            progress(f"Downloaded SHA-256: {identity.sha256}")
            return FirmwareLibraryResult((inspect_ipsw(identity.path),), ())
        self._start_task(operation)

    def check_release_signing(self) -> None:
        release = self.release_combo.currentData()
        if not isinstance(release, FirmwareRelease):
            self._show_error("Fetch Apple's catalog and select a firmware release first")
            return
        def operation(cancelled: Event, progress: Callable[[str], None]) -> FirmwareTaskResult:
            progress("Reading the remote BuildManifest through bounded ZIP64 HTTP ranges…")
            return FirmwareRemoteManifest(release, inspect_remote_ipsw(release, cancelled))
        self._start_task(operation)

    def _choose_remote_identity(self, remote: FirmwareRemoteManifest) -> None:
        candidates = tuple(identity for identity in remote.firmware.identities if identity.behavior == "Erase" and identity.variant == "Customer Erase Install (IPSW)")
        if self._recovery is not None and self._recovery.product_type == remote.release.product_type:
            candidates = tuple(identity for identity in candidates if identity.device_class == self._recovery.device_class and identity.chip_id == self._recovery.chip_id and identity.board_id == self._recovery.board_id)
        names = tuple(f"{identity.device_class} · chip 0x{identity.chip_id:x} / board 0x{identity.board_id:x}" for identity in candidates)
        if not candidates or len(set(names)) != len(names):
            self._show_error("The remote firmware has no unambiguous erase identity for signing inspection")
            return
        watch_was_active = self._watch_timer.isActive()
        self._watch_timer.stop()
        self._remote_picker_active = True
        self._update_controls()
        dialog = QInputDialog(self)
        dialog.setObjectName("firmwareRemoteBoardPicker")
        dialog.setWindowTitle("Choose Exact Firmware Board")
        dialog.setLabelText("Check signing for this exact firmware board. This does not authorize an installation.")
        dialog.setComboBoxItems(names)
        dialog.setComboBoxEditable(False)
        try:
            accepted = dialog.exec() == QInputDialog.DialogCode.Accepted
            selected = dialog.textValue()
        finally:
            dialog.deleteLater()
            self._remote_picker_active = False
            if watch_was_active and self.watch_recovery_checkbox.isChecked():
                self._watch_timer.start()
            self._update_controls()
        if not accepted:
            self.status_label.setText("Remote signing inspection was cancelled before contacting Apple's signing service.")
            return
        identity = candidates[names.index(selected)]
        def operation(cancelled: Event, progress: Callable[[str], None]) -> FirmwareTaskResult:
            if cancelled.is_set():
                raise FirmwareCancelled("Remote signing inspection was stopped")
            progress("Checking Apple's signing for the selected remote firmware identity…")
            check_signing(identity)
            return FirmwareMessage(f"Apple accepted signing for {remote.release.product_type} {remote.release.version} ({remote.release.build}), board {identity.device_class}. This uses a synthetic ECID/nonce; exact-device installation still requires local IPSW hashing, fresh target preflight, and personalization.")
        self._start_task(operation)

    def _set_recovery_watch(self, enabled: bool) -> None:
        if enabled:
            self._watch_timer.start()
            self._watch_recovery()
        else:
            self._watch_timer.stop()

    def _watch_recovery(self) -> None:
        if self._demo_mode or self.is_running() or self._plan is not None:
            return
        self.query_recovery()

    def _selection(self) -> tuple[str, str]:
        if self._demo_mode:
            raise FirmwareError("Demo Mode cannot run firmware device operations")
        if self._recovery is not None:
            return f"0x{self._recovery.ecid:x}", self._recovery.product_type
        if self._device is not None:
            return self._device.identifier, self._device.product_type
        raise FirmwareError("Select a physical device or query an exact recovery target")

    def _catalog_checksum(self, firmware: FirmwareFile) -> str | None:
        matches = tuple(release.sha1 for release in self._releases if release.build == firmware.build and release.version == firmware.version and release.product_type in firmware.product_types and release.sha1 is not None)
        known = self._catalog_digests.get(firmware.path)
        digests = set(matches)
        if known is not None:
            digests.add(known)
        if len(digests) > 1:
            raise FirmwareError("The fetched Apple catalog has conflicting checksums for this local firmware; refresh it before checking")
        return next(iter(digests)) if digests else None

    def check_local_integrity(self) -> None:
        firmware = self.local_combo.currentData()
        if not isinstance(firmware, FirmwareFile):
            self._show_error("Import or download an IPSW and select it first")
            return
        try:
            expected = self._catalog_checksum(firmware)
        except FirmwareError as error:
            self._show_error(str(error))
            return
        def operation(cancelled: Event, progress: Callable[[str], None]) -> FirmwareTaskResult:
            progress("Computing local IPSW SHA-1 and SHA-256…")
            identity = file_identity(firmware.path, cancelled.is_set)
            if expected is not None and expected != identity.sha1:
                raise FirmwareError("This IPSW does not match Apple's catalog SHA-1; installation is blocked")
            provenance = "Apple catalog SHA-1 matched" if expected else "Apple catalog SHA-1 unavailable; local hashes establish byte identity only"
            return FirmwareMessage(f"{firmware.path.name}\nSHA-1: {identity.sha1}\nSHA-256: {identity.sha256}\n{provenance}")
        self._start_task(operation)

    def _plan_inputs(self) -> tuple[Path, Path, str, str, str, str | None, Path]:
        firmware = self.local_combo.currentData()
        if not isinstance(firmware, FirmwareFile):
            raise FirmwareError("Import or download an IPSW and select it first")
        identifier, model = self._selection()
        mode = self.mode_combo.currentData()
        if not isinstance(mode, str):
            raise FirmwareError("Choose an explicit firmware install mode")
        return (firmware.path, Path(self.helper_field.text()).expanduser().absolute(), identifier, model, mode,
                self._catalog_checksum(firmware), Path(self.library_field.text()).expanduser().absolute())

    def check_before_installing(self) -> None:
        try:
            values = self._plan_inputs()
        except FirmwareError as error:
            self._show_error(str(error))
            return
        def operation(cancelled: Event, progress: Callable[[str], None]) -> FirmwareTaskResult:
            return FirmwarePreview(build_install_plan(*values, cancelled, progress))
        self._start_task(operation)

    def confirm_installation(self) -> None:
        plan = self._plan
        if plan is None:
            self._show_error("Run Check Before Installing before confirming an installation")
            return
        if not self.backup_acknowledgement.isChecked():
            self._show_error("Acknowledge the current-backup and potential-data-loss requirement first")
            return
        try:
            values = self._plan_inputs()
            require_current_plan(plan, values[2], plan.mode, time.time())
        except (FirmwareError, OSError) as error:
            self._show_error(str(error))
            return
        phrase = "ERASE" if plan.mode == "restore" else f"0x{plan.target.ecid:x}"
        typed, accepted = QInputDialog.getText(self, f"Confirm {plan.mode.title()}",
                                             self._plan_summary(plan) + f"\n\nType {phrase} to authorize this exact operation. Fresh checks run before installation.")
        if not accepted or typed != phrase:
            return
        expected = plan.target
        def operation(cancelled: Event, progress: Callable[[str], None]) -> FirmwareTaskResult:
            fresh = build_install_plan(*values, cancelled, progress)
            if fresh.target != expected or fresh.file_identity.sha256 != plan.file_identity.sha256 or fresh.helper_bundle.installer.sha256 != plan.helper_bundle.installer.sha256:
                raise FirmwareError("The target, firmware, or installer changed after confirmation; installation is blocked")
            return FirmwareInstallReady(fresh)
        self._start_task(operation)

    def query_recovery(self) -> None:
        if self._demo_mode:
            self._show_error("Demo Mode cannot query recovery devices")
            return
        helpers = Path(self.helper_field.text()).expanduser().absolute()
        directory = Path(self.library_field.text()).expanduser().absolute()
        def operation(cancelled: Event, progress: Callable[[str], None]) -> FirmwareTaskResult:
            require_helper_host()
            bundle = selected_helper_bundle(helpers, cancelled.is_set)
            private_library(directory)
            progress("Querying recovery or DFU identity without changing device state…")
            return parse_recovery_device(run_read_command(bundle.recovery.path, ("-q",), helper_environment(directory), 20))
        self._start_task(operation)

    def enter_recovery(self) -> None:
        device = self._device
        if device is None or self._demo_mode:
            self._show_error("Select the intended physical device before entering recovery")
            return
        if QMessageBox.question(self, "Enter Recovery", f"Restart {device.name} into recovery mode? This interrupts all device activity.") != QMessageBox.StandardButton.Yes:
            return
        command = pymobiledevice3_command()
        environment = dict(device_environment(device.identifier))
        def operation(cancelled: Event, progress: Callable[[str], None]) -> FirmwareTaskResult:
            if cancelled.is_set():
                raise FirmwareCancelled("Entering recovery was stopped before execution")
            progress("Asking the selected device to enter recovery…")
            argv = command_argv(command, ("lockdown", "recovery"))
            run_read_command(Path(argv[0]), tuple(argv[1:]), environment, 30)
            return FirmwareMessage("Recovery entry request completed. Query Recovery / DFU to identify the device before firmware installation.")
        self._start_task(operation)

    def exit_recovery(self) -> None:
        recovery = self._recovery
        if recovery is None or self._demo_mode or recovery.mode != "recovery":
            self._show_error("Query a device in recovery mode before exiting; DFU is not a recovery-exit target")
            return
        if QMessageBox.question(self, "Exit Recovery", f"Restart the recovery device with ECID 0x{recovery.ecid:x} into normal mode?") != QMessageBox.StandardButton.Yes:
            return
        helpers = Path(self.helper_field.text()).expanduser().absolute()
        directory = Path(self.library_field.text()).expanduser().absolute()
        def operation(cancelled: Event, progress: Callable[[str], None]) -> FirmwareTaskResult:
            require_helper_host()
            bundle = selected_helper_bundle(helpers, cancelled.is_set)
            private_library(directory)
            target = f"0x{recovery.ecid:x}"
            current = parse_recovery_device(run_read_command(bundle.recovery.path, ("-i", target, "-q"), helper_environment(directory), 20))
            if current != recovery or cancelled.is_set():
                raise FirmwareError("Recovery identity changed or cancellation was requested; exit is blocked")
            progress("Asking the exact recovery device to restart normally…")
            run_read_command(bundle.recovery.path, ("-i", target, "-n"), helper_environment(directory), 60)
            return FirmwareMessage("Recovery-exit request completed. Refresh device discovery to verify normal-mode reconnection.")
        self._start_task(operation)

    def stop_read_operation(self) -> None:
        if self._installer.is_running():
            self._show_error("Firmware installation cannot be stopped; wait for the helper to exit")
            return
        if self._task is not None:
            self._task.cancelled.set()
            self.stop_button.setEnabled(False)
            self.status_label.setText("Stopping the read or download at its next bounded I/O boundary…")

    def _installation_finished(self, exit_code: int, message: str) -> None:
        self.status_label.setText(message)
        self._append_output(message)
        self._recovery = None
        self._plan = None
        self.install_progress.setVisible(False)
        self._update_controls()

    def _installation_progress(self, stage: str, percentage: int) -> None:
        self.status_label.setText(f"{stage}. Keep the device connected; Stop and normal Quit remain blocked.")
        if percentage < 0:
            self.install_progress.setRange(0, 0)
        else:
            self.install_progress.setRange(0, 100)
            self.install_progress.setValue(percentage)
