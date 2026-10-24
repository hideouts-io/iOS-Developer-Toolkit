from __future__ import annotations

import os
import io
import hashlib
import math
import plistlib
import re
import secrets
import shutil
import stat
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Callable, Literal
from urllib.parse import parse_qs, unquote_to_bytes

import requests

from ios_developer_toolkit.firmware_models import (
    CATALOG_URL, MAX_CATALOG_BYTES, MAX_IPSW_BYTES, MAX_MANIFEST_BYTES, TSS_URL,
    BuildIdentity, FileIdentity, FirmwareCancelled, FirmwareError, FirmwareFile,
    FirmwareRelease, file_identity, inspect_ipsw, parse_catalog, parse_manifest, require_file_unchanged, required_mapping, validated_apple_url,
)


CATALOG_CACHE_MAX_AGE = 86_400
MAX_CATALOG_CACHE_BYTES = MAX_CATALOG_BYTES + 64 * 1024
CatalogFetcher = Callable[[Event], bytes]
CatalogSource = Literal["cache", "apple"]


@dataclass(frozen=True)
class CatalogSnapshot:
    payload: bytes
    fetched_at: float
    source: CatalogSource


class FirmwareSigningRejected(FirmwareError):
    """Apple explicitly rejected signing for the requested build identity."""


def send_tss_request(request: dict[str, object]) -> dict[str, object]:
    """Send an SDK-built TSS request over verified HTTPS with bounded I/O.

    The pinned SDK's send_receive uses HTTP, disabled TLS verification, and no
    timeout. This boundary reuses its request builder while preserving TLS.
    Responses and requests are deliberately excluded from logs and errors.
    """
    payload = plistlib.dumps(request)
    if len(payload) > 8 * 1024 * 1024:
        raise FirmwareError("The Apple signing request exceeds its 8 MiB limit")
    try:
        with requests.post(TSS_URL, data=payload,
                           headers={"Content-Type": 'text/xml; charset="utf-8"', "User-Agent": "InetURL/1.0", "Cache-Control": "no-cache"},
                           timeout=(10, 45), verify=True, allow_redirects=False, stream=True) as response:
            if response.status_code != 200:
                raise FirmwareError(f"Apple signing failed with HTTP {response.status_code}; installation is blocked")
            chunks: list[bytes] = []
            total = 0
            started = time.monotonic()
            for chunk in response.iter_content(64 * 1024):
                total += len(chunk)
                if total > 8 * 1024 * 1024 or time.monotonic() - started > 90:
                    raise FirmwareError("Apple signing response exceeded its size or total deadline")
                chunks.append(chunk)
            body = b"".join(chunks)
    except requests.RequestException as error:
        raise FirmwareError("Apple signing could not be reached over verified HTTPS; check connectivity and try again") from error
    prefix, separator, ticket = body.partition(b"REQUEST_STRING=")
    try:
        fields = parse_qs(prefix.decode("ascii"), strict_parsing=False)
    except UnicodeDecodeError as error:
        raise FirmwareError("Apple signing returned an invalid status encoding") from error
    status, message = fields.get("STATUS"), fields.get("MESSAGE")
    if status == ["94"]:
        raise FirmwareSigningRejected("Apple rejected signing for this exact build identity; installation is blocked")
    if status != ["0"] or message != ["SUCCESS"] or not separator:
        raise FirmwareError("Apple signing returned an unknown or incomplete status; installation is blocked")
    if ticket.startswith(b"%"):
        ticket = unquote_to_bytes(ticket)
    try:
        return required_mapping(plistlib.loads(ticket), "Apple signing ticket")
    except (plistlib.InvalidFileException, ValueError, TypeError, OverflowError) as error:
        raise FirmwareError("Apple signing returned an invalid ticket property list") from error


def signing_request(identity: BuildIdentity) -> dict[str, object]:
    """Use the installed pymobiledevice3 AP Image4 request builder without its transport."""
    from pymobiledevice3.restore.tss import TSSRequest

    parameters = required_mapping(plistlib.loads(identity.serialized_identity), "signing build identity")
    parameters.update({"ApChipID": identity.chip_id, "ApBoardID": identity.board_id,
                       "ApSecurityDomain": identity.security_domain, "ApECID": secrets.randbits(52) + 1,
                       "ApNonce": secrets.token_bytes(32), "ApSepNonce": secrets.token_bytes(20),
                       "ApProductionMode": True, "ApSecurityMode": True, "ApSupportsImg4": True, "UID_MODE": False})
    info = required_mapping(parameters.get("Info"), "signing identity Info")
    if "RequiresUIDMode" in info:
        if not isinstance(info["RequiresUIDMode"], bool):
            raise FirmwareError("RequiresUIDMode must be boolean")
        parameters["RequiresUIDMode"] = info["RequiresUIDMode"]
    builder = TSSRequest()
    builder.add_common_tags(parameters, None)
    builder.add_ap_tags(parameters, None)
    builder.add_ap_img4_tags(parameters)
    tags = required_mapping(builder.tags(), "SDK signing request")
    tags.pop("@BBTicket", None)
    if not any(isinstance(value, dict) and "Digest" in value for value in tags.values()):
        raise FirmwareError("The SDK did not produce firmware component signing tags for this identity")
    return tags


