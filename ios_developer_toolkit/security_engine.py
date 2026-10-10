"""Read-only artifact scanning, external MVT normalization, and lead correlation.

Backup payloads are dispatched by their logical Manifest.db path or validated
content signature, never by their extensionless hashed storage filename.
"""

from __future__ import annotations

import hashlib
import io
import ipaddress
import json
import os
import plistlib
import re
import sqlite3
import stat
import time
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Event
from xml.parsers.expat import ExpatError

from ios_developer_toolkit.security_intelligence import (
    check_cancelled, normalized_indicator, parse_json, read_regular_file, timestamp,
)
from ios_developer_toolkit.security_models import (
    CorrelatedSecurityFinding, JSONValue, SecurityAnalysisReport, SecurityCancelled,
    SecurityFinding, SecurityIndicator, SecurityScanRequest, SecurityValidationError,
)


LIMITATIONS: tuple[str, ...] = (
    "No result establishes that a device is clean, compromised, or infected.",
    "IOC matches and configuration concerns are investigative leads requiring context and independent validation.",
    "Only supported local artifacts within explicit file, byte, row, nesting, and time limits are examined.",
    "Encrypted backups must be decrypted separately; archives are never extracted, and symlinks are never followed.",
    "STIX equality/OR is supported; unsupported semantics and certificate-hash extraction remain coverage gaps.",
    "Existing MVT detection JSON is normalized only; external scanner version, inputs, and completeness are unverified.",
    "Local hashes identify examined bytes; they do not establish origin, authenticity, or runtime behavior.",
)
TEXT_EXTENSIONS: tuple[str, ...] = (".txt", ".log", ".csv", ".xml", ".json", ".plist", ".mobileconfig", ".ips", ".crash")
ARCHIVE_EXTENSIONS: tuple[str, ...] = (".zip", ".gz", ".tar", ".tgz", ".bz2", ".xz", ".7z")


@dataclass(frozen=True)
class EvidenceFile:
    path: Path
    logical_path: str


@dataclass(frozen=True)
class Observation:
    kind: str
    value: str
    file: EvidenceFile
    table: str | None
    record_id: str | None


def validated_root(path: Path) -> Path:
    if not path.is_absolute():
        raise SecurityValidationError("Security evidence requires an absolute local path.")
    for component in (path, *path.parents):
        if component.is_symlink():
            raise SecurityValidationError("The evidence path cannot contain symbolic links.")
    info = path.stat()
    if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
        raise SecurityValidationError("Evidence must be a local regular file or directory.")
    return path.resolve(strict=True)


def directory_inventory(root: Path, maximum_files: int, cancellation: Event) -> tuple[tuple[EvidenceFile, ...], tuple[str, ...]]:
    files: list[EvidenceFile] = []
    warnings: list[str] = []
    pending = [root]
    entries_examined = 0
    while pending:
        check_cancelled(cancellation)
        directory = pending.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    check_cancelled(cancellation)
                    entries_examined += 1
                    if entries_examined > maximum_files * 4:
                        return tuple(files), tuple(warnings + ["Evidence inventory reached the directory-entry safety limit."])
                    candidate = Path(entry.path)
                    if entry.is_symlink():
                        warnings.append(f"Skipped symbolic link: {candidate.relative_to(root)}")
                    elif entry.is_dir(follow_symlinks=False):
                        pending.append(candidate)
                    elif entry.is_file(follow_symlinks=False):
                        files.append(EvidenceFile(candidate, str(candidate.relative_to(root))))
                        if len(files) >= maximum_files:
                            return tuple(sorted(files, key=lambda item: item.logical_path)), tuple(warnings + [f"Evidence inventory reached the {maximum_files}-file safety limit."])
        except OSError as error:
            warnings.append(f"Could not enumerate {directory.name}: {error.strerror}")
    return tuple(sorted(files, key=lambda item: item.logical_path)), tuple(warnings)


def sqlite_connection(path: Path, cancellation: Event, deadline: float) -> sqlite3.Connection:
    """Immutable SQLite prevents journal/source writes; bounded VM progress supports cancellation."""
    validated_root(path)
    uri = path.as_uri() + "?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True, timeout=1)
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA trusted_schema=OFF")
    connection.set_progress_handler(lambda: int(cancellation.is_set() or time.monotonic() >= deadline), 1000)
    return connection


