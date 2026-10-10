"""Exercise Qt's native Cocoa cell ownership without device services or toolkit imports.

Run with the dedicated PySide6 6.9.3 environment and an explicitly selected repaired
platform plugin. Native Cocoa queries cover selected cells, converted placeholder
cells, model changes, and dialog destruction. An offscreen run is an error.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
from pathlib import Path
import sys

import PySide6
from PySide6.QtCore import QCoreApplication, qVersion
from PySide6.QtGui import QAccessible, QGuiApplication
from PySide6.QtWidgets import QApplication, QDialog, QListWidget, QTableWidget, QTableWidgetItem, QVBoxLayout
import shiboken6


def native_pointer(value: object) -> int:
    """Validate the nullable pointer returned by the Objective-C boundary."""
    if value is None:
        return 0
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise RuntimeError("Objective-C returned an invalid pointer value")
    return value


class CocoaAccessibility:
    """Narrow connector to the native Objective-C accessibility protocol."""

    def __init__(self) -> None:
        self._runtime: ctypes.CDLL = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
        self._runtime.objc_getClass.argtypes = [ctypes.c_char_p]
        self._runtime.objc_getClass.restype = ctypes.c_void_p
        self._runtime.sel_registerName.argtypes = [ctypes.c_char_p]
        self._runtime.sel_registerName.restype = ctypes.c_void_p
        self._runtime.objc_autoreleasePoolPush.argtypes = []
        self._runtime.objc_autoreleasePoolPush.restype = ctypes.c_void_p
        self._runtime.objc_autoreleasePoolPop.argtypes = [ctypes.c_void_p]
        self._runtime.objc_autoreleasePoolPop.restype = None
        self._message: ctypes._CFuncPtr = ctypes.CFUNCTYPE(
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p
        )(("objc_msgSend", self._runtime))
        self._make: ctypes._CFuncPtr = ctypes.CFUNCTYPE(
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32
        )(("objc_msgSend", self._runtime))
        self._count: ctypes._CFuncPtr = ctypes.CFUNCTYPE(
            ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p
        )(("objc_msgSend", self._runtime))
        self._item: ctypes._CFuncPtr = ctypes.CFUNCTYPE(
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t
        )(("objc_msgSend", self._runtime))
        self._class = native_pointer(self._runtime.objc_getClass(b"QMacAccessibilityElement"))
        if not self._class:
            raise RuntimeError("Qt's native Cocoa accessibility class was not loaded")

    def selector(self, name: str) -> int:
        return native_pointer(self._runtime.sel_registerName(name.encode("ascii")))

    def element(self, identifier: int) -> int:
        result = native_pointer(self._make(self._class, self.selector("elementWithId:"), identifier))
        if not result:
            raise RuntimeError("Native Cocoa accessibility element creation failed")
        return result

    def read(self, element: int, name: str) -> int:
        return native_pointer(self._message(element, self.selector(name)))

    def count(self, array: int) -> int:
        result = self._count(array, self.selector("count"))
        if not isinstance(result, int) or result < 0 or result > 1024:
            raise RuntimeError("Native accessibility array has an invalid or excessive count")
        return result

    def item(self, array: int, index: int) -> int:
        if index < 0 or index >= self.count(array):
            raise RuntimeError("Native accessibility array index is out of bounds")
        return native_pointer(self._item(array, self.selector("objectAtIndex:"), index))

    def begin_pool(self) -> int:
        return native_pointer(self._runtime.objc_autoreleasePoolPush())

    def drain_pool(self, pool: int) -> None:
        self._runtime.objc_autoreleasePoolPop(pool)


def loaded_cocoa_plugin() -> Path:
    """Read dyld's actual loaded image instead of trusting the plugin search path."""
    dyld = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
    dyld._dyld_image_count.argtypes = []
    dyld._dyld_image_count.restype = ctypes.c_uint32
    dyld._dyld_get_image_name.argtypes = [ctypes.c_uint32]
    dyld._dyld_get_image_name.restype = ctypes.c_char_p
    count = dyld._dyld_image_count()
    if not isinstance(count, int) or count > 4096:
        raise RuntimeError("dyld returned an invalid loaded image count")
    plugins: list[Path] = []
    for index in range(count):
        name = dyld._dyld_get_image_name(index)
        if not isinstance(name, bytes):
            raise RuntimeError("dyld returned an invalid loaded image name")
        path = Path(os.fsdecode(name))
        if path.name == "libqcocoa.dylib":
            plugins.append(path.resolve(strict=True))
    if len(plugins) != 1:
        raise RuntimeError("Expected exactly one loaded Cocoa platform plugin")
    return plugins[0]


def assert_parent(identifier: int) -> None:
    interface = QAccessible.accessibleInterface(identifier)
    if interface is None or not interface.isValid():
        raise RuntimeError("Native cell materialization invalidated the parent accessibility interface")


def materialize_cells(native: CocoaAccessibility, element: int) -> int:
    """Request roles of native placeholder children as an accessibility client does."""
    rows = native.read(element, "accessibilityRows")
    total = 0
    for row in range(native.count(rows)):
        row_element = native.item(rows, row)
        cells = native.read(row_element, "accessibilityChildren")
        for column in range(native.count(cells)):
            cell = native.item(cells, column)
            if not native.read(cell, "accessibilityRole"):
                raise RuntimeError("Native table cell has no accessibility role")
            native.read(cell, "accessibilityTitle")
            total += 1
    return total


