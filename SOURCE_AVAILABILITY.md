# Source availability for distributed application bundles

Every published iOS Developer Toolkit release is built from its release Git tag in [this public repository](https://github.com/hideouts-io/iOS-Developer-Toolkit). GitHub provides source archives for each release tag, and the complete project source can also be obtained with:

```bash
git clone --branch vVERSION --depth 1 https://github.com/hideouts-io/iOS-Developer-Toolkit.git
```

The prebuilt application is accompanied by an architecture-specific CycloneDX SBOM. It identifies the exact Python distribution versions used for that build, including the bundled `pymobiledevice3` component.

## Bundled third-party source

The release-critical upstream source locations and license information are recorded in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). In particular, the packaged `pymobiledevice3` release is available at its [matching upstream tag](https://github.com/doronz88/pymobiledevice3/tree/v11.15.1), including its GPL-3.0-or-later license. The project’s public tagged source, package inventory, and embedded notices are intended to make the source and license boundary inspectable before redistribution.

PySide6/Qt, Nuitka, CPython, and every other dependency remain subject to their own terms. Consult the generated `Contents/Resources/Licenses/` inventory in the application and the matching SBOM for the exact package set. This document is an availability and attribution statement, not legal advice.

Optional UFADE and MVT integrations launch user-managed external installations. Their source is not part of the application bundle or release SBOM; consult their upstream repositories and licenses for the exact external version selected by the operator.

## Firmware helper source

The firmware helpers are separate universal executables built by `scripts/build_restore_helpers_vendor.sh`. Its exact Git revisions and OpenSSL tarball digest are recorded in the bundled helper `SOURCES.txt`; component license files accompany the executables. The release builder produces an architecture-labeled `iOS-Developer-Toolkit-restore-helper-sources-vVERSION-macOS-ARCH.tar.gz` containing the pinned sources, build instructions/scripts, HTTPS transport patch and matching helper metadata. It must accompany the corresponding application and SBOM when distributed. Static helper libraries and their relinking/source obligations remain governed by their own licenses. File hashes identify bytes; they do not establish trusted build provenance.

## Cocoa accessibility plugin source

The macOS Cocoa plugin is rebuilt from matching Qt 6.9.3 source with two parent-ownership guards in its accessibility implementation. `scripts/build_qt_cocoa_plugin.py` records the published source and SDK digests, the exact patch, build recipe, native verification and plugin architectures. The architecture-labeled `iOS-Developer-Toolkit-qt-cocoa-sources-vVERSION-macOS-ARCH.tar.gz` contains the complete matching Qtbase source archive and repair/rebuild files. It must accompany the application and SBOM when distributed. The app embeds the plugin build manifest, patch and license notices; the SBOM binds the final packaged plugin and corresponding-source archive. Existing LGPL relinking/source obligations still apply.
