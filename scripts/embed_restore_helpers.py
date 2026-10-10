"""Copy verified task-owned helper output into a new app bundle; never overwrite helpers."""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

if __package__:
    from .restore_helper_metadata import PACKAGED_HELPER_NAMES, helper_files, packaged_helper_files, packaged_metadata_names, source_inventory
    from .verify_restore_helpers import helper_manifest
else:
    from restore_helper_metadata import PACKAGED_HELPER_NAMES, helper_files, packaged_helper_files, packaged_metadata_names, source_inventory
    from verify_restore_helpers import helper_manifest


def embed_restore_helpers(source: Path, app: Path) -> Path:
    contents = app / "Contents"
    resources = contents / "Resources"
    if any(path.is_symlink() or not path.is_dir() for path in (app, contents, resources)) or not (contents / "Info.plist").is_file() or (contents / "Info.plist").is_symlink():
        raise ValueError("Choose a newly built regular macOS app bundle")
    script = Path(__file__).with_name("build_restore_helpers_vendor.sh")
    inventory = source_inventory(script)
    hashes = helper_files(source, inventory)
    expected = helper_manifest(source, script)
    if expected["files"] != hashes:
        raise ValueError("Helper source manifest differs from the verified standalone files")
    packaged_metadata_names(hashes, inventory)
    code = contents / "Helpers"
    if code.is_symlink() or (code.exists() and (not code.is_dir() or any(code.iterdir()))):
        raise ValueError("The app already contains helper code; refusing to overwrite it")
    destination = resources / "restore-helpers"
    if destination.exists() or destination.is_symlink():
        raise ValueError("The app already contains helper metadata; refusing to overwrite it")
    code.mkdir(exist_ok=True)
    destination.mkdir()
    for logical, name in PACKAGED_HELPER_NAMES.items():
        shutil.copy2(source / logical, code / name)
    for name in ("SOURCES.txt", "STAMP", "restore-helper-manifest.json"):
        shutil.copy2(source / name, destination / name)
    shutil.copytree(source / "licenses", destination / "licenses")
    if packaged_helper_files(app, inventory) != hashes:
        raise ValueError("Copied helper package differs from the verified standalone output")
    return destination


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("Usage: embed_restore_helpers.py HELPER_OUTPUT NEW_APP_BUNDLE")
    print(embed_restore_helpers(Path(sys.argv[1]).absolute(), Path(sys.argv[2]).absolute()))
