from __future__ import annotations

from ios_developer_toolkit.models import IOSDevice


DEMO_DEVICE_IDENTIFIER = "DEMO-IPHONE-15-PRO"


def demo_device() -> IOSDevice:
    """Return the clearly simulated device shown by the local demo interface."""
    return IOSDevice(
        identifier=DEMO_DEVICE_IDENTIFIER,
        name="Demo iPhone 15 Pro (simulated)",
        product_type="iPhone16,1",
        product_version="26.3.1",
        build_version="23D123",
        connection_type="Demo",
    )


def demo_connection_banner() -> str:
    """Describe the demo boundary without implying a physical device connection."""
    return (
        "DEMO MODE — Showing a simulated iPhone for walkthroughs and screenshots. "
        "No physical device is connected; mounts, device commands, logs, backups, location changes, "
        "sideloading, and evidence collection remain disabled."
    )
