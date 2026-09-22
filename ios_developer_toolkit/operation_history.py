from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import TypeAlias

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ios_developer_toolkit.qt_process import OperationResult, ProcessOutcome


JsonScalar: TypeAlias = str | int | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


class OperationHistoryError(ValueError):
    """Raised when an operation record or explicit manifest export is invalid."""


@dataclass(frozen=True)
class OperationContext:
    title: str
    workspace: str
    target: str
    transport: str
    prerequisites: tuple[str, ...]
    output_paths: tuple[str, ...]


@dataclass(frozen=True)
class OperationRecord:
    identifier: str
    title: str
    workspace: str
    target: str
    transport: str
    argv: tuple[str, ...]
    started_at: str
    finished_at: str
    duration_milliseconds: int
    outcome: ProcessOutcome
    exit_code: int | None
    error_message: str | None
    stdout_bytes: int
    stdout_sha256: str
    stderr_bytes: int
    stderr_sha256: str
    prerequisites: tuple[str, ...]
    output_paths: tuple[str, ...]


def operation_context(
    title: str,
    workspace: str,
    target: str,
    transport: str,
    prerequisites: tuple[str, ...],
    output_paths: tuple[str, ...],
) -> OperationContext:
    required_values = {
        "title": title.strip(),
        "workspace": workspace.strip(),
        "target": target.strip(),
        "transport": transport.strip(),
    }
    empty_fields = tuple(name for name, value in required_values.items() if not value)
    if empty_fields:
        raise OperationHistoryError(f"Operation context fields cannot be empty: {', '.join(empty_fields)}")
    normalized_prerequisites = tuple(value.strip() for value in prerequisites)
    if any(not value for value in normalized_prerequisites):
        raise OperationHistoryError("Operation prerequisites cannot contain empty values")
    normalized_paths = _normalized_output_paths(output_paths)
    return OperationContext(
        required_values["title"],
        required_values["workspace"],
        required_values["target"],
        required_values["transport"],
        normalized_prerequisites,
        normalized_paths,
    )


def with_output_paths(context: OperationContext, output_paths: tuple[str, ...]) -> OperationContext:
    normalized_paths = _normalized_output_paths(output_paths)
    return replace(context, output_paths=normalized_paths)


def operation_record(context: OperationContext, result: OperationResult) -> OperationRecord:
    if not result.argv or not result.argv[0]:
        raise OperationHistoryError("Operation result must contain a non-empty argument vector")
    started = _parse_timestamp(result.started_at, "started_at")
    finished = _parse_timestamp(result.finished_at, "finished_at")
    duration_milliseconds = round((finished - started).total_seconds() * 1000)
    if duration_milliseconds < 0:
        raise OperationHistoryError("Operation finish time cannot precede its start time")
    identity_payload = "\0".join(
        (context.workspace, context.title, result.started_at, result.finished_at, *result.argv)
    ).encode("utf-8")
    identifier = hashlib.sha256(identity_payload).hexdigest()[:16]
    return OperationRecord(
        identifier,
        context.title,
        context.workspace,
        context.target,
        context.transport,
        result.argv,
        result.started_at,
        result.finished_at,
        duration_milliseconds,
        result.outcome,
        result.exit_code,
        result.error_message,
        len(result.stdout),
        hashlib.sha256(result.stdout).hexdigest(),
        len(result.stderr),
        hashlib.sha256(result.stderr).hexdigest(),
        context.prerequisites,
        context.output_paths,
    )


def append_operation_record(
    records: tuple[OperationRecord, ...],
    record: OperationRecord,
    maximum_records: int,
) -> tuple[OperationRecord, ...]:
    if maximum_records <= 0:
        raise OperationHistoryError(f"Operation history limit must be positive: {maximum_records}")
    if any(existing.identifier == record.identifier for existing in records):
        raise OperationHistoryError(f"Operation record identifier is duplicated: {record.identifier}")
    return (*records, record)[-maximum_records:]


def operation_manifest(record: OperationRecord) -> dict[str, JsonValue]:
    return {
        "schema_version": 1,
        "operation_id": record.identifier,
        "title": record.title,
        "workspace": record.workspace,
        "target": record.target,
        "transport": record.transport,
        "argv": list(record.argv),
        "timing": {
            "started_at": record.started_at,
            "finished_at": record.finished_at,
            "duration_milliseconds": record.duration_milliseconds,
        },
        "result": {
            "outcome": record.outcome,
            "exit_code": record.exit_code,
            "error_message": record.error_message,
        },
        "captured_output": {
            "stdout_bytes": record.stdout_bytes,
            "stdout_sha256": record.stdout_sha256,
            "stderr_bytes": record.stderr_bytes,
            "stderr_sha256": record.stderr_sha256,
            "raw_output_included": False,
        },
        "prerequisites": list(record.prerequisites),
        "output_paths": list(record.output_paths),
        "privacy_notice": (
            "This user-exported manifest omits raw command output but may contain device identifiers, "
            "local paths, and other sensitive values from the exact argument vector and target label."
        ),
    }


def render_operation_manifest(record: OperationRecord) -> str:
    return json.dumps(operation_manifest(record), indent=2, sort_keys=True) + "\n"


