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

if [[ ! -f "$repository_root/pyproject.toml" || ! -f "$repository_root/requirements/release-sbom.txt" || ! -d "$repository_root/ios_developer_toolkit" ]]; then
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
staging_root="$(mktemp -d /private/tmp/iosdevtoolkit-release.XXXXXX)"
export NUITKA_CACHE_DIR="$staging_root/nuitka-cache"
build_environment="$staging_root/release-venv"
metadata_environment="$staging_root/metadata-venv"
source_wrapper="$staging_root/main.py"
generated_app_path="$staging_root/build/iOS Developer Toolkit.app"
app_path="$staging_root/iOS Developer Toolkit.app"
archive_name="iOS-Developer-Toolkit-v${release_version}-macOS-${machine_architecture}.zip"
archive_path="$release_root/$archive_name"
sbom_name="iOS-Developer-Toolkit-v${release_version}-macOS-${machine_architecture}.cdx.json"
sbom_path="$release_root/$sbom_name"
runtime_requirements="$staging_root/runtime-requirements.txt"
post_build_requirements="$staging_root/post-build-requirements.txt"
sbom_requirements="$staging_root/sbom-requirements.txt"
license_directory="$staging_root/third-party-licenses"
deployment_config="$staging_root/pysidedeploy.spec"

/bin/rm -f "$archive_path" "$sbom_path"

"$python_executable" -m venv "$build_environment"
"$build_environment/bin/python" -m pip install --disable-pip-version-check --upgrade pip
"$build_environment/bin/python" -m pip install --disable-pip-version-check "$repository_root"
"$build_environment/bin/python" -m pip freeze --local --require-virtualenv > "$runtime_requirements"
/usr/bin/sed -i '' "s|^ios-developer-toolkit @ .*|ios-developer-toolkit==$release_version|" "$runtime_requirements"
/bin/cp "$runtime_requirements" "$sbom_requirements"
echo "Nuitka==4.1.1" >> "$sbom_requirements"
"$build_environment/bin/python" -m pip install --disable-pip-version-check "$repository_root[release]"
"$build_environment/bin/python" -m pip freeze --local --require-virtualenv > "$post_build_requirements"
/usr/bin/sed -i '' "s|^ios-developer-toolkit @ .*|ios-developer-toolkit==$release_version|" "$post_build_requirements"
missing_runtime_requirements="$(while IFS= read -r requirement; do
  /usr/bin/grep -Fqx -- "$requirement" "$post_build_requirements" || echo "$requirement"
done < "$runtime_requirements")"
if [[ -n "$missing_runtime_requirements" ]]; then
  echo "Installing release tooling changed the frozen runtime dependency set:" >&2
  echo "$missing_runtime_requirements" >&2
  exit 71
fi
"$python_executable" -m venv "$metadata_environment"
"$metadata_environment/bin/python" -m pip install --disable-pip-version-check \
  --requirement "$repository_root/requirements/release-sbom.txt"
"$metadata_environment/bin/cyclonedx-py" requirements "$sbom_requirements" \
  --pyproject "$repository_root/pyproject.toml" \
  --mc-type application \
  --sv 1.6 \
  --of JSON \
  -o "$sbom_path"
"$build_environment/bin/python" scripts/collect_third_party_licenses.py "$sbom_requirements" "$license_directory"

cd "$repository_root"
"$build_environment/bin/python" -m unittest discover -s tests -v
/bin/cp packaging/main.py "$source_wrapper"
/bin/cp packaging/pysidedeploy.spec "$deployment_config"
/usr/bin/sed -i '' \
  -e "s|^project_dir =.*|project_dir = $repository_root|" \
  -e "s|^input_file =.*|input_file = $source_wrapper|" \
  -e "s|^exec_directory =.*|exec_directory = $staging_root/build|" \
  -e "s|^icon =.*|icon = $repository_root/macos/iOSDeveloperToolkit.icns|" \
  "$deployment_config"
"$build_environment/bin/pyside6-deploy" -c "$deployment_config" --force --keep-deployment-files

if [[ ! -d "$generated_app_path" ]]; then
  echo "pyside6-deploy did not create the expected app bundle: $generated_app_path" >&2
  exit 68
fi
if [[ -e "$app_path" ]]; then
  echo "Release staging path already exists: $app_path" >&2
  exit 69
fi

/bin/cp -R "$generated_app_path" "$app_path"
plist_path="$app_path/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleIdentifier io.hideouts.ios-developer-toolkit" "$plist_path"
/usr/libexec/PlistBuddy -c "Set :CFBundleDisplayName iOS Developer Toolkit" "$plist_path"
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $release_version" "$plist_path"
/usr/libexec/PlistBuddy -c "Add :CFBundleVersion string 5" "$plist_path" 2>/dev/null || /usr/libexec/PlistBuddy -c "Set :CFBundleVersion 5" "$plist_path"
/usr/libexec/PlistBuddy -c "Add :LSMinimumSystemVersion string 13.0" "$plist_path" 2>/dev/null || /usr/libexec/PlistBuddy -c "Set :LSMinimumSystemVersion 13.0" "$plist_path"

bundle_license_directory="$app_path/Contents/Resources/Licenses"
/bin/mkdir -p "$bundle_license_directory"
/bin/cp "$repository_root/LICENSE" "$bundle_license_directory/IOS_DEVELOPER_TOOLKIT_LICENSE.txt"
/bin/cp "$repository_root/THIRD_PARTY_NOTICES.md" "$bundle_license_directory/THIRD_PARTY_NOTICES.md"
/bin/cp "$repository_root/SOURCE_AVAILABILITY.md" "$bundle_license_directory/SOURCE_AVAILABILITY.md"
/usr/bin/ditto --norsrc "$license_directory" "$bundle_license_directory/ThirdPartyPackages"
/bin/cp "$sbom_path" "$app_path/Contents/Resources/BOM.cdx.json"

if [[ ! -s "$bundle_license_directory/ThirdPartyPackages/THIRD_PARTY_PACKAGES.md" || ! -s "$app_path/Contents/Resources/BOM.cdx.json" ]]; then
  echo "Release bundle is missing its generated license inventory or SBOM" >&2
  exit 70
fi
"$build_environment/bin/python" scripts/verify_release_metadata.py "$app_path" "$sbom_path" "$release_version"

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
/usr/bin/shasum -a 256 "$archive_path" "$sbom_path"
echo "$archive_path"
echo "$sbom_path"
