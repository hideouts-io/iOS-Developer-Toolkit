"""Copy verified task-owned helper output into a new app bundle; never overwrite helpers."""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

from verify_restore_helpers import helper_manifest


def embed_restore_helpers(source: Path, app: Path) -> Path:
    if app.is_symlink() or not (app / "Contents/Info.plist").is_file():
        raise ValueError("Choose a newly built regular macOS app bundle")
    helper_manifest(source, Path(__file__).with_name("build_restore_helpers_vendor.sh"))
    destination = app / "Contents/Helpers/restore-helpers"
    if destination.exists() or destination.is_symlink():
        raise ValueError("The app already contains helpers; refusing to overwrite them")
    shutil.copytree(source, destination)
    return destination


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("Usage: embed_restore_helpers.py HELPER_OUTPUT NEW_APP_BUNDLE")
    print(embed_restore_helpers(Path(sys.argv[1]).resolve(strict=True), Path(sys.argv[2]).resolve(strict=True)))
