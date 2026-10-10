from __future__ import annotations

import hashlib
import json
import os
import plistlib
import sqlite3
import tempfile
import unittest
from pathlib import Path
from threading import Event

from ios_developer_toolkit.security_engine import analyze_security
from ios_developer_toolkit.security_intelligence import import_intelligence, parse_pattern
from ios_developer_toolkit.security_models import IntelligenceBundle, SecurityScanRequest, SecurityValidationError
from ios_developer_toolkit.security_reporting import redact_security_report, render_security_report, write_security_report


def bundle(root: Path) -> IntelligenceBundle:
    source = root / "iocs.json"
    source.write_text(json.dumps({"indicators": [
        {"id": "domain-1", "type": "domain", "value": "tracking.example", "confidence": 80},
        {"id": "url-1", "type": "url", "value": "https://tracking.example/Exact?key=A"},
        {"id": "ipv4-1", "type": "ipv4", "value": "192.0.2.8"},
    ]}), encoding="utf-8")
    return import_intelligence(source, 1_000_000, None, Event())


def scan_request(root: Path, intelligence: tuple[IntelligenceBundle, ...]) -> SecurityScanRequest:
    return SecurityScanRequest(root, "imported-evidence", intelligence, 100, 1_000_000, 5_000_000, 100)


class SecurityParityTests(unittest.TestCase):
    def test_conflicting_indicator_ids_invalid_revocation_and_truncated_values_are_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            source = root / "duplicate.json"
            source.write_text(json.dumps({"indicators": [{"id": "same", "type": "domain", "value": "one.example"}, {"id": "same", "type": "domain", "value": "two.example"}]}), encoding="utf-8")
            with self.assertRaisesRegex(SecurityValidationError, "conflicting"):
                import_intelligence(source, 1_000_000, None, Event())
            source.write_text(json.dumps({"indicators": [{"id": "same", "type": "domain", "value": "one.example", "revoked": "true"}]}), encoding="utf-8")
            with self.assertRaisesRegex(SecurityValidationError, "revoked must be boolean"):
                import_intelligence(source, 1_000_000, None, Event())
            intelligence = bundle(root)
            evidence = root / "long.txt"
            evidence.write_text("x" * 9000 + " tracking.example", encoding="utf-8")
            report = analyze_security(scan_request(evidence, (intelligence,)), Event())
            self.assertEqual(len(report.findings), 0)
            self.assertTrue(any("prefix examined only" in warning for warning in report.coverage_warnings))

    def test_stix_or_is_atomic_and_unsupported_and_is_not_downgraded(self) -> None:
        self.assertEqual(parse_pattern("[domain-name:value = 'one.example' OR domain-name:value = 'two.example']"), (("domain", "one.example"), ("domain", "two.example")))
        for pattern in ("[domain-name:value = 'one.example' AND domain-name:value = 'two.example']", "[url:value = 'https://example.test/a'] START t'2020-01-01T00:00:00Z'", "[domain-name:value = 'one.example' OR ]"):
            with self.assertRaises(SecurityValidationError):
                parse_pattern(pattern)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary).resolve() / "iocs.stix2"
            path.write_text(json.dumps({"type": "bundle", "objects": [
                {"type": "indicator", "id": "supported", "pattern": "[domain-name:value = 'tracking.example']"},
                {"type": "indicator", "id": "unsupported", "pattern": "[domain-name:value = 'tracking.example' AND process:name = 'example']"},
                {"type": "indicator", "id": "revoked", "revoked": True, "pattern": "[domain-name:value = 'revoked.example']"},
            ]}), encoding="utf-8")
            result = import_intelligence(path, 1_000_000, None, Event())
            self.assertEqual(len(result.indicators), 1)
            self.assertEqual(len(result.warnings), 2)
            self.assertEqual(result.provenance.sha256, hashlib.sha256(path.read_bytes()).hexdigest())

    def test_domains_ips_and_url_paths_preserve_match_boundaries_and_correlate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            intelligence = bundle(root)
            evidence = root / "evidence"
            evidence.mkdir()
            (evidence / "one.txt").write_text("sub.tracking.example https://tracking.example/Exact?key=A 192.0.2.8\n", encoding="utf-8")
            (evidence / "two.txt").write_text("tracking.example\n", encoding="utf-8")
            (evidence / "negative.txt").write_text("eviltracking.example tracking.example.evil https://other.example/exact?key=A 192.0.2.80\n", encoding="utf-8")
            report = analyze_security(scan_request(evidence, (intelligence,)), Event())
            self.assertEqual(report.status, "completed")
            self.assertEqual(len(report.findings), 4)
            self.assertEqual(len(report.correlated_findings), 3)
            self.assertFalse(any(finding.artifact_path == "negative.txt" for finding in report.findings))
            domain = next(finding for finding in report.correlated_findings if finding.title == "tracking.example")
            self.assertEqual(domain.artifact_paths, ("one.txt", "two.txt"))
            self.assertIn("independent", report.status_summary)

    def test_url_indicator_does_not_lowercase_paths_or_match_substrings(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            intelligence = bundle(root)
            path = root / "urls.txt"
            path.write_text("https://tracking.example/exact?key=A\nhttps://tracking.example/Exact?key=ABC\n", encoding="utf-8")
            report = analyze_security(scan_request(path, (intelligence,)), Event())
            self.assertFalse(any(finding.indicator_type == "url" for finding in report.findings))

    def test_hashed_backup_payloads_dispatch_by_logical_paths_and_sources_are_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            intelligence = bundle(root)
            backup = root / "backup"
            backup.mkdir()
            (backup / "Info.plist").write_bytes(plistlib.dumps({"Device Name": "Synthetic"}))
            (backup / "Manifest.plist").write_bytes(plistlib.dumps({"IsEncrypted": False}))
            manifest = sqlite3.connect(backup / "Manifest.db")
            manifest.execute("CREATE TABLE Files(fileID TEXT, domain TEXT, relativePath TEXT, flags INTEGER)")
            for position, name in enumerate(("Library/data.plist", "Library/data.json", "Library/data.db"), 1):
                file_id = str(position) * 40
                directory = backup / file_id[:2]
                directory.mkdir()
                payload = directory / file_id
                if name.endswith(".plist"):
                    payload.write_bytes(plistlib.dumps({"url": "tracking.example"}))
                elif name.endswith(".json"):
                    payload.write_text('{"host":"tracking.example"}', encoding="utf-8")
                else:
                    database = sqlite3.connect(payload)
                    database.execute("CREATE TABLE records(host TEXT)")
                    database.execute("INSERT INTO records VALUES(?)", ("tracking.example",))
                    database.commit()
                    database.close()
                manifest.execute("INSERT INTO Files VALUES(?, ?, ?, 1)", (file_id, "HomeDomain", name))
            manifest.execute("INSERT INTO Files VALUES(?, ?, ?, 1)", ("../outside", "HomeDomain", "invalid"))
            manifest.commit()
            manifest.close()
            original = {str(path.relative_to(backup)): hashlib.sha256(path.read_bytes()).hexdigest() for path in backup.rglob("*") if path.is_file()}
            request = SecurityScanRequest(backup, "backup", (intelligence,), 100, 1_000_000, 5_000_000, 100)
            report = analyze_security(request, Event())
            self.assertEqual(len(report.findings), 3)
            self.assertEqual({finding.artifact_path for finding in report.findings}, {"HomeDomain/Library/data.plist", "HomeDomain/Library/data.json", "HomeDomain/Library/data.db"})
            self.assertTrue(any("unsafe" in warning for warning in report.coverage_warnings))
            self.assertEqual(original, {str(path.relative_to(backup)): hashlib.sha256(path.read_bytes()).hexdigest() for path in backup.rglob("*") if path.is_file()})
            (backup / "Manifest.plist").write_bytes(plistlib.dumps({"IsEncrypted": True}))
            with self.assertRaises(SecurityValidationError):
                analyze_security(request, Event())

    def test_symlinks_size_limits_and_archive_coverage_remain_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            evidence = root / "evidence"
            evidence.mkdir()
            private = root / "private.txt"
            private.write_text("private.example", encoding="utf-8")
            (evidence / "escape.txt").symlink_to(private)
            (evidence / "large.txt").write_bytes(b"X" * 101)
            (evidence / "sysdiagnose.tar").write_bytes(b"archive")
            request = SecurityScanRequest(evidence, "sysdiagnose", (), 100, 100, 1000, 100)
            report = analyze_security(request, Event())
            self.assertEqual(report.files_examined, 1)
            warnings = "\n".join(report.coverage_warnings)
            self.assertIn("symbolic link", warnings)
            self.assertIn("oversized", warnings)
            self.assertIn("not extracted", warnings)
            with self.assertRaises(SecurityValidationError):
                analyze_security(scan_request(evidence / "escape.txt", ()), Event())

    def test_cancelled_scan_and_malformed_mvt_results_do_not_become_clean_results(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            evidence = root / "one.txt"
            evidence.write_text("example", encoding="utf-8")
            cancellation = Event()
            cancellation.set()
            cancelled = analyze_security(scan_request(evidence, ()), cancellation)
            self.assertEqual(cancelled.status, "cancelled")
            self.assertIn("partial", cancelled.status_summary)
            (root / "safari_detected.json").write_text('[{"matched_indicator":"tracking.example","timestamp":"2020-01-01"}]', encoding="utf-8")
            (root / "bad_detected.json").write_text('[1]', encoding="utf-8")
            (root / "escaped_detected.json").symlink_to(root / "safari_detected.json")
            report = analyze_security(SecurityScanRequest(root, "mvt-results", (), 100, 100_000, 1_000_000, 100), Event())
            self.assertEqual(len(report.findings), 1)
            self.assertIn("unverified", report.findings[0].ioc_source or "")
            self.assertTrue(any("must be objects" in warning for warning in report.coverage_warnings))
            self.assertTrue(any("symbolic link" in warning for warning in report.coverage_warnings))

    def test_configuration_payload_is_reviewable_without_inference_of_current_installation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary).resolve() / "profile.mobileconfig"
            path.write_bytes(plistlib.dumps({"PayloadContent": [{"PayloadType": "com.apple.security.root"}]}))
            report = analyze_security(scan_request(path, ()), Event())
            self.assertEqual(report.findings[0].classification, "configuration-concern")
            self.assertIn("does not establish", report.findings[0].explanation)

    def test_reports_are_private_exclusive_escaped_redacted_and_preserve_empty_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            intelligence = bundle(root)
            path = root / "private-name.txt"
            path.write_text('=tracking.example <script>alert(1)</script>', encoding="utf-8")
            report = analyze_security(scan_request(path, (intelligence,)), Event())
            document = render_security_report(report, "html").decode()
            self.assertNotIn("<script>alert", document)
            self.assertIn("&lt;script&gt;", document)
            self.assertIn("'=tracking.example", render_security_report(report, "csv").decode())
            redacted = render_security_report(redact_security_report(report), "json").decode()
            self.assertNotIn("tracking.example", redacted)
            self.assertNotIn("private-name", redacted)
            for format in ("json", "csv", "html"):
                destination = root / ("report." + format)
                write_security_report(report, format, destination)
                self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
                with self.assertRaises(FileExistsError):
                    write_security_report(report, format, destination)
            empty = analyze_security(scan_request(root / "iocs.json", ()), Event())
            csv = render_security_report(empty, "csv").decode()
            self.assertGreater(len(csv.splitlines()), 1)
            self.assertIn("No result establishes", csv)


if __name__ == "__main__":
    unittest.main()
