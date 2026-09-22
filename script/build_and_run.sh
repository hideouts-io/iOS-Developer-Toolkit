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

project_runtime_digest() {
  shasum -a 256 "$PROJECT_DIR/pyproject.toml" | awk '{print $1}'
}

python_is_compatible() {
  local python_command="$1"
  "$python_command" -c 'import sys; raise SystemExit(not ((3, 10) <= sys.version_info[:2] < (3, 14)))'
}

select_compatible_python() {
  local candidate
  local candidate_path
  for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
    if ! candidate_path="$(command -v "$candidate")"; then
      continue
    fi
    if python_is_compatible "$candidate_path"; then
      printf '%s\n' "$candidate_path"
      return 0
    fi
  done
  echo "Python 3.10 through 3.13 is required; no compatible interpreter was found" >&2
  return 1
}

runtime_matches_project() {
  local environment_directory="$1"
  local runtime_stamp="$environment_directory/.ios-developer-toolkit-runtime"
  if [[ ! -f "$runtime_stamp" ]]; then
    return 1
  fi
  local recorded_digest
  read -r recorded_digest < "$runtime_stamp"
  if [[ "$recorded_digest" != "$(project_runtime_digest)" ]]; then
    return 1
  fi
  "$environment_directory/bin/python" -c 'import PySide6; import pymobiledevice3'
  "$environment_directory/bin/python" -m pip check >/dev/null
  "$environment_directory/bin/pymobiledevice3" version >/dev/null
}

install_project_runtime() {
  local environment_directory="$1"
  local runtime_stamp="$environment_directory/.ios-developer-toolkit-runtime"
  for legacy_distribution in PySide6 PySide6-Addons; do
    if "$environment_directory/bin/python" -m pip show "$legacy_distribution" >/dev/null 2>&1; then
      "$environment_directory/bin/python" -m pip uninstall --yes "$legacy_distribution" || return 1
    fi
  done
  "$environment_directory/bin/python" -m pip install \
    --disable-pip-version-check \
    --force-reinstall \
    --upgrade \
    "$PROJECT_DIR" || return 1
  "$environment_directory/bin/python" -m pip check || return 1
  "$environment_directory/bin/python" -c 'import PySide6; import pymobiledevice3' || return 1
  project_runtime_digest > "$runtime_stamp"
}

replace_incompatible_environment() {
  local bootstrap_python="$1"
  local previous_environment=""
  if [[ -d "$VENV_DIR" ]]; then
    previous_environment="$(mktemp -d "$PROJECT_DIR/.toolkit-venv-previous.XXXXXX")"
    rmdir "$previous_environment"
    mv "$VENV_DIR" "$previous_environment"
  fi

  if ! "$bootstrap_python" -m venv "$VENV_DIR"; then
    if [[ -n "$previous_environment" ]]; then
      mv "$previous_environment" "$VENV_DIR"
    fi
    return 1
  fi
  if ! install_project_runtime "$VENV_DIR"; then
    /usr/bin/find "$VENV_DIR" -depth -delete
    if [[ -n "$previous_environment" ]]; then
      mv "$previous_environment" "$VENV_DIR"
    fi
    return 1
  fi
  if [[ -n "$previous_environment" ]]; then
    /usr/bin/find "$previous_environment" -depth -delete
  fi
}

build_app() {
  local bootstrap_python
  bootstrap_python="$(select_compatible_python)"
  if [[ ! -x "$VENV_DIR/bin/python" ]] || ! python_is_compatible "$VENV_DIR/bin/python"; then
    replace_incompatible_environment "$bootstrap_python"
  elif ! runtime_matches_project "$VENV_DIR"; then
    install_project_runtime "$VENV_DIR"
  fi
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
