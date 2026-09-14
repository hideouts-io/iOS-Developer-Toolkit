from __future__ import annotations

import codecs
import hashlib
import json
import os
import re
import shutil
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Literal, Pattern

from PySide6.QtCore import QProcess, QProcessEnvironment, Qt, Signal
from PySide6.QtGui import QCloseEvent, QFont, QIcon, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ios_developer_toolkit.runtime import ExecutableCommand, command_arguments, command_argv


MAX_RECENT_LINES = 50_000
MAX_VISIBLE_BLOCKS = 20_000
MAX_FINDING_TEXT_LENGTH = 20_000
MAX_FINDING_TAGS = 12
MAX_FINDING_TAG_LENGTH = 48

MetadataValue = str | int | bool | None | list[str]
FindingAssessment = Literal["observation", "lead", "needs-corroboration"]


class LiveLogError(RuntimeError):
    pass


@dataclass(frozen=True)
class LogStreamSpec:
    identifier: str
    title: str
    summary: str
    arguments: tuple[str, ...]
    requires_developer_services: bool
    structured: bool


@dataclass(frozen=True)
class LiveLogFinding:
    """An analyst annotation tied to selected text in a local log working view."""

    created_at: str
    note: str
    selected_text: str
    stream: str
    device_identifier: str
    raw_bytes_observed: int
    filter_expression: str
    filter_is_regex: bool
    filter_case_sensitive: bool
    assessment: FindingAssessment
    tags: tuple[str, ...]


@dataclass(frozen=True)
class LiveLogInvestigationReport:
    """A self-contained summary that distinguishes capture facts from analyst annotations."""

    stream: str
    stream_title: str
    device_name: str
    device_identifier: str
    started_at: str
    finished_at: str | None
    raw_filename: str
    raw_sha256: str | None
    raw_bytes: int
    decoded_lines: int
    investigation_reference: str


def parse_finding_tags(value: str) -> tuple[str, ...]:
    """Validate a comma-separated set of short local investigation tags."""

    tags: list[str] = []
    for raw_tag in value.split(","):
        tag = raw_tag.strip().lower()
        if not tag:
            continue
        if len(tag) > MAX_FINDING_TAG_LENGTH:
            raise LiveLogError(
                f"Finding tags must be {MAX_FINDING_TAG_LENGTH} characters or fewer: {raw_tag!r}"
            )
        if re.fullmatch(r"[a-z0-9][a-z0-9_-]*", tag) is None:
            raise LiveLogError(
                "Finding tags may use lowercase letters, numbers, hyphens, and underscores and must start with a letter or number"
            )
        if tag not in tags:
            tags.append(tag)
    if len(tags) > MAX_FINDING_TAGS:
        raise LiveLogError(f"A finding may have at most {MAX_FINDING_TAGS} tags")
    return tuple(tags)


def assessment_for_label(label: str) -> FindingAssessment:
    """Map the investigator-facing assessment label to its stable stored value."""

    if label == "Observation":
        return "observation"
    if label == "Lead to correlate":
        return "lead"
    if label == "Needs corroboration":
        return "needs-corroboration"
    raise LiveLogError(f"Unsupported investigation assessment label: {label!r}")


def render_investigation_report(
    report: LiveLogInvestigationReport,
    findings: tuple[LiveLogFinding, ...],
) -> str:
    """Render a portable Markdown index for one raw capture and its analyst findings."""

    lines = [
        f"# {report.stream_title} investigation report",
        "",
        "## Capture facts",
        "",
        f"- Stream: `{report.stream}`",
        f"- Device: `{report.device_name}` (`{report.device_identifier}`)",
        f"- Capture started: `{report.started_at}`",
        f"- Capture finished: `{report.finished_at or 'not finalized at export'}`",
        f"- Raw artifact: `{report.raw_filename}`",
        f"- Raw SHA-256: `{report.raw_sha256 or 'not finalized at export'}`",
        f"- Raw bytes observed: `{report.raw_bytes}`",
        f"- Decoded lines observed: `{report.decoded_lines}`",
        f"- Investigation reference: {report.investigation_reference or 'not provided'}",
        "",
        "## Analyst findings",
        "",
        "The records below are analyst annotations. They are not device-generated facts, proof of causality, or proof "
        "that a selected text fragment represents the complete event.",
        "",
    ]
    if not findings:
        lines.append("No analyst findings were recorded for this capture.")
        return "\n".join(lines) + "\n"
    for number, finding in enumerate(findings, start=1):
        tags = ", ".join(finding.tags) if finding.tags else "none"
        lines.extend(
            (
                f"### Finding {number}: {finding.assessment}",
                "",
                f"- Recorded: `{finding.created_at}`",
                f"- Tags: {tags}",
                f"- Capture position: `{finding.raw_bytes_observed}` raw bytes observed",
                f"- View filter: `{finding.filter_expression or 'none'}`; regex=`{finding.filter_is_regex}`; case-sensitive=`{finding.filter_case_sensitive}`",
                f"- Analyst note: {finding.note}",
                "",
                "Selected visible text:",
                "```text",
                finding.selected_text,
                "```",
                "",
            )
        )
    return "\n".join(lines) + "\n"


