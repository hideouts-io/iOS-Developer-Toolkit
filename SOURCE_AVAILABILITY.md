# Source availability

Every published release of iOS Developer Toolkit is built from a Git tag in
[this public repository](https://github.com/hideouts-io/iOS-Developer-Toolkit) by the release
workflow, with a GitHub build-provenance attestation that links the archive to that workflow run
and commit. GitHub provides source archives for each tag, or:

```bash
git clone --branch vVERSION --depth 1 https://github.com/hideouts-io/iOS-Developer-Toolkit.git
```

The app is written in Swift. Its third-party dependencies are Swift packages resolved from their
public repositories at the exact revisions recorded in [`Package.resolved`](Package.resolved) for
that tag. They are listed with their licenses in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)
and in the SPDX SBOM attached to each release. No binary-only third-party code is included.

Optional external tools (MVT, UFADE, idb Companion) are installed and managed by the user and are
not part of the app or its SBOM.

This document is an availability and attribution statement, not legal advice.
