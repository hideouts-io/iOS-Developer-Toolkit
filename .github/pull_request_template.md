## Purpose

Describe the user-visible problem and the smallest change that solves it.

## Verification

- [ ] `python -m unittest discover -s tests -v`
- [ ] `python -m compileall -q ios_developer_toolkit tests`
- [ ] `QT_QPA_PLATFORM=offscreen python -m ios_developer_toolkit --toolkit-internal-smoke-test`
- [ ] Relevant real-device or no-device behavior was exercised and is described below.

Device and host coverage:

<!-- Use product family and OS versions only. Do not include UDIDs, serial numbers, device names, account data, coordinates, or case identifiers. -->

## Safety and publication

- [ ] The change preserves explicit device selection and authorization boundaries.
- [ ] Mutating actions remain labeled, confirmed, bounded, and reversible where possible.
- [ ] Errors remain visible and actionable; unsupported states are not reported as success.
- [ ] No private device data, logs, PCAPs, backups, profiles, IPAs, DDIs, credentials, or evidence artifacts are included.
- [ ] New subprocess arguments avoid shell interpretation and are validated at the boundary.
- [ ] Documentation reflects current behavior without overstating iOS access or forensic coverage.

## Notes

List any capability, iOS-version, signing, packaging, or follow-up limitations.