def backup_inventory(request: SecurityScanRequest, cancellation: Event) -> tuple[tuple[EvidenceFile, ...], tuple[str, ...]]:
    root = request.evidence_root
    if not root.is_dir():
        raise SecurityValidationError("Backup analysis requires a decrypted backup directory.")
    manifest = root / "Manifest.db"
    info = root / "Info.plist"
    for required in (manifest, info):
        validated_root(required)
    encryption = root / "Manifest.plist"
    if encryption.exists() or encryption.is_symlink():
        try:
            metadata: object = plistlib.loads(read_regular_file(encryption, request.maximum_file_bytes))
        except (ValueError, plistlib.InvalidFileException, ExpatError, RecursionError, OverflowError) as error:
            raise SecurityValidationError("Backup Manifest.plist could not be parsed as bounded metadata.") from error
        if not isinstance(metadata, dict):
            raise SecurityValidationError("Backup Manifest.plist must be a dictionary.")
        encrypted = metadata.get("IsEncrypted")
        if encrypted is not None and not isinstance(encrypted, bool):
            raise SecurityValidationError("Backup IsEncrypted must be a boolean.")
        if encrypted is True:
            raise SecurityValidationError("Encrypted backups cannot be scanned. Decrypt a protected working copy separately.")
    files = [EvidenceFile(info, "Info.plist")]
    warnings = ["Immutable SQLite ignores WAL sidecars; live or uncheckpointed databases can contain unexamined records."]
    if not encryption.exists():
        warnings.append("Manifest.plist is absent; backup encryption metadata is unverified.")
    connection = sqlite_connection(manifest, cancellation, time.monotonic() + 10)
    try:
        rows = connection.execute("SELECT fileID, domain, relativePath FROM Files WHERE flags = 1 LIMIT ?", (request.maximum_files + 1,))
        for file_id, domain, logical_path in rows:
            check_cancelled(cancellation)
            if len(files) >= request.maximum_files:
                warnings.append(f"Backup inventory reached the {request.maximum_files}-file safety limit.")
                break
            if not isinstance(file_id, str) or re.fullmatch(r"[0-9a-fA-F]{40}", file_id) is None or not isinstance(domain, str) or not isinstance(logical_path, str):
                warnings.append("Skipped malformed or unsafe backup manifest row.")
                continue
            candidate = root / file_id[:2] / file_id
            if not candidate.exists() and not candidate.is_symlink():
                warnings.append(f"Missing backup payload: {domain}/{logical_path[:160]}")
                continue
            try:
                resolved = validated_root(candidate)
                if not resolved.is_relative_to(root):
                    raise SecurityValidationError("Backup payload escaped the selected root.")
            except (OSError, SecurityValidationError) as error:
                warnings.append(f"Skipped backup payload {file_id}: {error}")
                continue
            files.append(EvidenceFile(resolved, domain + "/" + logical_path.lstrip("/")))
    except sqlite3.Error as error:
        check_cancelled(cancellation)
        raise SecurityValidationError("Could not read bounded backup Manifest.db rows.") from error
    finally:
        connection.close()
    return tuple(files), tuple(warnings)


def scalar_fields(value: object, prefix: str, depth: int) -> tuple[tuple[str, str], ...]:
    if depth > 32:
        raise SecurityValidationError("Artifact exceeds the 32-level structured-data nesting limit.")
    if isinstance(value, dict):
        fields: list[tuple[str, str]] = []
        for key, item in value.items():
            if not isinstance(key, str):
                raise SecurityValidationError("Structured artifact keys must be strings.")
            fields.extend(scalar_fields(item, prefix + "." + key if prefix else key, depth + 1))
            if len(fields) > 100_000:
                raise SecurityValidationError("Structured artifact exceeds the field-count limit.")
        return tuple(fields)
    if isinstance(value, (list, tuple)):
        fields = []
        for index, item in enumerate(value):
            fields.extend(scalar_fields(item, f"{prefix}[{index}]", depth + 1))
            if len(fields) > 100_000:
                raise SecurityValidationError("Structured artifact exceeds the field-count limit.")
        return tuple(fields)
    if isinstance(value, (str, int, float, bool, datetime)):
        scalar = str(value)
        if len(scalar) > 8192:
            raise SecurityValidationError(f"Structured field {prefix[:160]} exceeds the 8,192-character value limit; content is not fully examined.")
        return ((prefix, scalar),)
    return ()


