"""Create exclusive, private reports with explicit coverage and optional redaction."""

from __future__ import annotations

import csv
import html
import io
import json
import os
from dataclasses import asdict, replace
from pathlib import Path
from typing import Literal

from ios_developer_toolkit.security_models import SecurityAnalysisReport, SecurityValidationError


SecurityReportFormat = Literal["json", "csv", "html"]


def redact_security_report(report: SecurityAnalysisReport) -> SecurityAnalysisReport:
    """Omit private values/paths, preserving coverage and stable lead relationships."""
    bundles = tuple(replace(bundle, provenance=replace(bundle.provenance, source_name="Redacted intelligence source", organization="Redacted organization", source_url=None, local_path="[redacted]"), indicators=tuple(replace(indicator, identifier="[redacted]", value="[redacted]", name="Redacted indicator", description="[redacted]", provenance=replace(indicator.provenance, source_name="Redacted intelligence source", organization="Redacted organization", source_url=None, local_path="[redacted]")) for indicator in bundle.indicators), warnings=tuple("Intelligence coverage warning; details redacted." for _ in bundle.warnings)) for bundle in report.intelligence)
    findings = tuple(replace(finding, artifact_path="[redacted]", source_file="[redacted]", table=None, record_id=None, timestamp=None, observed_value="[redacted]", matched_indicator="[redacted]" if finding.matched_indicator is not None else None, ioc_id="[redacted]" if finding.ioc_id is not None else None, ioc_source="Redacted source" if finding.ioc_source is not None else None, explanation="An investigative lead was recorded. Review the private original for artifact context.", false_positive_notes="Legitimate explanations require validation against the private original.", correlation_key=finding.identifier) for finding in report.findings)
    correlated = tuple(replace(finding, title="Redacted investigative lead", artifact_paths=("[redacted]",), timestamps=(), explanation="Review the private original for artifact and indicator context.") for finding in report.correlated_findings)
    warnings = tuple("Coverage warning retained; private details omitted." for _ in report.coverage_warnings) + ("Paths, artifact fields, IOC values, source names, and evidence timestamps were redacted. Review remaining metadata before sharing.",)
    return replace(report, evidence_root="[redacted]", intelligence=bundles, findings=findings, correlated_findings=correlated, coverage_warnings=warnings)


def csv_cell(value: str) -> str:
    """Neutralize spreadsheet formula injection while retaining the raw JSON original."""
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r")) else value


def render_security_report(report: SecurityAnalysisReport, format: SecurityReportFormat) -> bytes:
    if format == "json":
        return (json.dumps(asdict(report), ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if format == "csv":
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(("analysis_id", "status", "method", "evidence_root", "files_examined", "bytes_examined", "coverage_warnings", "limitations", "finding_id", "scanner", "classification", "severity", "artifact_path", "record_id", "timestamp", "observed_value", "matched_indicator", "indicator_type", "ioc_source", "explanation", "detection_basis", "false_positive_notes"))
        common = (report.analysis_id, report.status, report.acquisition_method, report.evidence_root, str(report.files_examined), str(report.bytes_examined), "; ".join(report.coverage_warnings), "; ".join(report.limitations))
        for finding in report.findings:
            writer.writerow(tuple(csv_cell(value) for value in common + (finding.identifier, finding.scanner, finding.classification, finding.severity, finding.artifact_path, finding.record_id or "", finding.timestamp or "", finding.observed_value, finding.matched_indicator or "", finding.indicator_type or "", finding.ioc_source or "", finding.explanation, finding.detection_basis, finding.false_positive_notes)))
        if not report.findings:
            writer.writerow(tuple(csv_cell(value) for value in common) + ("",) * 14)
        return output.getvalue().encode("utf-8")
    if format == "html":
        escape = html.escape
        coverage = "".join("<li>" + escape(warning) + "</li>" for warning in report.coverage_warnings)
        limitations = "".join("<li>" + escape(limit) + "</li>" for limit in report.limitations)
        leads = "".join("<article><h2>" + escape(finding.matched_indicator or finding.classification) + "</h2><p>" + escape(finding.explanation) + "</p><dl><dt>Classification / severity</dt><dd>" + escape(finding.classification + " / " + finding.severity) + "</dd><dt>Artifact</dt><dd>" + escape(finding.artifact_path) + "</dd><dt>Observed value</dt><dd><pre>" + escape(finding.observed_value) + "</pre></dd><dt>Detection basis</dt><dd>" + escape(finding.detection_basis) + "</dd><dt>Investigator detail</dt><dd>" + escape(f"Scanner: {finding.scanner}; field: {finding.record_id}; database table: {finding.table}; timestamp: {finding.timestamp}; IOC: {finding.ioc_id}; provenance SHA-256: {finding.provenance_sha256}") + "</dd><dt>False-positive context</dt><dd>" + escape(finding.false_positive_notes) + "</dd></dl></article>" for finding in report.findings)
        document = '<!doctype html><html lang="en"><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'"><title>Security analysis report</title><style>body{font:16px system-ui;max-width:960px;margin:2rem auto;padding:1rem}pre,dd{white-space:pre-wrap;overflow-wrap:anywhere}article{border-top:1px solid #aaa;margin-top:2rem}dt{font-weight:600}</style><body><h1>Security analysis report</h1><p>' + escape(report.status_summary) + '</p><p>' + escape(f"Analysis {report.analysis_id}; {report.acquisition_method}; status {report.status}; {report.files_examined} files, {report.bytes_examined} bytes.") + '</p><h2>Coverage and methodology</h2><ul>' + coverage + '</ul><ul>' + limitations + '</ul>' + leads + '</body></html>'
        return document.encode("utf-8")
    raise SecurityValidationError("Report format must be JSON, CSV, or HTML.")


def write_security_report(report: SecurityAnalysisReport, format: SecurityReportFormat, destination: Path) -> None:
    """Write a new owner-only report; existing files and symlink paths are refused."""
    if not destination.is_absolute() or destination.suffix.lower() != "." + format:
        raise SecurityValidationError(f"Choose an absolute new .{format} report path.")
    for component in (destination.parent, *destination.parent.parents):
        if component.is_symlink():
            raise SecurityValidationError("Report output cannot contain symbolic-link directories.")
    content = render_security_report(report, format)
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
