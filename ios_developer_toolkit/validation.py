from __future__ import annotations


def output_indicates_failure(payload: bytes | str) -> bool:
    text = payload.decode("utf-8", errors="replace") if isinstance(payload, bytes) else payload
    normalized = text.lower()
    failure_markers = (
        " error device not found:",
        "\nerror:",
        "no device connected",
        "device is not connected",
        "failed to access an invalid lockdown service",
        "developer mode is not enabled",
        "developermodeisnotenablederror",
        "traceback (most recent call last)",
    )
    return any(marker in normalized for marker in failure_markers)
