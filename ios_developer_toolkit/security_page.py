"""Qt boundary for owned cancellable security analysis and local report review."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from threading import Event

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QMessageBox, QPushButton, QSplitter, QTableWidget, QTableWidgetItem,
    QTextBrowser, QVBoxLayout, QWidget,
)

from ios_developer_toolkit.security_engine import analyze_security
from ios_developer_toolkit.security_intelligence import TRUSTED_SOURCES, import_intelligence, update_intelligence
from ios_developer_toolkit.security_models import (
    AcquisitionMethod, IntelligenceBundle, SecurityAnalysisReport, SecurityCancelled,
    SecurityNetworkError, SecurityScanRequest, SecurityValidationError,
)
from ios_developer_toolkit.security_reporting import SecurityReportFormat, redact_security_report, write_security_report


SecurityOperation = Callable[[], SecurityAnalysisReport | IntelligenceBundle]


class SecurityWorker(QThread):
    """Own one operation and report expected failures without losing thread lifetime."""

    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, operation: SecurityOperation, parent: QWidget) -> None:
        super().__init__(parent)
        self.operation = operation

    def run(self) -> None:
        try:
            result = self.operation()
        except (OSError, SecurityValidationError, SecurityNetworkError, SecurityCancelled) as error:
            self.failed.emit(f"{type(error).__name__}: {error}")
            return
        self.completed.emit(result)


class SecurityAnalysisPage(QWidget):
    """The host must defer closing while running and retry after becameIdle."""

    becameIdle = Signal()
    acquisitionRequested = Signal(str)

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("securityAnalysisPage")
        self._worker: SecurityWorker | None = None
        self._cancellation = Event()
        self._intelligence: tuple[IntelligenceBundle, ...] = ()
        self._report: SecurityAnalysisReport | None = None
        self._device: str | None = None
        layout = QVBoxLayout(self)
        title = QLabel("Security Analysis")
        title.setObjectName("securityTitle")
        layout.addWidget(title)
        boundary = QLabel("Analyze authorized local evidence. Matches are investigative leads; no result establishes a clean or compromised device. Evidence is never uploaded. Curated updates fetch public IOC data only when requested.")
        boundary.setWordWrap(True)
        layout.addWidget(boundary)
        source = QGroupBox("Evidence and acquisition")
        source_layout = QVBoxLayout(source)
        self.method = QComboBox()
        self.method.setObjectName("securityAcquisitionMethod")
        for label, value in (("Decrypted iOS backup", "backup"), ("Unpacked sysdiagnose", "sysdiagnose"), ("Imported file or folder", "imported-evidence"), ("Existing MVT results", "mvt-results")):
            self.method.addItem(label, value)
        source_layout.addWidget(self.method)
        path_row = QHBoxLayout()
        self.evidence_path = QLineEdit()
        self.evidence_path.setObjectName("securityEvidencePath")
        self.evidence_path.setPlaceholderText("Absolute local evidence path")
        path_row.addWidget(self.evidence_path, 1)
        path_row.addWidget(self._button("Choose Folder", "securityChooseFolder", self._choose_folder))
        path_row.addWidget(self._button("Choose File", "securityChooseFile", self._choose_file))
        source_layout.addLayout(path_row)
        handoffs = QHBoxLayout()
        handoffs.addWidget(self._button("Open Backup Acquisition", "securityOpenBackup", self._open_backup))
        handoffs.addWidget(self._button("Open Evidence Capture", "securityOpenEvidence", self._open_evidence))
        handoffs.addWidget(self._button("Open External MVT", "securityOpenMVT", self._open_mvt))
        source_layout.addLayout(handoffs)
        layout.addWidget(source)
        intelligence = QGroupBox("Threat intelligence")
        intelligence_layout = QVBoxLayout(intelligence)
        self.bundles = QListWidget()
        self.bundles.setObjectName("securityIntelligenceBundles")
        self.bundles.setMaximumHeight(85)
        intelligence_layout.addWidget(self.bundles)
        import_row = QHBoxLayout()
        import_row.addWidget(self._button("Import STIX / JSON IOC", "securityImportIOC", self._import_ioc))
        import_row.addWidget(self._button("Remove Selected IOC", "securityRemoveIOC", self._remove_ioc))
        self.trusted_source = QComboBox()
        self.trusted_source.setObjectName("securityTrustedSource")
        for item in TRUSTED_SOURCES:
            self.trusted_source.addItem(item.name, item.identifier)
        intelligence_layout.addLayout(import_row)
        update_row = QHBoxLayout()
        update_row.addWidget(self.trusted_source, 1)
        update_row.addWidget(self._button("Update Selected Source", "securityUpdateSource", self._update_source))
        intelligence_layout.addLayout(update_row)
        schema = QLabel('Custom IOC JSON: {"indicators":[{"id":"case-1","type":"domain","value":"example.test"}]}. STIX supports equality/OR only. Source digests establish byte identity; signatures are not supplied.')
        schema.setWordWrap(True)
        schema.setTextFormat(Qt.TextFormat.PlainText)
        intelligence_layout.addWidget(schema)
        layout.addWidget(intelligence)
        actions = QHBoxLayout()
        self.analyze_button = self._button("Analyze Local Evidence", "securityAnalyze", self._analyze)
        self.stop_button = self._button("Stop", "securityStop", self.shutdown)
        self.stop_button.setEnabled(False)
        actions.addWidget(self.analyze_button)
        actions.addWidget(self.stop_button)
        self.investigator = QCheckBox("Investigator details")
        self.investigator.setObjectName("securityInvestigatorMode")
        self.investigator.toggled.connect(self._show_details)
        actions.addWidget(self.investigator)
        self.redact = QCheckBox("Redact private export values")
        self.redact.setObjectName("securityRedactExport")
        self.redact.setChecked(True)
        actions.addWidget(self.redact)
        layout.addLayout(actions)
        exports = QHBoxLayout()
        exports.addWidget(self._button("Export JSON", "securityExportJSON", self._export_json))
        exports.addWidget(self._button("Export CSV", "securityExportCSV", self._export_csv))
        exports.addWidget(self._button("Export HTML", "securityExportHTML", self._export_html))
        layout.addLayout(exports)
        self.status = QLabel("Choose local evidence and optional intelligence before analysis.")
        self.status.setObjectName("securityAnalysisStatus")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.findings = QTableWidget(0, 4)
        self.findings.setObjectName("securityFindingsTable")
        self.findings.setHorizontalHeaderLabels(("Severity", "Classification", "Lead", "Artifacts"))
        self.findings.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.findings.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.findings.itemSelectionChanged.connect(self._show_details)
        self.detail = QTextBrowser()
        self.detail.setObjectName("securityFindingDetail")
        self.detail.setOpenExternalLinks(False)
        splitter.addWidget(self.findings)
        splitter.addWidget(self.detail)
        layout.addWidget(splitter, 1)

    def _button(self, label: str, identifier: str, action: Callable[[], None]) -> QPushButton:
        button = QPushButton(label)
        button.setObjectName(identifier)
        button.clicked.connect(action)
        return button

    def set_device(self, identifier: str | None) -> None:
        self._device = identifier

    def is_running(self) -> bool:
        return self._worker is not None

    def shutdown(self) -> None:
        if self._worker is not None:
            self._cancellation.set()
            self.status.setText("Cancellation requested; waiting for the owned worker to finish. Partial coverage remains explicit.")

    def _open_backup(self) -> None:
        self.acquisitionRequested.emit("Backup")

    def _open_evidence(self) -> None:
        self.acquisitionRequested.emit("Evidence Capture")

    def _open_mvt(self) -> None:
        self.acquisitionRequested.emit("Backup")

    def _choose_folder(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "Choose authorized local evidence")
        if selected:
            self.evidence_path.setText(selected)

    def _choose_file(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(self, "Choose authorized evidence file")
        if selected:
            self.method.setCurrentIndex(2)
            self.evidence_path.setText(selected)

    def _start(self, operation: SecurityOperation) -> None:
        if self.is_running():
            raise SecurityValidationError("A security operation is already running.")
        worker = SecurityWorker(operation, self)
        self._worker = worker
        for button in self.findChildren(QPushButton):
            button.setEnabled(button is self.stop_button)
        worker.completed.connect(self._completed)
        worker.failed.connect(self._failed)
        worker.finished.connect(self._finished)
        worker.start()

    def _import_ioc(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(self, "Import local IOC bundle", "", "Threat intelligence (*.stix *.stix2 *.json)")
        if selected:
            self._cancellation = Event()
            cancellation = self._cancellation
            path = Path(selected)
            self.status.setText("Validating local intelligence…")
            self._start(lambda: import_intelligence(path, 25 * 1024 * 1024, None, cancellation))

    def _remove_ioc(self) -> None:
        position = self.bundles.currentRow()
        if 0 <= position < len(self._intelligence):
            self._intelligence = self._intelligence[:position] + self._intelligence[position + 1:]
            self._refresh_bundles()

    def _update_source(self) -> None:
        position = self.trusted_source.currentIndex()
        if not 0 <= position < len(TRUSTED_SOURCES):
            self._failed("Choose a curated source.")
            return
        source = TRUSTED_SOURCES[position]
        cache = Path.home() / "Library" / "Application Support" / "iOS Developer Toolkit" / "Security Intelligence"
        self._cancellation = Event()
        cancellation = self._cancellation
        self.status.setText(f"Resolving and fetching public {source.name} indicators. No evidence is sent.")
        self._start(lambda: update_intelligence(source, cache, 25 * 1024 * 1024, cancellation))

    def _method(self) -> AcquisitionMethod:
        value = self.method.currentData()
        for method in ("backup", "sysdiagnose", "imported-evidence", "mvt-results"):
            if value == method:
                return method
        raise SecurityValidationError("Choose a supported acquisition method.")

    def _analyze(self) -> None:
        try:
            evidence = Path(self.evidence_path.text().strip()).expanduser()
            if not evidence.is_absolute():
                raise SecurityValidationError("Choose an absolute local evidence path.")
            request = SecurityScanRequest(evidence, self._method(), self._intelligence, 20_000, 25 * 1024 * 1024, 512 * 1024 * 1024, 25_000)
        except SecurityValidationError as error:
            self._failed(str(error))
            return
        self._report = None
        self.findings.setRowCount(0)
        self.detail.clear()
        self._cancellation = Event()
        cancellation = self._cancellation
        self.status.setText("Reading bounded local artifacts…")
        self._start(lambda: analyze_security(request, cancellation))

    def _refresh_bundles(self) -> None:
        self.bundles.clear()
        for bundle in self._intelligence:
            self.bundles.addItem(f"{bundle.provenance.source_name}: {len(bundle.indicators)} indicators; SHA-256 {bundle.provenance.sha256[:16]}; {len(bundle.warnings)} warnings")

    def _completed(self, result: object) -> None:
        if isinstance(result, IntelligenceBundle):
            self._intelligence = tuple(bundle for bundle in self._intelligence if bundle.provenance.source_name != result.provenance.source_name) + (result,)
            self._refresh_bundles()
            self.status.setText(f"Loaded {len(result.indicators)} indicators; {len(result.warnings)} explicit coverage warnings.")
            self.detail.setPlainText(json.dumps(asdict(result.provenance), indent=2) + "\n" + "\n".join(result.warnings))
        elif isinstance(result, SecurityAnalysisReport):
            self._report = result
            self.findings.setRowCount(len(result.correlated_findings))
            for row, finding in enumerate(result.correlated_findings):
                for column, value in enumerate((finding.severity, finding.classification, finding.title, str(len(finding.artifact_paths)))):
                    self.findings.setItem(row, column, QTableWidgetItem(value))
            self.status.setText(f"{result.status_summary} {result.files_examined} files, {result.bytes_examined} bytes; {len(result.coverage_warnings)} coverage warnings.")
            self._show_details()
        else:
            self._failed("Worker returned an invalid result type.")

    def _failed(self, message: str) -> None:
        self.status.setText("Security operation failed: " + message)
        self.detail.setPlainText(message + "\nNo successful or clean-device conclusion can be drawn.")

    def _finished(self) -> None:
        worker = self._worker
        self._worker = None
        if worker is not None:
            worker.deleteLater()
        for button in self.findChildren(QPushButton):
            button.setEnabled(button is not self.stop_button)
        self.becameIdle.emit()

    def _show_details(self) -> None:
        report = self._report
        if report is None:
            return
        summary = report.status_summary + "\n\nCoverage:\n" + "\n".join(report.coverage_warnings) + "\n\nMethodology and limits:\n" + "\n".join(report.limitations)
        row = self.findings.currentRow()
        if 0 <= row < len(report.correlated_findings):
            correlated = report.correlated_findings[row]
            summary = correlated.title + "\n" + correlated.explanation + "\n\n" + summary
            if self.investigator.isChecked():
                originals = tuple(finding for finding in report.findings if finding.identifier in correlated.finding_ids)
                summary += "\n\nInvestigator detail:\n" + json.dumps(tuple(asdict(finding) for finding in originals), indent=2, ensure_ascii=False)
        self.detail.setPlainText(summary)

    def _export(self, format: SecurityReportFormat) -> None:
        if self._report is None:
            self._failed("Run a local analysis before exporting.")
            return
        selected, _ = QFileDialog.getSaveFileName(self, "Create a new private security report", f"security-analysis.{format}", f"{format.upper()} (*.{format})")
        if selected:
            try:
                report = redact_security_report(self._report) if self.redact.isChecked() else self._report
                write_security_report(report, format, Path(selected))
            except (OSError, SecurityValidationError) as error:
                QMessageBox.critical(self, "Security Report Export Failed", str(error))
                return
            self.status.setText(f"Created owner-only {format.upper()} report: {selected}. Review remaining metadata before sharing.")

    def _export_json(self) -> None:
        self._export("json")

    def _export_csv(self) -> None:
        self._export("csv")

    def _export_html(self) -> None:
        self._export("html")