def check_signing(identity: BuildIdentity) -> float:
    result = send_tss_request(signing_request(identity))
    ticket = result.get("ApImg4Ticket")
    if not isinstance(ticket, bytes) or not ticket:
        raise FirmwareError("Apple returned success without an application-processor ticket; installation is blocked")
    return time.time()


def read_apple_document(url: str, limit: int, cancelled: Event) -> bytes:
    selected = validated_apple_url(url)
    started = time.monotonic()
    try:
        for _ in range(6):
            if cancelled.is_set():
                raise FirmwareCancelled("Firmware catalog loading was stopped")
            with requests.get(selected, timeout=(10, 20), verify=True, allow_redirects=False, stream=True) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get("Location")
                    if location is None:
                        raise FirmwareError("Apple's firmware server returned a redirect without a destination")
                    selected = validated_apple_url(location)
                    continue
                if response.status_code != 200:
                    raise FirmwareError(f"Apple's firmware catalog failed with HTTP {response.status_code}")
                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_content(64 * 1024):
                    if cancelled.is_set():
                        raise FirmwareCancelled("Firmware catalog loading was stopped")
                    total += len(chunk)
                    if total > limit or time.monotonic() - started > 90:
                        raise FirmwareError("Apple's firmware document exceeded its size or total deadline")
                    chunks.append(chunk)
                return b"".join(chunks)
        raise FirmwareError("Apple's firmware server exceeded the five-redirect limit")
    except requests.RequestException as error:
        raise FirmwareError("Apple's firmware catalog could not be read over verified HTTPS") from error


def fetch_catalog(cancelled: Event) -> bytes:
    return read_apple_document(CATALOG_URL, MAX_CATALOG_BYTES, cancelled)


