# Python → Swift migration record

This document tracks the rewrite of iOS Developer Toolkit from the PySide6 / `pymobiledevice3`
application (v0.3.4) to a native Swift/SwiftUI macOS application. It is the working checklist
for the migration and is updated as each feature is migrated, tested, and verified.

Status legend: ✅ migrated and verified end to end (real simulator, real Xcode tools, or the app
itself) · 🟡 migrated and tested against the protocol-accurate fake device or recorded tool output;
needs physical-device verification · 🔁 replaced by a different Apple-supported mechanism · ❌ not
migrated (see §6) or removed

## 1. Audit of the Python application (v0.3.4)

### 1.1 Repository inventory

| Area | Files | Notes |
|---|---|---|
| GUI | `ios_developer_toolkit/app.py` (7,795 lines), `gui_pages.py`, `live_logs.py` (pop-out windows), `action_palette.py`, `operation_history.py` | PySide6 widgets, 13 workspaces, QProcess controllers |
| Entry points | `__main__.py`, `entrypoint.py`, `packaging/main.py`, `macos/iOSDeveloperToolkit` launcher | Frozen-runtime dispatch for internal workers and the embedded `pymobiledevice3` CLI |
| CLI tools | `collector.py` (`ios-developer-collect`), `local_ddi.py` (`ios-local-ddi`), `ipa_inspector.py` (`ios-ipa-inspect`) | argparse-based |
| Device access | Every device operation shells out to the `pymobiledevice3` CLI (pinned 11.15.1) | No in-process protocol code |
| Process control | `qt_process.py`, `interactive_process.py`, `backup_process.py`, `collection_process.py`, `capability_matrix_worker.py` | Five separate QProcess lifecycle controllers |
| External providers | `external_tools.py` (go-ios, idb, ipsw), `mvt_connector.py`, `ufade_connector.py` | User-installed executables validated by path + SHA-256 |
| Pure logic | `command_catalog.py`, `catalog.py`, `action_safety.py`, `location_lab.py`, `installed_apps.py`, `case_workflow.py`, `device_compatibility.py`, `workspace_profile.py`, `support_bundle.py`, `connection_diagnostics.py`, `command_drift.py`, `demo_mode.py`, `validation.py`, `file_integrity.py`, `models.py`, `runtime.py`, `xcode_handoff.py` | Ported feature-by-feature |
| Tests | `tests/` — 17 unittest modules (133 tests) + a 138-button GUI smoke test inside `entrypoint.py` | Used as the behavioural reference for the Swift tests |
| Packaging | `scripts/build_macos_release.sh` (Nuitka), `packaging/pysidedeploy.spec`, `macos/Info.plist`, `scripts/verify_*.py`, `scripts/collect_third_party_licenses.py` | Nuitka-frozen Python bundle, ad-hoc signed |
| CI | `.github/workflows/ci.yml`, `frozen-macos-smoke.yml`, `release-macos.yml`, `docs.yml`, `codeql.yml`, `dependency-review.yml`, `dependabot.yml` | Python-based |
| Docs | `README.md` (1,224 lines), `docs/*.md`, `mkdocs.yml`, 22 screenshots in `docs/screenshots/` | Heavily `pymobiledevice3`-specific |
| Assets | `assets/iosdevtoolkit.png` (logo), `assets/location-world-map.png` (Natural Earth, public domain), `macos/iOSDeveloperToolkit.icns` | Reused |
| Config | `pyproject.toml`, `requirements/docs.txt`, `requirements/release-sbom.txt`, `.gitignore` | |

Secrets scan: no private keys, tokens, or credentials are committed.

### 1.2 Privilege model

The Python application never used `sudo`. Discovery polled `pymobiledevice3 usbmux list` every
3 seconds (a Python process launch per poll). The Swift version keeps the no-privilege model
and replaces polling with usbmuxd's `Listen` event stream.

### 1.3 go-ios and blacktop/ipsw

Both were **optional, user-installed adapters** in the *Ecosystem Tools* workspace
(`external_tools.py`). Each adapter validated an executable and ran exactly one read-only probe
(`ios list --details`, `ipsw idev list`). No other feature depended on them. Both adapters,
their tests, documentation, GUI tab, smoke-test steps, and README/third-party notice entries
are removed. Their only capability — listing connected devices — is covered natively by the
Swift device discovery (usbmuxd + CoreDevice + simctl), so nothing is lost.

## 2. Feature inventory and migration map

`pmd3` = `pymobiledevice3`. "Native lockdown" = the Swift usbmuxd/lockdown client in
`DeviceKit` (no external tools). "CoreDevice" = Apple's `xcrun devicectl` JSON interface.

