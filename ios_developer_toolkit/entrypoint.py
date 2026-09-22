from __future__ import annotations

import os
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from ios_developer_toolkit.qt_process import finite_process_request

from ios_developer_toolkit.runtime import (
    ExecutableCommand,
    INTERNAL_PYMOBILEDEVICE3_FLAG,
    INTERNAL_SMOKE_TEST_FLAG,
    INTERNAL_WORKER_FLAG,
    ToolkitWorker,
    pymobiledevice3_command,
)


def normalized_exit_code(value: int | None) -> int:
    return 0 if value is None else value


def invoke_argv_main(label: str, arguments: Sequence[str], main_function: Callable[[], int | None]) -> int:
    original_arguments = tuple(sys.argv)
    sys.argv = [label, *arguments]
    try:
        return normalized_exit_code(main_function())
    finally:
        sys.argv = list(original_arguments)


def parsed_worker(value: str) -> ToolkitWorker:
    if value == "backup":
        return "backup"
    if value == "capability":
        return "capability"
    if value == "collector":
        return "collector"
    if value == "ipa-inspector":
        return "ipa-inspector"
    if value == "local-ddi":
        return "local-ddi"
    raise ValueError(f"Unsupported internal worker: {value}")


def run_worker(worker: ToolkitWorker, arguments: Sequence[str]) -> int:
    if worker == "backup":
        from ios_developer_toolkit.backup_worker import main

        return invoke_argv_main("ios-developer-toolkit-backup", arguments, main)
    if worker == "capability":
        from ios_developer_toolkit.capability_matrix_worker import main

        return main(arguments)
    if worker == "collector":
        from ios_developer_toolkit.collector import main

        return invoke_argv_main("ios-developer-toolkit-collector", arguments, main)
    if worker == "ipa-inspector":
        from ios_developer_toolkit.ipa_inspector import main

        return invoke_argv_main("ios-developer-toolkit-ipa-inspector", arguments, main)
    if worker == "local-ddi":
        from ios_developer_toolkit.local_ddi import main

        return invoke_argv_main("ios-developer-toolkit-local-ddi", arguments, main)
    raise ValueError(f"Unsupported internal worker: {worker}")


def run_pymobiledevice3(arguments: Sequence[str]) -> int:
    from pymobiledevice3.__main__ import main

    return invoke_argv_main("pymobiledevice3", arguments, main)