def _catalog_timestamp(value: object, description: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise FirmwareError(f"{description} must be a finite positive timestamp")
    return float(value)


def _catalog_cache_info(cache: Path) -> os.stat_result | None:
    try:
        info = cache.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise FirmwareError("The Apple catalog cache must be a regular owner-only file; choose a private cache folder")
    return info


def read_catalog_cache(cache: Path, cancelled: Event, now: float) -> CatalogSnapshot | None:
    """Read a private bounded cache; only a missing file is an ordinary cache miss.

    Corruption, source mismatch, future timestamps, and unsafe files fail
    explicitly. They never cause an implicit network request or signing result.
    """
    current_time = _catalog_timestamp(now, "The catalog clock")
    if cancelled.is_set():
        raise FirmwareCancelled("Apple catalog loading was stopped")
    private_library(cache.parent)
    info = _catalog_cache_info(cache)
    if info is None:
        return None
    if info.st_size > MAX_CATALOG_CACHE_BYTES:
        raise FirmwareError("The Apple catalog cache exceeds its size limit; use Refresh from Apple to replace it")
    descriptor = os.open(cache, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        initial = os.fstat(descriptor)
        identity = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        if identity != (initial.st_dev, initial.st_ino, initial.st_size, initial.st_mtime_ns, initial.st_ctime_ns):
            raise FirmwareError("The Apple catalog cache changed before reading; load it again")
        with os.fdopen(descriptor, "rb", closefd=False) as source:
            encoded = source.read(MAX_CATALOG_CACHE_BYTES + 1)
        after = os.fstat(descriptor)
        if identity != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns) or len(encoded) != info.st_size:
            raise FirmwareError("The Apple catalog cache changed while reading; load it again")
    finally:
        os.close(descriptor)
    try:
        envelope = required_mapping(plistlib.loads(encoded), "catalog cache")
        if set(envelope) != {"schema_version", "source_url", "fetched_at", "payload_sha256", "payload"}:
            raise FirmwareError("The Apple catalog cache has unsupported fields")
        if type(envelope["schema_version"]) is not int or envelope["schema_version"] != 1 or envelope["source_url"] != CATALOG_URL:
            raise FirmwareError("The Apple catalog cache is not bound to the supported Apple catalog source")
        fetched_at = _catalog_timestamp(envelope["fetched_at"], "The cached catalog time")
        if fetched_at > current_time:
            raise FirmwareError("The cached catalog time is in the future; check the Mac's clock")
        payload = envelope["payload"]
        digest = envelope["payload_sha256"]
        if not isinstance(payload, bytes) or len(payload) > MAX_CATALOG_BYTES:
            raise FirmwareError("The cached Apple catalog payload is invalid or too large")
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None or hashlib.sha256(payload).hexdigest() != digest:
            raise FirmwareError("The cached Apple catalog payload does not match its recorded SHA-256")
        parse_catalog(payload, "")
    except (FirmwareError, plistlib.InvalidFileException, ValueError, TypeError, OverflowError) as error:
        raise FirmwareError("The Apple catalog cache is invalid; use Refresh from Apple to replace it") from error
    if cancelled.is_set():
        raise FirmwareCancelled("Apple catalog loading was stopped")
    return CatalogSnapshot(payload, fetched_at, "cache")


def refresh_catalog(cache: Path, cancelled: Event, now: float, fetcher: CatalogFetcher) -> CatalogSnapshot:
    """Explicitly fetch and atomically replace only this private catalog cache.

    The caller supplies a wall-clock timestamp and the verified Apple fetch
    boundary. This cache stores release metadata, never signing tickets.
    """
    fetched_at = _catalog_timestamp(now, "The catalog clock")
    if cancelled.is_set():
        raise FirmwareCancelled("Apple catalog refresh was stopped")
    private_library(cache.parent)
    _catalog_cache_info(cache)
    payload = fetcher(cancelled)
    parse_catalog(payload, "")
    if cancelled.is_set():
        raise FirmwareCancelled("Apple catalog refresh was stopped")
    encoded = plistlib.dumps({"schema_version": 1, "source_url": CATALOG_URL, "fetched_at": fetched_at,
                             "payload_sha256": hashlib.sha256(payload).hexdigest(), "payload": payload}, fmt=plistlib.FMT_BINARY)
    if len(encoded) > MAX_CATALOG_CACHE_BYTES:
        raise FirmwareError("The Apple catalog cache exceeds its size limit")
    descriptor, temporary_name = tempfile.mkstemp(prefix=".apple-catalog-", dir=cache.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(encoded)
            output.flush()
            os.fsync(output.fileno())
        if cancelled.is_set():
            raise FirmwareCancelled("Apple catalog refresh was stopped before replacing the cache")
        _catalog_cache_info(cache)
        os.replace(temporary, cache)
    finally:
        temporary.unlink(missing_ok=True)
    return CatalogSnapshot(payload, fetched_at, "apple")


def load_catalog(cache: Path, cancelled: Event, now: float, fetcher: CatalogFetcher) -> CatalogSnapshot:
    """Reuse a catalog for less than one day; stale or missing data is fetched normally."""
    snapshot = read_catalog_cache(cache, cancelled, now)
    if snapshot is not None and now - snapshot.fetched_at < CATALOG_CACHE_MAX_AGE:
        return snapshot
    return refresh_catalog(cache, cancelled, now, fetcher)


class AppleRangeReader(io.RawIOBase):
    """Expose a bounded Apple HTTPS archive to the standard ZIP64 reader through exact ranges."""

    def __init__(self, url: str, cancelled: Event) -> None:
        super().__init__()
        self._url = validated_apple_url(url)
        self._cancelled = cancelled
        self._position = 0
        self._received = 0
        self._started = time.monotonic()
        if cancelled.is_set():
            raise FirmwareCancelled("Remote firmware inspection was stopped before contacting Apple")
        try:
            with requests.head(self._url, timeout=(10, 20), verify=True, allow_redirects=False) as response:
                length = response.headers.get("Content-Length")
                if response.status_code != 200 or length is None or not length.isdecimal():
                    raise FirmwareError("Apple's firmware server did not return an exact archive length for range inspection")
                self._length = int(length)
                if not 22 <= self._length <= MAX_IPSW_BYTES:
                    raise FirmwareError("The remote IPSW is too small or exceeds the 64 GiB limit")
        except requests.RequestException as error:
            raise FirmwareError("Remote firmware metadata could not be read over verified HTTPS") from error

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._position

    def seek(self, offset: int, *whence_values: int) -> int:
        if len(whence_values) > 1:
            raise FirmwareError("Remote ZIP seek accepts only one optional origin")
        whence = whence_values[0] if whence_values else os.SEEK_SET
        origin = {os.SEEK_SET: 0, os.SEEK_CUR: self._position, os.SEEK_END: self._length}.get(whence)
        if origin is None or not 0 <= origin + offset <= self._length:
            raise FirmwareError("Remote ZIP inspection requested an invalid archive offset")
        self._position = origin + offset
        return self._position

    def read(self, *size_values: int) -> bytes:
        if len(size_values) > 1:
            raise FirmwareError("Remote ZIP read accepts only one optional size")
        size = size_values[0] if size_values else -1
        if self._cancelled.is_set():
            raise FirmwareCancelled("Remote firmware inspection was stopped")
        requested = self._length - self._position if size == -1 else min(size, self._length - self._position)
        if requested == 0:
            return b""
        if requested < 0 or requested > MAX_CATALOG_BYTES or self._received + requested > 2 * MAX_CATALOG_BYTES:
            raise FirmwareError("Remote ZIP inspection exceeded its bounded range-read budget")
        end = self._position + requested - 1
        try:
            with requests.get(self._url, headers={"Range": f"bytes={self._position}-{end}"},
                              timeout=(10, 20), verify=True, allow_redirects=False, stream=True) as response:
                expected_range = f"bytes {self._position}-{end}/{self._length}"
                if response.status_code != 206 or response.headers.get("Content-Range") != expected_range:
                    raise FirmwareError("Apple's server did not return the exact requested firmware range; full-download fallback is disabled")
                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_content(64 * 1024):
                    if self._cancelled.is_set():
                        raise FirmwareCancelled("Remote firmware inspection was stopped")
                    total += len(chunk)
                    if total > requested or time.monotonic() - self._started > 180:
                        raise FirmwareError("Remote firmware inspection exceeded its range size or total deadline")
                    chunks.append(chunk)
                if total != requested:
                    raise FirmwareError("Apple's server truncated the requested firmware range")
        except requests.RequestException as error:
            raise FirmwareError("Remote firmware range inspection failed over verified HTTPS") from error
        self._position += requested
        self._received += requested
        return b"".join(chunks)


def inspect_remote_ipsw(release: FirmwareRelease, cancelled: Event) -> FirmwareFile:
    """Read only a catalog IPSW's bounded BuildManifest using HTTP ranges and ZIP64."""
    try:
        with AppleRangeReader(release.url, cancelled) as reader, zipfile.ZipFile(reader, "r", allowZip64=True) as archive:
            entries = archive.infolist()
            if len(entries) > 100_000 or sum(entry.file_size for entry in entries) > MAX_IPSW_BYTES:
                raise FirmwareError("The remote IPSW exceeds its entry or expanded-size limit")
            matches = [entry for entry in entries if entry.filename == "BuildManifest.plist"]
            if len(matches) != 1:
                raise FirmwareError("The remote IPSW must contain exactly one root BuildManifest.plist")
            entry = matches[0]
            if entry.flag_bits & 1 or entry.file_size > MAX_MANIFEST_BYTES or entry.compress_size > MAX_MANIFEST_BYTES:
                raise FirmwareError("The remote firmware manifest is encrypted or exceeds its size limit")
            with archive.open(entry, "r") as source:
                payload = source.read(MAX_MANIFEST_BYTES + 1)
            if len(payload) != entry.file_size:
                raise FirmwareError("The remote manifest length does not match its ZIP record")
            firmware = parse_manifest(payload, Path(release.filename))
            if release.product_type not in firmware.product_types or firmware.build != release.build or firmware.version != release.version:
                raise FirmwareError("The remote manifest contradicts the selected Apple catalog release")
            return firmware
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError) as error:
        if isinstance(error, FirmwareError):
            raise
        raise FirmwareError("The remote firmware ZIP64 manifest could not be read") from error


def private_library(directory: Path) -> None:
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if directory.is_symlink() or not directory.is_dir():
        raise FirmwareError("The firmware library must be a regular directory")
    info = directory.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise FirmwareError("Choose a firmware library folder owned by this user with private mode 0700")


def import_ipsw(source: Path, directory: Path, cancelled: Event, progress: Callable[[str], None]) -> FirmwareFile:
    private_library(directory)
    firmware = inspect_ipsw(source)
    progress("Computing the source IPSW hashes before copying…")
    identity = file_identity(source, cancelled.is_set)
    destination = directory / source.name
    if source.absolute() == destination.absolute():
        return firmware
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as output, source.open("rb") as input_file:
            while True:
                if cancelled.is_set():
                    raise FirmwareCancelled("Firmware import was stopped")
                chunk = input_file.read(8 * 1024 * 1024)
                if not chunk:
                    break
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        copied = file_identity(destination, cancelled.is_set)
        require_file_unchanged(identity)
        if copied.sha256 != identity.sha256:
            raise FirmwareError("The imported IPSW hash differs from the source")
        return inspect_ipsw(destination)
    except (OSError, FirmwareError):
        destination.unlink(missing_ok=True)
        raise


def download_ipsw(release: FirmwareRelease, directory: Path, cancelled: Event, progress: Callable[[str], None]) -> FileIdentity:
    """Resume only a catalog-digest-bound partial download, preserving it on cancellation."""
    private_library(directory)
    url = validated_apple_url(release.url)
    if not re.fullmatch(r"[A-Za-z0-9_.+,-]+\.ipsw", release.filename):
        raise FirmwareError("The firmware catalog filename contains unsupported characters")
    destination = directory / release.filename
    partial = directory / f"{release.filename}.partial"
    if destination.exists() or destination.is_symlink():
        raise FirmwareError("This firmware already exists in the library; import or inspect it instead")
    if partial.is_symlink():
        raise FirmwareError("The firmware partial download must not be a symbolic link")
    offset = partial.stat().st_size if partial.exists() else 0
    if offset and release.sha1 is None:
        raise FirmwareError("Resume requires Apple's catalog checksum; choose a fresh library folder for this checksum-free download")
    headers = {"Range": f"bytes={offset}-"} if offset else {}
    progress(f"Downloading firmware; resuming at {offset:,} bytes" if offset else "Downloading firmware…")
    try:
        with requests.get(url, headers=headers, timeout=(10, 20), verify=True, allow_redirects=False, stream=True) as response:
            expected_status = 206 if offset else 200
            if response.status_code != expected_status:
                raise FirmwareError(f"Firmware download returned HTTP {response.status_code}; expected {expected_status}. Partial bytes were retained")
            length = response.headers.get("Content-Length")
            if length is None or not length.isdecimal():
                raise FirmwareError("Apple's firmware server did not return an exact download length")
            remaining = int(length)
            total = offset + remaining
            if remaining <= 0 or total > MAX_IPSW_BYTES:
                raise FirmwareError("The firmware download is empty or exceeds the 64 GiB limit")
            if offset:
                content_range = response.headers.get("Content-Range", "")
                if content_range != f"bytes {offset}-{total - 1}/{total}":
                    raise FirmwareError("Apple's firmware server returned a mismatched resume range")
            if shutil.disk_usage(directory).free < remaining + 512 * 1024 * 1024:
                raise FirmwareError("The firmware library needs the download size plus 512 MiB of free space")
            flags = os.O_WRONLY | os.O_NOFOLLOW | (os.O_APPEND if offset else os.O_CREAT | os.O_EXCL)
            descriptor = os.open(partial, flags, 0o600)
            downloaded = offset
            started = time.monotonic()
            with os.fdopen(descriptor, "ab" if offset else "wb") as output:
                for chunk in response.iter_content(1024 * 1024):
                    if cancelled.is_set():
                        raise FirmwareCancelled("Firmware download was stopped; partial bytes remain for checksum verification after resume")
                    if time.monotonic() - started > 24 * 3600:
                        raise FirmwareError("Firmware download exceeded its 24-hour total deadline; partial bytes were retained")
                    downloaded += len(chunk)
                    if downloaded > total:
                        raise FirmwareError("Firmware download exceeded its declared length")
                    output.write(chunk)
                    progress(f"Downloaded {downloaded:,} / {total:,} bytes")
                output.flush()
                os.fsync(output.fileno())
            if downloaded != total:
                raise FirmwareError("Firmware download ended before the declared length; partial bytes were retained")
    except requests.RequestException as error:
        raise FirmwareError("Firmware download failed over verified HTTPS; partial bytes were retained") from error
    progress("Computing downloaded SHA-1 and SHA-256…")
    identity = file_identity(partial, cancelled.is_set)
    if release.sha1 is not None and identity.sha1 != release.sha1:
        raise FirmwareError("Downloaded bytes do not match Apple's catalog SHA-1; installation is blocked. Choose a fresh library folder to download again")
    os.link(partial, destination, follow_symlinks=False)
    partial.unlink()
    inspect_ipsw(destination)
    return file_identity(destination, cancelled.is_set)
