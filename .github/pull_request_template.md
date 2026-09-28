## Purpose

Describe the user-visible problem and the smallest change that solves it.

## Verification

- [ ] `swift build -Xswiftc -warnings-as-errors`
- [ ] `swift test`
- [ ] `xcodebuild -project iOSDeveloperToolkit.xcodeproj -scheme iOSDeveloperToolkit -destination 'platform=macOS' build`
- [ ] UI changes: UI tests and a minimum-size (`-window-size 900x560`) render without `SQUEEZED`/`OVERFLOW`
- [ ] Relevant device, simulator, or no-device behavior was exercised and is described below.

Device and host coverage:

<!-- Device family and OS versions only. No UDIDs, serial numbers, device names, account data, coordinates, or case identifiers. -->

## Safety and privacy

- [ ] Operations still take an explicit target; physical devices and simulators stay separate.
- [ ] Device-changing actions are classified correctly and confirmed.
- [ ] No `Process` outside `CommandRunner`; arguments are a vector; no shell; no `sudo`.
- [ ] Errors are actionable; unsupported states are not reported as success.
- [ ] No private device data, logs, captures, backups, profiles, IPAs, credentials, or evidence are included.
- [ ] Documentation matches the new behavior without overstating access or coverage.

## Notes

List limitations, iOS-version differences, or follow-up work.