def sqlite_observations(file: EvidenceFile, request: SecurityScanRequest, cancellation: Event) -> tuple[tuple[Observation, ...], tuple[str, ...]]:
    observations: list[Observation] = []
    warnings = [f"Immutable SQLite cannot inspect uncheckpointed WAL records: {file.logical_path}"]
    connection = sqlite_connection(file.path, cancellation, time.monotonic() + 10)
    rows_examined = 0
    characters_examined = 0
    try:
        tables = connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' LIMIT 101").fetchall()
        if len(tables) > 100:
            warnings.append(f"SQLite table inventory limited to 100: {file.logical_path}")
        for (table,) in tables[:100]:
            check_cancelled(cancellation)
            if not isinstance(table, str):
                continue
            quoted = '"' + table.replace('"', '""') + '"'
            cursor = connection.execute(f"SELECT * FROM {quoted} LIMIT ?", (request.maximum_sqlite_rows - rows_examined + 1,))
            names = tuple(column[0] for column in cursor.description or ())
            for position, row in enumerate(cursor):
                check_cancelled(cancellation)
                if rows_examined >= request.maximum_sqlite_rows:
                    warnings.append(f"SQLite row limit reached: {file.logical_path}")
                    return tuple(observations), tuple(warnings)
                rows_examined += 1
                for name, value in zip(names, row):
                    if isinstance(value, (str, int, float)):
                        text = str(value)
                        if len(text) > 8192:
                            warnings.append(f"SQLite field exceeds the 8,192-character limit; prefix examined only: {file.logical_path}, table {table}, row {position + 1}, field {name}")
                            text = text[:8192]
                        characters_examined += len(text)
                        if characters_examined > request.maximum_file_bytes or len(observations) >= 100_000:
                            return tuple(observations), tuple(warnings + [f"SQLite decoded-content limit reached: {file.logical_path}"])
                        observations.append(Observation("text", text, file, table, f"{position + 1}:{name}"))
                    elif isinstance(value, bytes) and len(value) <= 8192:
                        try:
                            decoded = value.decode("utf-8")
                        except UnicodeError:
                            continue
                        characters_examined += len(decoded)
                        if characters_examined > request.maximum_file_bytes or len(observations) >= 100_000:
                            return tuple(observations), tuple(warnings + [f"SQLite decoded-content limit reached: {file.logical_path}"])
                        observations.append(Observation("text", decoded, file, table, f"{position + 1}:{name}"))
    except sqlite3.Error as error:
        check_cancelled(cancellation)
        raise SecurityValidationError(f"SQLite read failed or exceeded its 10-second VM deadline: {file.logical_path}") from error
    finally:
        connection.close()
    return tuple(observations), tuple(warnings)


def observations_for_file(file: EvidenceFile, content: bytes, request: SecurityScanRequest, cancellation: Event) -> tuple[tuple[Observation, ...], tuple[str, ...]]:
    logical = Path(file.logical_path)
    base = (Observation("filename", logical.name, file, None, None), Observation("path", file.logical_path, file, None, None))
    hash_kinds = tuple(dict.fromkeys(indicator.kind for bundle in request.intelligence for indicator in bundle.indicators if indicator.kind in ("md5", "sha1", "sha256")))
    hashes = tuple(Observation(kind, hashlib.new(kind, content).hexdigest(), file, None, None) for kind in hash_kinds)
    if content.startswith(b"SQLite format 3\x00"):
        observations, warnings = sqlite_observations(file, request, cancellation)
        return base + hashes + observations, warnings
    suffix = logical.suffix.lower()
    if suffix in ARCHIVE_EXTENSIONS:
        return base + hashes, (f"Archive content was not extracted: {file.logical_path}",)
    fields: tuple[tuple[str, str], ...]
    try:
        if suffix in (".plist", ".mobileconfig") or content.startswith(b"bplist") or b"<plist" in content[:256]:
            structured: object = plistlib.loads(content)
            fields = scalar_fields(structured, "", 0)
        elif suffix == ".json":
            fields = scalar_fields(parse_json(content), "", 0)
        elif suffix in TEXT_EXTENSIONS:
            text = content.decode("utf-8")
            text_fields: list[tuple[str, str]] = []
            text_warnings: list[str] = []
            with io.StringIO(text) as lines:
                for index, line in enumerate(lines):
                    if index >= request.maximum_sqlite_rows:
                        text_warnings.append(f"Text row limit reached: {file.logical_path}")
                        break
                    if len(line) > 8192:
                        text_warnings.append(f"Text line exceeds the 8,192-character limit; prefix examined only: {file.logical_path}, line {index + 1}")
                    text_fields.append((str(index + 1), line[:8192]))
            observations = tuple(Observation("text", value, file, None, field) for field, value in text_fields)
            return base + hashes + observations, tuple(text_warnings)
        else:
            return base + hashes, (f"Unsupported content type; only path and requested hashes examined: {file.logical_path}",)
    except (ValueError, UnicodeError, plistlib.InvalidFileException, ExpatError, RecursionError, OverflowError) as error:
        raise SecurityValidationError(f"Could not parse supported artifact: {file.logical_path}") from error
    return base + hashes + tuple(Observation("text", value, file, None, field) for field, value in fields), ()


