# Release verification

## What a published release contains

Apple Silicon and Intel applications are built separately on native GitHub-hosted runners. Each ZIP is accompanied by a CycloneDX SBOM, a release-wide SHA-256 inventory, and GitHub build-provenance and SBOM attestations. The application is ad-hoc signed and is not Apple-notarized.

## Verification order

1. Download the archive matching the Mac architecture from the [latest release](https://github.com/hideouts-io/iOS-Developer-Toolkit/releases/latest).
2. Verify the archive against `SHA256SUMS.txt` before extracting it.
3. Verify GitHub build provenance for that exact archive.
4. Inspect the architecture label and embedded SBOM.
5. After extraction, inspect the ad-hoc signature and apply the documented Gatekeeper procedure only if the provenance is acceptable.

The canonical commands and current signing caveats live in the [README release section](https://github.com/hideouts-io/iOS-Developer-Toolkit#release-model) and [security policy](https://github.com/hideouts-io/iOS-Developer-Toolkit/security/policy). Source and bundled-component boundaries are recorded in [SOURCE_AVAILABILITY.md](https://github.com/hideouts-io/iOS-Developer-Toolkit/blob/main/SOURCE_AVAILABILITY.md) and [THIRD_PARTY_NOTICES.md](https://github.com/hideouts-io/iOS-Developer-Toolkit/blob/main/THIRD_PARTY_NOTICES.md).

!!! danger "Do not infer notarization"

    A valid checksum, ad-hoc signature, SBOM, or GitHub attestation does not make the bundle Apple-notarized. Each mechanism answers a different provenance or integrity question.
