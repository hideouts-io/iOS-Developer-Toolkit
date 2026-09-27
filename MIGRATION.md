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
| 4 | Personalized DDI mount (iOS 17+) | `pmd3 mounter auto-mount` (TSS) | 🔁 CoreDevice `device info ddiServices --auto-mount-ddis` (Apple mounts the correct personalized DDI) | devicectl | No | 🔁 🟡 |
| 5 | Local Xcode DDI Cryptex install | `hdiutil` + `pmd3 cryptex auto-install` | 🔁 `devicectl manage ddis update` + `list preferredDDI` (host DDI store managed by Apple) | devicectl | No | 🔁 🟡 (route checked by Toolchain Check; not run) |
| 6 | Mounted image list / unmount | `pmd3 mounter list/umount` | Native `mobile_image_mounter` (`CopyDevices`, `UnmountImage`) | Lockdown service | No | 🟡 |
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
| 27 | Processes | `pmd3 processes ps`, DVT proclist, CoreDevice list-processes | CoreDevice `info processes` | Yes | No | 🟡 |
| 28 | Launch app / open URL | DVT launch, Web Inspector launch | CoreDevice `process launch` / `process openURL`; Simulator `simctl launch` / `openurl` | Yes | No | ✅ simulators · 🟡 physical |
| 29 | Configuration / provisioning profiles | `pmd3 profile list`, `provision list` | CoreDevice `profile list`; native `misagent` | Yes | No | 🟡 |
| 30 | Diagnostics, battery, IORegistry, MobileGestalt | `pmd3 diagnostics …` | Native `diagnostics_relay` | Lockdown service | No | 🟡 |
| 31 | SpringBoard orientation / icon metrics | `pmd3 springboard …` | Native `springboardservices`; CoreDevice `orientation get` | Yes | No | 🟡 |
| 32 | Activation state, personalization identifiers | `pmd3 activation state`, `mounter query-personalization-identifiers` | Native lockdown / `mobile_image_mounter` | Lockdown | No | 🟡 |
| 33 | DVT telemetry (sysmon, energy, graphics, netstat, notifications, KDebug/CoreProfile) | `pmd3 developer dvt …` | 🔁 Instruments recordings via `xcrun xctrace record --device` (Activity Monitor, Network, Power Profiler, System Trace, Time Profiler…) | xctrace | No | 🔁 🟡 |
| 34 | RSD / RemoteXPC Bonjour discovery | `pmd3 bonjour rsd`, `remote browse` | Network.framework `NWBrowser` for `_remotepairing._tcp` / `_apple-mobdev2._tcp` | Network.framework | No | 🟡 |
| 35 | Safari/WebView tab list | `pmd3 webinspector opened-tabs` | ❌ Not migrated in 1.0 (see §6) | — | No | ❌ |
| 36 | Bluetooth HCI capture | `pmd3 btlogger` | ❌ Not migrated in 1.0 (see §6) | — | No | ❌ |
| 37 | DVT filesystem listing (`dvt ls /`), AFC media listing | `pmd3 developer dvt ls`, `afc ls` | AFC via native `com.apple.afc`; DVT listing ❌ (see §6) | Lockdown | No | 🟡 AFC · ❌ DVT |
| 38 | Session Activity journal + manifest export | `operation_history.py` | Ported (`OperationJournal` actor) | Foundation | No | ✅ |
| 39 | Workspace profiles import/export | `workspace_profile.py` | Ported (Codable + validation) | Foundation | No | ✅ |
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
| `DeviceKitTests` | 62 | usbmuxd framing and `Listen` events, pairing-record handling, lockdown TLS with certificate pinning and UDID check, the lockdown service clients (syslog, os_trace, pcapd, MobileBackup2, diagnostics, installation proxy, AFC, image mounter, springboard) against an in-process **fake usbmuxd + lockdownd device**; CoreDevice JSON parsing; `simctl` parsing | ✅ pass |
| `ToolkitFeaturesTests` | 55 | Location Lab, GPX, location mechanism routing and legacy-service message encoding, provisioning profiles (misagent) and packet capture through the action executor, IPA inspection (fixtures incl. malicious archives), live-log capture/findings/export, action catalog and safety policy, actions and readiness against the fake device, evidence collection, workspace profiles, support bundle, external-tool validation | ✅ pass |
| Real simulator (opt-in, `IDT_SIMULATOR_TESTS=1`) | 1 | Boots an iOS 26.3.1 iPhone simulator; sets, routes, and clears location; screenshot; app list; live unified log capture with hash; launches an app; Open URL action; readiness | ✅ pass (9.6 s) |
| XCUITest smoke tests (`App/UITests`) | 7 | Window size, Demo Mode labelling, every workspace, disabled demo actions, command palette, Location Lab validation, minimum size | ✅ all 7 pass locally (2026-09-27, run by the maintainer). The first run failed `testDemoActionsAreBlockedWithExplanation`: each Actions row exposed its identifier on three child elements, so the click was ambiguous (and VoiceOver read three items). Rows are now single accessibility elements; the three affected tests were re-run and pass. Also run in CI |