def indicator_matches(observation: Observation, indicator: SecurityIndicator) -> bool:
    expected = indicator.value
    if indicator.kind in ("md5", "sha1", "sha256"):
        return observation.kind == indicator.kind and observation.value.lower() == expected
    if indicator.kind == "certificate-sha256":
        return False
    if indicator.kind == "filename":
        return observation.kind == "filename" and observation.value == expected
    if indicator.kind == "path":
        return observation.kind == "path" and (observation.value == expected or observation.value.endswith("/" + expected.lstrip("/")))
    if observation.kind != "text":
        return False
    text = observation.value
    if indicator.kind == "domain":
        candidates = re.findall(r"(?<![A-Za-z0-9.-])(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,63}(?![A-Za-z0-9.-])", text)
        return any(candidate.lower().rstrip(".") == expected or candidate.lower().rstrip(".").endswith("." + expected) for candidate in candidates)
    if indicator.kind == "url":
        for candidate in re.findall(r"https?://[^\s\"'<>]+", text):
            try:
                if normalized_indicator("url", candidate) == expected:
                    return True
            except SecurityValidationError:
                continue
        return False
    if indicator.kind in ("ipv4", "ipv6"):
        for candidate in re.findall(r"(?<![0-9A-Fa-f:.])[0-9A-Fa-f:.]+(?![0-9A-Fa-f:.])", text):
            try:
                if str(ipaddress.ip_address(candidate)) == expected:
                    return True
            except ValueError:
                continue
        return False
    if indicator.kind == "phone":
        return any(re.sub(r"\D", "", candidate) == expected for candidate in re.findall(r"(?<!\w)\+?\d[\d\s().-]{5,}\d(?!\w)", text))
    return re.search(r"(?<![A-Za-z0-9_.@+-])" + re.escape(expected) + r"(?![A-Za-z0-9_.@+-])", text, re.IGNORECASE if indicator.kind in ("email", "bundle-id", "configuration-profile-id") else 0) is not None


def native_finding(observation: Observation, indicator: SecurityIndicator) -> SecurityFinding:
    key = "\x00".join((indicator.provenance.sha256, indicator.identifier, observation.file.logical_path, observation.table or "", observation.record_id or ""))
    high = indicator.confidence is not None and indicator.confidence >= 70
    return SecurityFinding(hashlib.sha256(key.encode()).hexdigest()[:20], "Native IOC Scanner 1", "high-confidence-ioc-match" if high else "ioc-match", "high" if high else "medium", observation.file.logical_path, str(observation.file.path), observation.table, observation.record_id, None, observation.value[:1000], indicator.value, indicator.kind, indicator.provenance.source_name, indicator.identifier, indicator.provenance.sha256, f"{indicator.name} matched an explicitly loaded {indicator.kind} indicator. Validate this lead against surrounding evidence.", "Typed exact comparison; domains include true subdomains; URL paths and queries remain case-sensitive.", "Legitimate browsing, messages, testing, shared infrastructure, and stale intelligence can produce the same observable. Published confidence does not establish compromise.", indicator.kind + ":" + indicator.value)


def configuration_finding(observation: Observation) -> SecurityFinding | None:
    concerns = {
        "com.apple.security.root": "A root certificate payload can change device trust when installed.",
        "com.apple.mdm": "An MDM payload can represent administrative management when enrolled.",
        "com.apple.vpn.managed": "A managed VPN payload can change network routing when active.",
    }
    if observation.kind != "text" or not observation.record_id or not observation.record_id.endswith("PayloadType") or observation.value not in concerns:
        return None
    key = "configuration:" + observation.file.logical_path + ":" + observation.record_id
    return SecurityFinding(hashlib.sha256(key.encode()).hexdigest()[:20], "Configuration Review 1", "configuration-concern", "low", observation.file.logical_path, str(observation.file.path), None, observation.record_id, None, observation.value, None, None, None, None, None, concerns[observation.value] + " This local artifact does not establish current installation, ownership, activity, or malicious intent.", "Explicit PayloadType in a parsed local plist; manual review only.", "Enterprise, test, and personally installed profiles can be legitimate.", key)


