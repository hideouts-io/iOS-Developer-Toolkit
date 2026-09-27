# Architecture

iOS Developer Toolkit is a SwiftUI app on top of a Swift package. All device, process, and file
logic lives in the package, so it builds and tests with `swift test` without Xcode's UI tooling.

```
App/iOSDeveloperToolkit (SwiftUI views and view state)      Sources/idt (command-line tool)
                 │                                                     │
                 └──────────────► ToolkitFeatures ◄────────────────────┘
                                  Location Lab, IPA inspection, live-log capture and findings,
                                  actions and their safety policy, readiness, evidence
                                  collection, external tools, profiles, support bundle
                                        │
                                  DeviceKit
                                  usbmuxd client · lockdown client and services ·
                                  CoreDevice (devicectl) · simulators (simctl) · discovery
                                        │
                                  ToolkitCore
                                  CommandRunner · ToolkitError · OSLog categories ·
                                  SecureFileIO · Sanitizer · OperationJournal · ZipWriter
```

## How the app reaches a device

| Path | Used for | Needs |
|---|---|---|
| **usbmuxd** (`/var/run/usbmuxd`) | Discovery events, pairing records, connections to device services | A trusted device over USB or Wi-Fi sync |
| **Lockdown** (TLS, swift-nio-ssl) | Identity, Developer Mode status, syslog and os_trace relays, pcapd, MobileBackup2, diagnostics, installation proxy, AFC, misagent, image mounter (check, personalize, upload, mount, unmount developer images), legacy location | Trust; nothing from Xcode except the developer image files it installs |
| **CoreDevice** (`xcrun devicectl`, JSON output) | Developer services (personalized DDI), screenshots, location on iOS 17+, processes, launch, profiles, sysdiagnose, network-only devices | Xcode |
| **Simulators** (`xcrun simctl`) | Everything in the Simulators section | Xcode |
| **Instruments** (`xcrun xctrace`) | Recordings | Xcode |

Each lockdown session pins the device certificate from the pairing record and checks that the
device answering has the requested UDID. Pairing records come from usbmuxd's `ReadPairRecord`,
which macOS allows without administrator rights. The app never reads `/var/db/lockdown` and never
creates pairing records.

## Developer images

Developer-image support lives in `Sources/DeviceKit/DeveloperImage` and is the only code that
talks to the private `com.apple.mobile.mobile_image_mounter` service or to Apple's signing server:

| Type | Role |
|---|---|
| `ImageMounter` | The image-mounter protocol (lookup, list, nonce, personalization identifiers and manifest, upload, mount, unmount) and the mapping of its errors to plain language |
| `DeveloperImageLibrary` | Images on this Mac: Xcode's `/Library/Developer/DeveloperDiskImages/iOS_DDI`, user folders, legacy `DeveloperDiskImage.dmg` + `.signature`; build-identity selection by chip and board |
| `ImagePersonalization` | Builds the TSS request and reads the ticket; the network call is behind `PersonalizationTransport` so tests never reach Apple |
| `DeveloperImageManager` | Reads device facts, evaluates the state (a pure function), and mounts or unmounts — through Xcode's `devicectl` or the built-in client |

The rest of the app sees only `DeveloperImageStatus` (state, explanation, next step, details).
The private service and TSS formats follow the open-source implementations the 0.3.x app used;
they are covered by tests against a stateful fake image mounter, and still need verification on
physical devices ([PHYSICAL_DEVICE_TEST_PROTOCOL.md](PHYSICAL_DEVICE_TEST_PROTOCOL.md), Stage 3).

## Processes

`CommandRunner` in ToolkitCore is the only place that creates a `Process`. A request is an
executable URL, an argument vector, a minimal environment, an optional timeout, and a display
name for the Session Activity log. The runner never uses a shell. Every run can be cancelled,
drains output without deadlocks, and returns a typed result. Timeouts, non-zero exits, and
cancellation surface as `ToolkitError`. External tools (MVT, UFADE, idb Companion) are
validated by path and SHA-256 before they run.

## Errors and logging

`ToolkitError` carries a plain-language message, a recovery suggestion, and a technical detail
kept separate for the diagnostic log. The app logs to the unified log under the subsystem
`io.hideouts.iOSDeveloperToolkit` with the categories Application, DeviceDiscovery,
DeviceCommunication, Commands, Diagnostics, Security, Networking, Filesystem, Backup, Location,
LiveLogs, and Evidence. Identifiers, names, and paths are logged as private. **Help › Diagnostic
Log** shows this session's entries.

```bash
log stream --predicate 'subsystem == "io.hideouts.iOSDeveloperToolkit"' --level info
```

## Targets and concurrency

- Swift 6 language mode with strict concurrency checking; the project builds with zero warnings.
- Every operation takes an immutable `DeviceTarget` captured when it starts, so a change of
  selection never redirects a running operation.
- Discovery is event-driven: usbmuxd attach and detach events trigger refreshes. A slow timer
  refreshes only network-only CoreDevice devices.

## Project files

| Path | Contents |
|---|---|
| `Package.swift` | ToolkitCore, DeviceKit, ToolkitFeatures, idt, DeviceTestSupport, test targets |
| `project.yml` | XcodeGen description of the app and UI-test targets (`iOSDeveloperToolkit.xcodeproj` is generated from it and committed) |
| `App/iOSDeveloperToolkit` | SwiftUI app: `Model/` (view state, screenshot harness), `Views/`, `Components/` |
| `App/UITests` | XCUITest smoke tests (Demo Mode) |
| `Tests/` | Package tests, including an in-process fake usbmuxd and lockdownd device |
| `scripts/build-release.sh` | Universal, ad-hoc-signed release build with checksums and SBOM |