def run_smoke_test(arguments: Sequence[str]) -> int:
    if arguments:
        raise ValueError(f"Internal smoke test does not accept arguments: {tuple(arguments)}")
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide6.QtCore import SIGNAL, Qt
    from PySide6.QtWidgets import QApplication, QLabel, QPlainTextEdit, QPushButton, QTableWidget

    from ios_developer_toolkit.action_palette import ActionPaletteDialog
    from ios_developer_toolkit.app import MainWindow
    from ios_developer_toolkit.backup_protocol import BackupRequest
    from ios_developer_toolkit.mvt_connector import create_mvt_analysis_request
    from ios_developer_toolkit.operation_history import OperationHistoryDialog

    application = QApplication(["ios-developer-toolkit-smoke-test"])
    window = MainWindow()
    window._scanner.stop()
    window._devices_changed(())
    application.processEvents()
    buttons = tuple(window.findChildren(QPushButton))
    missing_identifiers = tuple(button.text() for button in buttons if not button.objectName())
    disconnected = tuple(
        button.objectName() for button in buttons if button.receivers(SIGNAL("clicked(bool)")) == 0
    )
    if missing_identifiers:
        raise RuntimeError(f"GUI buttons are missing stable identifiers: {missing_identifiers}")
    if disconnected:
        raise RuntimeError(f"GUI buttons are missing click handlers: {disconnected}")
    for button_name in (
        "coreDeviceDetailsButton",
        "listRVIInterfacesButton",
        "openXcodeProjectButton",
        "openXcodeArtifactButton",
    ):
        button = window.findChild(QPushButton, button_name)
        if button is None:
            raise RuntimeError(f"GUI Xcode handoff action is missing: {button_name}")
    coredevice_button = window.findChild(QPushButton, "coreDeviceDetailsButton")
    rvi_button = window.findChild(QPushButton, "listRVIInterfacesButton")
    if coredevice_button is None or coredevice_button.isEnabled():
        raise RuntimeError("GUI CoreDevice handoff must require a selected device")
    if rvi_button is None or not rvi_button.isEnabled():
        raise RuntimeError("GUI RVI status handoff should be available without a selected device")
    navigation_actions = {
        "homeOpenDevice&DDIButton": "Device & DDI",
        "homeOpenCapabilityMatrixButton": "Capability Matrix",
        "homeOpenLocationLabButton": "Location Lab",
        "homeOpenCommandCenterButton": "Command Center",
        "homeOpenEvidenceCaptureButton": "Evidence Capture",
        "homeOpenManPagesButton": "Man Pages",
        "openLogPresetsButton": "Command Center",
        "openEvidenceCaptureButton": "Evidence Capture",
    }
    for button_name, destination in navigation_actions.items():
        button = window.findChild(QPushButton, button_name)
        if button is None:
            raise RuntimeError(f"GUI navigation action is missing: {button_name}")
        button.click()
        application.processEvents()
        selected_item = window.navigation_list.currentItem()
        if selected_item is None or selected_item.text() != destination:
            actual_destination = None if selected_item is None else selected_item.text()
            raise RuntimeError(
                f"GUI navigation action {button_name} reached {actual_destination!r}, expected {destination!r}"
            )
    for button_name in ("cancelCommandDriftButton", "copyCommandDriftReportButton"):
        button = window.findChild(QPushButton, button_name)
        if button is None:
            raise RuntimeError(f"GUI command-drift action is missing: {button_name}")
        if button.isEnabled():
            raise RuntimeError(f"GUI command-drift action should be disabled before a drift check: {button_name}")
    command_drift_button = window.findChild(QPushButton, "checkCommandDriftButton")
    command_drift_copy_button = window.findChild(QPushButton, "copyCommandDriftReportButton")
    if command_drift_button is None or command_drift_copy_button is None:
        raise RuntimeError("GUI command-drift controls are incomplete")
    command_drift_button.click()
    command_drift_deadline = time.monotonic() + 180
    while not command_drift_copy_button.isEnabled() and time.monotonic() < command_drift_deadline:
        application.processEvents()
        time.sleep(0.001)
    application.processEvents()
    if not command_drift_copy_button.isEnabled():
        window.cancel_command_drift_check()
        raise RuntimeError("GUI command-drift check did not complete within its bounded smoke-test window")
    command_drift_report = window.command_drift_output.toPlainText()
    if "Verified: 49" not in command_drift_report or "All guided preset routes" not in command_drift_report:
        raise RuntimeError(f"GUI command-drift check reported incompatible guidance: {command_drift_report}")
    command_readiness = window.findChild(QLabel, "commandReadinessStatus")
    if command_readiness is None or not command_readiness.text().strip():
        raise RuntimeError("GUI selected-command readiness has no visible state")
    command_readiness_button = window.findChild(QPushButton, "runCommandReadinessButton")
    if command_readiness_button is None:
        raise RuntimeError("GUI selected-command readiness action is missing")
    if command_readiness_button.isEnabled():
        raise RuntimeError("GUI selected-command readiness must remain disabled without a selected device")
    window.navigate_to_page("Man Pages")
    refresh_manpage_button = window.findChild(QPushButton, "refreshManpageButton")
    if refresh_manpage_button is None or not refresh_manpage_button.isEnabled():
        raise RuntimeError("GUI live-help action is unavailable")
    selected_manpage = window.selected_manpage_entry()
    if selected_manpage is None:
        raise RuntimeError("GUI live-help index has no selected route")
    refresh_manpage_button.click()
    live_help_deadline = time.monotonic() + 20
    while window._manpage_controller.is_running() and time.monotonic() < live_help_deadline:
        application.processEvents()
    application.processEvents()
    if window._manpage_controller.is_running():
        raise RuntimeError("GUI live-help action did not complete within its bounded smoke-test window")
    if selected_manpage.command_path not in window._manpage_cache:
        raise RuntimeError(f"GUI live-help action did not cache successful output: {window.manpage_output.toPlainText()}")
    action_palette_button = window.findChild(QPushButton, "actionPaletteButton")
    if action_palette_button is None:
        raise RuntimeError("GUI action-palette launcher is missing")
    action_palette_entries = window._eligible_action_palette_entries()
    action_palette_identifiers = {entry.identifier for entry in action_palette_entries}
    if "preset:devices" not in action_palette_identifiers:
        raise RuntimeError("GUI action palette omitted the host-only devices preset")
    device_only_presets = {
        f"preset:{preset.identifier}" for preset in window._presets if preset.requires_device
    }
    exposed_device_only_presets = device_only_presets & action_palette_identifiers
    if exposed_device_only_presets:
        raise RuntimeError(
            f"GUI action palette exposed device-only presets without a selected device: "
            f"{sorted(exposed_device_only_presets)}"
        )
    action_palette_dialog = ActionPaletteDialog(action_palette_entries, window)
    action_palette_dialog.search.setText("session manifest")
    application.processEvents()
    if action_palette_dialog.results.count() != 1:
        raise RuntimeError("GUI action-palette search did not isolate the session-manifest utility")
    action_palette_item = action_palette_dialog.results.item(0)
    if action_palette_item.data(Qt.ItemDataRole.UserRole) != "utility:session-activity":
        raise RuntimeError("GUI action-palette search selected an unexpected entry")
    action_palette_dialog.close()
    with tempfile.TemporaryDirectory() as mvt_temporary_directory:
        mvt_root = Path(mvt_temporary_directory)
        mvt_executable = mvt_root / "mvt-ios"
        mvt_executable.write_text(
            "#!/bin/sh\n"
            "for argument in \"$@\"; do\n"
            "  if [ \"$argument\" = \"version\" ]; then\n"
            "    printf \"MVT - Mobile Verification Toolkit\\nVersion: 2026.9.21\\n\"\n"
            "    exit 0\n"
            "  fi\n"
            "done\n"
            "output=\"\"\n"
            "while [ \"$#\" -gt 0 ]; do\n"
            "  if [ \"$1\" = \"--output\" ]; then\n"
            "    shift\n"
            "    output=\"$1\"\n"
            "  fi\n"
            "  shift\n"
            "done\n"
            "mkdir -p \"$output\"\n"
            "printf \"{\\\"synthetic\\\":true}\\n\" > \"$output/info.json\"\n"
            "printf \"Synthetic MVT analysis completed\\n\"\n",
            encoding="utf-8",
        )
        mvt_executable.chmod(0o700)
        mvt_backup = mvt_root / "backup"
        mvt_backup.mkdir()
        (mvt_backup / "Manifest.db").write_bytes(b"synthetic manifest")
        (mvt_backup / "Info.plist").write_bytes(b"synthetic info")
        mvt_output = mvt_root / "analysis"
        window.mvt_executable_field.setText(str(mvt_executable))
        window.validate_mvt_from_ui()
        mvt_validation_deadline = time.monotonic() + 10
        while window._mvt_controller.is_running() and time.monotonic() < mvt_validation_deadline:
            application.processEvents()
            time.sleep(0.001)
        application.processEvents()
        if window._mvt_controller.is_running() or window._mvt_installation is None:
            raise RuntimeError(f"GUI MVT validation did not complete: {window.mvt_output.toPlainText()}")
        if window._mvt_installation.version != "2026.9.21":
            raise RuntimeError("GUI MVT validation retained an unexpected version")
        request = create_mvt_analysis_request(
            window._mvt_installation,
            mvt_backup,
            mvt_output,
            (),
            False,
            False,
            False,
        )
        window._start_mvt_analysis_request(request)
        mvt_analysis_deadline = time.monotonic() + 10
        while window._mvt_controller.is_running() and time.monotonic() < mvt_analysis_deadline:
            application.processEvents()
            time.sleep(0.001)
        application.processEvents()
        if window._mvt_controller.is_running():
            window._mvt_controller.cancel()
            raise RuntimeError("GUI MVT analysis did not complete within its bounded smoke-test window")
        if not (mvt_output / "info.json").is_file():
            raise RuntimeError(f"GUI MVT analysis did not create isolated output: {window.mvt_output.toPlainText()}")
        if "does not prove" not in window.mvt_status.text():
            raise RuntimeError("GUI MVT completion omitted the no-clean-device interpretation boundary")
    expected_shortcuts = {
        "shortcutRetryDeviceScan",
        "shortcutShowActionPalette",
        "shortcutFocusWorkspaceNavigation",
        "shortcutFocusWorkspaceSearch",
        "shortcutShowKeyboardReference",
        "shortcutPreviousWorkspace",
        "shortcutNextWorkspace",
        "shortcutOpenCommandCenter",
        "shortcutOpenManPages",
        "shortcutOpenScopeAndSafety",
    }
    actual_shortcuts = {shortcut.objectName() for shortcut in window._keyboard_shortcuts}
    missing_shortcuts = expected_shortcuts - actual_shortcuts
    if missing_shortcuts:
        raise RuntimeError(f"GUI keyboard shortcuts are missing: {sorted(missing_shortcuts)}")
    connection_diagnostic = window.findChild(QLabel, "connectionDiagnosticValue")
    if connection_diagnostic is None:
        raise RuntimeError("GUI connection diagnostic is missing")
    if not connection_diagnostic.text().strip():
        raise RuntimeError("GUI connection diagnostic has no visible state")
    support_bundle_button = window.findChild(QPushButton, "createSupportBundleButton")
    if support_bundle_button is None:
        raise RuntimeError("GUI support-bundle action is missing")
    demo_mode_button = window.findChild(QPushButton, "demoModeButton")
    if demo_mode_button is None:
        raise RuntimeError("GUI demo-mode action is missing")
    demo_mode_button.click()
    application.processEvents()
    if window.selected_device() is not None:
        raise RuntimeError("Demo mode must not expose a simulated device to operational actions")
    if not window.connection_banner.text().startswith("DEMO MODE"):
        raise RuntimeError("Demo mode must visibly identify the simulated connection")
    if window.mount_button.isEnabled():
        raise RuntimeError("Demo mode must disable device-affecting actions")
    live_log_button = window.findChild(QPushButton, "openUnifiedLogButton")
    if live_log_button is None or live_log_button.isEnabled():
        raise RuntimeError("Demo mode must disable live-device log collection")
    demo_mode_button.click()
    application.processEvents()
    window._start_action(
        pymobiledevice3_command(),
        ("version",),
        {},
        "smoke",
        20_000,
        window._host_operation_context("Smoke Device Action", "Device & DDI", "pymobiledevice3 host command", ()),
    )
    action_deadline = time.monotonic() + 20
    while window._action_controller.is_running() and time.monotonic() < action_deadline:
        application.processEvents()
        time.sleep(0.001)
    application.processEvents()
    if window._action_controller.is_running():
        window._action_controller.cancel()
        raise RuntimeError("GUI DDI action controller did not complete within its bounded smoke-test window")
    action_output = window.action_output.toPlainText()
    if "[finished: succeeded; exit 0]" not in action_output:
        raise RuntimeError(f"GUI DDI action controller failed its host-only smoke command: {action_output}")
    window.console_input.setText("version")
    window.console_run_button.click()
    console_deadline = time.monotonic() + 20
    while window._console_controller.is_running() and time.monotonic() < console_deadline:
        application.processEvents()
        time.sleep(0.001)
    application.processEvents()
    if window._console_controller.is_running():
        window._console_controller.cancel()
        raise RuntimeError("GUI Command Center controller did not complete within its smoke-test window")
    console_output = window.console_output.toPlainText()
    if "[finished: succeeded; exit 0]" not in console_output:
        raise RuntimeError(f"GUI Command Center controller failed its host-only smoke command: {console_output}")
    synthetic_inventory = (
        '{"com.example.toolkit-smoke": {'
        '"CFBundleIdentifier": "com.example.toolkit-smoke", '
        '"CFBundleDisplayName": "Toolkit Smoke", '
        '"ApplicationType": "User"}}'
    )
    window._apps_context = "inventory"
    window._begin_operation(
        "installed-apps",
        window._host_operation_context("Smoke App Inventory", "Installed Apps", "synthetic smoke process", ()),
    )
    window._apps_controller.start(
        finite_process_request(
            ExecutableCommand(Path("/usr/bin/printf"), ()),
            (synthetic_inventory,),
            {},
            5_000,
            500,
        )
    )
    apps_deadline = time.monotonic() + 10
    while window._apps_controller.is_running() and time.monotonic() < apps_deadline:
        application.processEvents()
        time.sleep(0.001)
    application.processEvents()
    if window._apps_controller.is_running():
        window._apps_controller.cancel()
        raise RuntimeError("GUI installed-apps controller did not complete within its bounded smoke-test window")
    if window.installed_apps_table.rowCount() != 1 or "Loaded 1 installed" not in window.apps_status.text():
        raise RuntimeError(
            f"GUI installed-apps controller did not render its synthetic inventory: {window.apps_status.text()}"
        )
    synthetic_inspection = (
        '{"ipa_path":"/tmp/ToolkitSmoke.ipa","app_name":"Toolkit Smoke",'
        '"bundle_identifier":"com.example.toolkit-smoke","version":"1.0","build":"1",'
        '"minimum_os_version":"17.0","executable_name":"ToolkitSmoke",'
        '"provisioning":{"status":"present","name":"Toolkit Smoke Profile","uuid":"smoke-uuid",'
        '"team_identifiers":["SMOKETEAM"],'
        '"application_identifier":"SMOKETEAM.com.example.toolkit-smoke",'
        '"expiration":"2030-01-01T00:00:00+00:00","provisioned_device_count":1,'
        '"provisions_all_devices":false,"get_task_allow":true,'
        '"developer_certificate_count":1,"detail":"Synthetic smoke-test profile"},'
        '"signature":{"status":"valid","identifier":"com.example.toolkit-smoke",'
        '"team_identifier":"SMOKETEAM","authorities":["Toolkit Smoke Authority"],'
        '"detail":"Synthetic smoke-test signature"}}'
    )
    window._begin_operation(
        "ipa-inspection",
        window._host_operation_context("Smoke IPA Inspection", "Sideload IPA", "synthetic smoke process", ()),
    )
    window._ipa_inspection_controller.start(
        finite_process_request(
            ExecutableCommand(Path("/usr/bin/printf"), ()),
            (synthetic_inspection,),
            {},
            5_000,
            500,
        )
    )
    inspection_deadline = time.monotonic() + 10
    while window._ipa_inspection_controller.is_running() and time.monotonic() < inspection_deadline:
        application.processEvents()
        time.sleep(0.001)
    application.processEvents()
    if window._ipa_inspection_controller.is_running():
        window._ipa_inspection_controller.cancel()
        raise RuntimeError("GUI IPA inspection controller did not complete within its bounded smoke-test window")
    if window._ipa_inspection is None or window._ipa_inspection.signature.status != "valid":
        raise RuntimeError(f"GUI IPA inspection controller rejected typed metadata: {window.sideload_status.text()}")
    window._sideload_context = "smoke"
    window._begin_operation(
        "sideload-ipa",
        window._host_operation_context("Smoke IPA Operation", "Sideload IPA", "synthetic smoke process", ()),
    )
    window._sideload_controller.start(
        finite_process_request(
            ExecutableCommand(Path("/usr/bin/printf"), ()),
            ("Synthetic IPA operation output",),
            {},
            5_000,
            500,
        )
    )
    sideload_deadline = time.monotonic() + 10
    while window._sideload_controller.is_running() and time.monotonic() < sideload_deadline:
        application.processEvents()
        time.sleep(0.001)
    application.processEvents()
    if window._sideload_controller.is_running():
        window._sideload_controller.cancel()
        raise RuntimeError("GUI IPA installation controller did not complete within its bounded smoke-test window")
    sideload_output = window.sideload_output.toPlainText()
    if "Synthetic IPA operation output" not in sideload_output or "[finished: succeeded; exit 0]" not in sideload_output:
        raise RuntimeError(f"GUI IPA installation controller did not preserve its output: {sideload_output}")
    if window.sideload_status.text() != "Smoke completed successfully.":
        raise RuntimeError(f"GUI IPA installation controller reported the wrong state: {window.sideload_status.text()}")
    backup_smoke_program = (
        'BEGIN { delete ARGV[1] } END { print "{\\"event\\":\\"encryption-state\\",'
        '\\"message\\":\\"Synthetic encryption status.\\",\\"encrypted\\":true}" }'
    )
    window._backup_action = "status"
    window._begin_operation(
        "backup",
        window._host_operation_context("Smoke Backup Status", "Backup", "synthetic smoke process", ()),
    )
    window._backup_controller.start(
        ExecutableCommand(Path("/usr/bin/awk"), (backup_smoke_program,)),
        "status",
        BackupRequest("toolkit-smoke-device", Path("/tmp"), False, "", False),
        {},
        500,
    )
    backup_deadline = time.monotonic() + 10
    while window._backup_controller.is_running() and time.monotonic() < backup_deadline:
        application.processEvents()
        time.sleep(0.001)
    application.processEvents()
    if window._backup_controller.is_running():
        window._backup_controller.cancel()
        raise RuntimeError("GUI backup controller did not complete within its bounded smoke-test window")
    if window._backup_encryption_state is not True:
        raise RuntimeError(f"GUI backup controller did not apply its typed event: {window.backup_output.toPlainText()}")
    if "Encryption status check completed." not in window.backup_output.toPlainText():
        raise RuntimeError(f"GUI backup controller reported the wrong completion: {window.backup_output.toPlainText()}")
    synthetic_collection_event = (
        '{"event":"case-finished","message":"Synthetic evidence finalization.",'
        '"timestamp":"2026-09-22T00:00:00+00:00","path":"/tmp/toolkit-smoke-case",'
        '"status":"completed","failures":0}'
    )
    window._collection_case_finished = False
    window._begin_operation(
        "evidence-collection",
        window._host_operation_context(
            "Smoke Evidence Collection",
            "Evidence Capture",
            "synthetic smoke process",
            (),
        ),
    )
    window._collection_controller.start(
        ExecutableCommand(Path("/usr/bin/printf"), ()),
        (synthetic_collection_event,),
        {},
        5_000,
    )
    collection_deadline = time.monotonic() + 10
    while window._collection_controller.is_running() and time.monotonic() < collection_deadline:
        application.processEvents()
        time.sleep(0.001)
    application.processEvents()
    if window._collection_controller.is_running():
        window._collection_controller.cancel()
        raise RuntimeError("GUI evidence controller did not complete within its bounded smoke-test window")
    if not window._collection_case_finished or window._last_case_path != Path("/tmp/toolkit-smoke-case"):
        raise RuntimeError(f"GUI evidence controller did not apply finalization: {window.collection_output.toPlainText()}")
    if "Collection process finished: succeeded; exit 0." not in window.collection_output.toPlainText():
        raise RuntimeError(f"GUI evidence controller reported the wrong completion: {window.collection_output.toPlainText()}")
    if len(window._operation_records) < 8:
        raise RuntimeError(f"GUI session activity did not correlate typed operations: {len(window._operation_records)}")
    activity_button = window.findChild(QPushButton, "sessionActivityButton")
    if activity_button is None or f"({len(window._operation_records)})" not in activity_button.text():
        raise RuntimeError("GUI session activity count did not update after typed operations")
    activity_dialog = OperationHistoryDialog(window._operation_records, window)
    activity_table = activity_dialog.findChild(QTableWidget, "sessionActivityTable")
    activity_preview = activity_dialog.findChild(QPlainTextEdit, "sessionActivityManifestPreview")
    if activity_table is None or activity_table.rowCount() != len(window._operation_records):
        raise RuntimeError("GUI session activity dialog did not render every typed operation")
    if activity_preview is None or '"raw_output_included": false' not in activity_preview.toPlainText():
        raise RuntimeError("GUI session activity manifest preview did not preserve its raw-output boundary")
    activity_dialog.close()
    window.close()
    application.processEvents()
    print(f"GUI smoke test passed with {len(buttons)} action buttons", flush=True)
    return 0


def dispatch_internal(arguments: Sequence[str]) -> int | None:
    if not arguments:
        return None
    mode = arguments[0]
    remaining = arguments[1:]
    if mode == INTERNAL_PYMOBILEDEVICE3_FLAG:
        return run_pymobiledevice3(remaining)
    if mode == INTERNAL_SMOKE_TEST_FLAG:
        return run_smoke_test(remaining)
    if mode == INTERNAL_WORKER_FLAG:
        if not remaining:
            raise ValueError(f"{INTERNAL_WORKER_FLAG} requires a worker name")
        return run_worker(parsed_worker(remaining[0]), remaining[1:])
    return None
