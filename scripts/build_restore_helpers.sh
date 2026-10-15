#!/bin/bash
set -euo pipefail
if [[ "$#" -ne 2 ]]; then
    echo "Usage: $0 OUTPUT_DIRECTORY PYTHON_EXECUTABLE" >&2
    exit 64
fi
repository_root="$(cd "$(dirname "$0")/.." && pwd)"
/bin/bash "$repository_root/scripts/build_restore_helpers_vendor.sh" "$1"
"$2" "$repository_root/scripts/verify_restore_helpers.py" "$1"
