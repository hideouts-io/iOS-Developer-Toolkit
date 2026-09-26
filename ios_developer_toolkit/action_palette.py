from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)


class ActionPaletteError(ValueError):
    """Raised when an eligible action palette cannot be represented safely."""


@dataclass(frozen=True)
class ActionPaletteEntry:
    identifier: str
    title: str
    category: str
    summary: str
    keywords: tuple[str, ...]


def action_palette_entry(
    identifier: str,
    title: str,
    category: str,
    summary: str,
    keywords: tuple[str, ...],
) -> ActionPaletteEntry:
    normalized_values = tuple(value.strip() for value in (identifier, title, category, summary))
    if any(not value for value in normalized_values):
        raise ActionPaletteError("Action palette identifier, title, category, and summary must be non-empty")
    normalized_keywords = tuple(keyword.strip() for keyword in keywords)
    if any(not keyword for keyword in normalized_keywords):
        raise ActionPaletteError("Action palette keywords cannot contain empty values")
    return ActionPaletteEntry(*normalized_values, normalized_keywords)


def validate_action_palette(entries: tuple[ActionPaletteEntry, ...]) -> tuple[ActionPaletteEntry, ...]:
    identifiers = tuple(entry.identifier for entry in entries)
    if len(set(identifiers)) != len(identifiers):
        duplicates = sorted(identifier for identifier in set(identifiers) if identifiers.count(identifier) > 1)
        raise ActionPaletteError(f"Action palette identifiers must be unique: {', '.join(duplicates)}")
    return entries


def filter_action_palette(
    entries: tuple[ActionPaletteEntry, ...],
    query: str,
) -> tuple[ActionPaletteEntry, ...]:
    terms = tuple(term for term in query.strip().casefold().split() if term)
    matching: list[tuple[int, str, str, ActionPaletteEntry]] = []
    for entry in entries:
        title = entry.title.casefold()
        haystack = " ".join((entry.title, entry.category, entry.summary, *entry.keywords)).casefold()
        if not all(term in haystack for term in terms):
            continue
        rank = 0 if not terms or title.startswith(terms[0]) else 1 if any(term in title for term in terms) else 2
        matching.append((rank, entry.category.casefold(), title, entry))
    return tuple(item[3] for item in sorted(matching, key=lambda item: item[:3]))


class ActionPaletteDialog(QDialog):
    """Search and return one action from the caller-provided eligible set."""

    def __init__(self, entries: tuple[ActionPaletteEntry, ...], parent: QWidget | None) -> None:
        super().__init__(parent)
        self._entries = validate_action_palette(entries)
        self._selected_identifier: str | None = None
        self.setObjectName("actionPaletteDialog")
        self.setWindowTitle("Action Palette")
        self.resize(720, 520)
        layout = QVBoxLayout(self)
        heading = QLabel("Run or open an action that is eligible in the current app state")
        heading.setWordWrap(True)
        layout.addWidget(heading)
        self.search = QLineEdit()
        self.search.setObjectName("actionPaletteSearch")
        self.search.setPlaceholderText("Search workspaces, commands, diagnostics, and utilities")
        self.search.setAccessibleName("Search eligible actions")
        self.search.textChanged.connect(self._filter_entries)
        self.search.returnPressed.connect(self._accept_current)
        layout.addWidget(self.search)
        self.results = QListWidget()
        self.results.setObjectName("actionPaletteResults")
        self.results.setAccessibleName("Eligible action results")
        self.results.currentItemChanged.connect(self._selection_changed)
        self.results.itemActivated.connect(self._item_activated)
        layout.addWidget(self.results, 1)
        self.summary = QLabel()
        self.summary.setObjectName("actionPaletteSummary")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Open | QDialogButtonBox.StandardButton.Cancel)
        buttons.setObjectName("actionPaletteButtons")
        open_button = buttons.button(QDialogButtonBox.StandardButton.Open)
        cancel_button = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        open_button.setObjectName("actionPaletteOpenButton")
        cancel_button.setObjectName("actionPaletteCancelButton")
        open_button.setAccessibleName("Open selected eligible action")
        cancel_button.setAccessibleName("Close action palette")
        buttons.accepted.connect(self._accept_current)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._open_button = open_button
        self._filter_entries()
        self.search.setFocus(Qt.FocusReason.ShortcutFocusReason)

    def selected_identifier(self) -> str:
        if self._selected_identifier is None:
            raise ActionPaletteError("Action palette closed without selecting an eligible action")
        return self._selected_identifier

    def _filter_entries(self) -> None:
        matching = filter_action_palette(self._entries, self.search.text())
        self.results.blockSignals(True)
        self.results.clear()
        for entry in matching:
            item = QListWidgetItem(f"{entry.title}  ·  {entry.category}")
            item.setData(Qt.ItemDataRole.UserRole, entry.identifier)
            item.setToolTip(entry.summary)
            self.results.addItem(item)
        if matching:
            self.results.setCurrentRow(0)
            self.summary.setText(matching[0].summary)
        else:
            self.summary.setText("No eligible action matches this search in the current app state.")
        self._open_button.setEnabled(bool(matching))
        self.results.blockSignals(False)

    def _selection_changed(self, current: QListWidgetItem | None, previous: QListWidgetItem | None) -> None:
        del previous
        if current is None:
            self._open_button.setEnabled(False)
            return
        identifier = current.data(Qt.ItemDataRole.UserRole)
        if not isinstance(identifier, str):
            raise ActionPaletteError("Selected action palette row has no string identifier")
        entry = next((candidate for candidate in self._entries if candidate.identifier == identifier), None)
        if entry is None:
            raise ActionPaletteError(f"Selected action palette entry is unavailable: {identifier}")
        self.summary.setText(entry.summary)
        self._open_button.setEnabled(True)

    def _item_activated(self, item: QListWidgetItem) -> None:
        self.results.setCurrentItem(item)
        self._accept_current()

    def _accept_current(self) -> None:
        current = self.results.currentItem()
        if current is None:
            return
        identifier = current.data(Qt.ItemDataRole.UserRole)
        if not isinstance(identifier, str):
            raise ActionPaletteError("Selected action palette row has no string identifier")
        self._selected_identifier = identifier
        self.accept()
