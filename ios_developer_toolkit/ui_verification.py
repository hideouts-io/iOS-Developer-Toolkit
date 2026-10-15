"""Device-free native UI checks and screenshots of the actual application widgets."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPalette
from PySide6.QtWidgets import QApplication, QPushButton, QScrollArea

from ios_developer_toolkit.action_palette import ActionPaletteDialog
from ios_developer_toolkit.app import MainWindow, application_icon_path
from ios_developer_toolkit.ui_theme import appearance_colors
from ios_developer_toolkit.workspace_profile import WORKSPACE_NAMES


def contrast_ratio(foreground: QColor, background: QColor) -> float:
    """Return WCAG relative-luminance contrast for the colors actually used by the UI."""
    def luminance(color: QColor) -> float:
        channels = (color.redF(), color.greenF(), color.blueF())
        linear = tuple(value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4 for value in channels)
        return sum(value * weight for value, weight in zip(linear, (0.2126, 0.7152, 0.0722), strict=True))
    values = sorted((luminance(foreground), luminance(background)))
    return (values[1] + 0.05) / (values[0] + 0.05)


def demo_window(application: QApplication) -> MainWindow:
    """Create an isolated walkthrough with discovery stopped and device actions disabled."""
    application.setApplicationName("iOS Developer Toolkit")
    application.setOrganizationName("hideouts.io")
    application.setWindowIcon(QIcon(str(application_icon_path())))
    window = MainWindow()
    window._scanner.stop()
    window._devices_changed(())
    window.toggle_demo_mode()
    if window.selected_device() is not None:
        raise RuntimeError("UI verification must never expose a physical-device target")
    return window


def save_window(window: MainWindow | ActionPaletteDialog, destination: Path) -> None:
    """Render an exposed Qt window and fail explicitly if its pixels cannot be saved."""
    window.repaint()
    if not window.grab().save(str(destination)):
        raise RuntimeError(f"Could not save UI screenshot: {destination}")


def run_ui_check(arguments: Sequence[str]) -> int:
    """Check both themes and window sizes in the source or frozen app without device operations."""
    if len(arguments) != 1:
        raise ValueError("UI check requires one new output directory for screenshots and layout results")
    output = Path(arguments[0]).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=False)
    application = QApplication(["toolkit-ui-check"])
    original_palette = QPalette(application.palette())
    window = demo_window(application)
    window.show()
    records: list[dict[str, str | int]] = []
    overflow: list[str] = []
    try:
        for theme, background, foreground in (("light", "#f5f6f9", "#202b3e"), ("dark", "#171a21", "#edf1f8")):
            palette = QPalette(original_palette)
            for role in (QPalette.ColorRole.Window, QPalette.ColorRole.Base, QPalette.ColorRole.Button):
                palette.setColor(role, QColor(background))
            for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
                palette.setColor(role, QColor(foreground))
            application.setPalette(palette)
            application.processEvents()
            colors = appearance_colors(palette)
            for ink, surface in (("ink", "surface"), ("muted", "surface"), ("on_accent", "accent"), ("warning_ink", "warning"), ("danger_ink", "danger")):
                ratio = contrast_ratio(QColor(colors[ink]), QColor(colors[surface]))
                if ratio < 4.5:
                    raise RuntimeError(f"{theme} theme {ink}/{surface} contrast is {ratio:.2f}; requires at least 4.5")
            for width, height in ((1280, 860), (1040, 680)):
                window.resize(width, height)
                for destination in WORKSPACE_NAMES:
                    window.navigate_to_page(destination)
                    application.processEvents()
                    if window.workspace_title.text() != destination:
                        raise RuntimeError(f"Workspace header failed to follow navigation: {destination}")
                    scroll = window.page_stack.currentWidget()
                    if not isinstance(scroll, QScrollArea):
                        raise TypeError(f"Workspace has no scroll container: {destination}")
                    horizontal = scroll.horizontalScrollBar().maximum()
                    records.append({"workspace": destination, "theme": theme, "width": width, "height": height, "horizontal_overflow": horizontal})
                    if horizontal:
                        overflow.append(f"{destination} {theme} {width}x{height}: {horizontal}px")
                    if destination in ("Home", "Device & DDI", "Command Center", "Firmware", "Security Analysis"):
                        slug = destination.lower().replace(" & ", "-").replace(" ", "-")
                        save_window(window, output / f"{slug}-{theme}-{width}.png")
                window.navigate_to_page("Home")
                dialog = ActionPaletteDialog(window._eligible_action_palette_entries(), window)
                dialog.show()
                dialog.search.setText("firmware")
                application.processEvents()
                if dialog.results.count() != 1:
                    raise RuntimeError("The Action Palette did not find the Firmware workspace")
                save_window(dialog, output / f"palette-{theme}-{width}.png")
                dialog.close()
            device_commands = tuple(
                row for row in range(window.command_preset_list.count())
                if window.command_preset_list.item(row).data(Qt.ItemDataRole.UserRole) == "lockdown"
            )
            if len(device_commands) != 1:
                raise RuntimeError("UI verification requires the Lockdown device command in the guided catalog")
            window.command_preset_list.setCurrentRow(device_commands[0])
            application.processEvents()
            for button_name in ("runGuidedCommandButton", "runCommandReadinessButton", "installIPAButton", "startDeviceBackupButton"):
                button = window.findChild(QPushButton, button_name)
                if button is None:
                    raise RuntimeError(f"UI verification could not find the required device action: {button_name}")
                if button.isEnabled():
                    raise RuntimeError(f"Demo Mode exposed a device action: {button_name}")
        (output / "layout-results.json").write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
        if overflow:
            raise RuntimeError("Workspace content overflows horizontally: " + "; ".join(overflow))
        print(f"UI check passed: {len(records)} workspace/theme/size layouts, contrast checks, palette search, and Demo Mode boundaries. Screenshots: {output}")
        return 0
    finally:
        window.close()
        application.processEvents()
        application.setPalette(original_palette)


def run_demo_preview(arguments: Sequence[str]) -> int:
    """Open a safe interactive preview of the exact source or frozen build for native UI inspection."""
    if arguments:
        raise ValueError("Demo preview does not accept arguments")
    application = QApplication(["toolkit-demo-preview"])
    window = demo_window(application)
    window.show()
    window.raise_()
    window.activateWindow()
    return application.exec()
