# Security Policy

## Supported versions

| Version | Supported |
|---|---:|
| Latest release (1.x, Swift) | Yes |
| `main` | Yes |
| 0.3.x and earlier (Python) | No |

## Report a vulnerability privately

Use [GitHub private vulnerability reporting](https://github.com/hideouts-io/iOS-Developer-Toolkit/security/advisories/new).
Do not disclose a suspected vulnerability in a public issue, discussion, pull request, log,
screenshot, or evidence archive.

Include:

- the affected version or commit;
- the affected page, action, `idt` command, or release file;
- the impact and the preconditions (trust, Developer Mode, Xcode, physical access, and so on);
- minimal reproduction steps with synthetic or sanitized data;
- macOS version, Mac architecture, and iOS or iPadOS version.

Never include credentials, pairing records, private keys, UDIDs, serial numbers, account data,
coordinates, packet payloads, backups, profiles, IPAs, crash report contents, or evidence. If a
reproduction cannot be sanitized, describe it first and wait for a private handling plan.

The maintainer will acknowledge complete reports when practical, validate them, coordinate a fix
and release, and credit the reporter on request. No response deadline is guaranteed.

## Design boundaries

In scope for reports: anything that lets the app act on a device other than the selected one,
run a command through a shell or with attacker-controlled arguments, write outside the chosen
folder, overwrite existing files, leak identifiers or passwords into logs or exports, accept a
lockdown peer that is not the paired device, or be driven by crafted device responses, IPA
archives, backups, GPX files, or workspace profiles.

The app:

- runs without administrator rights and never uses `sudo`;
- never reads `/var/db/lockdown`, creates pairing records, or restarts system services;
- pins the device certificate from the pairing record and checks the device's UDID on every
  lockdown session;
- starts external processes only through one runner, from fixed paths, with an argument vector
  and a minimal environment, never through a shell;
- keeps passwords (backup encryption) in memory only and never passes them as process arguments;
- connects to the internet from its own code only to personalize a developer image (iOS 17 and
  later), after the user confirms: it sends the device's chip, board, and ECID with a one-time
  nonce to Apple's signing server over HTTPS, as Xcode does, and never downloads images from
  third parties;
- writes captures, backups, and reports with owner-only permissions and never overwrites files.

It does not jailbreak iOS, bypass a passcode or activation, defeat code signing, disable the
sandbox, decrypt protected traffic, or provide unrestricted file-system access. A mounted
developer image, an available developer service, or an unusual log line is not by itself evidence
of compromise.

## Releases

Release builds are ad-hoc signed with the hardened runtime and are **not notarized**. Verify
`SHA256SUMS.txt` and the GitHub attestation before opening a download
(see [docs/release-verification.md](docs/release-verification.md)). Never run an archive whose
checksum or attestation does not match.