def correlate_findings(findings: tuple[SecurityFinding, ...]) -> tuple[CorrelatedSecurityFinding, ...]:
    groups: dict[str, list[SecurityFinding]] = {}
    for finding in findings:
        groups.setdefault(finding.correlation_key, []).append(finding)
    ranks = {"informational": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
    result: list[CorrelatedSecurityFinding] = []
    for key, group in groups.items():
        strongest = max(group, key=lambda finding: ranks.get(finding.severity, 0))
        result.append(CorrelatedSecurityFinding(hashlib.sha256(key.encode()).hexdigest()[:20], strongest.matched_indicator or strongest.observed_value[:120], strongest.classification, strongest.severity, tuple(sorted({item.scanner for item in group})), tuple(sorted({item.artifact_path for item in group})), tuple(sorted({item.timestamp for item in group if item.timestamp is not None})), tuple(item.identifier for item in group), " ".join(dict.fromkeys(item.explanation for item in group))))
    return tuple(sorted(result, key=lambda item: (-ranks.get(item.severity, 0), item.title)))


def mvt_findings(file: EvidenceFile, content: bytes, maximum_records: int) -> tuple[tuple[SecurityFinding, ...], tuple[str, ...]]:
    payload = parse_json(content)
    records = payload if isinstance(payload, list) else [payload]
    if any(not isinstance(record, dict) for record in records):
        raise SecurityValidationError(f"MVT detected records must be objects: {file.logical_path}")
    findings: list[SecurityFinding] = []
    for position, record in enumerate(records[:maximum_records]):
        fields = scalar_fields(record, "", 0)
        matched = next((value for name, value in fields if name.rsplit(".", 1)[-1] in ("matched_indicator", "indicator", "ioc", "matched_ioc")), None)
        observed_time = next((value for name, value in fields if name.rsplit(".", 1)[-1] in ("timestamp", "time", "date")), None)
        serialized = json.dumps(record, ensure_ascii=False, sort_keys=True)
        identifier = hashlib.sha256((file.logical_path + str(position) + serialized).encode()).hexdigest()[:20]
        findings.append(SecurityFinding(identifier, "External MVT Output Adapter 1", "ioc-match" if matched else "requires-manual-review", "medium" if matched else "low", file.logical_path, str(file.path), file.path.name.removesuffix("_detected.json"), str(position + 1), observed_time, serialized[:1000], matched, None, "External MVT run; provenance unverified", None, None, "An independently generated MVT output contains this detection record. Review its original data, scanner version, and IOC set.", "Normalized existing *_detected.json; MVT was not run by this analysis.", "MVT detections and heuristics require manual artifact-level validation.", "mvt:" + (matched or identifier)))
    warnings = (f"MVT record limit reached: {file.logical_path}",) if len(records) > maximum_records else ()
    return tuple(findings), warnings


def analyze_security(request: SecurityScanRequest, cancellation: Event) -> SecurityAnalysisReport:
    """Return retained partial coverage on cancellation; invalid inputs fail explicitly."""
    if request.method not in ("backup", "sysdiagnose", "imported-evidence", "mvt-results") or min(request.maximum_files, request.maximum_file_bytes, request.maximum_total_bytes, request.maximum_sqlite_rows) <= 0:
        raise SecurityValidationError("Choose a supported acquisition method and positive analysis limits.")
    if request.maximum_files > 20_000 or request.maximum_file_bytes > 50 * 1024 * 1024 or request.maximum_total_bytes > 512 * 1024 * 1024 or request.maximum_sqlite_rows > 25_000:
        raise SecurityValidationError("Requested analysis limits exceed the supported safety maximums.")
    root = validated_root(request.evidence_root)
    started = timestamp()
    findings: list[SecurityFinding] = []
    warnings: list[str] = [warning for bundle in request.intelligence for warning in bundle.warnings]
    files_examined = 0
    bytes_examined = 0
    status = "completed"
    deadline = time.monotonic() + 300
    indicators: list[SecurityIndicator] = []
    now = datetime.now(timezone.utc)
    for bundle in request.intelligence:
        for indicator in bundle.indicators:
            if indicator.valid_until is not None and datetime.fromisoformat(indicator.valid_until.replace("Z", "+00:00")) < now:
                warnings.append(f"Skipped expired indicator {indicator.identifier}.")
            elif indicator.valid_from is not None and datetime.fromisoformat(indicator.valid_from.replace("Z", "+00:00")) > now:
                warnings.append(f"Skipped not-yet-valid indicator {indicator.identifier}.")
            else:
                indicators.append(indicator)
    if not indicators and request.method != "mvt-results":
        warnings.append("No active intelligence is loaded; only supported configuration concerns are reviewed.")
    if any(indicator.kind == "certificate-sha256" for indicator in indicators):
        warnings.append("Certificate SHA-256 indicators were not scanned: certificate extraction is unavailable.")
    seen: set[str] = set()
    try:
        check_cancelled(cancellation)
        if request.method == "backup":
            files, inventory_warnings = backup_inventory(request, cancellation)
        elif root.is_dir():
            files, inventory_warnings = directory_inventory(root, request.maximum_files, cancellation)
        else:
            files, inventory_warnings = (EvidenceFile(root, root.name),), ()
        warnings.extend(inventory_warnings)
        if request.method == "mvt-results":
            if not root.is_dir():
                raise SecurityValidationError("Existing MVT results require a folder.")
            files = tuple(file for file in files if file.path.name.endswith("_detected.json"))
            if not files:
                warnings.append("No *_detected.json files found; no external MVT detection coverage imported.")
        for file in files:
            check_cancelled(cancellation)
            if time.monotonic() >= deadline:
                warnings.append("Analysis stopped at its five-minute deadline; coverage is incomplete.")
                break
            try:
                info = file.path.lstat()
                if info.st_size > request.maximum_file_bytes:
                    warnings.append(f"Skipped oversized content: {file.logical_path}")
                    continue
                if bytes_examined + info.st_size > request.maximum_total_bytes:
                    warnings.append("Analysis reached the total-byte safety limit; coverage is incomplete.")
                    break
                content = read_regular_file(file.path, request.maximum_file_bytes)
                bytes_examined += len(content)
                files_examined += 1
                if request.method == "mvt-results":
                    remaining = 25_000 - len(findings)
                    if remaining <= 0:
                        warnings.append("MVT import reached the 25,000-finding safety limit.")
                        break
                    imported, coverage = mvt_findings(file, content, min(request.maximum_sqlite_rows, remaining))
                    findings.extend(imported)
                else:
                    observations, coverage = observations_for_file(file, content, request, cancellation)
                    comparisons = 0
                    for observation in observations:
                        check_cancelled(cancellation)
                        concern = configuration_finding(observation)
                        if concern is not None:
                            findings.append(concern)
                        for indicator in indicators:
                            comparisons += 1
                            if comparisons % 1000 == 0:
                                check_cancelled(cancellation)
                                if time.monotonic() >= deadline:
                                    raise SecurityValidationError("IOC comparison exceeded the five-minute analysis deadline.")
                            if indicator_matches(observation, indicator):
                                finding = native_finding(observation, indicator)
                                if finding.identifier not in seen:
                                    seen.add(finding.identifier)
                                    findings.append(finding)
                                    if len(findings) >= 25_000:
                                        raise SecurityValidationError("Analysis reached the 25,000-finding safety limit.")
                warnings.extend(coverage)
            except (OSError, SecurityValidationError, plistlib.InvalidFileException) as error:
                warnings.append(f"Content not fully examined: {file.logical_path}: {error}")
                if "analysis deadline" in str(error) or "finding safety limit" in str(error):
                    break
    except SecurityCancelled:
        status = "cancelled"
        warnings.append("User cancellation left coverage incomplete; retained findings are partial.")
    finished = timestamp()
    identity = hashlib.sha256((str(root) + started + finished).encode()).hexdigest()[:20]
    unique_findings = tuple({finding.identifier: finding for finding in findings}.values())
    return SecurityAnalysisReport(1, identity, started, finished, str(root), request.method, status, files_examined, bytes_examined, request.intelligence, unique_findings, correlate_findings(unique_findings), tuple(dict.fromkeys(warnings)), LIMITATIONS)