def write_investigation_report(
    path: Path,
    report: LiveLogInvestigationReport,
    findings: tuple[LiveLogFinding, ...],
) -> None:
    """Write the investigation report beside exported evidence artifacts."""

    try:
        path.write_text(render_investigation_report(report, findings), encoding="utf-8")
    except OSError as error:
        raise LiveLogError(f"Could not write live-log investigation report to {path}: {error}") from error


def log_stream_specs() -> tuple[LogStreamSpec, ...]:
    return (
        LogStreamSpec(
            identifier="unified",
            title="Unified Logs",
            summary="Structured os_trace_relay stream with subsystem, category, process, and level fields.",
            arguments=("syslog", "live", "--format", "json", "--label"),
            requires_developer_services=False,
            structured=True,
        ),
        LogStreamSpec(
            identifier="classic",
            title="Classic Syslog",
            summary="Raw compatibility stream from the older Apple syslog relay service.",
            arguments=("syslog", "live-old"),
            requires_developer_services=False,
            structured=False,
        ),
        LogStreamSpec(
            identifier="dvt-oslog",
            title="DVT OSLog",
            summary="Developer-service OSLog stream; requires Developer Mode, a mounted DDI, and the tunnel when applicable.",
            arguments=("developer", "dvt", "oslog", "--format", "json"),
            requires_developer_services=True,
            structured=True,
        ),
    )


def stream_spec(identifier: str) -> LogStreamSpec:
    matches = tuple(specification for specification in log_stream_specs() if specification.identifier == identifier)
    if len(matches) != 1:
        raise LiveLogError(f"Expected one live-log stream named {identifier!r}, found {len(matches)}")
    return matches[0]


def compile_line_filter(expression: str, regex: bool, case_sensitive: bool) -> Pattern[str] | None:
    if not expression:
        return None
    flags = 0 if case_sensitive else re.IGNORECASE
    pattern = expression if regex else re.escape(expression)
    try:
        return re.compile(pattern, flags)
    except re.error as error:
        raise LiveLogError(f"Invalid regular expression: {error}") from error


def line_matches(line: str, pattern: Pattern[str] | None) -> bool:
    return pattern is None or pattern.search(line) is not None


def live_log_cache_directory(home: Path) -> Path:
    return home.expanduser().resolve() / "Library" / "Caches" / "iOS Developer Toolkit" / "Live Logs"


