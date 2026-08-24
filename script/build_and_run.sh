#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-run}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_DIR="$ROOT_DIR"
VENV_DIR="$PROJECT_DIR/venv"
DIST_DIR="$PROJECT_DIR/dist"
APP_BUNDLE="$DIST_DIR/iOS Developer Toolkit.app"
APP_CONTENTS="$APP_BUNDLE/Contents"
APP_MACOS="$APP_CONTENTS/MacOS"
APP_RESOURCES="$APP_CONTENTS/Resources"
APP_EXECUTABLE="$APP_MACOS/iOSDeveloperToolkit"
PROCESS_PATTERN="[i]os_developer_toolkit"

stop_existing() {
  while IFS= read -r process_id; do
    if [[ -n "$process_id" ]]; then
      kill "$process_id"
    fi
  done < <(pgrep -f "$PROCESS_PATTERN" || true)
}

build_app() {
  if [[ ! -x "$VENV_DIR/bin/python" ]]; then
    python3 -m venv "$VENV_DIR"
  fi
  "$VENV_DIR/bin/python" -m pip install --disable-pip-version-check --quiet "$PROJECT_DIR"
  mkdir -p "$APP_MACOS" "$APP_RESOURCES"
  cp "$PROJECT_DIR/macos/Info.plist" "$APP_CONTENTS/Info.plist"
  cp "$PROJECT_DIR/macos/iOSDeveloperToolkit" "$APP_EXECUTABLE"
  cp "$PROJECT_DIR/macos/iOSDeveloperToolkit.icns" "$APP_RESOURCES/iOSDeveloperToolkit.icns"
  chmod +x "$APP_EXECUTABLE"
}

open_app() {
  /usr/bin/open -n "$APP_BUNDLE"
}

stop_existing
build_app

case "$MODE" in
  run)
    open_app
    ;;
  --debug|debug)
    lldb -- "$VENV_DIR/bin/python" -m ios_developer_toolkit
    ;;
  --logs|logs)
    open_app
    /usr/bin/log stream --info --style compact --predicate 'process == "Python"'
    ;;
  --telemetry|telemetry)
    open_app
    /usr/bin/log stream --info --style compact --predicate 'process == "Python" AND eventMessage CONTAINS[c] "ios_developer_toolkit"'
    ;;
  --verify|verify)
    open_app
    sleep 3
    if ! APP_PID="$(pgrep -f "$PROCESS_PATTERN" | head -n 1)"; then
      echo "iOS Developer Toolkit exited before launch verification completed" >&2
      exit 1
    fi
    sleep 2
    kill -0 "$APP_PID"
    ;;
  *)
    echo "usage: $0 [run|--debug|--logs|--telemetry|--verify]" >&2
    exit 2
    ;;
esac