def write_operation_manifest(path: Path, record: OperationRecord) -> Path:
    destination = path.expanduser().resolve()
    if destination.suffix.casefold() != ".json":
        raise OperationHistoryError(f"Operation manifest destination must end in .json: {destination}")
    if not destination.parent.is_dir():
        raise OperationHistoryError(f"Operation manifest parent directory does not exist: {destination.parent}")
    try:
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as error:
        raise OperationHistoryError(f"Refusing to overwrite existing operation manifest: {destination}") from error
    except OSError as error:
        raise OperationHistoryError(f"Could not create operation manifest at {destination}: {error}") from error
    try:
        payload = render_operation_manifest(record).encode("utf-8")
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as error:
        destination.unlink(missing_ok=True)
        raise OperationHistoryError(f"Could not write operation manifest at {destination}: {error}") from error
    return destination


class OperationHistoryDialog(QDialog):
    """Present session-only operation records and explicit manifest export controls."""

    def __init__(self, records: tuple[OperationRecord, ...], parent: QWidget | None) -> None:
        super().__init__(parent)
        self._records = records
        self.setObjectName("sessionActivityDialog")
        self.setWindowTitle("Session Activity")
        self.resize(980, 680)
        layout = QVBoxLayout(self)
        explanation = QLabel(
            "Completed typed operations from this app session appear here. Nothing is saved automatically. "
            "An exported JSON manifest omits raw output but can contain device identifiers and local paths."
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)

        self.table = QTableWidget(len(records), 5)
        self.table.setObjectName("sessionActivityTable")
        self.table.setHorizontalHeaderLabels(("Finished", "Workspace", "Operation", "Target", "Outcome"))
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        for row, record in enumerate(reversed(records)):
            values = (record.finished_at, record.workspace, record.title, record.target, record.outcome)
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, record.identifier)
                self.table.setItem(row, column, item)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        layout.addWidget(self.table, 1)

        self.detail = QPlainTextEdit()
        self.detail.setObjectName("sessionActivityManifestPreview")
        self.detail.setReadOnly(True)
        self.detail.setMaximumBlockCount(4000)
        layout.addWidget(self.detail, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.copy_button = QPushButton("Copy Selected Manifest")
        self.copy_button.setObjectName("copySessionActivityManifestButton")
        self.copy_button.clicked.connect(self._copy_selected)
        buttons.addButton(self.copy_button, QDialogButtonBox.ButtonRole.ActionRole)
        self.save_button = QPushButton("Save Selected Manifest…")
        self.save_button.setObjectName("saveSessionActivityManifestButton")
        self.save_button.clicked.connect(self._save_selected)
        buttons.addButton(self.save_button, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        if records:
            self.table.selectRow(0)
        else:
            self.detail.setPlainText("No typed operations have completed in this session.")
            self.copy_button.setEnabled(False)
            self.save_button.setEnabled(False)

    def _selected_record(self) -> OperationRecord:
        selected_items = self.table.selectedItems()
        if not selected_items:
            raise OperationHistoryError("Select an operation before copying or saving its manifest")
        identifier = selected_items[0].data(Qt.ItemDataRole.UserRole)
        if not isinstance(identifier, str):
            raise OperationHistoryError("Selected operation has no valid record identifier")
        record = next((candidate for candidate in self._records if candidate.identifier == identifier), None)
        if record is None:
            raise OperationHistoryError(f"Selected operation record is unavailable: {identifier}")
        return record

    def _selection_changed(self) -> None:
        try:
            record = self._selected_record()
        except OperationHistoryError:
            self.detail.clear()
            self.copy_button.setEnabled(False)
            self.save_button.setEnabled(False)
            return
        self.detail.setPlainText(render_operation_manifest(record))
        self.copy_button.setEnabled(True)
        self.save_button.setEnabled(True)

    def _copy_selected(self) -> None:
        try:
            record = self._selected_record()
        except OperationHistoryError as error:
            QMessageBox.warning(self, "No Operation Selected", str(error))
            return
        QGuiApplication.clipboard().setText(render_operation_manifest(record))

    def _save_selected(self) -> None:
        try:
            record = self._selected_record()
        except OperationHistoryError as error:
            QMessageBox.warning(self, "No Operation Selected", str(error))
            return
        suggested = str(Path.home() / f"ios-toolkit-operation-{record.identifier}.json")
        selected, _ = QFileDialog.getSaveFileName(self, "Save operation manifest", suggested, "JSON (*.json)")
        if not selected:
            return
        try:
            destination = write_operation_manifest(Path(selected), record)
        except OperationHistoryError as error:
            QMessageBox.critical(self, "Could Not Save Manifest", str(error))
            return
        QMessageBox.information(self, "Manifest Saved", f"Saved operation manifest to:\n{destination}")


def _parse_timestamp(value: str, field_name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise OperationHistoryError(f"Operation {field_name} is not a valid ISO-8601 timestamp: {value}") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise OperationHistoryError(f"Operation {field_name} must include a timezone: {value}")
    return parsed


def _normalized_output_paths(output_paths: tuple[str, ...]) -> tuple[str, ...]:
    normalized: list[str] = []
    for value in output_paths:
        if not value.strip():
            raise OperationHistoryError("Operation output paths cannot contain empty values")
        normalized.append(str(Path(value).expanduser().resolve()))
    return tuple(normalized)
