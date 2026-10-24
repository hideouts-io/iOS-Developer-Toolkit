"""Qt combo inventory updates that retain native accessibility model ownership."""

from __future__ import annotations

from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QComboBox


def replace_combo_entries(
    combo: QComboBox,
    entries: tuple[tuple[str, str], ...],
    selected_identifier: str | None,
) -> None:
    """Retain a requested ID, otherwise select the first row or an empty inventory.

    Populate a detached model before attaching it. Qt's model reset clears cached
    Cocoa accessibility IDs; removing attached rows can dereference expired IDs.
    Identical entries retain their model. Combo signals are blocked during the
    update, so the caller must update any dependent controls afterward.
    """
    current = tuple((combo.itemText(index), combo.itemData(index)) for index in range(combo.count()))
    with QSignalBlocker(combo):
        if current != entries:
            model = QStandardItemModel(combo)
            for label, identifier in entries:
                item = QStandardItem(label)
                item.setData(identifier, Qt.ItemDataRole.UserRole)
                model.appendRow(item)
            combo.setModel(model)
        index = combo.findData(selected_identifier)
        combo.setCurrentIndex(index if index >= 0 else 0 if entries else -1)