def safe_log_fragment(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", value).strip("-")
    return cleaned[:80] or "device"


def create_spool_paths(directory: Path, specification: LogStreamSpec, device_identifier: str) -> tuple[Path, Path]:
    resolved_directory = directory.expanduser().resolve()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suffix = ".jsonl" if specification.structured else ".log"
    basename = f"{timestamp}-{safe_log_fragment(device_identifier)}-{specification.identifier}"
    return resolved_directory / f"{basename}{suffix}", resolved_directory / f"{basename}.meta.json"


def annotation_path_for(raw_path: Path) -> Path:
    return raw_path.with_name(f"{raw_path.name}.findings.jsonl")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_finding(
    note: str,
    selected_text: str,
    stream: str,
    device_identifier: str,
    raw_bytes_observed: int,
    filter_expression: str,
    filter_is_regex: bool,
    filter_case_sensitive: bool,
    assessment: FindingAssessment,
    tags: tuple[str, ...],
) -> LiveLogFinding:
    normalized_note = note.strip()
    normalized_selection = selected_text.strip()
    if not normalized_note:
        raise LiveLogError("A finding requires an analyst note")
    if not normalized_selection:
        raise LiveLogError("Select one or more visible log lines before marking a finding")
    if len(normalized_selection) > MAX_FINDING_TEXT_LENGTH:
        raise LiveLogError(f"Selected finding text must be {MAX_FINDING_TEXT_LENGTH:,} characters or fewer")
    if assessment not in ("observation", "lead", "needs-corroboration"):
        raise LiveLogError(f"Unsupported investigation assessment: {assessment!r}")
    if len(tags) > MAX_FINDING_TAGS:
        raise LiveLogError(f"A finding may have at most {MAX_FINDING_TAGS} tags")
    for tag in tags:
        if re.fullmatch(r"[a-z0-9][a-z0-9_-]*", tag) is None:
            raise LiveLogError(f"Invalid normalized finding tag: {tag!r}")
    return LiveLogFinding(
        created_at=datetime.now(timezone.utc).isoformat(),
        note=normalized_note,
        selected_text=normalized_selection,
        stream=stream,
        device_identifier=device_identifier,
        raw_bytes_observed=raw_bytes_observed,
        filter_expression=filter_expression,
        filter_is_regex=filter_is_regex,
        filter_case_sensitive=filter_case_sensitive,
        assessment=assessment,
        tags=tags,
    )


def append_finding(path: Path, finding: LiveLogFinding) -> None:
    record = json.dumps(asdict(finding), sort_keys=True) + "\n"
    try:
        with path.open("a", encoding="utf-8") as output:
            output.write(record)
            output.flush()
            os.fsync(output.fileno())
    except OSError as error:
        raise LiveLogError(f"Could not append live-log finding to {path}: {error}") from error


def write_metadata(path: Path, metadata: dict[str, MetadataValue]) -> None:
    temporary_path = path.with_name(f".{path.name}.{os.getpid()}.new")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary_path.replace(path)
    except OSError as error:
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError as cleanup_error:
            raise LiveLogError(
                f"Could not write live-log metadata to {path}: {error}; cleanup also failed: {cleanup_error}"
            ) from error
        raise LiveLogError(f"Could not write live-log metadata to {path}: {error}") from error


class LiveLogWindow(QMainWindow):
    closed = Signal(object)

    def __init__(
        self,
        executable: ExecutableCommand,
        specification: LogStreamSpec,
        device_identifier: str,
        device_name: str,
        environment: dict[str, str],
        icon_path: Path,
    ) -> None:
        super().__init__()
        self._executable = executable
        self._specification = specification
        self._device_identifier = device_identifier
        self._device_name = device_name
        self._environment = environment
        self._recent_lines: deque[str] = deque(maxlen=MAX_RECENT_LINES)
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._partial_line = ""
        self._raw_file: BinaryIO | None = None
        self._process: QProcess | None = None
        self._total_bytes = 0
        self._total_lines = 0
        self._view_paused = False
        self._unsaved = False
        self._closing = False
        self._started_at = datetime.now(timezone.utc).isoformat()
        self._finished_at: str | None = None
        self._exit_code: int | None = None
        self._process_error: str | None = None
        self._raw_sha256: str | None = None
        self._findings_count = 0
        self._findings: list[LiveLogFinding] = []
        self._spool_path, self._metadata_path = create_spool_paths(
            live_log_cache_directory(Path.home()),
            specification,
            device_identifier,
        )
        self._findings_path = annotation_path_for(self._spool_path)
        self.setWindowTitle(f"{specification.title} — {device_name}")
        self.setWindowIcon(QIcon(str(icon_path)))
        self.resize(1120, 720)
        self._build_ui()
        self._start()

    @property
    def spool_path(self) -> Path:
        return self._spool_path

    def _build_ui(self) -> None:
        root = QWidget()
        layout = QVBoxLayout(root)
        heading = QLabel(f"{self._specification.title} — {self._device_name}")
        heading.setFont(QFont(heading.font().family(), 18, QFont.Weight.Bold))
        layout.addWidget(heading)
        summary = QLabel(self._specification.summary)
        summary.setWordWrap(True)
        layout.addWidget(summary)

        investigation_row = QHBoxLayout()
        investigation_label = QLabel("Investigation reference")
        investigation_row.addWidget(investigation_label)
        self.investigation_reference_field = QLineEdit()
        self.investigation_reference_field.setObjectName("liveLogInvestigationReference")
        self.investigation_reference_field.setPlaceholderText("Optional local case or ticket reference")
        self.investigation_reference_field.editingFinished.connect(self._record_investigation_reference)
        investigation_row.addWidget(self.investigation_reference_field, 1)
        layout.addLayout(investigation_row)

        filter_row = QHBoxLayout()
        self.filter_field = QLineEdit()
        self.filter_field.setObjectName("liveLogFilter")
        self.filter_field.setPlaceholderText("Filter the visible view; raw capture remains unchanged")
        self.filter_field.textChanged.connect(self._refresh_view)
        filter_row.addWidget(self.filter_field, 1)
        self.regex_checkbox = QCheckBox("Regex")
        self.regex_checkbox.setObjectName("liveLogRegex")
        self.regex_checkbox.toggled.connect(self._refresh_view)
        filter_row.addWidget(self.regex_checkbox)
        self.case_checkbox = QCheckBox("Case sensitive")
        self.case_checkbox.setObjectName("liveLogCaseSensitive")
        self.case_checkbox.toggled.connect(self._refresh_view)
        filter_row.addWidget(self.case_checkbox)
        layout.addLayout(filter_row)

        button_row = QHBoxLayout()
        self.pause_button = QPushButton("Pause View")
        self.pause_button.setObjectName("pauseLiveLogViewButton")
        self.pause_button.clicked.connect(self._toggle_pause)
        button_row.addWidget(self.pause_button)
        self.follow_checkbox = QCheckBox("Follow tail")
        self.follow_checkbox.setObjectName("followLiveLogTail")
        self.follow_checkbox.setChecked(True)
        button_row.addWidget(self.follow_checkbox)
        stop_button = QPushButton("Stop Capture")
        stop_button.setObjectName("stopLiveLogCaptureButton")
        stop_button.clicked.connect(self.stop_capture)
        button_row.addWidget(stop_button)
        mark_finding_button = QPushButton("Mark Finding…")
        mark_finding_button.setObjectName("markLiveLogFindingButton")
        mark_finding_button.setToolTip("Select visible log lines, then add an analyst note without changing the raw stream.")
        mark_finding_button.clicked.connect(self._mark_finding)
        button_row.addWidget(mark_finding_button)
        self.review_findings_button = QPushButton("Review Findings (0)")
        self.review_findings_button.setObjectName("reviewLiveLogFindingsButton")
        self.review_findings_button.setToolTip("Review the local analyst annotations separately from the raw device output.")
        self.review_findings_button.clicked.connect(self._review_findings)
        button_row.addWidget(self.review_findings_button)
        button_row.addStretch()
        copy_button = QPushButton("Copy Visible")
        copy_button.setObjectName("copyVisibleLiveLogButton")
        copy_button.clicked.connect(self._copy_visible)
        button_row.addWidget(copy_button)
        save_filtered_button = QPushButton("Save Filtered As…")
        save_filtered_button.setObjectName("saveFilteredLiveLogButton")
        save_filtered_button.clicked.connect(self._save_filtered_as)
        button_row.addWidget(save_filtered_button)
        save_raw_button = QPushButton("Save Raw As…")
        save_raw_button.setObjectName("saveRawLiveLogButton")
        save_raw_button.clicked.connect(self._save_raw_as)
        button_row.addWidget(save_raw_button)
        export_bundle_button = QPushButton("Export Evidence Bundle…")
        export_bundle_button.setObjectName("exportLiveLogEvidenceBundleButton")
        export_bundle_button.clicked.connect(self._export_evidence_bundle)
        button_row.addWidget(export_bundle_button)
        layout.addLayout(button_row)

        self.output = QPlainTextEdit()
        self.output.setObjectName("liveLogOutput")
        self.output.setReadOnly(True)
        self.output.setMaximumBlockCount(MAX_VISIBLE_BLOCKS)
        self.output.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.output.verticalScrollBar().valueChanged.connect(self._viewport_moved)
        layout.addWidget(self.output, 1)
        self.status_label = QLabel()
        self.status_label.setObjectName("liveLogStatus")
        self.status_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.status_label)
        self.setCentralWidget(root)
        self._update_status("Preparing capture")

    def _start(self) -> None:
        try:
            self._spool_path.parent.mkdir(parents=True, exist_ok=True)
            self._raw_file = self._spool_path.open("xb")
            self._write_metadata()
        except (OSError, LiveLogError) as error:
            self._close_raw_file()
            try:
                self._spool_path.unlink(missing_ok=True)
                self._metadata_path.unlink(missing_ok=True)
            except OSError as cleanup_error:
                raise LiveLogError(
                    f"Could not initialize live-log spool {self._spool_path}: {error}; cleanup also failed: {cleanup_error}"
                ) from error
            raise LiveLogError(f"Could not initialize live-log spool {self._spool_path}: {error}") from error
        process = QProcess(self)
        process.setProgram(str(self._executable.program))
        process.setArguments(list(command_arguments(self._executable, self._specification.arguments)))
        process_environment = QProcessEnvironment.systemEnvironment()
        for key, value in self._environment.items():
            process_environment.insert(key, value)
        process.setProcessEnvironment(process_environment)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.readyReadStandardOutput.connect(self._read_output)
        process.finished.connect(self._finished)
        process.errorOccurred.connect(self._process_failed)
        self._process = process
        process.start()
        self._update_status("Starting")

    def _read_output(self) -> None:
        process = self._process
        if process is None:
            return
        payload = bytes(process.readAllStandardOutput())
        if not payload:
            return
        if self._raw_file is None:
            raise LiveLogError("Live-log output arrived after the raw spool was closed")
        try:
            self._raw_file.write(payload)
            self._raw_file.flush()
        except OSError as error:
            self.stop_capture()
            QMessageBox.critical(self, "Raw Capture Write Failed", f"Could not write {self._spool_path}: {error}")
            return
        self._unsaved = True
        self._total_bytes += len(payload)
        decoded = self._partial_line + self._decoder.decode(payload)
        lines = decoded.splitlines(keepends=True)
        self._partial_line = ""
        if lines and not lines[-1].endswith(("\n", "\r")):
            self._partial_line = lines.pop()
        for line in lines:
            self._append_line(line.rstrip("\r\n"))
        self._update_status("Capturing")

    def _append_line(self, line: str) -> None:
        self._recent_lines.append(line)
        self._total_lines += 1
        if self._view_paused:
            return
        pattern = self._current_pattern(show_error=False)
        if line_matches(line, pattern):
            self.output.appendPlainText(line)
            if self.follow_checkbox.isChecked():
                self.output.moveCursor(QTextCursor.MoveOperation.End)

    def _current_pattern(self, show_error: bool) -> Pattern[str] | None:
        try:
            pattern = compile_line_filter(
                self.filter_field.text(),
                self.regex_checkbox.isChecked(),
                self.case_checkbox.isChecked(),
            )
        except LiveLogError as error:
            self.filter_field.setStyleSheet("border: 1px solid #d65a5a;")
            if show_error:
                QMessageBox.critical(self, "Invalid Log Filter", str(error))
            return None
        self.filter_field.setStyleSheet("")
        return pattern

    def _refresh_view(self) -> None:
        if self._view_paused:
            return
        pattern = self._current_pattern(show_error=False)
        self.output.setPlainText("\n".join(line for line in self._recent_lines if line_matches(line, pattern)))
        if self.follow_checkbox.isChecked():
            self.output.moveCursor(QTextCursor.MoveOperation.End)
        self._update_status(self._state_text())

    def _toggle_pause(self) -> None:
        self._view_paused = not self._view_paused
        self.pause_button.setText("Resume View" if self._view_paused else "Pause View")
        if not self._view_paused:
            self._refresh_view()
        self._update_status(self._state_text())

    def _viewport_moved(self, value: int) -> None:
        scrollbar = self.output.verticalScrollBar()
        if value < scrollbar.maximum():
            self.follow_checkbox.setChecked(False)

    def _state_text(self) -> str:
        process = self._process
        if process is not None and process.state() != QProcess.ProcessState.NotRunning:
            return "Capturing; view paused" if self._view_paused else "Capturing"
        return "Stopped"

    def _update_status(self, state: str) -> None:
        visible_lines = self.output.document().blockCount() if self.output.toPlainText() else 0
        retained_note = ""
        if self._total_lines > MAX_RECENT_LINES:
            retained_note = f" • view retains latest {MAX_RECENT_LINES:,}"
        reference_note = self.investigation_reference_field.text().strip()
        if reference_note:
            reference_note = f" • reference {reference_note}"
        self.status_label.setText(
            f"{state} • raw {self._total_bytes:,} bytes / {self._total_lines:,} lines • "
            f"visible {visible_lines:,} • findings {self._findings_count}{retained_note}{reference_note} • spool {self._spool_path}"
        )

    def stop_capture(self) -> None:
        process = self._process
        if process is not None and process.state() != QProcess.ProcessState.NotRunning:
            process.terminate()
            if not process.waitForFinished(3000):
                process.kill()
                process.waitForFinished(2000)

    def _finished(self, exit_code: int, exit_status: QProcess.ExitStatus) -> None:
        del exit_status
        self._read_output()
        remainder = self._partial_line + self._decoder.decode(b"", final=True)
        self._partial_line = ""
        if remainder:
            self._append_line(remainder)
        self._exit_code = exit_code
        self._finished_at = datetime.now(timezone.utc).isoformat()
        self._close_raw_file()
        self._write_metadata_or_report()
        self._update_status(f"Stopped with exit code {exit_code}")

    def _process_failed(self, process_error: QProcess.ProcessError) -> None:
        process = self._process
        self._process_error = process.errorString() if process is not None else "Unknown QProcess error"
        if process_error == QProcess.ProcessError.FailedToStart:
            self._finished_at = datetime.now(timezone.utc).isoformat()
            self._close_raw_file()
            self._write_metadata_or_report()
        self._update_status(f"Process error: {self._process_error}")

    def _close_raw_file(self) -> None:
        if self._raw_file is not None:
            try:
                self._raw_file.flush()
                self._raw_file.close()
            except OSError as error:
                self._process_error = f"Could not finalize raw spool: {error}"
            self._raw_file = None
        if self._spool_path.is_file() and self._raw_file is None:
            try:
                self._raw_sha256 = sha256_file(self._spool_path)
            except OSError as error:
                self._process_error = f"Could not hash raw spool: {error}"

    def _metadata(self) -> dict[str, MetadataValue]:
        return {
            "schema_version": 1,
            "stream": self._specification.identifier,
            "stream_title": self._specification.title,
            "structured": self._specification.structured,
            "requires_developer_services": self._specification.requires_developer_services,
            "device_identifier": self._device_identifier,
            "device_name": self._device_name,
            "command": list(command_argv(self._executable, self._specification.arguments)),
            "started_at": self._started_at,
            "finished_at": self._finished_at,
            "exit_code": self._exit_code,
            "process_error": self._process_error,
            "raw_bytes": self._total_bytes,
            "decoded_lines": self._total_lines,
            "raw_path": str(self._spool_path),
            "raw_sha256": self._raw_sha256,
            "findings_path": str(self._findings_path),
            "findings_count": self._findings_count,
            "investigation_reference": self.investigation_reference_field.text().strip(),
            "interpretation_boundary": "Findings are analyst annotations, not device-generated facts or proof of causality.",
        }

    def _write_metadata(self) -> None:
        write_metadata(self._metadata_path, self._metadata())

    def _write_metadata_or_report(self) -> None:
        try:
            self._write_metadata()
        except LiveLogError as error:
            self._process_error = str(error)
            QMessageBox.critical(self, "Could Not Write Capture Metadata", str(error))

    def _copy_visible(self) -> None:
        QApplication.clipboard().setText(self.output.toPlainText())

    def _record_investigation_reference(self) -> None:
        self._unsaved = True
        self._write_metadata_or_report()
        self._update_status(self._state_text())

    def _investigation_report(self, raw_filename: str, raw_sha256: str | None) -> LiveLogInvestigationReport:
        return LiveLogInvestigationReport(
            stream=self._specification.identifier,
            stream_title=self._specification.title,
            device_name=self._device_name,
            device_identifier=self._device_identifier,
            started_at=self._started_at,
            finished_at=self._finished_at,
            raw_filename=raw_filename,
            raw_sha256=raw_sha256,
            raw_bytes=self._total_bytes,
            decoded_lines=self._total_lines,
            investigation_reference=self.investigation_reference_field.text().strip(),
        )

    def _review_findings(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Findings — {self._specification.title}")
        dialog.resize(860, 620)
        layout = QVBoxLayout(dialog)
        explanation = QLabel(
            "This register contains analyst annotations. It is separate from the unmodified raw capture and does not establish causality."
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        register = QPlainTextEdit()
        register.setObjectName("liveLogFindingRegister")
        register.setReadOnly(True)
        register.setPlainText(
            render_investigation_report(
                self._investigation_report(self._spool_path.name, self._raw_sha256),
                tuple(self._findings),
            )
        )
        layout.addWidget(register, 1)
        controls = QHBoxLayout()
        copy_button = QPushButton("Copy Register")
        copy_button.setObjectName("copyLiveLogFindingRegisterButton")
        copy_button.clicked.connect(lambda: QApplication.clipboard().setText(register.toPlainText()))
        controls.addWidget(copy_button)
        controls.addStretch()
        close_button = QPushButton("Close")
        close_button.setObjectName("closeLiveLogFindingRegisterButton")
        close_button.clicked.connect(dialog.accept)
        controls.addWidget(close_button)
        layout.addLayout(controls)
        dialog.exec()

    def _mark_finding(self) -> None:
        selected_text = self.output.textCursor().selectedText().replace("\u2029", "\n")
        if not selected_text.strip():
            QMessageBox.information(self, "Select Log Lines", "Select one or more visible log lines before marking a finding.")
            return
        selected_label, accepted = QInputDialog.getItem(
            self,
            "Classify Investigation Finding",
            "Assessment (your analytical judgment, not a device fact):",
            ("Observation", "Lead to correlate", "Needs corroboration"),
            0,
            False,
        )
        if not accepted:
            return
        tag_text, accepted = QInputDialog.getText(
            self,
            "Tag Investigation Finding",
            "Optional comma-separated tags (for example: auth, network, crash):",
        )
        if not accepted:
            return
        note, accepted = QInputDialog.getMultiLineText(
            self,
            "Mark Investigation Finding",
            "Analyst note (stored locally beside the raw spool):",
        )
        if not accepted:
            return
        try:
            finding = create_finding(
                note,
                selected_text,
                self._specification.identifier,
                self._device_identifier,
                self._total_bytes,
                self.filter_field.text(),
                self.regex_checkbox.isChecked(),
                self.case_checkbox.isChecked(),
                assessment_for_label(selected_label),
                parse_finding_tags(tag_text),
            )
            append_finding(self._findings_path, finding)
        except LiveLogError as error:
            QMessageBox.critical(self, "Could Not Mark Finding", str(error))
            return
        self._findings_count += 1
        self._findings.append(finding)
        self._unsaved = True
        self.review_findings_button.setText(f"Review Findings ({self._findings_count})")
        self._write_metadata_or_report()
        self._update_status(self._state_text())

    def _copy_raw_and_findings(self, raw_destination: Path, metadata_destination: Path) -> str:
        if self._raw_file is not None:
            self._raw_file.flush()
        shutil.copyfile(self._spool_path, raw_destination)
        saved_metadata = dict(self._metadata())
        saved_metadata["raw_path"] = str(raw_destination)
        raw_sha256 = sha256_file(raw_destination)
        saved_metadata["raw_sha256"] = raw_sha256
        findings_destination = annotation_path_for(raw_destination)
        if self._findings_path.is_file():
            shutil.copyfile(self._findings_path, findings_destination)
            saved_metadata["findings_path"] = str(findings_destination)
        else:
            saved_metadata["findings_path"] = None
        write_metadata(metadata_destination, saved_metadata)
        return raw_sha256

    def _write_hash_manifest(self, directory: Path) -> Path:
        manifest_path = directory / "SHA256SUMS.txt"
        candidates = sorted(path for path in directory.iterdir() if path.is_file() and path != manifest_path)
        lines = [f"{sha256_file(path)}  {path.name}" for path in candidates]
        try:
            manifest_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError as error:
            raise LiveLogError(f"Could not write evidence bundle hashes to {manifest_path}: {error}") from error
        return manifest_path

    def _save_raw_as(self) -> None:
        suffix = ".jsonl" if self._specification.structured else ".log"
        destination, _ = QFileDialog.getSaveFileName(
            self,
            "Save complete raw capture",
            str(Path.home() / "Documents" / f"{self._specification.identifier}{suffix}"),
            "JSON Lines (*.jsonl)" if self._specification.structured else "Log files (*.log)",
        )
        if not destination:
            return
        if self._raw_file is not None:
            self._raw_file.flush()
        destination_path = Path(destination).expanduser().resolve()
        try:
            metadata_destination = destination_path.with_name(f"{destination_path.name}.meta.json")
            self._copy_raw_and_findings(destination_path, metadata_destination)
        except (OSError, LiveLogError) as error:
            QMessageBox.critical(self, "Could Not Save Raw Capture", str(error))
            return
        self._unsaved = False
        QMessageBox.information(
            self,
            "Raw Capture Saved",
            f"Saved the complete unfiltered byte stream to:\n{destination_path}\n\nMetadata and any local findings were saved beside it.",
        )

    def _export_evidence_bundle(self) -> None:
        destination = QFileDialog.getExistingDirectory(
            self,
            "Choose folder for live-log evidence bundle",
            str(Path.home() / "Documents"),
        )
        if not destination:
            return
        parent = Path(destination).expanduser().resolve()
        bundle_directory = parent / f"{self._spool_path.stem}-investigation"
        try:
            bundle_directory.mkdir(mode=0o700, parents=False, exist_ok=False)
            raw_destination = bundle_directory / self._spool_path.name
            metadata_destination = bundle_directory / self._metadata_path.name
            raw_sha256 = self._copy_raw_and_findings(raw_destination, metadata_destination)
            report_destination = bundle_directory / "investigation-report.md"
            write_investigation_report(
                report_destination,
                self._investigation_report(raw_destination.name, raw_sha256),
                tuple(self._findings),
            )
            hashes_path = self._write_hash_manifest(bundle_directory)
        except (OSError, LiveLogError) as error:
            QMessageBox.critical(self, "Could Not Export Evidence Bundle", str(error))
            return
        self._unsaved = False
        QMessageBox.information(
            self,
            "Evidence Bundle Exported",
            f"Saved raw capture, metadata, findings, investigation report, and SHA-256 inventory to:\n{bundle_directory}\n\nHashes:\n{hashes_path}",
        )

    def _save_filtered_as(self) -> None:
        pattern = self._current_pattern(show_error=True)
        if self.filter_field.text() and pattern is None:
            return
        destination, _ = QFileDialog.getSaveFileName(
            self,
            "Save filtered text view",
            str(Path.home() / "Documents" / f"{self._specification.identifier}-filtered.log"),
            "Log files (*.log)",
        )
        if not destination:
            return
        if self._raw_file is not None:
            self._raw_file.flush()
        destination_path = Path(destination).expanduser().resolve()
        try:
            with self._spool_path.open("r", encoding="utf-8", errors="replace") as source:
                with destination_path.open("x", encoding="utf-8") as output:
                    for line in source:
                        if line_matches(line, pattern):
                            output.write(line)
        except FileExistsError:
            QMessageBox.critical(self, "Could Not Save Filtered View", f"Destination already exists: {destination_path}")
            return
        except OSError as error:
            QMessageBox.critical(self, "Could Not Save Filtered View", f"Could not write {destination_path}: {error}")
            return
        QMessageBox.information(self, "Filtered View Saved", f"Saved filtered text to:\n{destination_path}")

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._closing:
            event.accept()
            return
        if self._unsaved:
            message = QMessageBox(self)
            message.setIcon(QMessageBox.Icon.Warning)
            message.setWindowTitle("Save Live Log Before Closing?")
            message.setText("This window has a complete raw capture in its temporary spool.")
            message.setInformativeText("Save it, explicitly discard it, or keep the window open.")
            save_button = message.addButton("Save Raw…", QMessageBox.ButtonRole.AcceptRole)
            discard_button = message.addButton("Discard", QMessageBox.ButtonRole.DestructiveRole)
            cancel_button = message.addButton(QMessageBox.StandardButton.Cancel)
            message.setDefaultButton(cancel_button)
            message.exec()
            clicked = message.clickedButton()
            if clicked == cancel_button:
                event.ignore()
                return
            if clicked == save_button:
                self._save_raw_as()
                if self._unsaved:
                    event.ignore()
                    return
            elif clicked == discard_button:
                self._unsaved = False
            else:
                raise LiveLogError("Live-log close dialog returned an unknown button")
        self._closing = True
        self.stop_capture()
        self._close_raw_file()
        if not self._unsaved:
            try:
                self._spool_path.unlink(missing_ok=True)
                self._metadata_path.unlink(missing_ok=True)
                self._findings_path.unlink(missing_ok=True)
            except OSError as error:
                QMessageBox.critical(self, "Could Not Remove Temporary Capture", str(error))
                self._closing = False
                event.ignore()
                return
        self.closed.emit(self)
        event.accept()
