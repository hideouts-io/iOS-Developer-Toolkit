# Source availability for distributed application bundles

Every published iOS Developer Toolkit release is built from a signed Git tag in [this public repository](https://github.com/hideouts-io/iOS-Developer-Toolkit). GitHub provides source archives for each release tag, and the complete project source can also be obtained with:

```bash
git clone --branch vVERSION --depth 1 https://github.com/hideouts-io/iOS-Developer-Toolkit.git
```

The prebuilt application is accompanied by an architecture-specific CycloneDX SBOM. It identifies the exact Python distribution versions used for that build, including the bundled `pymobiledevice3` component.

## Bundled third-party source

The release-critical upstream source locations and license information are recorded in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). In particular, the packaged `pymobiledevice3` release is available at its [matching upstream tag](https://github.com/doronz88/pymobiledevice3/tree/v10.11.0), including its GPL-3.0-or-later license. The project’s public tagged source, package inventory, and embedded notices are intended to make the source and license boundary inspectable before redistribution.

PySide6/Qt, Nuitka, CPython, and every other dependency remain subject to their own terms. Consult the generated `Contents/Resources/Licenses/` inventory in the application and the matching SBOM for the exact package set. This document is an availability and attribution statement, not legal advice.
