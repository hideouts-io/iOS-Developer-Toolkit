"""Shared desktop appearance and locally rendered vector icons; no downloaded assets."""

from __future__ import annotations

from typing import Mapping

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QIcon, QPainter, QPalette, QPixmap
from PySide6.QtSvg import QSvgRenderer


def appearance_colors(palette: QPalette) -> Mapping[str, str]:
    """Choose contrasting surfaces from the host's actual window palette."""
    if palette.color(QPalette.ColorRole.Window).lightness() < 128:
        return {
            "canvas": "#171a21", "surface": "#212630", "sidebar": "#1c2029",
            "field": "#181d26", "ink": "#edf1f8", "muted": "#aeb8c9",
            "border": "#3c4657", "hover": "#2e3747", "accent": "#98b8ff",
            "on_accent": "#14213c", "selection": "#293c60", "disabled": "#8590a3",
            "warning": "#392f1e", "warning_ink": "#f5d999", "success": "#21372e",
            "success_ink": "#a0e3bd", "danger": "#40282b", "danger_ink": "#ffb8bd",
        }
    return {
        "canvas": "#f5f6f9", "surface": "#ffffff", "sidebar": "#eef0f5",
        "field": "#ffffff", "ink": "#202b3e", "muted": "#5d687c",
        "border": "#d5dbe6", "hover": "#e6ebf4", "accent": "#285bd0",
        "on_accent": "#ffffff", "selection": "#e0eaff", "disabled": "#737f92",
        "warning": "#fff6e3", "warning_ink": "#77551b", "success": "#e7f5ec",
        "success_ink": "#23643b", "danger": "#ffebec", "danger_ink": "#982d3b",
    }