Totals: 147 package tests pass with `-warnings-as-errors`; the app and UI-test targets build with
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
`idt` listed devices. The x86_64 slice is present (`lipo`) but was **not executed**: this Mac has
no Rosetta. `spctl` rejects the app, as expected for an app that is not notarized.

### 5.4 Physical devices

**Not tested on hardware.** No iPhone or iPad was available. Discovery, trust, live logs, backup,
packet capture, app installation, diagnostics, and multi-device handling are verified only against
the fake device (byte-level protocol tests) and against macOS's real usbmuxd with zero devices.
Everything marked 🟡 in §2 needs a pass of
[docs/PHYSICAL_DEVICE_TEST_PROTOCOL.md](docs/PHYSICAL_DEVICE_TEST_PROTOCOL.md).

| Device | iOS | Connection | Stage 1 | Stage 2 | Stage 3 | Stage 4 | Tester, date |
|---|---|---|---|---|---|---|---|
| — | — | — | not tested | not tested | not tested | not tested | — |

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
| **Safari/WebView tab listing** (`pmd3 webinspector opened-tabs`) | Needs the undocumented WebKit remote-inspector RPC protocol (`com.apple.webinspector`: `_rpc_reportIdentifier:`, `_rpc_getConnectedApplications:`, `_rpc_forwardGetListing:`), plus *Web Inspector* enabled on the device. Without a device the protocol cannot be verified, and shipping an unverified reverse-engineered protocol conflicts with the "do not claim it works" rule. | Safari › Develop menu on the Mac (Apple-supported, lists and inspects tabs on a connected device); `ios_webkit_debug_proxy` (third-party executable — rejected: no hidden shell-outs to third-party tools); CoreDevice has no web-inspector command. | **Yes.** The service is reachable through lockdown with the existing `DeviceSession`; it needs a plist RPC client and hardware verification. Candidate for 1.1. |
| **Bluetooth HCI capture** (`pmd3 btlogger`) | `com.apple.bluetooth.BTPacketLogger` only streams after Apple's *Bluetooth logging profile* is installed on the device, and there was no device to verify the record format. | Apple **PacketLogger** (Additional Tools for Xcode) captures from a connected iOS device with the same profile — the documented route, recommended in the meantime; a sysdiagnose taken with the profile installed also contains the HCI log. | **Yes**, over lockdown with the existing service plumbing (framing is similar to pcapd). Needs the profile and a device to verify. |
| **DVT file-system listing** (`pmd3 developer dvt ls`) | DVT uses Apple's private DTX protocol (NSKeyedArchiver messages over `com.apple.instruments.remoteserver*`). On iOS 17+ it is only reachable through the RemoteXPC tunnel that CoreDevice owns; creating that tunnel needs a utun interface (root) or CoreDevice's private frameworks — both excluded (no `sudo`, no private frameworks). | AFC (`com.apple.afc`, Media folder — implemented as *List Media folder*); `devicectl device info files` and `device copy from` for app containers and supported domains (available through Advanced Mode); crash reports through `crashreportcopymobile` (implemented). | **Not for iOS 17+** without privileges or private frameworks. For iOS 16 and earlier, DTX over lockdown is possible but serves only legacy devices and is not planned. |
| **Mounting a developer disk image on iOS 16 and earlier** (`pmd3 mounter auto-mount` with a version-specific DDI) | The image mounter protocol (`UploadImage`/`MountImage`) is simple, and listing/unmounting are implemented natively. The blocker is the image: current Xcode (27.0 here) ships no per-version `DeviceSupport` images for iOS 16 and earlier, and redistributing Apple's DDIs is not permitted. `devicectl`'s DDI services (`--auto-mount-ddis`) apply to iOS 17+ personalized images only. | Connect the device to an Xcode version that supports it once (Xcode mounts the image); user-supplied images from an older Xcode (would need a file picker and signature validation — deferred); third-party DDI repositories (rejected: licensing and provenance). | **Yes, technically** (upload + mount with a user-supplied `DeveloperDiskImage.dmg` and `.signature`), but only useful with an image the user already has. Deferred until there is demand. |

### 6.2 Verification gaps

- **Native lockdown services need physical-device verification.** usbmuxd, lockdown TLS, and all
  service clients pass byte-level tests against the fake device, which reproduces Apple's framing
  (plist headers, TLS upgrade, DeviceLink, AFC packets, pcapd records, os_trace records) from
  public protocol documentation and prior implementations. Real devices can differ in details
  (record versions, error codes, timing). Until the protocol in §5.4 has been run, treat 🟡 rows
  as unverified.
- **CoreDevice commands** are verified for argument construction, JSON parsing (from recorded
  output shapes), and presence in the installed Xcode (Toolchain Check), not against a device.
- **UI tests** run only in CI (Automation Mode authorization cannot be granted non-interactively).
- **Intel Macs:** the universal build is produced and signed, but the x86_64 slice has not been run.
- **CI** has not run yet: the workflows were validated as YAML and their scripts were run locally
  with Xcode 27. GitHub's `macos-26` image ships an older Xcode; if the Swift 6.4 toolchain is
  required, set the `XCODE_VERSION` repository variable.
- **Simulator app installation** (`simctl install`) is covered by argument tests only; the
  end-to-end test does not install an app.

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
