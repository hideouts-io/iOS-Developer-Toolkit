# Security Policy

## Supported versions

Security fixes are made against the latest release and the current `main` branch. Older releases are not supported once a newer release is available.

| Version | Supported |
|---|---:|
| Latest release | Yes |
| `main` | Yes |
| Older releases | No |

## Report a vulnerability privately

Use [GitHub private vulnerability reporting](https://github.com/hideouts-io/iOS-Developer-Toolkit/security/advisories/new). Do not disclose a suspected vulnerability in a public issue, Discussion, pull request, log, screenshot, or evidence archive.

Include:

- the affected toolkit version or commit;
- the affected workspace, command, or release artifact;
- the security impact and required preconditions;
- minimal reproduction steps using synthetic or sanitized data;
- the relevant macOS, architecture, iOS or iPadOS, and device family;
- whether the behavior requires trust, Developer Mode, a DDI, a tunnel, elevated privileges, or physical device access;
- a proposed mitigation, if known.

Never include credentials, pairing records, private keys, real UDIDs, serial numbers, account data, coordinates, packet payloads, backups, profiles, IPAs, crash contents, or forensic evidence. If a minimal reproducer cannot be sanitized, describe it first and wait for a private handling plan.

The maintainer will acknowledge a complete report when practical, validate scope and impact, coordinate a fix and release, and credit the reporter when requested. No response or remediation deadline is guaranteed.

## Security boundaries

The toolkit is a local orchestration interface. It does not jailbreak iOS, bypass a passcode or activation, evade code signing, disable the sandbox, remove supervision, decrypt protected traffic, or provide unrestricted filesystem access. A mounted DDI, successful developer service, entitlement, profile, or surprising log entry is not by itself evidence of compromise.

Published application bundles are currently ad-hoc signed and are not Apple-notarized. Verify `SHA256SUMS.txt` from the release, use the archive matching the Mac architecture, and review the stated signing status before opening it. Never trust an archive whose checksum does not match.

Device output can contain highly sensitive information. Store logs, PCAPs, backups, acquisitions, coordinates, screenshots, crash reports, manifests, and evidence cases in access-controlled local storage and apply the user's retention policy.
