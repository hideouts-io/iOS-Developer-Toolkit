# Contributing

Contributions that make authorized iOS development, diagnostics, testing, backup, and evidence
workflows safer and easier to understand are welcome, including from first-time contributors.

## Scope

iOS Developer Toolkit is a native macOS app that uses Apple's device services (usbmuxd and
lockdown), Xcode's CoreDevice, `simctl`, and Instruments. It does not aim to jailbreak devices,
bypass a passcode or activation, defeat code signing, remove supervision, decrypt protected
traffic, or expose unrestricted file-system access.

Use only devices and data you own or are authorized to test. Never submit UDIDs, serial numbers,
phone numbers, Apple Account data, coordinates, pairing records, backup contents, packet payloads,
profiles, certificates, crash report contents, or evidence.

Use a bug report for reproducible defects, a feature request for a new workflow, Discussions for
questions, and private vulnerability reporting for security issues.

## Setup

Requirements: macOS 14 or later and Xcode 16 or later (Swift 6).

```bash
git clone https://github.com/hideouts-io/iOS-Developer-Toolkit.git
cd iOS-Developer-Toolkit
swift build
swift test
open iOSDeveloperToolkit.xcodeproj      # run the iOSDeveloperToolkit scheme
```

The Xcode project is generated from `project.yml`. After adding, removing, or moving files in
`App/`, or changing build settings, run `xcodegen generate`
([XcodeGen](https://github.com/yonaskolb/XcodeGen)) and commit the updated project. Code in
`Sources/` is picked up by SwiftPM automatically.

Turn on **Device › Demo Mode** to work on the UI without a device.

## Design rules

- **Swift 6 strict concurrency, zero warnings.** CI builds with warnings as errors.
- **One process runner.** Never create a `Process` outside `CommandRunner` in ToolkitCore. Pass
  arguments as a vector; never use a shell, `sh -c`, or string interpolation into commands.
- **No privilege escalation.** No `sudo`, no reading `/var/db/lockdown`, no restarting system
  services, no disabling macOS protections.
- **Apple mechanisms first.** Prefer native protocol code or Apple's own tools. Do not add hidden
  calls to third-party command-line tools; optional external tools belong on the External Tools
  page with path and hash validation.
- **Explicit targets.** Operations take a `DeviceTarget` when they start and never read the
  current selection later. Keep physical devices and simulators separate.
- **Accurate risk.** Classify every action (read-only, saves files, changes the device, high
  impact) and require the matching confirmation.
- **Actionable errors.** Throw `ToolkitError` with a plain-language message, a recovery step,
  and technical detail. Log through `ToolkitLog` with identifiers marked private.
- **Untrusted input.** Validate everything from devices, archives, and files at the boundary.
- **Evidence integrity.** Keep raw captures separate from filtered output and notes; record
  failures as coverage gaps, never as success.

## Checks before a pull request

```bash
swift build -Xswiftc -warnings-as-errors
swift test
xcodebuild -project iOSDeveloperToolkit.xcodeproj -scheme iOSDeveloperToolkit \
  -destination 'platform=macOS' SWIFT_TREAT_WARNINGS_AS_ERRORS=YES build
```

Also, when relevant:

- **UI changes:** run the UI tests (`xcodebuild … test`; macOS asks once to allow UI
  automation) and render every page at the minimum size:

  ```bash
  "…/iOS Developer Toolkit.app/Contents/MacOS/iOS Developer Toolkit" \
    -demo-mode YES -window-size 900x560 -capture-screenshots /tmp/shots
  ```

  `/tmp/shots/window-geometry.txt` must contain no `SQUEEZED` or `OVERFLOW` lines.
- **Simulator code:** `IDT_SIMULATOR_TESTS=1 swift test --filter RealSimulator`.
- **Device protocol code:** add a test against the fake device in `Tests/DeviceTestSupport`, and
  if you can, run the relevant part of [docs/PHYSICAL_DEVICE_TEST_PROTOCOL.md](docs/PHYSICAL_DEVICE_TEST_PROTOCOL.md).
  State the device family, iOS version, and connection you tested — no identifiers.
- **Release packaging:** `scripts/build-release.sh 0.0.0-test` must succeed.

## Pull requests

Open a focused pull request against `main`. Explain the problem, the change, how you verified it,
device coverage, privacy impact, and any remaining limitation. CI must pass. Screenshots and logs
must use Demo Mode, a simulator, or thoroughly sanitized data.

By contributing, you agree that your contribution is licensed under the repository's MIT License
and that participation follows the [Code of Conduct](CODE_OF_CONDUCT.md).