| # | Feature (Python) | Python implementation | Swift implementation | Apple API? | go-ios/ipsw? | Status |
|---|---|---|---|---|---|---|
| 1 | Device discovery | `pmd3 usbmux list` polled every 3 s | usbmuxd `Listen` event stream (event-driven) + CoreDevice `list devices` + `simctl list` | usbmuxd socket, devicectl, simctl | No | ✅ simulators · 🟡 physical |
| 2 | Device identity (name, model, iOS, build, UDID, connection) | `usbmux list` / `lockdown info` | Native lockdown `GetValue` + CoreDevice details, with plain-language explanations | Yes | No | 🟡 |
| 3 | Developer Mode status + on-device guide | `pmd3 amfi developer-mode-status` | Native lockdown (`com.apple.security.mac.amfi`) and CoreDevice `developerModeStatus`; guide sheet | Yes | No | 🟡 |
| 4 | Developer image mount — personalized (iOS 17+) and DeveloperDiskImage (iOS ≤ 16) | `pmd3 mounter auto-mount` (TSS; images from a third-party mirror) | Native `mobile_image_mounter` client + Apple TSS personalization, using the image Xcode installs or a user folder; or CoreDevice `ddiServices --auto-mount-ddis`. State model, no remount, error mapping (§8) | Yes (devicectl) + private lockdown service | No | 🟡 |
| 5 | Local Xcode DDI Cryptex install | `hdiutil` + `pmd3 cryptex auto-install` | 🔁 `devicectl manage ddis update` + `list preferredDDI` (host DDI store managed by Apple) | devicectl | No | 🔁 🟡 (Cryptex route replaced by devicectl and the native personalized mount of the same Xcode image) |
| 6 | Mounted image list / lookup / unmount | `pmd3 mounter list/lookup/umount` | Native `mobile_image_mounter` (`CopyDevices`, `LookupImage`, `UnmountImage` for `/System/Developer` and `/Developer`) | Lockdown service | No | 🟡 |
| 7 | CoreDevice details, RVI list, open project (`xed`), open .xcresult/.trace | `xcrun`, `rvictl`, `xed`, `open` | Same Apple tools through the central `CommandRunner` | Yes | No | 🟡 |
| 8 | Capability Matrix | Worker running `pmd3` probes | Native probes (usbmuxd, pair record, lockdown session, AMFI, image mounter) + CoreDevice probes (details, lock state, DDI services) + Xcode tools | Yes | No | ✅ simulators · 🟡 physical |
| 9 | Real-device compatibility history + sanitized JSON/Markdown export | `device_compatibility.py` | Ported (`CompatibilityStore`) | Foundation, CryptoKit | No | ✅ |
| 10 | Location Lab (coordinate, nudge, saved places, offline map, map-link parsing, route generator, GPX inspection/replay, evidence log, clear) | `pmd3 developer dvt simulate-location` | Physical: CoreDevice `simulate location coordinate/route/clear`; Simulator: `simctl location`; GPX replay driven by the app; offline MapKit-free world map | devicectl, simctl | No | ✅ simulators · 🟡 physical |
| 11 | Live Logs — Unified | `pmd3 syslog live --format json` (os_trace_relay) | Native `com.apple.os_trace_relay` client; Simulator: `simctl spawn log stream --style ndjson` | Lockdown service / simctl | No | ✅ simulators · 🟡 physical |
| 12 | Live Logs — Classic syslog | `pmd3 syslog live-old` | Native `com.apple.syslog_relay` client | Lockdown service | No | 🟡 |
| 13 | Live Logs — DVT OSLog | `pmd3 developer dvt oslog` | 🔁 Covered by #11 (os_trace_relay needs no DDI); DVT/DTX is not an Apple-public interface | — | No | 🔁 🟡 |
| 14 | Live log spool, pause, filter (literal/regex/case), findings, review, raw/filtered save, evidence bundle, metadata sidecar | `live_logs.py` | Ported (`LogCapture`, `FindingsStore`, `InvestigationReport`) | Foundation | No | ✅ |
| 15 | Command Center — 49 `pmd3` presets + Advanced Mode + risk classes + typed confirmation | `command_catalog.py`, `action_safety.py` | 🔁 Guided **Actions** catalog backed by native services / devicectl / simctl / xctrace, same risk classes and device-bound `RUN XXXXXX` / `IRREVERSIBLE XXXXXX` phrases; Advanced Mode for `devicectl` with safety classification | Yes | No | 🔁 ✅ simulators · 🟡 physical |
| 16 | Guided Command Drift | `pmd3 <route> --help` probes | 🔁 **Toolchain Check**: verifies every devicectl/simctl/xctrace route the app uses is present in the installed Xcode | Yes | No | 🔁 ✅ |
| 17 | Man Pages (59 `pmd3` routes) | `pmd3 --help` | 🔁 Help browser for the Apple tools actually used (`devicectl help …`, `simctl help …`, `xctrace help …`) | Yes | No | 🔁 ✅ |
| 18 | Installed Apps (search, sort, sizes, copy bundle ID, uninstall) | `pmd3 apps list/uninstall` | Native `installation_proxy` (sizes) with CoreDevice `info apps` fallback; uninstall via native `installation_proxy` over USB, CoreDevice for network-only devices, `simctl` for simulators | Yes | No | ✅ simulators (list) · 🟡 physical |
| 19 | MobileBackup2 (encryption status, require encryption + new password, full/incremental, progress, cancel) | `pmd3` backup2 worker | Native `com.apple.mobilebackup2` DeviceLink client + `notification_proxy` sync lock; password never in argv | Lockdown service | No | 🟡 |
| 20 | UFADE external launch | `ufade_connector.py` | Kept as optional external provider through `CommandRunner` | — | No | 🟡 (stand-in executables) |
| 21 | MVT analysis handoff | `mvt_connector.py` | Kept as optional external provider through `CommandRunner` | — | No | 🟡 (stand-in executables) |
| 22 | Sideload IPA (safe archive validation, Info.plist, provisioning via `security cms`, `codesign --verify`) | `ipa_inspector.py` | Native ZIP reader + validated extraction, `CMSDecoder` (Security.framework) for provisioning, `SecStaticCode` for signature; install via CoreDevice when Xcode is available, otherwise native AFC upload + `installation_proxy`; simulators via `simctl install` | Security.framework | No | ✅ inspection · 🟡 install |
| 23 | Evidence Capture (guided case intake, 15 snapshots, syslog/OSLog/PCAP streams, screenshot, crash pull, manifest, SHA256SUMS) | `collector.py`, `case_workflow.py` | Ported collection engine over native services / CoreDevice | Yes | No | 🟡 |
| 24 | Network PCAP | `pmd3 pcap` | Native `com.apple.pcapd` client writing libpcap files | Lockdown service | No | 🟡 |
| 25 | Screenshot | `pmd3 developer dvt screenshot` | CoreDevice `capture screenshot`; Simulator `simctl io screenshot` | Yes | No | ✅ simulators · 🟡 physical |
| 26 | Crash report list / pull | `pmd3 crash ls/pull` | Native AFC over `com.apple.crashreportcopymobile` (after `crashreportmover`), no Xcode needed | Yes | No | 🟡 |
| 27 | Processes | `pmd3 processes ps`, DVT proclist, CoreDevice list-processes | Native `os_trace_relay` `PidList` over USB (§9 G1); CoreDevice `info processes` for network-only devices | Private lockdown service; devicectl | No | ✅ (read on one iPhone, §5.4) |
| 28 | Launch app / open URL | DVT launch, Web Inspector launch | CoreDevice `process launch` / `process openURL`; Simulator `simctl launch` / `openurl` | Yes | No | ✅ simulators · 🟡 physical |
| 29 | Configuration / provisioning profiles | `pmd3 profile list`, `provision list` | CoreDevice `profile list`; native `misagent` | Yes | No | 🟡 |
| 30 | Diagnostics, battery, IORegistry, MobileGestalt | `pmd3 diagnostics …` | Native `diagnostics_relay` | Lockdown service | No | 🟡 |
| 31 | SpringBoard orientation / icon metrics | `pmd3 springboard …` | Native `springboardservices`; CoreDevice `orientation get` | Yes | No | 🟡 |
| 32 | Activation state, personalization identifiers | `pmd3 activation state`, `mounter query-personalization-identifiers` | Native lockdown / `mobile_image_mounter` | Lockdown | No | 🟡 |
| 33 | DVT telemetry (sysmon, energy, graphics, netstat, notifications, KDebug/CoreProfile) | `pmd3 developer dvt …` | 🔁 Instruments recordings via `xcrun xctrace record --device` (Activity Monitor, Network, Power Profiler, System Trace, Time Profiler…) | xctrace | No | 🔁 🟡 |
| 34 | RSD / RemoteXPC Bonjour discovery | `pmd3 bonjour rsd`, `remote browse` | Network.framework `NWBrowser` for `_remotepairing._tcp` / `_apple-mobdev2._tcp` | Network.framework | No | 🟡 |
| 35 | Safari/WebView tab list | `pmd3 webinspector opened-tabs` | Native `com.apple.webinspector` client (Action “Safari and web view tabs”, Readiness row) — §9 G3 | Private lockdown service | No | 🟡 |
| 36 | Bluetooth HCI capture | `pmd3 btlogger` | Native `com.apple.bluetooth.BTPacketLogger` client writing `.pklg` (Action “Bluetooth capture”) — §9 G4 | Private lockdown service | No | 🟡 |
| 37 | DVT filesystem listing (`dvt ls /`), AFC media listing | `pmd3 developer dvt ls`, `afc ls` | AFC via native `com.apple.afc`; DVT listing ❌ (see §6) | Lockdown | No | 🟡 AFC · ❌ DVT |
| 38 | Session Activity journal + manifest export | `operation_history.py` | Ported (`OperationJournal` actor) | Foundation | No | ✅ |
| 39 | Workspace profiles import/export | `workspace_profile.py` | Ported (Codable + validation); imports 0.3.x files (§9 G6) | Foundation | No | ✅ |
| 40 | Sanitized support bundle | `support_bundle.py` | Ported; native ZIP writer; includes redacted OSLog export | OSLog, Foundation | No | ✅ |
| 41 | Action Palette (⌘K), keyboard shortcuts | `action_palette.py` | SwiftUI command palette + `Commands` | SwiftUI | No | ✅ (UI test runs in CI) |
| 42 | Demo Mode | `demo_mode.py` | Ported; also drives deterministic UI tests | — | No | ✅ |
| 43 | Connection diagnostics / Reconnect & Retry | `connection_diagnostics.py` | Ported to usbmuxd states; guided reconnect sheet | — | No | ✅ |
| 44 | Scope & Safety page, Home page | GUI text | Redesigned in SwiftUI | — | No | ✅ |
| 45 | Ecosystem Tools: go-ios adapter | `external_tools.py` | ❌ **Removed by request** — capability covered by #1 | — | **go-ios** | ❌ removed |
| 46 | Ecosystem Tools: blacktop ipsw adapter | `external_tools.py` | ❌ **Removed by request** — capability covered by #1 | — | **ipsw** | ❌ removed |
| 47 | Ecosystem Tools: idb Companion adapter | `external_tools.py` | Kept as an optional external provider | — | No | 🟡 (stand-in executable) |
| 48 | CLI: evidence collector, IPA inspector, local DDI | argparse scripts | `idt` Swift command-line tool (`collect`, `inspect-ipa`, `devices`, `ddi`) | — | No | ✅ |
| 49 | Simulators | Not supported | **New**: simulator discovery, boot/shutdown, install, launch, screenshot, location, logs, open URL — clearly separated from physical devices | simctl | No | ✅ |

