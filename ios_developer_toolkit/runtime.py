from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Mapping


def pymobiledevice3_executable() -> Path:
    candidate = Path(sys.executable).with_name("pymobiledevice3")
    if not candidate.is_file():
        raise FileNotFoundError(
            f"pymobiledevice3 executable was not found next to the active Python interpreter: {candidate}"
        )
    return candidate


def device_environment(udid: str) -> Mapping[str, str]:
    environment = dict(os.environ)
    environment["PYMOBILEDEVICE3_UDID"] = udid
    environment["PYTHONUNBUFFERED"] = "1"
    environment["NO_COLOR"] = "1"
    return environment
