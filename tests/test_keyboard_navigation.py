from __future__ import annotations

import unittest

from PySide6.QtCore import QEvent, QObject, QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QPushButton

from ios_developer_toolkit.ui_verification import demo_window


class ShownDialogObserver(QObject):
    """Close the real shortcut dialog after its show event reaches Qt."""

    def __init__(self, parent: QObject) -> None:
        super().__init__(parent)
        self.dialogs: list[QDialog] = []

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Show and isinstance(watched, QDialog):
            self.dialogs.append(watched)
            QTimer.singleShot(0, watched.reject)
        return False


class KeyboardNavigationTests(unittest.TestCase):
    def setUp(self) -> None:
        application = QApplication.instance()
        if application is None:
            application = QApplication([])
        if not isinstance(application, QApplication):
            raise RuntimeError("Keyboard navigation checks require a QApplication")
        self.application = application
        self.window = demo_window(application)
        self.addCleanup(self.application.processEvents)
        self.addCleanup(self.window.close)
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()
        self.assertTrue(QTest.qWaitForWindowActive(self.window, 5000))
        self.assertIsNone(self.window.selected_device())

    def test_command_modifier_navigates_and_focuses_workspaces(self) -> None:
        """Qt ControlModifier represents the physical Command key on macOS."""
        QTest.keyClick(self.window, Qt.Key.Key_6, Qt.KeyboardModifier.ControlModifier)
        self.application.processEvents()
        current = self.window.navigation_list.currentItem()
        self.assertIsNotNone(current)
        self.assertEqual(current.text(), "Command Center")
        self.assertIs(self.application.focusWidget(), self.window.command_search_field)

        QTest.keyClick(self.window, Qt.Key.Key_1, Qt.KeyboardModifier.MetaModifier)
        self.application.processEvents()
        current = self.window.navigation_list.currentItem()
        self.assertIsNotNone(current)
        self.assertEqual(current.text(), "Command Center")

        QTest.keyClick(self.window, Qt.Key.Key_1, Qt.KeyboardModifier.ControlModifier)
        self.application.processEvents()
        current = self.window.navigation_list.currentItem()
        self.assertIsNotNone(current)
        self.assertEqual(current.text(), "Home")
        home_action = self.window.findChild(QPushButton, "homeOpenDevice&DDIButton")
        self.assertIsNotNone(home_action)
        self.assertIs(self.application.focusWidget(), home_action)

    def test_command_modifier_opens_actual_keyboard_reference(self) -> None:
        observer = ShownDialogObserver(self.window)
        self.application.installEventFilter(observer)
        self.addCleanup(self.application.removeEventFilter, observer)

        QTest.keyClick(self.window, Qt.Key.Key_Slash, Qt.KeyboardModifier.ControlModifier)
        self.application.processEvents()

        self.assertEqual(len(observer.dialogs), 1)
        dialog = observer.dialogs[0]
        self.assertEqual(dialog.objectName(), "keyboardShortcutsDialog")
        self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)
        self.assertIsNone(self.window.selected_device())