## 3. Architecture decisions

1. **SwiftUI app + Swift Package libraries.** Logic lives in a Swift package (`Package.swift`)
   so it builds and tests with `swift test` and in Xcode. The app target
   (`iOSDeveloperToolkit.xcodeproj`, generated from `project.yml` with XcodeGen and committed)
   contains only SwiftUI views and view state.
2. **Modules.**
   - `ToolkitCore` — logging (`OSLog` categories), `ToolkitError` (user message + technical
     detail + recovery suggestion), the single `CommandRunner` (the only place that creates a
     `Process`), secure file helpers (owner-only, no-overwrite, atomic), hashing, sanitizer,
     operation journal.
   - `DeviceKit` — device models and plain-language explanations, usbmuxd client, lockdown
     client, lockdown services, CoreDevice (`devicectl`) client, simulator (`simctl`) client,
     discovery coordinator, capability probes.
   - `ToolkitFeatures` — Location Lab, IPA inspection, live-log capture/findings, evidence
     collection, workspace profiles, support bundle, compatibility history, action safety.
   - `idt` — command-line tool.
3. **Physical devices use two Apple paths.** CoreDevice (`devicectl`, Xcode ≥ 15) for developer
   services on iOS 17+ (it owns the RemoteXPC tunnel and personalized DDI); and a native Swift
   usbmuxd/lockdown client for services available to any trusted device without Xcode
   (identity, syslog, os_trace_relay, pcapd, MobileBackup2, diagnostics, installation proxy,
   misagent, image mounter). Pair records are read through usbmuxd's `ReadPairRecord`, which
   macOS allows without root. The app never creates pair records, never reads
   `/var/db/lockdown`, and never uses `sudo`.
4. **TLS for lockdown uses swift-nio-ssl.** Lockdown upgrades an established plaintext stream
   to TLS with the pair record's host certificate. Network.framework has no public STARTTLS
   and SecureTransport has been deprecated since macOS 10.15, so the lockdown channel uses
   Apple's open-source `swift-nio` + `swift-nio-ssl` (Apache-2.0). The device certificate is
   pinned to the `DeviceCertificate` stored in the pair record.
5. **Event-driven discovery.** usbmuxd `Listen` pushes attach/detach events; CoreDevice and
   simulator lists refresh on those events, on explicit refresh, and on a slow (30 s) timer only
   for network-only CoreDevice devices that usbmuxd cannot report.
6. **Device targeting.** Every operation takes an immutable `DeviceTarget` (kind + UDID +
   display name) captured when the operation starts; operations never read "the current
   selection" later. Device-changing actions require a phrase bound to the target's UDID.
7. **Minimum macOS 14** (Observation, modern SwiftUI). Universal binary (arm64 + x86_64).
8. **Swift 6 language mode** with strict concurrency.

## 4. Removed dependencies

| Dependency | Reason |
|---|---|
| Python 3.10–3.13 runtime, PySide6, Nuitka | Replaced by native Swift/SwiftUI app |
| `pymobiledevice3` 11.15.1 (and its transitive deps: xonsh, IPython, etc.) | Replaced by native lockdown client + Apple CoreDevice/simctl/xctrace |
| go-ios adapter | Removed by request |
| blacktop/ipsw adapter | Removed by request |
| mkdocs-material, cyclonedx-bom (Python) | Docs moved into repository Markdown; SBOM generated from `Package.resolved` |

## 5. Test results

Environment: MacBook Pro (Apple silicon), macOS 27.0, Xcode 27.0 (Swift 6.4). No physical iPhone
or iPad was connected during the migration.

### 5.1 Automated tests

