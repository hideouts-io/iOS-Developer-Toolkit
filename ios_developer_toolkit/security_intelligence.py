"""Validate IOC imports and explicitly update fixed public, commit-pinned sources."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import os
import re
import stat
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Event

from ios_developer_toolkit.security_models import (
    IntelligenceBundle, IntelligenceProvenance, IndicatorType, JSONValue,
    SecurityCancelled, SecurityIndicator, SecurityNetworkError, SecurityValidationError,
)


@dataclass(frozen=True)
class TrustedIntelligenceSource:
    identifier: str
    name: str
    organization: str
    repository: str
    branch: str
    path: str


TRUSTED_SOURCES: tuple[TrustedIntelligenceSource, ...] = (
    TrustedIntelligenceSource("amnesty-pegasus", "Amnesty Security Lab — Pegasus", "Amnesty International Security Lab", "AmnestyTech/investigations", "master", "2021-07-18_nso/pegasus.stix2"),
    TrustedIntelligenceSource("mvt-triangulation", "MVT — Operation Triangulation", "MVT project contributors", "mvt-project/mvt-indicators", "main", "2023-06_01_operation_triangulation/operation_triangulation.stix2"),
    TrustedIntelligenceSource("imazing-kingspawn", "iMazing — KingsPawn / QuaDream", "DigiDNA / iMazing", "DigiDNA/iMazing-Indicators-Of-Compromise", "main", "spywares/2023-04-11_KingsPawn-QuaDream/kingspawn.stix2"),
)
FIELD_TYPES: dict[str, IndicatorType] = {
    "domain-name:value": "domain", "url:value": "url", "ipv4-addr:value": "ipv4",
    "ipv6-addr:value": "ipv6", "file:name": "filename", "file:path": "path",
    "process:name": "process", "app:id": "bundle-id", "email-addr:value": "email",
    "phone-number:value": "phone", "configuration-profile:id": "configuration-profile-id",
    "file:hashes.md5": "md5", "file:hashes.sha1": "sha1", "file:hashes.sha256": "sha256",
    "x509-certificate:hashes.sha256": "certificate-sha256",
}


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def check_cancelled(cancellation: Event) -> None:
    if cancellation.is_set():
        raise SecurityCancelled("Security operation cancelled by the user.")


def json_value(value: object, depth: int) -> JSONValue:
    """Validate decoder output at its boundary, rejecting excessive nesting/nonfinite data."""
    if depth > 32:
        raise SecurityValidationError("JSON exceeds the 32-level nesting limit.")
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not (-float("inf") < value < float("inf")):
            raise SecurityValidationError("JSON contains a nonfinite number.")
        return value
    if isinstance(value, list):
        return [json_value(item, depth + 1) for item in value]
    if isinstance(value, dict):
        result: dict[str, JSONValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise SecurityValidationError("JSON object keys must be strings.")
            result[key] = json_value(item, depth + 1)
        return result
    raise SecurityValidationError("JSON contains an unsupported value type.")


def parse_json(content: bytes) -> JSONValue:
    try:
        decoded: object = json.loads(content)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise SecurityValidationError("Input is not valid bounded JSON.") from error
    return json_value(decoded, 0)


def read_regular_file(path: Path, maximum_bytes: int) -> bytes:
    """Open a bounded local regular file without following its final symlink."""
    if not path.is_absolute() or maximum_bytes <= 0:
        raise SecurityValidationError("Choose an absolute file path and positive byte limit.")
    for component in (path, *path.parents):
        if component.is_symlink():
            raise SecurityValidationError(f"Input path contains a symbolic link: {path.name}")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise SecurityValidationError(f"Input is not a regular file: {path.name}")
        if before.st_size > maximum_bytes:
            raise SecurityValidationError(f"Input {path.name} exceeds the {maximum_bytes}-byte limit.")
        content = stream.read(maximum_bytes + 1)
        after = os.fstat(stream.fileno())
        current = path.lstat()
        metadata = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
        if len(content) > maximum_bytes or not stat.S_ISREG(current.st_mode) or metadata != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns) or metadata != (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns, current.st_ctime_ns):
            raise SecurityValidationError(f"Input changed or exceeded its byte limit while reading: {path.name}")
        return content


def required_string(entry: dict[str, JSONValue], key: str, context: str) -> str:
    value = entry.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > 8192:
        raise SecurityValidationError(f"{context} requires a nonempty bounded string field {key}.")
    return value.strip()


def optional_string(entry: dict[str, JSONValue], key: str) -> str | None:
    value = entry.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 8192:
        raise SecurityValidationError(f"IOC field {key} must be a bounded string.")
    return value


def indicator_kind(value: str) -> IndicatorType:
    for candidate in FIELD_TYPES.values():
        if value == candidate:
            return candidate
    raise SecurityValidationError(f"Unsupported IOC type: {value[:100]}")


def normalized_indicator(kind: IndicatorType, value: str) -> str:
    if kind in ("ipv4", "ipv6"):
        try:
            address = ipaddress.ip_address(value)
        except ValueError as error:
            raise SecurityValidationError(f"Invalid {kind} indicator.") from error
        if address.version != (4 if kind == "ipv4" else 6):
            raise SecurityValidationError(f"Indicator is not a {kind} address.")
        return str(address)
    if kind in ("md5", "sha1", "sha256", "certificate-sha256"):
        size = {"md5": 32, "sha1": 40, "sha256": 64, "certificate-sha256": 64}[kind]
        if re.fullmatch(r"[0-9a-fA-F]{" + str(size) + r"}", value) is None:
            raise SecurityValidationError(f"Invalid {kind} digest.")
        return value.lower()
    if kind == "domain":
        try:
            domain = value.rstrip(".").encode("idna").decode("ascii").lower()
        except UnicodeError as error:
            raise SecurityValidationError("Invalid domain indicator.") from error
        if len(domain) > 253 or "." not in domain or any(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) is None for label in domain.split(".")):
            raise SecurityValidationError("Invalid domain indicator.")
        return domain
    if kind == "url":
        try:
            parts = urllib.parse.urlsplit(value)
            if parts.scheme not in ("http", "https") or not parts.hostname or parts.username is not None or parts.password is not None:
                raise SecurityValidationError("URL indicators require HTTP(S), a host, and no credentials.")
            host = parts.hostname.encode("idna").decode("ascii").lower()
            netloc = "[" + host + "]" if ":" in host else host
            if parts.port is not None:
                netloc += ":" + str(parts.port)
            return urllib.parse.urlunsplit((parts.scheme.lower(), netloc, parts.path or "/", parts.query, parts.fragment))
        except (ValueError, UnicodeError) as error:
            raise SecurityValidationError("Invalid URL indicator.") from error
    if kind == "phone":
        result = re.sub(r"\D", "", value)
        if not 7 <= len(result) <= 15:
            raise SecurityValidationError("Phone indicators require 7–15 digits.")
        return result
    return value.lower() if kind in ("email", "bundle-id", "configuration-profile-id") else value


def parse_pattern(pattern: str) -> tuple[tuple[IndicatorType, str], ...]:
    """Accept complete STIX equality/OR expressions; never reinterpret AND as OR."""
    if not pattern.startswith("[") or not pattern.endswith("]"):
        raise SecurityValidationError("Unsupported STIX envelope or temporal qualifiers.")
    body = pattern[1:-1]
    expression = re.compile(r"\s*([A-Za-z0-9_-]+:[A-Za-z0-9_.\-'\[\]]+)\s*=\s*'((?:\\['\\]|[^'\\])*)'\s*")
    comparisons: list[tuple[IndicatorType, str]] = []
    position = 0
    while position < len(body):
        match = expression.match(body, position)
        if match is None:
            raise SecurityValidationError("Unsupported STIX semantics; only equality and OR are analyzed.")
        field = match.group(1).lower().replace("['", ".").replace("']", "").replace("'", "").replace("sha-256", "sha256").replace("sha-1", "sha1")
        kind = FIELD_TYPES.get(field)
        if kind is None:
            raise SecurityValidationError(f"Unsupported STIX field: {field}")
        value = match.group(2).replace("\\'", "'").replace("\\\\", "\\").strip()
        if not value:
            raise SecurityValidationError("STIX equality contains an empty value.")
        comparisons.append((kind, normalized_indicator(kind, value)))
        position = match.end()
        if position == len(body):
            break
        separator = re.match(r"OR\s+", body[position:], re.IGNORECASE)
        if separator is None:
            raise SecurityValidationError("Unsupported STIX semantics; only equality and OR are analyzed.")
        position += separator.end()
        if position == len(body):
            raise SecurityValidationError("STIX OR expression is incomplete.")
    if not comparisons:
        raise SecurityValidationError("STIX pattern has no comparisons.")
    return tuple(comparisons)


def import_intelligence(path: Path, maximum_bytes: int, provenance: IntelligenceProvenance | None, cancellation: Event) -> IntelligenceBundle:
    check_cancelled(cancellation)
    if path.suffix.lower() not in (".json", ".stix", ".stix2"):
        raise SecurityValidationError("IOC files must use .json, .stix, or .stix2.")
    content = read_regular_file(path, maximum_bytes)
    payload = parse_json(content)
    if not isinstance(payload, dict):
        raise SecurityValidationError("IOC input requires a JSON object root.")
    digest = hashlib.sha256(content).hexdigest()
    origin = provenance if provenance is not None else IntelligenceProvenance(path.name, "Local import", None, None, timestamp(), digest, str(path), "not-provided")
    if origin.sha256 != digest:
        raise SecurityValidationError("IOC bytes do not match the supplied provenance digest.")
    stix = payload.get("type") == "bundle"
    entries = payload.get("objects" if stix else "indicators")
    if not isinstance(entries, list) or len(entries) > 100_000:
        raise SecurityValidationError("IOC input requires a bounded objects/indicators array.")
    indicators: list[SecurityIndicator] = []
    indexed_indicators: dict[str, SecurityIndicator] = {}
    warnings: list[str] = []
    for position, entry in enumerate(entries):
        check_cancelled(cancellation)
        if not isinstance(entry, dict):
            raise SecurityValidationError(f"IOC entry {position} is not an object.")
        if stix and entry.get("type") != "indicator":
            continue
        identifier = required_string(entry, "id", f"IOC entry {position}")
        if "revoked" in entry and not isinstance(entry["revoked"], bool):
            raise SecurityValidationError(f"IOC {identifier} revoked must be boolean.")
        if entry.get("revoked") is True:
            warnings.append(f"Skipped revoked indicator {identifier}.")
            continue
        confidence = entry.get("confidence")
        if confidence is not None and (not isinstance(confidence, int) or isinstance(confidence, bool) or not 0 <= confidence <= 100):
            raise SecurityValidationError(f"IOC {identifier} confidence must be an integer from 0 to 100.")
        try:
            if stix:
                if entry.get("pattern_type", "stix") != "stix":
                    raise SecurityValidationError("Unsupported STIX pattern type.")
                comparisons = parse_pattern(required_string(entry, "pattern", identifier))
            else:
                kind = indicator_kind(required_string(entry, "type", identifier))
                comparisons = ((kind, normalized_indicator(kind, required_string(entry, "value", identifier))),)
        except SecurityValidationError as error:
            if not stix:
                raise
            warnings.append(f"Skipped {identifier}: {error}")
            continue
        valid_from = optional_string(entry, "valid_from")
        valid_until = optional_string(entry, "valid_until")
        for date_text in (valid_from, valid_until):
            if date_text is not None:
                try:
                    parsed = datetime.fromisoformat(date_text.replace("Z", "+00:00"))
                except ValueError as error:
                    raise SecurityValidationError(f"IOC {identifier} has an invalid validity timestamp.") from error
                if parsed.tzinfo is None:
                    raise SecurityValidationError(f"IOC {identifier} validity requires a timezone.")
        for index, (kind, value) in enumerate(comparisons):
            indicator = SecurityIndicator(identifier + (f"#{index + 1}" if len(comparisons) > 1 else ""), kind, value, optional_string(entry, "name") or identifier, optional_string(entry, "description") or "Imported indicator", confidence, valid_from, valid_until, origin)
            previous = indexed_indicators.get(indicator.identifier)
            if previous is not None and previous != indicator:
                raise SecurityValidationError(f"IOC identifier {indicator.identifier} is repeated with conflicting content.")
            indexed_indicators[indicator.identifier] = indicator
            indicators.append(indicator)
    if not indicators:
        raise SecurityValidationError(f"IOC file contains no supported indicators; {len(warnings)} unsupported or revoked entries.")
    return IntelligenceBundle(origin, tuple(dict.fromkeys(indicators)), tuple(warnings))


def private_directory(path: Path) -> None:
    if not path.is_absolute() or path.is_symlink():
        raise SecurityValidationError("The intelligence cache must be an absolute real directory.")
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise SecurityValidationError("The intelligence cache path cannot contain symbolic links.")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not path.is_dir():
        raise SecurityValidationError("The intelligence cache is not a directory.")
    path.chmod(0o700)


class IntelligenceRedirectPolicy(urllib.request.HTTPRedirectHandler):
    """Refuse redirects before any request reaches an unreviewed endpoint."""

    def redirect_request(self, req: urllib.request.Request, fp: object, code: int, msg: str, headers: object, newurl: str) -> None:
        raise SecurityNetworkError("Threat-intelligence redirects are refused; review the source endpoint.")


def fetch_public(url: str, maximum_bytes: int, cancellation: Event) -> bytes:
    """Fetch only fixed public hosts; retry safe transient GET failures within a deadline."""
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in ("api.github.com", "raw.githubusercontent.com") or parsed.username or parsed.password:
        raise SecurityValidationError("Threat intelligence requires an approved public HTTPS host.")
    deadline = time.monotonic() + 45
    for attempt in range(3):
        check_cancelled(cancellation)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise SecurityNetworkError("Threat-intelligence request exceeded its 45-second deadline.")
        try:
            request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json, application/json, text/plain", "User-Agent": "iOS-Developer-Toolkit"})
            opener = urllib.request.build_opener(IntelligenceRedirectPolicy())
            with opener.open(request, timeout=min(10, remaining)) as response:
                final_url = urllib.parse.urlsplit(response.geturl())
                if final_url.scheme != "https" or final_url.hostname != parsed.hostname:
                    raise SecurityNetworkError("Threat-intelligence response redirected away from its approved HTTPS host.")
                chunks: list[bytes] = []
                size = 0
                while True:
                    check_cancelled(cancellation)
                    if time.monotonic() >= deadline:
                        raise SecurityNetworkError("Threat-intelligence download exceeded its deadline.")
                    chunk = response.read(min(65536, maximum_bytes - size + 1))
                    if not chunk:
                        return b"".join(chunks)
                    chunks.append(chunk)
                    size += len(chunk)
                    if size > maximum_bytes:
                        raise SecurityNetworkError(f"Threat-intelligence response exceeds {maximum_bytes} bytes.")
        except urllib.error.HTTPError as error:
            if error.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise SecurityNetworkError(f"Threat-intelligence GET returned HTTP {error.code}; source {parsed.hostname}.") from error
            retry_after = error.headers.get("Retry-After", "")
            delay = float(retry_after) if retry_after.isdecimal() else float(attempt + 1)
            if delay >= deadline - time.monotonic():
                raise SecurityNetworkError(f"HTTP {error.code} Retry-After exceeds the request deadline; update this source later.") from error
        except (urllib.error.URLError, TimeoutError) as error:
            if attempt == 2:
                raise SecurityNetworkError(f"Threat-intelligence GET failed for {parsed.hostname}; check this Mac's network and TLS trust.") from error
            delay = float(attempt + 1)
        logging.getLogger(__name__).warning("Threat-intelligence request will retry", extra={"operation": "public-ioc-get", "attempt": attempt + 1, "delay_seconds": delay, "host": parsed.hostname})
        if cancellation.wait(min(delay, max(0, deadline - time.monotonic()))):
            check_cancelled(cancellation)
    raise SecurityNetworkError("Threat-intelligence request exhausted its attempts.")


def update_intelligence(source: TrustedIntelligenceSource, cache: Path, maximum_bytes: int, cancellation: Event) -> IntelligenceBundle:
    """Resolve a fixed source's commit, preserve digest provenance, and reject cache mismatch."""
    if source not in TRUSTED_SOURCES or maximum_bytes <= 0:
        raise SecurityValidationError("Choose a fixed curated source and a positive download byte limit.")
    check_cancelled(cancellation)
    query = urllib.parse.urlencode({"path": source.path, "sha": source.branch, "per_page": "1"})
    metadata = parse_json(fetch_public(f"https://api.github.com/repos/{source.repository}/commits?{query}", 1_000_000, cancellation))
    if not isinstance(metadata, list) or not metadata or not isinstance(metadata[0], dict):
        raise SecurityValidationError("GitHub commit metadata is not a nonempty object array.")
    commit = required_string(metadata[0], "sha", "GitHub commit metadata")
    if re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        raise SecurityValidationError("GitHub returned an invalid commit SHA.")
    url = f"https://raw.githubusercontent.com/{source.repository}/{commit}/{urllib.parse.quote(source.path, safe='/')}"
    content = fetch_public(url, maximum_bytes, cancellation)
    check_cancelled(cancellation)
    private_directory(cache)
    destination_root = cache / source.identifier
    private_directory(destination_root)
    destination = destination_root / (commit + ".stix2")
    if destination.exists() or destination.is_symlink():
        if read_regular_file(destination, maximum_bytes) != content:
            raise SecurityValidationError("Cached IOC bytes differ from the commit-pinned download.")
    else:
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    origin = IntelligenceProvenance(source.name, source.organization, url, commit, timestamp(), hashlib.sha256(content).hexdigest(), str(destination), "not-provided")
    return import_intelligence(destination, maximum_bytes, origin, cancellation)
