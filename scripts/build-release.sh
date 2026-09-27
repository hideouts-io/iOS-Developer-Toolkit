#!/bin/bash
# Builds a release of iOS Developer Toolkit, locally or in CI. Needs only Xcode.
#
#   scripts/build-release.sh [VERSION] [OUTPUT_DIR]
#
# VERSION defaults to ToolkitVersion.current and must match it and MARKETING_VERSION in
# project.yml. OUTPUT_DIR defaults to build-output/release/VERSION (replaced on each run); any
# other OUTPUT_DIR must be empty or absent.
#
# Produces, in OUTPUT_DIR:
#   iOS-Developer-Toolkit-VERSION-macOS-universal.zip  the app (arm64 + x86_64), ad-hoc signed
#                                                      with the hardened runtime; idt is at
#                                                      Contents/MacOS/idt and dependency licenses,
#                                                      notices, and the SBOM are in
#                                                      Contents/Resources/Licenses
#   iOS-Developer-Toolkit-VERSION.spdx.json            SPDX 2.3 SBOM from Package.resolved
#   SHA256SUMS.txt                                     checksums of both files
set -euo pipefail

fail() { echo "build-release: $*" >&2; exit 1; }
step() { echo "==> $*"; }

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

current="$(sed -n 's/.*static let current = "\(.*\)".*/\1/p' Sources/ToolkitCore/ToolkitVersion.swift)"
marketing="$(sed -n 's/^ *MARKETING_VERSION: "\(.*\)"$/\1/p' project.yml)"
VERSION="${1:-$current}"
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail "version must look like 1.2.3 (got '$VERSION')"
[[ "$VERSION" == "$current" ]] || fail "version $VERSION does not match ToolkitVersion.current ($current)"
[[ "$VERSION" == "$marketing" ]] || fail "version $VERSION does not match MARKETING_VERSION in project.yml ($marketing)"

COMMIT="$(git rev-parse HEAD 2>/dev/null || echo unknown)"
if [[ "$COMMIT" != unknown && -n "$(git status --porcelain --untracked-files=no 2>/dev/null)" ]]; then
    echo "build-release: warning: the working tree has uncommitted changes; the SBOM records $COMMIT-dirty" >&2
    COMMIT="$COMMIT-dirty"
fi

DEFAULT_OUT="$ROOT/build-output/release/$VERSION"
OUT="${2:-$DEFAULT_OUT}"
if [[ "$OUT" == "$DEFAULT_OUT" ]]; then
    rm -rf "$OUT"
elif [[ -e "$OUT" && -n "$(ls -A "$OUT")" ]]; then
    fail "$OUT is not empty"
fi
mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd)"

CACHE="$ROOT/build-output/release-cache"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/idt-release.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT

NAME="iOS-Developer-Toolkit-$VERSION"
ZIP="$OUT/$NAME-macOS-universal.zip"
SBOM="$OUT/$NAME.spdx.json"
ENTITLEMENTS="App/iOSDeveloperToolkit/iOSDeveloperToolkit.entitlements"

step "Xcode: $(xcodebuild -version | tr '\n' ' ')"

step "Archiving the app (arm64 + x86_64, Release)"
xcodebuild -project iOSDeveloperToolkit.xcodeproj -scheme iOSDeveloperToolkit \
    -configuration Release -destination 'generic/platform=macOS' \
    -derivedDataPath "$CACHE/DerivedData" -archivePath "$WORK/app.xcarchive" \
    ARCHS="arm64 x86_64" ONLY_ACTIVE_ARCH=NO \
    -quiet archive
APP="$WORK/app.xcarchive/Products/Applications/iOS Developer Toolkit.app"
[[ -d "$APP" ]] || fail "the archive does not contain the app"

step "Building idt (arm64 + x86_64, release)"
swift build -c release --product idt --arch arm64 --arch x86_64 --scratch-path "$CACHE/spm" --quiet
BIN="$(swift build -c release --product idt --arch arm64 --arch x86_64 --scratch-path "$CACHE/spm" --show-bin-path)"
cp "$BIN/idt" "$APP/Contents/MacOS/idt"