| Suite | Tests | What it exercises | Result |
|---|---:|---|---|
| `ToolkitCoreTests` | 30 | `CommandRunner` (argument vectors, timeouts, cancellation, output draining, minimal environment), `ToolkitError`, secure file I/O (owner-only, no overwrite, path traversal), sanitizer, hashing, journal, ZIP writer | ✅ pass |
| `DeviceKitTests` | 76 | usbmuxd framing and `Listen` events, developer images (image mounter, image library, TSS request, state evaluation, personalized and legacy mount/unmount), pairing-record handling, lockdown TLS with certificate pinning and UDID check, the lockdown service clients (syslog, os_trace, pcapd, MobileBackup2, diagnostics, installation proxy, AFC, image mounter, springboard) against an in-process **fake usbmuxd + lockdownd device**; CoreDevice JSON parsing; `simctl` parsing | ✅ pass |
| `ToolkitFeaturesTests` | 55 | Location Lab, GPX, location mechanism routing and legacy-service message encoding, provisioning profiles (misagent) and packet capture through the action executor, IPA inspection (fixtures incl. malicious archives), live-log capture/findings/export, action catalog and safety policy, actions and readiness against the fake device, evidence collection, workspace profiles, support bundle, external-tool validation | ✅ pass |
| Real simulator (opt-in, `IDT_SIMULATOR_TESTS=1`) | 1 | Boots an iOS 26.3.1 iPhone simulator; waits for boot to complete; sets, routes, and clears location; screenshot; app list; live unified log capture with hash; launches an app; Open URL action; readiness; compiles, installs, lists, launches, and uninstalls a minimal simulator app | ✅ pass locally (about 24 s) |
| XCUITest smoke tests (`App/UITests`) | 8 | Window size, Demo Mode labelling, every workspace, disabled demo actions, command palette, Location Lab validation, minimum size, developer-image card | ✅ the original 7 pass locally (2026-09-27, run by the maintainer). The first run failed `testDemoActionsAreBlockedWithExplanation`: each Actions row exposed its identifier on three child elements, so the click was ambiguous (and VoiceOver read three items). Rows are now single accessibility elements; the three affected tests were re-run and pass. All 8 (including the developer-image card test added in §8) pass in CI on Xcode 26.6 (PR #13). |

Totals: 161 package tests pass with `-warnings-as-errors`; the app and UI-test targets build with
`SWIFT_TREAT_WARNINGS_AS_ERRORS=YES` and zero warnings.

### 5.2 GUI verification

The app's screenshot harness (`-capture-screenshots`, see `scripts/check-layout.sh`) rendered all
14 workspaces in Demo Mode at 1180×700 (default) and 900×560 (minimum): no page is squeezed or
overflows the window. The same harness rendered the Device, Live Logs (real simulator log stream,
about 35,000 lines in 6 s), Location Lab, and Actions pages with a booted simulator. The README
screenshots come from these renders.

### 5.3 Release packaging

`scripts/build-release.sh` produced a universal (arm64 + x86_64) app, ad-hoc signed with the
hardened runtime (`flags=0x10002(adhoc,runtime)`), with `idt`, dependency licenses, and the SPDX
SBOM inside, and verified it again from the ZIP. The release build launched and rendered, and its
`idt` listed devices. After Rosetta 2 was installed (2026-09-27), the x86_64 slices were run under
Rosetta: the release script's check ran the Intel `idt`; the Intel `idt` listed devices, found all
27 Xcode routes, and reported developer-image status; the Intel app rendered all 14 pages at both
window sizes without layout problems; and all 161 package tests pass when built for x86_64 and run
with `arch -x86_64 xctest`. This is Rosetta on Apple silicon, not a real Intel Mac. `spctl`
rejects the app, as expected for an app that is not notarized.

### 5.4 Physical devices

**Partly tested on hardware (2026-09-27).** One iPhone 17 Pro (`iPhone18,1`) on iOS 26.3.1, on USB,
trusted, **locked, with Developer Mode off**. The native protocol layer was checked with the
opt-in, read-only suite `RealDeviceTests` (`IDT_DEVICE_TESTS=1 swift test --filter RealDeviceTests`)
and `idt`. The GUI protocol in
[docs/PHYSICAL_DEVICE_TEST_PROTOCOL.md](docs/PHYSICAL_DEVICE_TEST_PROTOCOL.md) has not been run. Nothing
that changes the device was run: no mounting, location, installation, or backup.

| Check | Service | Result on the iPhone |
|---|---|---|
| Discovery, TLS session, UDID match | usbmuxd, lockdown | ✅ |
| Process list | `os_trace_relay` `PidList` | ✅ 519 processes, including launchd |
| Configuration profiles | `com.apple.mobile.MCInstall` | ✅ 2 profiles |
| Provisioning profiles | `misagent` | ✅ 3 profiles |
| Installed apps | `installation_proxy` | ✅ 462 apps |
| Diagnostics | `diagnostics_relay` | ✅ |
| Mounted images | `mobile_image_mounter` lookup | ✅ none mounted (correct: Developer Mode off) |
| Developer image state | evaluator | ✅ *Needs attention: Developer Mode is off*. With Developer Mode assumed on, the evaluator selects Xcode image 27A266a's `iPhone18,1` identity (*Personalization required*) |
| Classic syslog | `syslog_relay` | ✅ 3,476 lines in 4 s |
| Unified Logging | `os_trace_relay` | ✅ 15,535 records in 4 s |
| Packet capture | `pcapd` | ✅ 27 packets in 5 s, valid `.pcap` |
| Bluetooth capture | `BTPacketLogger` | ◐ service starts, 0 records (no Bluetooth logging profile installed) |
| Safari and web view tabs | `webinspector` | ◐ not answering (Web Inspector presumably off); the refusal message was shown |
| CoreDevice | `devicectl` | ✅ connected, tunnel connected |

| Device | iOS | Connection | Stage 1 | Stage 2 | Stage 3 | Stage 4 | Tester, date |
|---|---|---|---|---|---|---|---|
| iPhone 17 Pro | 26.3.1 | USB | discovery and trust only | protocol layer only (above), GUI not run | not run (Developer Mode off) | not run | maintainer, 2026-09-27 |

### 5.5 Final verification (2026-09-27)

| Check | Result |
|---|---|
| Fresh clone of `swift-native-migration` to a temporary folder; `swift build -Xswiftc -warnings-as-errors`; `swift test` | ✅ builds with no warnings; 147 tests pass |
| Clean `xcodebuild … clean build-for-testing` of the app and UI tests | ✅ succeeded. It exposed 78 Swift 6 actor-isolation warnings in the UI tests that a plain `build` never compiles and that `SWIFT_TREAT_WARNINGS_AS_ERRORS` does not promote; fixed, and CI now fails on any warning in the build log |
| Launch and render every screen | ✅ all 14 pages at 1180×700 and 900×560 (Demo Mode, `scripts/check-layout.sh`), and all 14 with a booted iOS 26.3.1 simulator selected and its live log streaming |
| Nothing requires Python | ✅ no Python in the repository except the optional, user-installed UFADE and MVT integrations (Python programs themselves); the release script fails if a binary links Python; the build, tests, SBOM generator, and release script use only Xcode |
| `idt devices`, `idt toolchain` | ✅ no devices → guidance and exit 0; simulators listed with `--simulators`; all 27 `devicectl`/`simctl`/`xctrace` routes present in Xcode 27.0 |
| Real simulator end-to-end (`IDT_SIMULATOR_TESTS=1`) | ✅ passed (9.6 s) |
| No Xcode (simulated with `DEVELOPER_DIR=/Library/Developer/CommandLineTools`) | ✅ usbmuxd discovery still works; CoreDevice and simulators report "Xcode is not installed…"; the app's sidebar shows them as Unavailable. The Toolchain Check blamed each individual command; fixed to report the missing Xcode (exit 2) |
| usbmuxd missing | ✅ the app (discovery pointed at a nonexistent socket) shows USB & Wi-Fi as Unavailable with the reason in the tooltip; the library error names usbmuxd (tested) |
| No device | ✅ Overview shows "No device selected" with next steps; `idt devices` explains how to connect |
| Unified log review | ✅ subsystem `io.hideouts.iOSDeveloperToolkit` logs discovery, commands (start/finish, duration, status), and outcomes; errors seen were the tests' deliberate negative cases. Found and fixed: default command names could put a simulator UDID in a public field, and some error descriptions and operation titles (paths, app names) were public |
| Default window on a 1280×800 display | ✅ first launch opens at 1180×700 including title bar and toolbar (minimum 900×612), within the ~1280×705 usable area below the menu bar with a bottom Dock |
| README matches the app | ✅ menus, shortcuts, pages, labels, `idt` options and exit codes, file locations, and the with/without-Xcode table checked against the code; the no-Xcode column was checked against the implementation (native lockdown paths) |
| Physical iPhone/iPad | ❌ **none connected** — discovery, trust, live logs, backup, packet capture, and multi-device handling are **untested on hardware** (§5.4) |

## 6. Known limitations and features not reproduced

### 6.1 Features not migrated

| Feature (Python) | Why not in 1.0 | Alternatives investigated | Native implementation possible? |
|---|---|---|---|
| **Safari/WebView tab listing** (`pmd3 webinspector opened-tabs`) | ✅ Implemented after the parity audit (§9 G3): native `com.apple.webinspector` client, retrying refusals until a deadline. Opening a URL through Web Inspector (automation) is still not reproduced — it needs a WebDriver session and Safari’s Remote Automation setting; Open URL uses CoreDevice. | — | — |
| **Bluetooth HCI capture** (`pmd3 btlogger`) | ✅ Implemented after the parity audit (§9 G4): native `com.apple.bluetooth.BTPacketLogger` client; records are written as Apple PacketLogger `.pklg` (opened by PacketLogger and Wireshark) instead of 0.3.x's pcapng. Needs Apple's Bluetooth logging profile on the device. | — | — |
| **DVT file-system listing** (`pmd3 developer dvt ls`) | DVT uses Apple's private DTX protocol (NSKeyedArchiver messages over `com.apple.instruments.remoteserver*`). On iOS 17+ it is only reachable through the RemoteXPC tunnel that CoreDevice owns; creating that tunnel needs a utun interface (root) or CoreDevice's private frameworks — both excluded (no `sudo`, no private frameworks). | AFC (`com.apple.afc`, Media folder — implemented as *List Media folder*); `devicectl device info files` and `device copy from` for app containers and supported domains (available through Advanced Mode); crash reports through `crashreportcopymobile` (implemented). | **Not for iOS 17+** without privileges or private frameworks. For iOS 16 and earlier, DTX over lockdown is possible but serves only legacy devices and is not planned. |

### 6.2 Verification gaps

- **Developer images** (§8): the image-mounter protocol and Apple personalization are verified
  against a stateful fake image mounter and a fake signing server, and the request is built from
  the real image Xcode installed on this Mac. No device has mounted an image through this code yet.
- **Native lockdown services need physical-device verification.** usbmuxd, lockdown TLS, and all
  service clients pass byte-level tests against the fake device, which reproduces Apple's framing
  (plist headers, TLS upgrade, DeviceLink, AFC packets, pcapd records, os_trace records) from
  public protocol documentation and prior implementations. Real devices can differ in details
  (record versions, error codes, timing). Until the protocol in §5.4 has been run, treat 🟡 rows
  as unverified.
- **CoreDevice commands** are verified for argument construction, JSON parsing (from recorded
  output shapes), and presence in the installed Xcode (Toolchain Check), not against a device.
- **UI tests** run in CI and when the maintainer runs them locally (macOS asks once to allow UI
  automation); all 8 pass in both.
- **Intel Macs:** the x86_64 slice is verified under Rosetta 2 on Apple silicon (tests, CLI, and
  rendering), not on Intel hardware.
- **CI** (PR #13, `macos-26`, Xcode 26.6 / Swift 6.3.3): the app build with the zero-warning check,
  all 8 UI tests, the layout check, and dependency review pass. Two fixes came out of the first
  runs: an array-type inference difference in Swift 6.3 (test code), and waiting for simulators to
  finish booting (`simctl bootstatus -b`) before launching apps.
- **Older Xcode:** Xcode 26.6 (the CI runner) lacks `devicectl device simulate location`, the
  screenshot `--destination` option, `device process openURL`, and `device profile list --type`.
  With Xcode 26, location simulation on iOS 17+ and those actions through Xcode's device service are
  unavailable; the app says so (“needs a newer Xcode”) and the Toolchain Check lists them. Native
  replacements that avoid `devicectl` are tracked in §9 (G1, G2). The app is verified with Xcode 27.

### 6.3 Behaviour differences from 0.3.x

- Release builds are universal instead of separate Apple silicon and Intel downloads; minimum
  macOS is 14 (was 13).
- Guided actions replace the 49 raw `pymobiledevice3` presets; Advanced Mode runs `devicectl`
  instead of arbitrary `pymobiledevice3` subcommands.
- DVT telemetry streams are replaced by Instruments recordings (`xctrace`); the DVT OSLog stream by
  the Unified Logging stream, which needs no developer image.
- Features that need a developer tunnel (iOS 17+) now require Xcode, which owns the tunnel.

## 7. Migration log

- 2026-09-26 — Audit complete; migration branch `swift-native-migration` created.
- 2026-09-26 — Swift package (ToolkitCore, DeviceKit, ToolkitFeatures, idt CLI) complete with an end-to-end fake usbmuxd/lockdownd device, a real-Xcode toolchain check, and an opt-in real-simulator test. SwiftUI app and XCUITests written.
- 2026-09-26 — GUI layout fixed at the minimum size; documentation screenshot mode added.
- 2026-09-26 — Standalone packet capture action (parity with the Python app); warnings are errors in every target.
- 2026-09-26 — README and documentation rewritten for the Swift app; mkdocs removed.
- 2026-09-26 — GitHub Actions replaced (CI, release, CodeQL for Swift, dependency review); `scripts/build-release.sh` verified locally.
- 2026-09-27 — Test results and known limitations recorded (§5, §6); feature statuses set (§2).
- 2026-09-27 — Python implementation, packaging, and go-ios/ipsw references removed.
- 2026-09-27 — Final verification (§5.5): fresh clone, clean builds, every screen rendered, no-Xcode / no-usbmuxd / no-device states, unified log review. Fixed on the way: UI-test concurrency warnings (and a CI check for them), Toolchain Check message without Xcode, identifiers in public log fields. Physical-device verification remains open.
- 2026-09-27 — Developer-image (DDI) capability audited and restored natively (§8): detection, state model, personalized and legacy mounting, unmount, GUI, readiness, actions, and `idt ddi`. Physical-device verification remains open.

## 8. Developer image (DDI) audit and restoration

### 8.1 What the Python app did (0.3.4, via `pymobiledevice3` 11.15.1)

| Path | Python behaviour |
|---|---|
| “Mount Personalized DDI” (default) | `pymobiledevice3 mounter auto-mount`. **iOS ≤ 16:** download `DeveloperDiskImage.dmg` + `.signature` for the device's `major.minor` from the third-party GitHub mirror `doronz88/DeveloperDiskImage`, `ReceiveBytes` (ImageType `Developer`) + `MountImage` over `com.apple.mobile.mobile_image_mounter`, mounted at `/Developer`. **iOS ≥ 17:** download `Image.dmg`, `Image.dmg.trustcache`, `BuildManifest.plist` (Xcode's personalized DDI) from the same mirror into `~/.pymobiledevice3`; `QueryPersonalizationManifest` (SHA-384 of the image) to reuse a manifest the device already holds, otherwise `QueryPersonalizationIdentifiers` + `QueryNonce` and an Apple TSS request (`gs.apple.com`) for an `ApImg4Ticket`; then `ReceiveBytes`/`MountImage` with ImageType `Personalized` and the trust cache; mounted at `/System/Developer`. Refused to mount when an image was already mounted or Developer Mode was off. |
| “Install Local Xcode DDI Cryptex” | `local_ddi.py`: `hdiutil attach -readonly` of `/Library/Developer/CoreDevice/CandidateDDIs/iOS_DDI.dmg`, then `pymobiledevice3 cryptex auto-install --restore-dir …/Restore` (personalized Cryptex install through the RemoteXPC `cryptexd` service), then detach. |
| Unmount | `mounter umount-personalized` (`/System/Developer`) or `cryptex uninstall com.apple.MobileAsset.DDI`. |
| Status | `mounter list`, `mounter lookup`, `query-developer-mode-status`, `query-nonce`, `query-personalization-identifiers`; Capability Matrix row “developer-image” from `mounter list`. |

### 8.2 Gaps found in the Swift app (before this work)

| # | Gap |
|---|---|
| G1 | No native detection of the mounted image (`LookupImage`) or of its compatibility; state came only from CoreDevice. |
| G2 | Chip ID, board ID, and ECID (needed to select and personalize an image) were never read. |
| G3 | No native iOS 17+ personalized mount (manifest reuse, TSS personalization, upload, mount). Only `devicectl … ddiServices --auto-mount-ddis`, which needs Xcode **and** a CoreDevice-paired device. |
| G4 | No iOS ≤ 16 mount at all. |
| G5 | No “already mounted → do not remount” guard on a native path. |
| G6 | Unmount handled only `/System/Developer`, not the legacy `/Developer`. |
| G7 | No image-state model; the GUI showed only “Available / Not prepared / Needs Xcode”. |
| G8 | Image-mounter errors surfaced as “The image mounter service did not answer”. |
| G9 | Readiness row and `idt ddi` depended on CoreDevice only. |

Not reproduced by design: downloading Apple's images from the third-party mirror (redistributed Apple binaries — the Swift app uses the images Xcode installs in `/Library/Developer/DeveloperDiskImages`, or a folder the user chooses), and the RemoteXPC Cryptex install (needs a privileged tunnel; `devicectl` covers it).

### 8.3 What the Swift app does now

| Gap | Resolution |
|---|---|
| G1, G5 | `ImageMounter.lookup` (`LookupImage`) and `CopyDevices` decide whether a compatible image is mounted; mounting returns immediately (no upload, no Apple request) when one is. |
| G2 | Facts from lockdown (`ProductVersion`, `BuildVersion`, `ProductType`, `CPUArchitecture`, `HardwareModel`, `ChipID`, `BoardId`, Developer Mode), falling back to the image mounter's personalization identifiers for chip and board. ECID is read only for the signing request and never displayed. |
| G3 | Native personalized mount, as `pymobiledevice3` did: pick the build identity for the chip and board from the image Xcode installs (`/Library/Developer/DeveloperDiskImages/iOS_DDI/Restore`, 140 identities for 24 chip families with Xcode 27) or a user folder; reuse a manifest the device already holds (`QueryPersonalizationManifest`, SHA-384); otherwise `QueryPersonalizationIdentifiers` + `QueryNonce` + Apple TSS over HTTPS; then `ReceiveBytes` and `MountImage` with the trust cache. Alternatively (and by default when CoreDevice can reach the device) Xcode's `devicectl … ddiServices --auto-mount-ddis`. |
| G4 | Native legacy mount of `DeveloperDiskImage.dmg` + `.signature` for the exact iOS `major.minor`, found in any installed Xcode's DeviceSupport folder or a user folder. |
| G6 | Unmount handles `/System/Developer` and `/Developer`. |
| G7 | `DeveloperImageState`: not required, mounted, available, personalization required, missing, incompatible, needs attention (trust, Developer Mode, lock, USB), failed — each with a headline, explanation, next step, and details, shown on the Device page's **Developer image** card. |
| G8 | Image-mounter and TSS replies are classified (locked, Developer Mode off, already mounted, not mounted, signature rejected, unsupported, Apple refused, offline) into plain-language errors; raw replies go only to technical details. |
| G9 | Readiness Check, the Actions catalog, and `idt ddi status|mount|unmount` use the same `DeveloperImageManager`. |

No runtime dependency was added: the image-mounter client uses the existing lockdown stack, and
personalization uses `URLSession`. The private service and the TSS request are isolated in
`Sources/DeviceKit/DeveloperImage` (see [docs/architecture.md](docs/architecture.md#developer-images)).

### 8.4 Verification

| Check | Result |
|---|---|
| Unit and fake-device tests (`DeveloperImageTests`, 14 tests) | ✅ every state; TSS request fields and restore-request rules; reply parsing and Apple's refusal codes; personalized mount end to end (identifiers, nonce, TSS, upload size and signature, trust cache); no remount when mounted; reuse of a stored personalization without contacting Apple; unmount; legacy mount without personalization; Developer Mode off and locked device; identifier fallback; simulators and network-only devices |
| The image Xcode 27 installed on this Mac | ✅ parsed: build identity for iPhone18,1 found, image and trust cache readable, a complete TSS request built |
| Apple's signing endpoint | ✅ `https://gs.apple.com/TSS/controller?action=2` reachable with valid TLS (plain GET, no device data). A real signing request needs a device nonce and was **not** sent |
| App | ✅ Developer image card rendered in Demo Mode at 1180×700 and 900×560 (no overflow); all pages pass `scripts/check-layout.sh`; a UI test checks the card and that demo mounting is blocked |
| `idt ddi` | ✅ `status` (text and JSON, exit codes), `mount`/`unmount` confirmation, hidden `prepare` alias |
| **Physical devices** | ❌ **Not tested.** No iPhone or iPad was connected. Still to verify on hardware: the real image-mounter replies, Apple's acceptance of the TSS request, the mount itself, and error wording on real failures — [docs/PHYSICAL_DEVICE_TEST_PROTOCOL.md](docs/PHYSICAL_DEVICE_TEST_PROTOCOL.md) Stage 3, steps 1–5 |

Not reproduced: downloading images from the third-party mirror (the app uses Xcode's images or a
folder you choose), and the RemoteXPC Cryptex install (`devicectl` covers it without a privileged
tunnel).

## 9. Full parity audit (against `origin/main`, Python 0.3.4)

Scope: all 40 modules in `ios_developer_toolkit/`, the 253 named GUI controls and 130 user actions
in `app.py`/`gui_pages.py`, the smoke-test button list in `entrypoint.py`, all 49 Command Center
presets, the 17 evidence snapshots and man-page routes in `catalog.py`, the 11 Capability Matrix
rows, every CLI option, workspace-profile fields, shortcuts, and the behaviours asserted in
`tests/`. Classification: **=** equivalent · **🔁** replaced by a better Apple mechanism ·
**◐** partial · **✗** missing · **—** intentionally excluded.

### 9.1 Command Center presets (49)

| Python preset (`pymobiledevice3 …`) | Swift | Class | Evidence |
|---|---|---|---|
| devices (`usbmux list`) | usbmuxd discovery, `idt devices` | = | `LockdownStackTests`, `idt devices` run |
| lockdown, activation, developer-mode | Actions `lockdown-values`, `activation-state`, `developer-mode-status` (native) | = | `nativeActionsRunAgainstTheCapturedTarget` |
| diagnostics, battery, ioregistry, mobilegestalt | Actions (native `diagnostics_relay`) | = | same |
| processes (`processes ps`, os_trace `PidList`, no Xcode) | Action `processes` and the evidence snapshot use native `PidList` over USB; CoreDevice only for network-only devices | = (**G1 resolved**) | `processListParsesPidListReplies`, `nativeActionsRunAgainstTheCapturedTarget`, `collectsSnapshotsStreamsAndHashes` |
| profiles (`profile list`, MCInstall, no Xcode) | Action `configuration-profiles` and the evidence snapshot use native MCInstall `GetProfileList` over USB; CoreDevice only for network-only devices | = (**G2 resolved**) | `configurationProfileListParsesMCInstallReplies`, `nativeActionsRunAgainstTheCapturedTarget`, `collectsSnapshotsStreamsAndHashes` |
| provisioning, orientation, icon-metrics | Actions (native misagent, springboardservices) | = | `ServiceTests.springBoardServicesAnswerQueries` |
| apps-list, apps-query | Apps page; Action `app-query` (native installation_proxy) | = | `ServiceTests` |
| afc-list | Action `media-list` (path parameter) | = | `nativeActionsRunAgainstTheCapturedTarget` |
| dvt-list (`developer dvt ls`) | — | — | §6.1: DTX over RemoteXPC on iOS 17+ |
| crash-list, crash-pull | Actions (native AFC) | = | `BackupAndAFCTests` |
| syslog | Live Logs · Classic syslog | = | `ServiceTests` |
| oslog (DVT) | Live Logs · Unified (os_trace_relay, no DDI) | 🔁 | `ServiceTests`, real-simulator test |
| pcap | Action `packet-capture`, Evidence stream | = | `nativeActionsRunAgainstTheCapturedTarget` |
| btlogger (`--format pcapng`) | Action `bluetooth-capture` (native, `.pklg`) | 🔁 (**G4 resolved**; PacketLogger format instead of pcapng) | `bluetoothRecordsBecomeAPacketLoggerFile`, `nativeActionsRunAgainstTheCapturedTarget` |
| dvt-device, dvt-proclist, dvt-applist | device details / processes / apps | 🔁 | — |
| dvt-netstat, dvt-energy, sysmon-system, sysmon-process, graphics, core-profile | Action `instruments` (xctrace templates) | 🔁 | `instrumentsRequestsAreBounded` |
| dvt-pid-check | Action `processes` (the list shows whether a pid runs) | 🔁 | — |
| notifications (DVT app-state notifications) | — | — | DTX-only; Instruments “App Launch”/“Activity Monitor” cover app state |
| screenshot, core-device-info, core-display, core-lock, core-processes, core-apps | Actions via CoreDevice (and simctl) | = | real-simulator test (screenshot) |
| mounted-images, personalization | Actions (native image mounter) | = | `DeveloperImageTests` |
| bonjour-rsd | Action `bonjour` (Network.framework) | = | — |
| remote-browse (RSD service list) | — | — | needs RemoteXPC (HTTP/2 + XPC over the device's USB network link); `devicectl device info details` lists capabilities instead |
| web-tabs (`webinspector opened-tabs`) | Action `web-tabs` (native `com.apple.webinspector`) | = (**G3 resolved**) | `webInspectorListsPagesAfterRetryingRefusals`, `nativeActionsRunAgainstTheCapturedTarget` |
| launch-app, location-set, location-clear | Actions via CoreDevice / simctl / legacy service | = | real-simulator test |
| open-url (`webinspector launch`, Safari automation) | Action `open-url` via CoreDevice / simctl | ◐ | the Web Inspector route needs a WebDriver automation session and Settings › Safari › Remote Automation; kept on CoreDevice |

### 9.2 Workspaces, controls, and workflows

| Area | Python | Swift | Class |
|---|---|---|---|
| Device & DDI | device info, Developer Mode check and guide, DDI source choice, mount/unmount, list images, CoreDevice details, RVI list, open project/artifact | Device page, Developer image card (§8), handoffs | = |
| Connection | banner, **Reconnect & Retry…** (guided 30-second reconnect window) | sidebar summary, Connection diagnostics, Next-step card, **Device › Reconnect a Device…** (guided 30-second window that watches discovery) | = (**G5 resolved**) |
| Capability Matrix | 11 rows incl. `rsd-tunnel`, `dvt`, `webinspector`; copy report; per-preset and per-case readiness buttons | Readiness Check (16 rows for devices; tunnel state in the CoreDevice row; “Safari Web Inspector” and “Instruments (xctrace)” rows); copy report; readiness status with Run / Check Again / Open Readiness Check on every action and on Evidence Capture | = (**G3**, **G8**, **G12** resolved) |
| Compatibility history | table, refresh, JSON/Markdown export with preview | Readiness history and exports | = |
| Location Lab | coordinates, map links, map, nudge, saved places, routes (speed presets, interval, traversals), **add current coordinate as waypoint**, GPX (ignore timing, randomness), evidence log, clear on stop | all, including **Add Current Coordinate** on the Route card | = (**G9 resolved**) |
| Live Logs | streams, reference, regex/case filter, pause, follow, stop, findings, **findings register with Copy**, copy visible, save raw/filtered, evidence bundle, pop-out | all, including **Copy Register** in the Findings sheet | = (**G10 resolved**) |
| Command Center / Man Pages / Drift | presets, console, prerequisites, risk badge, man pages, **use man-page command in console**, drift check | Actions, Advanced Mode, Tool Reference (**Use in Advanced Mode** fills in the `devicectl` command; nothing runs until Run), Toolchain Check | = (**G12 resolved**) |
| Installed Apps | table, filter, sizes, copy bundle ID, uninstall, stop | Apps page | = |
| Backup | encryption check/enable, destination, require encryption, full, progress, stop, open folder | Backup page | = |
| Sideload IPA | choose, inspect, developer package, install, stop | Install App | = |
| Evidence | guided case, authorization, streams, screenshot, crash pull, open last case, readiness | Evidence Capture, with the collection's own readiness status (screenshot adds Xcode's device service) | = |
| MVT | executable, backup, IOC files, output, fast, hashes, network, acknowledgements, guides | External Tools · MVT | = |
| UFADE | checkout, Python, output, validation incl. **developer-image submodule status**, guides, launch | External Tools · UFADE | ◐ → **G13** |
| Ecosystem tools | go-ios, ipsw, idb | idb Companion | = (go-ios/ipsw removed by request) |
| Workspace profiles | export/import with preview; fields incl. **`ddi_source`, `command_preset`**; imports schema-1 files | export/import with preview; developer-image mechanism, Actions category, and selected action; imports 0.3.x (schema 1) files, translating workspaces, presets (§9.1), `ddi_source` (→ built-in mounter) and DVT OSLog (→ Unified Logging), with notes in the preview | = (**G6 resolved**) |
| Support bundle, Session Activity, Demo Mode, Action Palette | — | ported | = |
| Shortcuts | ⌘1–9, ⌘0, ⌘R, ⌘K, ⌘L, ⌘F, **⌘/ reference**, **⌥⌘←/→ previous/next workspace** | ⌘1–9, ⌘R, ⇧⌘R, ⌘K, **⌘/** (Help › Keyboard Shortcuts), **⌥⌘←/→**, ⌃⌘S (sidebar). Not reproduced: ⌘0 and ⇧⌘E/M/S for the tenth and later pages (use ⌘K or ⌥⌘←/→), ⌘L/⌘F focus shortcuts (Tab and the search fields' own focus) | = (**G11 resolved**) |

### 9.3 Command-line tools, evidence snapshots, tests

| Item | Swift | Class |
|---|---|---|
| `ios-developer-collect` (all options) | `idt collect` — `--include-oslog` renamed `--include-unified-logs`; the old name is still accepted (hidden from help) | = (**G7 resolved**) |
| `ios-ipa-inspect`, `ios-local-ddi` | `idt inspect-ipa`, `idt ddi` | = / 🔁 |
| Evidence snapshots (17) | lockdown, images, diagnostics ×4, apps, provisioning, crashes, AFC root, CoreDevice details; processes and configuration profiles (native over USB since G1/G2); cryptex list and DVT ×3 excluded (RemoteXPC/DTX) | = (**G1**, **G2** resolved) |
| `tests/` behaviours | ported to Swift tests (see §5.1); packaging/runtime tests replaced by `scripts/build-release.sh` checks | = |

### 9.4 Gap list (priority order)

| # | Gap | Priority | Why |
|---|---|---|---|
| G1 | Native process list (`os_trace_relay` `PidList`) for actions and evidence | P1 | ✅ resolved — no Xcode needed over USB |
| G2 | Native configuration profiles (`com.apple.mobile.MCInstall` `GetProfileList`) | P1 | ✅ resolved — no Xcode needed over USB (also avoids `profile list --type`, missing in Xcode 26) |
| G3 | Safari/WebView tab listing (`com.apple.webinspector`) + Web Inspector readiness row | P1 | ✅ resolved — action “Safari and web view tabs”, Readiness row “Safari Web Inspector” |
| G4 | Bluetooth HCI capture (`com.apple.bluetooth.BTPacketLogger`) to `.pklg` | P1 | ✅ resolved — action “Bluetooth capture” |
| G5 | Guided reconnect | P2 | ✅ resolved — Device › Reconnect a Device…, also on the Connection diagnostics and No-device cards |
| G6 | Import 0.3.x workspace profiles; profile fields for the developer-image mechanism and selected action | P2 | ✅ resolved — tested with a profile written by 0.3.4's own exporter |
| G7 | `idt collect --include-oslog` accepted as an alias | P2 | ✅ resolved — hidden alias of `--include-unified-logs` |
| G8 | Instruments readiness row (replaces the DVT row) | P2 | ✅ resolved — Readiness row “Instruments (xctrace)” from `xctrace list devices` (available / offline / not listed); the Instruments recording action waits for it |
| G9 | Add current coordinate as a route waypoint | P3 | ✅ resolved — Location Lab › Route › Add Current Coordinate |
| G10 | Copy the findings register | P3 | ✅ resolved — Live Logs › Findings › Copy Register (Markdown, same as the evidence bundle's report) |
| G11 | Keyboard shortcut reference (⌘/) and previous/next workspace (⌥⌘← / ⌥⌘→) | P3 | ✅ resolved — Help › Keyboard Shortcuts; View › Previous/Next Workspace |
| G12 | “Use in Advanced Mode” from Tool Reference; readiness shortcuts on Actions and Evidence | P3 | ✅ resolved |
| G13 | UFADE developer-image submodule status | P3 | diagnostic detail |
