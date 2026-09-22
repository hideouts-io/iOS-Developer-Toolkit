from __future__ import annotations

import os
import sys
from collections.abc import Callable, Sequence

from ios_developer_toolkit.runtime import (
    INTERNAL_PYMOBILEDEVICE3_FLAG,
    INTERNAL_SMOKE_TEST_FLAG,
    INTERNAL_WORKER_FLAG,
    ToolkitWorker,
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
    from PySide6.QtCore import SIGNAL
    from PySide6.QtWidgets import QApplication, QLabel, QPushButton

    from ios_developer_toolkit.app import MainWindow

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
    expected_shortcuts = {
        "shortcutRetryDeviceScan",
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
