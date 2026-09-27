#!/bin/bash
# Selects Xcode for a GitHub Actions job by exporting DEVELOPER_DIR (no sudo, no xcode-select).
# Uses Xcode_$XCODE_VERSION.app when XCODE_VERSION is set (for example "26.2"), otherwise the
# newest non-beta Xcode installed on the runner.
set -euo pipefail

if [[ -n "${XCODE_VERSION:-}" ]]; then
    xcode="/Applications/Xcode_${XCODE_VERSION}.app"
    [[ -d "$xcode" ]] || { echo "Xcode $XCODE_VERSION is not installed on this runner:" >&2; ls -d /Applications/Xcode*.app >&2; exit 1; }
else
    xcode="$(ls -d /Applications/Xcode_*.app 2>/dev/null | grep -Eiv 'beta|rc|release_candidate' | sort -V | tail -1 || true)"
    [[ -n "$xcode" ]] || xcode="/Applications/Xcode.app"
fi

developer_dir="$xcode/Contents/Developer"
if [[ -n "${GITHUB_ENV:-}" ]]; then
    echo "DEVELOPER_DIR=$developer_dir" >> "$GITHUB_ENV"
fi
DEVELOPER_DIR="$developer_dir" xcodebuild -version
DEVELOPER_DIR="$developer_dir" swift --version