step "Adding licenses, notices, and the SBOM"
LICENSES="$APP/Contents/Resources/Licenses"
mkdir -p "$LICENSES"
cp LICENSE "$LICENSES/LICENSE-iOS-Developer-Toolkit.txt"
cp THIRD_PARTY_NOTICES.md SOURCE_AVAILABILITY.md "$LICENSES/"
for identity in $(sed -n 's/.*"identity" : "\(.*\)".*/\1/p' Package.resolved); do
    checkout="$CACHE/spm/checkouts/$identity"
    [[ -d "$checkout" ]] || fail "missing checkout for $identity"
    mkdir -p "$LICENSES/$identity"
    found=0
    for file in "$checkout"/LICENSE* "$checkout"/NOTICE*; do
        [[ -f "$file" ]] || continue
        cp "$file" "$LICENSES/$identity/"
        found=1
    done
    [[ "$found" == 1 ]] || fail "no license file found for $identity"
done
xcrun swift scripts/generate-sbom.swift Package.resolved "$CACHE/spm/checkouts" "$VERSION" "$COMMIT" "$SBOM"
cp "$SBOM" "$LICENSES/sbom.spdx.json"

step "Signing (ad hoc, hardened runtime)"
codesign --force --sign - --options runtime --timestamp=none \
    --identifier io.hideouts.iOSDeveloperToolkit.idt "$APP/Contents/MacOS/idt"
codesign --force --sign - --options runtime --timestamp=none \
    --entitlements "$ENTITLEMENTS" "$APP"

verify_app() {
    local app="$1"
    codesign --verify --deep --strict "$app" || fail "codesign verification failed for $app"
    local details
    for binary in "$app/Contents/MacOS/iOS Developer Toolkit" "$app/Contents/MacOS/idt"; do
        details="$(codesign --display --verbose=2 "$binary" 2>&1)"
        grep -q 'Signature=adhoc' <<<"$details" || fail "$(basename "$binary") is not ad-hoc signed"
        grep -Eq 'flags=0x[0-9a-f]+\(.*runtime' <<<"$details" || fail "$(basename "$binary") lacks the hardened runtime"
        local archs
        archs="$(lipo -archs "$binary")"
        [[ " $archs " == *" arm64 "* && " $archs " == *" x86_64 "* ]] || fail "$(basename "$binary") is not universal ($archs)"
        if otool -L "$binary" | grep -qi python; then fail "$(basename "$binary") links Python"; fi
    done
    local plist_version
    plist_version="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$app/Contents/Info.plist")"
    [[ "$plist_version" == "$VERSION" ]] || fail "Info.plist version is $plist_version, expected $VERSION"
    local idt_version
    idt_version="$("$app/Contents/MacOS/idt" --version)"
    [[ "$idt_version" == "$VERSION" ]] || fail "idt reports $idt_version, expected $VERSION"
    if [[ "$(uname -m)" == arm64 ]] && arch -x86_64 /usr/bin/true 2>/dev/null; then
        [[ "$(arch -x86_64 "$app/Contents/MacOS/idt" --version)" == "$VERSION" ]] || fail "the x86_64 slice of idt does not run"
    fi
    [[ -f "$app/Contents/Resources/Licenses/swift-nio-ssl/NOTICE.txt" ]] || fail "license notices are missing"
}

step "Verifying the signed app"
verify_app "$APP"

step "Packaging"
ditto -c -k --sequesterRsrc --keepParent "$APP" "$ZIP"
(cd "$OUT" && shasum -a 256 "$(basename "$ZIP")" "$(basename "$SBOM")" > SHA256SUMS.txt)

step "Verifying the ZIP"
mkdir "$WORK/unzipped"
ditto -x -k "$ZIP" "$WORK/unzipped"
verify_app "$WORK/unzipped/iOS Developer Toolkit.app"
(cd "$OUT" && shasum -a 256 -c SHA256SUMS.txt)

step "Done: $OUT"
ls -l "$OUT"