def assert_selected(native: CocoaAccessibility, element: int, count: int, identifier: int) -> None:
    actual = native.count(native.read(element, "accessibilitySelectedChildren"))
    if actual != count:
        raise RuntimeError(f"Native selected-child count {actual} differs from expected {count}")
    assert_parent(identifier)


def populate_table(view: QTableWidget, columns: int) -> None:
    view.setColumnCount(columns)
    for row in range(view.rowCount()):
        for column in range(columns):
            view.setItem(row, column, QTableWidgetItem(f"row {row}, column {column}"))


def verify_table(application: QApplication, native: CocoaAccessibility) -> None:
    pool = native.begin_pool()
    dialog = QDialog()
    layout = QVBoxLayout(dialog)
    view = QTableWidget(2, 4, dialog)
    populate_table(view, 4)
    view.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    layout.addWidget(view)
    dialog.resize(1000, 600)
    dialog.show()
    application.processEvents()
    native.read(int(dialog.winId()), "accessibilityChildren")
    if not QAccessible.isActive():
        raise RuntimeError("The native NSView query did not activate Qt accessibility notifications")
    interface = QAccessible.queryAccessibleInterface(view)
    if interface is None:
        raise RuntimeError("Qt did not provide a table accessibility interface")
    identifier = QAccessible.uniqueId(interface)
    element = native.element(identifier)
    for iteration in range(12):
        view.selectRow(iteration % 2)
        application.processEvents()
        assert_selected(native, element, 4, identifier)
        if materialize_cells(native, element) != 8:
            raise RuntimeError("Native placeholder materialization missed table cells")
        assert_parent(identifier)
    for columns in (5, 2, 4):
        populate_table(view, columns)
        view.clearSelection()
        view.selectRow(0)
        if len(view.selectedItems()) != columns:
            raise RuntimeError("The synthetic row selection does not cover the changed table")
        application.processEvents()
        assert_selected(native, element, columns, identifier)
        if materialize_cells(native, element) != columns * 2:
            raise RuntimeError("Native cells did not follow table column changes")
        assert_parent(identifier)
    view.clearContents()
    populate_table(view, 4)
    view.selectRow(1)
    application.processEvents()
    assert_selected(native, element, 4, identifier)
    materialize_cells(native, element)
    dialog.hide()
    dialog.show()
    application.processEvents()
    assert_selected(native, element, 4, identifier)
    shiboken6.delete(dialog)
    application.processEvents()
    native.drain_pool(pool)


def verify_list_teardown(application: QApplication, native: CocoaAccessibility) -> None:
    for cycle in range(3):
        pool = native.begin_pool()
        dialog = QDialog()
        layout = QVBoxLayout(dialog)
        view = QListWidget(dialog)
        view.addItems(["one", "two", "three"])
        view.setCurrentRow(0)
        layout.addWidget(view)
        dialog.resize(600, 400)
        dialog.show()
        application.processEvents()
        interface = QAccessible.queryAccessibleInterface(view)
        if interface is None:
            raise RuntimeError("Qt did not provide a list accessibility interface")
        identifier = QAccessible.uniqueId(interface)
        element = native.element(identifier)
        materialize_cells(native, element)
        assert_selected(native, element, 1, identifier)
        view.clear()
        view.addItems(["filtered result"])
        view.setCurrentRow(0)
        application.processEvents()
        materialize_cells(native, element)
        assert_selected(native, element, 1, identifier)
        dialog.hide()
        dialog.show()
        application.processEvents()
        materialize_cells(native, element)
        dialog.accept()
        shiboken6.delete(dialog)
        application.processEvents()
        native.drain_pool(pool)
        print(f"Native list model refresh and dialog destruction cycle {cycle + 1}: passed", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-plugin", required=True, type=Path)
    arguments = parser.parse_args()
    plugin_argument = arguments.expected_plugin.absolute()
    if plugin_argument.parent.name != "platforms":
        raise RuntimeError("The expected plugin must be in an explicit plugins/platforms directory")
    expected = plugin_argument.resolve(strict=True)
    if sys.platform != "darwin":
        raise RuntimeError("The native Cocoa ownership check requires macOS")
    if PySide6.__version__ != "6.9.3" or shiboken6.__version__ != "6.9.3" or qVersion() != "6.9.3":
        raise RuntimeError("The repaired Cocoa plugin requires exact PySide6/Shiboken/Qt 6.9.3")
    QCoreApplication.setLibraryPaths([
        str(plugin_argument.parent.parent), str(Path(PySide6.__file__).parent / "Qt/plugins")
    ])
    application = QApplication(["toolkit-native-cocoa-ownership-check", "-platform", "cocoa"])
    if QGuiApplication.platformName() != "cocoa" or loaded_cocoa_plugin() != expected:
        raise RuntimeError("Native ownership check did not load the exact expected Cocoa plugin")
    native = CocoaAccessibility()
    verify_table(application, native)
    verify_list_teardown(application, native)
    print(json.dumps({"qt_version": qVersion(), "native_selection": "passed", "native_placeholder_teardown": "passed"}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