def toolkit_stylesheet(palette: QPalette) -> str:
    """Style every workspace and dialog while retaining native control semantics."""
    c = appearance_colors(palette)
    return f"""
        QWidget {{ color: {c['ink']}; font-size: 13px; }}
        QMainWindow, QDialog, QMessageBox {{ background: {c['canvas']}; }}
        QLabel {{ background: transparent; }}
        QScrollArea, QStackedWidget {{ border: none; background: {c['canvas']}; }}
        QScrollArea > QWidget > QWidget {{ background: {c['canvas']}; }}
        QGroupBox {{ background: {c['surface']}; border: 1px solid {c['border']};
            border-radius: 12px; margin-top: 18px; padding: 16px 12px 12px; font-weight: 600; }}
        QGroupBox::title {{ subcontrol-origin: margin; left: 14px; padding: 0 5px; }}
        QPushButton {{ min-height: 26px; padding: 4px 12px; border: 1px solid {c['border']};
            border-radius: 7px; background: {c['surface']}; font-weight: 500; }}
        QPushButton:hover {{ background: {c['hover']}; border-color: {c['accent']}; }}
        QPushButton:pressed, QPushButton:checked {{ background: {c['selection']}; }}
        QPushButton:focus, QLineEdit:focus, QComboBox:focus, QSpinBox:focus,
        QDoubleSpinBox:focus, QPlainTextEdit:focus, QTextBrowser:focus,
        QListWidget:focus, QTableWidget:focus {{ border: 2px solid {c['accent']}; }}
        QPushButton:disabled {{ color: {c['disabled']}; background: {c['canvas']}; }}
        QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextBrowser,
        QTableWidget, QListWidget {{ border: 1px solid {c['border']}; border-radius: 7px;
            background: {c['field']}; padding: 6px; selection-background-color: {c['selection']};
            selection-color: {c['ink']}; }}
        QComboBox QAbstractItemView {{ background: {c['surface']}; color: {c['ink']};
            selection-background-color: {c['selection']}; selection-color: {c['ink']}; }}
        QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled,
        QDoubleSpinBox:disabled {{ color: {c['disabled']}; background: {c['canvas']}; }}
        QPlainTextEdit, QTextBrowser {{ padding: 10px; }}
        QPlainTextEdit {{ font-family: Menlo; font-size: 12px; }}
        QHeaderView::section {{ background: {c['sidebar']}; color: {c['muted']};
            border: none; border-bottom: 1px solid {c['border']}; padding: 8px; font-weight: 600; }}
        QTableWidget {{ gridline-color: {c['border']}; alternate-background-color: {c['canvas']}; }}
        QTabWidget::pane {{ border: 1px solid {c['border']}; border-radius: 10px; background: {c['surface']}; }}
        QTabBar::tab {{ background: {c['sidebar']}; color: {c['muted']}; padding: 9px 14px;
            border-bottom: 2px solid transparent; }}
        QTabBar::tab:selected {{ background: {c['surface']}; color: {c['ink']}; border-bottom-color: {c['accent']}; }}
        QProgressBar {{ border: 1px solid {c['border']}; border-radius: 5px; background: {c['field']};
            text-align: center; min-height: 18px; }}
        QProgressBar::chunk {{ background: {c['accent']}; border-radius: 4px; }}
        QSplitter::handle {{ background: {c['border']}; width: 1px; }}
        QToolTip {{ background: {c['surface']}; color: {c['ink']}; border: 1px solid {c['border']}; padding: 5px; }}
        QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
        QScrollBar::handle:vertical {{ background: {c['border']}; min-height: 30px; border-radius: 4px; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
        #workspaceSidebar {{ background: {c['sidebar']}; border: none; }}
        #appTitle {{ font-size: 15px; font-weight: 700; }}
        #appSubtitle, #sidebarVersion, #workspaceSubtitle, #homeStats, #liveLogRequirement,
        #commandAdvancedNotes, #commandPrerequisites {{ color: {c['muted']}; }}
        #sidebarVersion {{ font-size: 11px; }}
        #workspaceNavigation {{ background: transparent; border: none; padding: 0; }}
        #workspaceNavigation::item {{ min-height: 24px; border-radius: 7px; padding: 3px 9px; }}
        #workspaceNavigation::item:disabled {{ color: {c['muted']}; font-size: 11px; }}
        #workspaceNavigation::item:hover {{ background: {c['hover']}; }}
        #workspaceNavigation::item:selected {{ background: {c['selection']}; color: {c['ink']}; }}
        #deviceToolbar, #workspaceHeader {{ background: {c['canvas']}; border: none;
            border-bottom: 1px solid {c['border']}; }}
        #workspaceTitle {{ font-size: 20px; font-weight: 700; }}
        #workspaceSubtitle {{ font-size: 12px; }}
        #devicePicker {{ min-height: 28px; }}
        #connectionBanner {{ background: {c['selection']}; color: {c['ink']};
            border: none; border-radius: 8px; padding: 9px 12px; }}
        #homeHero {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 14px; }}
        #pageTitle {{ font-size: 27px; font-weight: 700; }}
        #homeEyebrow {{ color: {c['accent']}; font-size: 11px; font-weight: 700; }}
        #homeStats {{ font-size: 12px; padding-top: 6px; }}
        QFrame[workflowCard="true"] {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 12px; }}
        QLabel[cardTitle="true"] {{ font-size: 14px; font-weight: 600; }}
        QLabel[cardDescription="true"] {{ color: {c['muted']}; }}
        QPushButton[primaryAction="true"] {{ background: {c['accent']}; color: {c['on_accent']};
            border-color: {c['accent']}; font-weight: 600; }}
        QPushButton[primaryAction="true"]:hover {{ border: 2px solid {c['ink']}; }}
        QPushButton[primaryAction="true"]:disabled {{ background: {c['canvas']}; color: {c['disabled']}; border-color: {c['border']}; }}
        #commandPresetBrowser, #commandPresetDetail {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 12px; }}
        #commandRiskBadge {{ border-radius: 6px; padding: 5px 9px; font-size: 11px; font-weight: 600; }}
        #commandRiskBadge[risk="read-only"] {{ background: {c['success']}; color: {c['success_ink']}; }}
        #commandRiskBadge[risk="host-write"] {{ background: {c['warning']}; color: {c['warning_ink']}; }}
        #commandRiskBadge[risk="device-change"] {{ background: {c['danger']}; color: {c['danger_ink']}; }}
        #protocolStackSummary {{ color: {c['muted']}; font-size: 12px; }}
        #collectionPrivacyWarning, #installedAppsPrivacyWarning, #backupEncryptionWarning,
        #locationPrivacyWarning, #capabilityMatrixBoundary, #firmwareSafetyBoundary {{
            background: {c['warning']}; color: {c['warning_ink']}; border: 1px solid {c['border']};
            border-radius: 8px; padding: 10px; }}
        #capabilityMatrixStatus {{ background: {c['selection']}; border-radius: 8px; padding: 10px; }}
    """


