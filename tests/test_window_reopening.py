from __future__ import annotations

import unittest

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QMainWindow

from ios_developer_toolkit.app import activate_application_window


class WindowReopeningTests(unittest.TestCase):
    def setUp(self) -> None:
        application = QApplication.instance()
        if application is None:
            application = QApplication([])
        if not isinstance(application, QApplication):
            raise RuntimeError("Window reopening checks require a QApplication")
        self.application = application
        self.main_window = QMainWindow()
        self.main_window.resize(640, 480)
        self.addCleanup(self.main_window.close)

    def test_activation_restores_hidden_and_minimized_main_window(self) -> None:
        self.main_window.show()
        self.application.processEvents()
        self.main_window.hide()
        self.application.processEvents()
        self.assertTrue(self.main_window.isHidden())
        activate_application_window(self.main_window, Qt.ApplicationState.ApplicationActive)
        self.application.processEvents()
        self.assertTrue(self.main_window.isVisible())
        self.main_window.showMinimized()
        self.application.processEvents()
        self.assertTrue(self.main_window.isMinimized())
        activate_application_window(self.main_window, Qt.ApplicationState.ApplicationActive)
        self.application.processEvents()
        self.assertFalse(self.main_window.isMinimized())
        self.assertTrue(self.main_window.isVisible())

    def test_inactive_notifications_leave_main_window_hidden(self) -> None:
        for state in (Qt.ApplicationState.ApplicationInactive, Qt.ApplicationState.ApplicationHidden):
            with self.subTest(state=state):
                activate_application_window(self.main_window, state)
                self.application.processEvents()
                self.assertTrue(self.main_window.isHidden())

    def test_activation_preserves_fullscreen_and_maximized_state(self) -> None:
        for state in (Qt.WindowState.WindowFullScreen, Qt.WindowState.WindowMaximized):
            with self.subTest(state=state):
                self.main_window.setWindowState(state)
                self.main_window.show()
                self.application.processEvents()
                activate_application_window(self.main_window, Qt.ApplicationState.ApplicationActive)
                self.application.processEvents()
                self.assertTrue(self.main_window.windowState() & state)
                self.main_window.showNormal()
                self.application.processEvents()

    def test_activation_keeps_modal_dialog_in_front(self) -> None:
        self.main_window.show()
        dialog = QDialog()
        self.addCleanup(dialog.close)
        dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
        dialog.show()
        self.assertTrue(QTest.qWaitForWindowExposed(dialog, 5000))
        self.assertIs(self.application.activeModalWidget(), dialog)
        self.main_window.hide()
        self.application.processEvents()
        activate_application_window(self.main_window, Qt.ApplicationState.ApplicationActive)
        self.assertTrue(QTest.qWaitForWindowActive(dialog, 5000))
        self.assertTrue(self.main_window.isVisible())
        self.assertIs(self.application.activeModalWidget(), dialog)
        self.assertTrue(dialog.isActiveWindow())
        self.assertFalse(self.main_window.isActiveWindow())

    def test_activation_keeps_existing_floating_window_in_front(self) -> None:
        self.main_window.show()
        floating = QDialog(self.main_window)
        self.addCleanup(floating.close)
        floating.show()
        self.assertTrue(QTest.qWaitForWindowExposed(floating, 5000))
        floating.raise_()
        floating.activateWindow()
        self.assertTrue(QTest.qWaitForWindowActive(floating, 5000))
        self.assertIs(self.application.activeWindow(), floating)
        activate_application_window(self.main_window, Qt.ApplicationState.ApplicationActive)
        self.assertTrue(QTest.qWaitForWindowActive(floating, 5000))
        self.assertTrue(floating.isActiveWindow())
        self.assertFalse(self.main_window.isActiveWindow())
