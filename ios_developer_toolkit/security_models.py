"""Immutable inputs and results for bounded, local security evidence analysis."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypeAlias


AcquisitionMethod = Literal["backup", "sysdiagnose", "imported-evidence", "mvt-results"]
IndicatorType = Literal[
    "domain", "url", "ipv4", "ipv6", "filename", "path", "process", "bundle-id",
    "md5", "sha1", "sha256", "certificate-sha256", "email", "phone", "configuration-profile-id",
]
JSONValue: TypeAlias = str | int | float | bool | None | list["JSONValue"] | dict[str, "JSONValue"]


class SecurityValidationError(ValueError):
    """A local input violates the security analysis contract."""


class SecurityCancelled(Exception):
    """The user requested cancellation of an owned analysis operation."""


class SecurityNetworkError(RuntimeError):
    """A bounded public threat-intelligence request failed."""


@dataclass(frozen=True)
class IntelligenceProvenance:
    source_name: str
    organization: str
    source_url: str | None
    commit: str | None
    imported_at: str
    sha256: str
    local_path: str
    signature_status: str


@dataclass(frozen=True)
class SecurityIndicator:
    identifier: str
    kind: IndicatorType
    value: str
    name: str
    description: str
    confidence: int | None
    valid_from: str | None
    valid_until: str | None
    provenance: IntelligenceProvenance


@dataclass(frozen=True)
class IntelligenceBundle:
    provenance: IntelligenceProvenance
    indicators: tuple[SecurityIndicator, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class SecurityScanRequest:
    evidence_root: Path
    method: AcquisitionMethod
    intelligence: tuple[IntelligenceBundle, ...]
    maximum_files: int
    maximum_file_bytes: int
    maximum_total_bytes: int
    maximum_sqlite_rows: int


@dataclass(frozen=True)
class SecurityFinding:
    identifier: str
    scanner: str
    classification: str
    severity: str
    artifact_path: str
    source_file: str
    table: str | None
    record_id: str | None
    timestamp: str | None
    observed_value: str
    matched_indicator: str | None
    indicator_type: str | None
    ioc_source: str | None
    ioc_id: str | None
    provenance_sha256: str | None
    explanation: str
    detection_basis: str
    false_positive_notes: str
    correlation_key: str


@dataclass(frozen=True)
class CorrelatedSecurityFinding:
    identifier: str
    title: str
    classification: str
    severity: str
    scanner_names: tuple[str, ...]
    artifact_paths: tuple[str, ...]
    timestamps: tuple[str, ...]
    finding_ids: tuple[str, ...]
    explanation: str


@dataclass(frozen=True)
class SecurityAnalysisReport:
    schema_version: int
    analysis_id: str
    started_at: str
    finished_at: str
    evidence_root: str
    acquisition_method: AcquisitionMethod
    status: str
    files_examined: int
    bytes_examined: int
    intelligence: tuple[IntelligenceBundle, ...]
    findings: tuple[SecurityFinding, ...]
    correlated_findings: tuple[CorrelatedSecurityFinding, ...]
    coverage_warnings: tuple[str, ...]
    limitations: tuple[str, ...]

    @property
    def status_summary(self) -> str:
        if self.status == "cancelled":
            return "Analysis cancelled; retained results and coverage are partial."
        if self.status == "failed":
            return "Analysis failed; coverage is incomplete. Review the reported failure."
        if self.findings:
            return f"{len(self.correlated_findings)} investigative leads; validate context and independent evidence."
        return "No known indicators detected in analyzed coverage. This does not establish a clean device."
