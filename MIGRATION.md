# Python → Swift migration record

This document tracks the rewrite of iOS Developer Toolkit from the PySide6 / `pymobiledevice3`
application (v0.3.4) to a native Swift/SwiftUI macOS application. It is the working checklist
for the migration and is updated as each feature is migrated, tested, and verified.

Status legend: ✅ migrated and tested · 🟡 migrated, unit-tested only against simulated device
traffic (needs physical-device verification) · 🔁 replaced by a different Apple-supported
mechanism · ❌ intentionally not migrated (see reason) · ⏳ in progress

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
| 1 | Device discovery | `pmd3 usbmux list` polled every 3 s | usbmuxd `Listen` event stream (event-driven) + CoreDevice `list devices` + `simctl list` | usbmuxd socket, devicectl, simctl | No | ⏳ |
| 2 | Device identity (name, model, iOS, build, UDID, connection) | `usbmux list` / `lockdown info` | Native lockdown `GetValue` + CoreDevice details, with plain-language explanations | Yes | No | ⏳ |
| 3 | Developer Mode status + on-device guide | `pmd3 amfi developer-mode-status` | Native lockdown (`com.apple.security.mac.amfi`) and CoreDevice `developerModeStatus`; guide sheet | Yes | No | ⏳ |
| 4 | Personalized DDI mount (iOS 17+) | `pmd3 mounter auto-mount` (TSS) | 🔁 CoreDevice `device info ddiServices --auto-mount-ddis` (Apple mounts the correct personalized DDI) | devicectl | No | ⏳ |
| 5 | Local Xcode DDI Cryptex install | `hdiutil` + `pmd3 cryptex auto-install` | 🔁 `devicectl manage ddis update` + `list preferredDDI` (host DDI store managed by Apple) | devicectl | No | ⏳ |
| 6 | Mounted image list / unmount | `pmd3 mounter list/umount` | Native `mobile_image_mounter` (`CopyDevices`, `UnmountImage`) | Lockdown service | No | ⏳ |
| 7 | CoreDevice details, RVI list, open project (`xed`), open .xcresult/.trace | `xcrun`, `rvictl`, `xed`, `open` | Same Apple tools through the central `CommandRunner` | Yes | No | ⏳ |
| 8 | Capability Matrix | Worker running `pmd3` probes | Native probes (usbmuxd, pair record, lockdown session, AMFI, image mounter) + CoreDevice probes (details, lock state, DDI services) + Xcode tools | Yes | No | ⏳ |
| 9 | Real-device compatibility history + sanitized JSON/Markdown export | `device_compatibility.py` | Ported (`CompatibilityStore`) | Foundation, CryptoKit | No | ⏳ |
| 10 | Location Lab (coordinate, nudge, saved places, offline map, map-link parsing, route generator, GPX inspection/replay, evidence log, clear) | `pmd3 developer dvt simulate-location` | Physical: CoreDevice `simulate location coordinate/route/clear`; Simulator: `simctl location`; GPX replay driven by the app; offline MapKit-free world map | devicectl, simctl | No | ⏳ |
| 11 | Live Logs — Unified | `pmd3 syslog live --format json` (os_trace_relay) | Native `com.apple.os_trace_relay` client; Simulator: `simctl spawn log stream --style ndjson` | Lockdown service / simctl | No | ⏳ |
| 12 | Live Logs — Classic syslog | `pmd3 syslog live-old` | Native `com.apple.syslog_relay` client | Lockdown service | No | ⏳ |
| 13 | Live Logs — DVT OSLog | `pmd3 developer dvt oslog` | 🔁 Covered by #11 (os_trace_relay needs no DDI); DVT/DTX is not an Apple-public interface | — | No | ⏳ |
| 14 | Live log spool, pause, filter (literal/regex/case), findings, review, raw/filtered save, evidence bundle, metadata sidecar | `live_logs.py` | Ported (`LogCapture`, `FindingsStore`, `InvestigationReport`) | Foundation | No | ⏳ |
| 15 | Command Center — 49 `pmd3` presets + Advanced Mode + risk classes + typed confirmation | `command_catalog.py`, `action_safety.py` | 🔁 Guided **Actions** catalog backed by native services / devicectl / simctl / xctrace, same risk classes and device-bound `RUN XXXXXX` / `IRREVERSIBLE XXXXXX` phrases; Advanced Mode for `devicectl` with safety classification | Yes | No | ⏳ |
| 16 | Guided Command Drift | `pmd3 <route> --help` probes | 🔁 **Toolchain Check**: verifies every devicectl/simctl/xctrace route the app uses is present in the installed Xcode | Yes | No | ⏳ |
| 17 | Man Pages (59 `pmd3` routes) | `pmd3 --help` | 🔁 Help browser for the Apple tools actually used (`devicectl help …`, `simctl help …`, `xctrace help …`) | Yes | No | ⏳ |
| 18 | Installed Apps (search, sort, sizes, copy bundle ID, uninstall) | `pmd3 apps list/uninstall` | Native `installation_proxy` (sizes) with CoreDevice `info apps` fallback; uninstall via CoreDevice / simctl | Yes | No | ⏳ |
| 19 | MobileBackup2 (encryption status, require encryption + new password, full/incremental, progress, cancel) | `pmd3` backup2 worker | Native `com.apple.mobilebackup2` DeviceLink client + `notification_proxy` sync lock; password never in argv | Lockdown service | No | ⏳ |
| 20 | UFADE external launch | `ufade_connector.py` | Kept as optional external provider through `CommandRunner` | — | No | ⏳ |
| 21 | MVT analysis handoff | `mvt_connector.py` | Kept as optional external provider through `CommandRunner` | — | No | ⏳ |
| 22 | Sideload IPA (safe archive validation, Info.plist, provisioning via `security cms`, `codesign --verify`) | `ipa_inspector.py` | Native ZIP reader + validated extraction, `CMSDecoder` (Security.framework) for provisioning, `SecStaticCode` for signature; install via CoreDevice | Security.framework | No | ⏳ |
| 23 | Evidence Capture (guided case intake, 15 snapshots, syslog/OSLog/PCAP streams, screenshot, crash pull, manifest, SHA256SUMS) | `collector.py`, `case_workflow.py` | Ported collection engine over native services / CoreDevice | Yes | No | ⏳ |
| 24 | Network PCAP | `pmd3 pcap` | Native `com.apple.pcapd` client writing libpcap files | Lockdown service | No | ⏳ |
| 25 | Screenshot | `pmd3 developer dvt screenshot` | CoreDevice `capture screenshot`; Simulator `simctl io screenshot` | Yes | No | ⏳ |
| 26 | Crash report list / pull | `pmd3 crash ls/pull` | CoreDevice `info files` / `copy from --domain-type systemCrashLogs` | Yes | No | ⏳ |
| 27 | Processes | `pmd3 processes ps`, DVT proclist, CoreDevice list-processes | CoreDevice `info processes` | Yes | No | ⏳ |
| 28 | Launch app / open URL | DVT launch, Web Inspector launch | CoreDevice `process launch` / `process openURL`; Simulator `simctl launch` / `openurl` | Yes | No | ⏳ |
| 29 | Configuration / provisioning profiles | `pmd3 profile list`, `provision list` | CoreDevice `profile list`; native `misagent` | Yes | No | ⏳ |
| 30 | Diagnostics, battery, IORegistry, MobileGestalt | `pmd3 diagnostics …` | Native `diagnostics_relay` | Lockdown service | No | ⏳ |
| 31 | SpringBoard orientation / icon metrics | `pmd3 springboard …` | Native `springboardservices`; CoreDevice `orientation get` | Yes | No | ⏳ |
| 32 | Activation state, personalization identifiers | `pmd3 activation state`, `mounter query-personalization-identifiers` | Native lockdown / `mobile_image_mounter` | Lockdown | No | ⏳ |
| 33 | DVT telemetry (sysmon, energy, graphics, netstat, notifications, KDebug/CoreProfile) | `pmd3 developer dvt …` | 🔁 Instruments recordings via `xcrun xctrace record --device` (Activity Monitor, Network, Power Profiler, System Trace, Time Profiler…) | xctrace | No | ⏳ |
| 34 | RSD / RemoteXPC Bonjour discovery | `pmd3 bonjour rsd`, `remote browse` | Network.framework `NWBrowser` for `_remotepairing._tcp` / `_apple-mobdev2._tcp` | Network.framework | No | ⏳ |
| 35 | Safari/WebView tab list | `pmd3 webinspector opened-tabs` | ❌ Not migrated in 1.0 (see §6) | — | No | ⏳ |
| 36 | Bluetooth HCI capture | `pmd3 btlogger` | ❌ Not migrated in 1.0 (see §6) | — | No | ⏳ |
| 37 | DVT filesystem listing (`dvt ls /`), AFC media listing | `pmd3 developer dvt ls`, `afc ls` | AFC via native `com.apple.afc`; DVT listing ❌ (see §6) | Lockdown | No | ⏳ |
| 38 | Session Activity journal + manifest export | `operation_history.py` | Ported (`OperationJournal` actor) | Foundation | No | ⏳ |
| 39 | Workspace profiles import/export | `workspace_profile.py` | Ported (Codable + validation) | Foundation | No | ⏳ |
| 40 | Sanitized support bundle | `support_bundle.py` | Ported; native ZIP writer; includes redacted OSLog export | OSLog, Foundation | No | ⏳ |
| 41 | Action Palette (⌘K), keyboard shortcuts | `action_palette.py` | SwiftUI command palette + `Commands` | SwiftUI | No | ⏳ |
| 42 | Demo Mode | `demo_mode.py` | Ported; also drives deterministic UI tests | — | No | ⏳ |
| 43 | Connection diagnostics / Reconnect & Retry | `connection_diagnostics.py` | Ported to usbmuxd states; guided reconnect sheet | — | No | ⏳ |
| 44 | Scope & Safety page, Home page | GUI text | Redesigned in SwiftUI | — | No | ⏳ |
| 45 | Ecosystem Tools: go-ios adapter | `external_tools.py` | ❌ **Removed by request** — capability covered by #1 | — | **go-ios** | ⏳ |
| 46 | Ecosystem Tools: blacktop ipsw adapter | `external_tools.py` | ❌ **Removed by request** — capability covered by #1 | — | **ipsw** | ⏳ |
| 47 | Ecosystem Tools: idb Companion adapter | `external_tools.py` | Kept as an optional external provider | — | No | ⏳ |
| 48 | CLI: evidence collector, IPA inspector, local DDI | argparse scripts | `idt` Swift command-line tool (`collect`, `inspect-ipa`, `devices`, `ddi`) | — | No | ⏳ |
| 49 | Simulators | Not supported | **New**: simulator discovery, boot/shutdown, install, launch, screenshot, location, logs, open URL — clearly separated from physical devices | simctl | No | ⏳ |

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

(Updated during migration — see §7.)

## 6. Known limitations and features not reproduced

(Updated during migration.)

## 7. Migration log

- 2026-09-26 — Audit complete; migration branch `swift-native-migration` created.
- 2026-09-26 — Swift package (ToolkitCore, DeviceKit, ToolkitFeatures, idt CLI) complete with 170+ passing tests, including an end-to-end fake usbmuxd/lockdownd device, a real-Xcode toolchain check, and an opt-in real-simulator test. SwiftUI app builds with zero warnings; GUI verified by in-app window rendering (Demo Mode) at default and minimum sizes. XCUITests written but blocked locally by macOS Automation Mode authentication.
- Remaining: README/docs rewrite, GitHub Actions for Swift/Xcode, release packaging, physical-device verification of the native lockdown services, removal of the Python implementation and go-ios/ipsw references, final verification pass.
