# Release verification

## What a release contains

Each tagged release is built by `.github/workflows/release.yml` with `scripts/build-release.sh` on
a GitHub-hosted macOS runner:

| File | Contents |
|---|---|
| `iOS-Developer-Toolkit-VERSION-macOS-universal.zip` | The app (arm64 + x86_64), ad-hoc signed with the hardened runtime, with `idt` in `Contents/MacOS` and dependency licenses in `Contents/Resources/Licenses` |
| `SHA256SUMS.txt` | SHA-256 of every release file |
| `iOS-Developer-Toolkit-VERSION.spdx.json` | SPDX 2.3 SBOM of the Swift package dependencies, generated from `Package.resolved` |

GitHub build-provenance and SBOM attestations are published for the ZIP. The app is **not**
notarized by Apple.

## Verify

```bash
shasum -a 256 -c SHA256SUMS.txt --ignore-missing
gh attestation verify iOS-Developer-Toolkit-VERSION-macOS-universal.zip --repo hideouts-io/iOS-Developer-Toolkit
```

After unzipping:

```bash
codesign --verify --deep --strict --verbose=2 "iOS Developer Toolkit.app"
codesign --display --verbose=2 "iOS Developer Toolkit.app"   # expect: Signature=adhoc, flags=0x10002(adhoc,runtime)
lipo -archs "iOS Developer Toolkit.app/Contents/MacOS/iOS Developer Toolkit"   # expect: x86_64 arm64
```

Then open the app with Control-click › **Open** (or **Open Anyway** in *System Settings › Privacy &
Security*). Do not disable Gatekeeper.

## Build it yourself and compare

```bash
scripts/build-release.sh 1.0.0
```

Builds are not bit-for-bit reproducible (signatures and timestamps differ), but the SBOM and the
source commit can be compared with a release.

> A checksum, ad-hoc signature, SBOM, or attestation does not make the app notarized. Each answers
> a different question about where the file came from and whether it changed.
