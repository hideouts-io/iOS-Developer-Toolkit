#!/bin/bash

set -euo pipefail

if [[ "$#" -ne 3 ]]; then
  echo "Usage: $0 VERSION OUTPUT_DIRECTORY PYTHON_EXECUTABLE" >&2
  exit 64
fi

release_version="$1"
output_directory="$2"
repository_root="$(cd "$(dirname "$0")/.." && pwd)"
python_executable="$3"
machine_architecture="$(uname -m)"
export COPYFILE_DISABLE=1

if [[ ! -f "$repository_root/pyproject.toml" || ! -d "$repository_root/ios_developer_toolkit" ]]; then
  echo "Release builder could not validate the repository root: $repository_root" >&2
  exit 65
fi

if [[ "$machine_architecture" != "arm64" && "$machine_architecture" != "x86_64" ]]; then
  echo "Unsupported macOS architecture: $machine_architecture" >&2
  exit 66
fi

configured_version="$(cd "$repository_root" && "$python_executable" -c 'from ios_developer_toolkit import APP_VERSION; print(APP_VERSION)')"
if [[ "$configured_version" != "$release_version" ]]; then
  echo "Requested version $release_version does not match application version $configured_version" >&2
  exit 67
fi

release_root="$(cd "$repository_root" && mkdir -p "$output_directory" && cd "$output_directory" && pwd)"
build_environment="$repository_root/build/release-venv-$machine_architecture"
generated_app_path="$repository_root/build/release/iOS Developer Toolkit.app"
staging_root="$(mktemp -d /private/tmp/iosdevtoolkit-release.XXXXXX)"
app_path="$staging_root/iOS Developer Toolkit.app"
archive_name="iOS-Developer-Toolkit-v${release_version}-macOS-${machine_architecture}.zip"
archive_path="$release_root/$archive_name"
generated_directory="$repository_root/packaging/deployment"
deployment_config="$repository_root/build/pysidedeploy-$machine_architecture.spec"

/bin/rm -rf "$generated_directory/main.app" "$generated_directory/main.dist" "$generated_app_path"
/bin/rm -f "$archive_path"

"$python_executable" -m venv "$build_environment"
"$build_environment/bin/python" -m pip install --disable-pip-version-check --upgrade pip
"$build_environment/bin/python" -m pip install --disable-pip-version-check "$repository_root[release]"

cd "$repository_root"
"$build_environment/bin/python" -m unittest discover -s tests -v
/bin/cp packaging/pysidedeploy.spec "$deployment_config"
"$build_environment/bin/pyside6-deploy" -c "$deployment_config" --force --keep-deployment-files

if [[ ! -d "$generated_app_path" ]]; then
  echo "pyside6-deploy did not create the expected app bundle: $generated_app_path" >&2
  exit 68
fi

/usr/bin/ditto --norsrc "$generated_app_path" "$app_path"
plist_path="$app_path/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleIdentifier io.hideouts.ios-developer-toolkit" "$plist_path"
/usr/libexec/PlistBuddy -c "Set :CFBundleDisplayName iOS Developer Toolkit" "$plist_path"
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $release_version" "$plist_path"
/usr/libexec/PlistBuddy -c "Add :CFBundleVersion string 4" "$plist_path" 2>/dev/null || /usr/libexec/PlistBuddy -c "Set :CFBundleVersion 4" "$plist_path"
/usr/libexec/PlistBuddy -c "Add :LSMinimumSystemVersion string 13.0" "$plist_path" 2>/dev/null || /usr/libexec/PlistBuddy -c "Set :LSMinimumSystemVersion 13.0" "$plist_path"

/usr/bin/xattr -cr "$app_path"
/usr/bin/codesign --force --deep --sign - --timestamp=none "$app_path"
/usr/bin/codesign --verify --deep --strict --verbose=2 "$app_path"

bundle_executable="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleExecutable' "$plist_path")"
compiled_executable="$app_path/Contents/MacOS/$bundle_executable"
compiled_architecture="$(/usr/bin/lipo -archs "$compiled_executable")"
if [[ "$compiled_architecture" != "$machine_architecture" ]]; then
  echo "Compiled executable architecture is $compiled_architecture; expected $machine_architecture" >&2
  exit 69
fi

"$compiled_executable" --toolkit-internal-pymobiledevice3 version
"$compiled_executable" --toolkit-internal-worker capability --help
QT_QPA_PLATFORM=offscreen "$compiled_executable" --toolkit-internal-smoke-test

/usr/bin/ditto -c -k --sequesterRsrc --keepParent "$app_path" "$archive_path"
/usr/bin/shasum -a 256 "$archive_path"
echo "$archive_path"