def interface_icon(identifier: str, palette: QPalette) -> QIcon:
    """Render a consistent icon for a closed set of workspace and toolbar identifiers."""
    paths: Mapping[str, str] = {
        "Home": '<path d="m3 10 9-7 9 7v10H3Z M9 20v-7h6v7"/>',
        "Device & DDI": '<rect x="6" y="2" width="12" height="20" rx="3"/><path d="M10 18h4"/>',
        "Capability Matrix": '<path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6Z m-4 9 3 3 5-6"/>',
        "Location Lab": '<path d="M19 9c0 6-7 12-7 12S5 15 5 9a7 7 0 0 1 14 0Z"/><circle cx="12" cy="9" r="2"/>',
        "Live Logs": '<path d="M3 12h4l3-7 4 14 3-7h4"/>',
        "Command Center": '<path d="m5 7 5 5-5 5 M13 17h6"/>',
        "Installed Apps": '<rect x="3" y="3" width="7" height="7" rx="2"/><rect x="14" y="3" width="7" height="7" rx="2"/><rect x="3" y="14" width="7" height="7" rx="2"/><rect x="14" y="14" width="7" height="7" rx="2"/>',
        "Backup": '<path d="M4 7h16v14H4Z M3 3h18v4H3Z M9 11h6"/>',
        "Sideload IPA": '<path d="M4 15v6h16v-6 M12 16V3 m-5 5 5-5 5 5"/>',
        "Evidence Capture": '<path d="M14 3H5v18h14V8Z M14 3v5h5 M8 12h8 M8 16h5"/>',
        "Man Pages": '<path d="M12 5C9 3 5 3 2 4v16c3-1 7-1 10 1 3-2 7-2 10-1V4c-3-1-7-1-10 1Z M12 5v16"/>',
        "Scope & Safety": '<path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6Z M12 8v5 M12 16h.01"/>',
        "Simulators": '<rect x="2" y="3" width="14" height="12" rx="2"/><rect x="15" y="8" width="7" height="14" rx="2"/><path d="M5 20h8 M9 15v5"/>',
        "Xcode Tools": '<path d="m4 3 17 17-3 3L1 6Z M17 3l4 4-9 9-4-4Z M4 16l4 4-5 1Z"/>',
        "Security Analysis": '<circle cx="10" cy="10" r="7"/><path d="m15 15 6 6 M7 10h6 M10 7v6"/>',
        "Firmware": '<rect x="5" y="5" width="14" height="14" rx="2"/><path d="M9 2v3 M15 2v3 M9 19v3 M15 19v3 M2 9h3 M2 15h3 M19 9h3 M19 15h3 M9 9h6v6H9Z"/>',
        "refresh": '<path d="M20 11a8 8 0 1 0-2 7 M20 3v8h-8"/>',
        "reconnect": '<path d="m10 14-3 3a4 4 0 0 1-6-6l4-4a4 4 0 0 1 6 0 m2 3 3-3a4 4 0 0 1 6 6l-4 4a4 4 0 0 1-6 0 M8 16l8-8"/>',
        "shortcuts": '<rect x="2" y="5" width="20" height="14" rx="2"/><path d="M6 9h.01 M10 9h.01 M14 9h.01 M18 9h.01 M6 13h.01 M10 13h.01 M14 13h.01 M18 13h.01 M8 16h8"/>',
        "support": '<circle cx="12" cy="12" r="9"/><path d="M9 9a3 3 0 1 1 4 3c-1 1-1 2-1 2 M12 17h.01"/>',
        "palette": '<circle cx="10" cy="10" r="6"/><path d="m15 15 6 6"/>',
        "activity": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l4 2"/>',
    }
    color = appearance_colors(palette)["muted"]
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24"><g fill="none" stroke="{color}" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">{paths[identifier]}</g></svg>'
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    if not renderer.isValid():
        raise RuntimeError(f"Invalid interface icon: {identifier}")
    pixmap = QPixmap(48, 48)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    pixmap.setDevicePixelRatio(2)
    return QIcon(pixmap)
