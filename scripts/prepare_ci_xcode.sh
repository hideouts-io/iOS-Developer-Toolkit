#!/usr/bin/env bash
set -euo pipefail

# Configure only the disposable hosted runner, before native service checks.
if [[ "${GITHUB_ACTIONS:-}" != "true" || "${RUNNER_ENVIRONMENT:-}" != "github-hosted" ]]; then
  echo "Xcode CI initialization requires a disposable GitHub-hosted runner" >&2
  exit 64
fi

developer_directory="/Applications/Xcode_16.4.app/Contents/Developer"
xcodebuild="$developer_directory/usr/bin/xcodebuild"
if [[ "${DEVELOPER_DIR:-}" != "$developer_directory" || ! -x "$xcodebuild" ]]; then
  echo "Xcode CI initialization requires the installed Xcode 16.4 selected by DEVELOPER_DIR" >&2
  exit 65
fi

/usr/bin/sudo -n /usr/bin/xcode-select --switch "$developer_directory"
"$xcodebuild" -version
/usr/bin/sudo -n "$xcodebuild" -runFirstLaunch
"$xcodebuild" -checkFirstLaunchStatus
