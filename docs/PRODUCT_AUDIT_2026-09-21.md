# Product audit — 2026-09-21

## Executive assessment

iOS Developer Toolkit has a stronger foundation than its small version number suggests. It is a macOS PySide6 desktop application that turns a deliberately curated subset of `pymobiledevice3`, Xcode/CoreDevice, Developer Disk Image, RVI, backup, and evidence-preservation workflows into guided operations. Its differentiators are its explicit authorization boundaries, local-first evidence handling, typed acknowledgement for device-changing work, capability matrix, device compatibility observations, investigation-oriented live-log windows, and release artifacts with SBOMs and provenance.

Its primary product risk was reliability at the first screen. At audit start, the application imported the MobileBackup2 transport implementation while constructing the desktop UI, so a slow or damaged third-party transport import could prevent the interface from becoming available even though backup was not being used. Separately, `DeviceScanner` only consumed `QProcess` output from readiness signals and did not consume bytes still available when the child exited. That created a confirmed race: a packaged build could successfully run `pymobiledevice3 usbmux list` but parse an empty discovery buffer. The P0 implementation delivered with this audit moves transport imports into the backup worker and drains completion output before parsing; it also adds a deterministic fast-exit regression test.

The correct next investment is therefore a **reliable startup and device-discovery foundation**, not another device command. It makes the existing workbench usable for beginners, gives experts dependable process semantics, and establishes the abstraction needed before further QProcess-heavy workflows are added.

## What exists today

The product has twelve workspaces: Home, Device & DDI, Capability Matrix, Location Lab, Live Logs, Command Center, Installed Apps, Backup, Sideload IPA, Evidence Capture, Man Pages, and Scope & Safety. It currently provides 49 declarative guided command presets, a live-help/command-drift check, DDI mounting, RSD/CoreDevice/DVT checks, GPX location simulation with cleanup, separate Unified/syslog/oslog windows, installed app inventory, encrypted MobileBackup2 workflow, UFADE setup guidance, IPA inspection and installation, RVI/PCAP and artifact collection, guided case intake, support bundles, compatibility history, and keyboard-first navigation.

The repository is a Python 3.10+ PySide6 project with a bundled `pymobiledevice3` runtime model. `ios_developer_toolkit/app.py` is a 5,600+ line `MainWindow`, while domain modules cover capability probing, collectors, live logs, location testing, IPA inspection, support bundles, and device compatibility. CI runs unit tests, compile checks, CLI help checks, and a headless GUI smoke test on macOS. Tagged release CI produces Apple Silicon and Intel bundles, CycloneDX SBOMs, checksums, and GitHub attestations. The app is ad-hoc signed, not Developer ID signed or notarized.

## Strengths worth protecting

* The command catalog is declarative, reviewed, parameter-validated, and avoids feeding guided fields into a shell.
* The capability matrix makes the iOS developer stack legible: trust, Developer Mode, DDI, RSD, CoreDevice, DVT, lock state, and Web Inspector are distinguished rather than collapsed into “device failed.”
* The safety model appropriately classifies host writes, device changes, and high-impact operations, and binds acknowledgement phrases to the selected target.
* Live Logs is notably better than a terminal wrapper: it separates raw capture from rendered filtering, supports annotations as analyst claims rather than facts, preserves hashes, and explains capture boundaries.
* Evidence cases use restrictive local permissions, store a local authorization acknowledgement, and state their chain-of-custody limits plainly.
* The sanitized support bundle intentionally excludes identifiers, pairing material, raw captures, and user-entered values.
* The product already has a real-device compatibility observation format that fingerprints a device rather than retaining its raw UDID.
* Release engineering is unusually good for a young desktop project: dual architecture builds, SBOMs, third-party notices, checksums, and build provenance are present.

## Weaknesses and user impact

| Finding | User impact | Priority |
| --- | --- | --- |
| GUI startup imported `pymobiledevice3.lockdown` through `backup_worker` before Backup was opened. | A failure in one optional subsystem could block all workflows. Fixed in this audit by moving transport imports to the worker execution path. | Resolved P0 |
| `DeviceScanner` did not drain final `QProcess` stdout/stderr in its completion handler. | A connected device could be invisible in the packaged UI despite the bundled CLI returning valid JSON. Fixed with completion-time draining and a real fast-exit QProcess regression test. | Resolved P0 |
| The repository pinned `pymobiledevice3==10.11.0` while a clean Dependabot PR existed for 11.12.4 and upstream had newer releases. | The app missed modern iOS tunnel fixes and could present stale command assumptions. Fixed with a validated upgrade to 11.15.1: the full test suite, GUI smoke test, CLI discovery, and all 49 live-help routes passed. | Resolved P0 |
| `MainWindow` owns dozens of process/buffer/timer lifecycles. | Completion, cancellation, timeout, and output handling can diverge across workspaces; the scanner defect is evidence of that risk. | P1 |
| Release-only packaging is validated only after a tag is pushed. | A frozen-app regression can escape pull-request CI. | P1 |
| Source `macos/Info.plist` exposes an older version than `pyproject.toml`; release CI corrects it later. | Local app testing can be confusing and screenshots can show stale metadata. | P1 |
| The test suite is mainly pure-function/unit coverage and a structural GUI smoke test. | It now exercises a fast-exit discovery result and launch failure, but still needs shared operation cancellation/relaunch coverage beyond those paths. | P1 |
| The first-run experience assumes familiarity with DDI, RSD, and CoreDevice. | Beginners receive good instructions, but not a single coherent “make my device ready” decision flow. | P1 |
| The README is extensive but is the dominant documentation surface. | It is difficult to keep operational recipes, scope boundaries, architecture, release verification, and contributor guidance discoverable. | P2 |

## Beginner UX audit

The first screen has strong visual hierarchy and a useful six-step map, but it asks a new user to understand multiple Apple service layers before confirming the one prerequisite that matters: “Can this Mac see and trust my device?” A first-run assistant should remain optional, but should reduce the path to: connect → unlock/trust → verify connection → enable Developer Mode if needed → choose whether a task needs a DDI → run a safe first action.

The toolkit should keep its advanced vocabulary, but display it progressively. “RSD tunnel” is useful evidence for an expert; for a beginner it should be introduced as the iOS 17+ developer connection path, with the exact observed status and a one-click non-destructive recheck. The current reconnect guidance is careful not to restart SIP-protected/root-owned services, which is correct and should remain a hard boundary.

## Expert UX audit

Experts need less prose and better state correlation. The next UI layer should expose a compact operation record for every command: target, transport, exact argv, start/end time, exit status, timeout/cancel reason, output paths, hashes, and prerequisite states. Existing Live Logs and Evidence Capture show the right pattern, but it is not shared by Command Center, DDI, app, and backup operations. Experts also need a clear distinction between an upstream command being available in live help, a device service being advertised, and a particular operation having completed successfully.

## Missing product categories

The toolkit intentionally does not need to become an IDE, jailbreak suite, MDM, spyware scanner, signing service, or remote device farm. It can, however, become more useful in five bounded areas:

1. A shared diagnostic/remediation engine that maps an operation to explicit prerequisites and reruns only the checks relevant to that operation.
2. A centralized operation lifecycle service for QProcess/subprocess work, with start, final-drain, cancellation, timeout, structured result, and copyable support record semantics.
3. A project-oriented developer workflow that can hand off to Xcode tools for test destinations, `.xcresult` inspection, and selected `devicectl` operations without pretending to replace Xcode.
4. A scoped ecosystem handoff layer: MVT for consented backup analysis, `ipsw` for firmware research, and configurable external tool adapters rather than bundled forks.
5. A device-lab/compatibility contribution path that can export redacted, opt-in capability observations and reproduce upstream `pymobiledevice3` bugs with a standard report.

## Architecture and maintainability audit

The project has good domain modules, immutable data classes, clear validation errors, and a runtime wrapper that makes frozen builds invoke internal workers safely. The central weakness is orchestration concentration. `MainWindow` manages process ownership, byte buffers, timers, error mapping, UI enablement, and output rendering for many unrelated workflows. That makes process behavior difficult to test and encourages near-duplicate cleanup logic.

The target architecture is not a wholesale framework rewrite. Keep PySide6 and the current declarative catalog. Introduce small domain-level operation records and a reusable Qt process controller, then migrate one workflow at a time. UI builders should consume typed readiness and operation results rather than parse child-process bytes. The first change in this direction is to ensure GUI import paths do not import transport-specific worker dependencies and that discovery always consumes terminal output.

## Reliability, testing, and release audit

The existing CI/release pipeline is a substantial strength. Its gap is placement: tagged releases build the frozen app, but ordinary pull requests only test source. Add a scheduled or opt-in release-smoke workflow that builds one native frozen artifact and verifies the internal CLI, worker, GUI smoke path, bundle metadata, license inventory, and SBOM. Keep both full architecture builds for releases.

The highest-value test additions are deterministic process-lifecycle tests: a process that writes valid discovery JSON and exits before readiness delivery; non-zero process errors with stderr only available at exit; cancellation while an operation is active; and clean relaunch without inheriting stale state. A physical-device matrix should remain opt-in, explicitly labeled, and never required to merge a change.

## Security, privacy, and distribution audit

The app’s local-first posture is credible: no analytics, no cloud account, and support bundles are reviewed for data minimization. Improve it by surfacing a privacy inventory in the UI, documenting retention paths by workflow, and requiring review before any future export/upload integration. Do not collect telemetry by default.

Distribution remains the largest trust hurdle. A Developer ID certificate and notarization are unavailable without an Apple Developer Program membership, so the correct present posture is transparent ad-hoc signing, dual architecture artifacts, checksums, SBOMs, provenance, source reproducibility, and precise Gatekeeper instructions. Do not imply that ad-hoc signing makes the app generally trusted. When a signing identity becomes available, add notarized Developer ID releases and an automated post-notarization assessment step.

## Ecosystem map and integration strategy

| Project/tool | What it offers | Recommendation |
| --- | --- | --- |
| `pymobiledevice3` | Core cross-platform protocol library/CLI: discovery, tunnels, DDI/DVT, logs, PCAP, backups, apps, Web Inspector. | Primary dependency. Upgrade deliberately, keep live-help drift checks, and contribute minimal reproducible protocol or CLI fixes upstream. |
| Xcode `devicectl`, `simctl`, `xctrace`, `rvictl` | Apple-supported macOS device, simulator, trace, and RVI tooling. | Prefer for macOS-native actions; show exact preconditions and hand off rather than reimplementing Xcode. |
| `libimobiledevice` | Mature cross-platform device library/CLIs for backup, syslog, crash reports, screenshot, pairing, and image mounting. | Optional external adapter only. It overlaps with the current core and adds LGPL/GPL packaging complexity. |
| `go-ios` | Cross-platform static CLI/library, JSON output, app/UI test and accessibility tooling, optional REST API. | Learn from its JSON and device-lab design. Evaluate a user-configured adapter after a stable operation framework; do not bundle a second protocol stack now. |
| Facebook `idb` | Simulator/device automation via a macOS companion and remote client. | Do not embed. Offer documented interoperability for teams already using it; its private-framework and companion model is a separate product surface. |
| MVT | Consented mobile-forensics analysis of iOS backups and IOC checking with its own forensic scope/license. | Add a guided handoff/export later, not an embedded scanner. Do not make “clean” claims or weaken its warning model. |
| `blacktop/ipsw` | Firmware/OTA research, device database, kernel/dyld analysis. | Document as an external firmware-research companion. Do not turn this GUI into an IPSW reverse-engineering suite. |

Upstream contribution candidates are concrete: report the fast-exit scanner packaging behavior as a Qt application lifecycle pattern if it reproduces outside this project; test the current `pymobiledevice3` upgrade against the toolkit command catalog; and offer redacted iOS/macOS compatibility findings to its issue tracker when a command/service regression is isolated.

## Competitive positioning

| Need | Toolkit position | Better companion | Product response |
| --- | --- | --- | --- |
| Developer readiness | Strong guided DDI/RSD/DVT visibility | Xcode Device Hub | Make connection and prerequisites dependable first. |
| Raw protocol coverage | Strong through `pymobiledevice3` | `pymobiledevice3`, `go-ios`, `libimobiledevice` | Do not duplicate every CLI command; curate and expose evidence. |
| Simulator/device automation at scale | Limited | `idb`, Xcode, Appium/WDA ecosystems | Add safe handoffs, not a competing farm. |
| Backup forensics | Bounded acquisition/evidence support | MVT | Build consented MVT handoff with limitations, not a compromise verdict. |
| Firmware research | Minimal | `ipsw` | Offer links/recipes and artifact provenance only. |
| Network capture | Strong macOS RVI workflow | `rvictl` + tcpdump/Wireshark | Continue to clarify encrypted-payload and whole-stack limits. |

## Prioritized roadmap

### P0 — make the existing product dependable

* Remove eager transport imports from desktop startup; load backup transport only in the backup worker.
* Drain final QProcess output for device discovery and add a deterministic fast-exit test.
* Keep the pinned `pymobiledevice3` runtime current through isolated upgrade checks, full tests, GUI smoke testing, and command-catalog live-help validation. The audit implementation validates and pins 11.15.1.
* Add a connection diagnostic record that reports whether discovery failed to launch, returned malformed data, returned zero devices, or returned a selectable device. The audit implementation now provides this record in Device & DDI and the sanitized support bundle without raw discovery output or device identity.

### P1 — turn diagnostics into a coherent workbench

* Continue migrating finite subprocess workflows to the reusable operation controller and typed `OperationResult`. Device discovery and Man Pages now share final-drain, timeout, cancellation, launch-failure, clean-relaunch, and structured completion semantics; command drift, DDI, backup, apps, and capture remain incremental migrations.
* Make a contextual readiness pane for the selected action, with one-click scoped rechecks and copyable remediation.
* Maintain the opt-in physical-device compatibility protocol and its explicit USB, usbmux, CoreDevice, developer-service, privacy, and state-changing test boundaries. A pre-release dual-architecture frozen-artifact smoke workflow is now present. The release builder rejects any bundled Mach-O whose minimum macOS version is newer than the advertised 13.0 floor or lacks the native release architecture.
* Generate concise changelog/release notes from tested behavior. Source, bundle, citation, packaging, and third-party-source metadata drift is now covered by automated tests.
* Add Xcode project/device handoffs: selected `devicectl` discovery, RVI status, and `.xcresult`/`xctrace` opening without reimplementing those formats.

### P2 — deepen expert workflows without scope creep

* Add per-operation history, structured output manifests, and a universal command/action palette that only exposes eligible operations.
* Implement a guided MVT backup-analysis handoff with explicit consent, no password persistence, output isolation, and no “clean device” conclusion.
* Add optional user-configured adapters for `go-ios`, `idb`, and `ipsw`, each with executable provenance and version display.
* Publish a small documentation site split into quick start, architecture, safety, troubleshooting, release verification, and contributor paths.

### P3 — ecosystem growth and scale

* Opt-in anonymized compatibility contribution workflow with a local preview and explicit export confirmation.
* Team/workspace import-export that remains local by default.
* Notarized Developer ID distribution when an eligible signing identity exists.
* Optional device-lab integration through external services, never a mandatory cloud account.

### Do not build

* Jailbreak, passcode bypass, root filesystem acquisition, code-signing circumvention, or credential/profile theft features.
* A permanent or stealth location-changing service. Location testing must remain explicit, visibly tracked, and clearable.
* A general “run any destructive command” button or an automated recovery/restore/erase path.
* An embedded MVT-like compromise verdict or claims that lack of findings proves a device is safe.
* A cloud telemetry/sync system for device identifiers, logs, captures, backups, or case records.
* A second bundled iOS protocol stack merely for feature-count parity.

## Single best next thing to build

**Reliable startup and lossless device discovery.** This is the right first build because the device picker is a dependency for nearly every existing workspace, there is direct evidence of a released UI/CLI disagreement, and the current eager import makes a non-backup dependency capable of blocking the app before the user can receive diagnostics. It improves both personas: beginners see a usable application and accurate connection state; experts get predictable process results that can later underpin every operation.

## Implementation plan

1. Extract backup request/event schema validation into a dependency-free `backup_protocol` module. The desktop UI and tests import that module; only the backup worker imports the MobileBackup2 transport implementation.
2. Make `DeviceScanner` consume any remaining stdout/stderr synchronously in its completion handler before evaluating exit status or parsing JSON.
3. Add tests for the backup protocol and a real, short-lived QProcess whose valid JSON is available only after it has exited.
4. Update the README’s troubleshooting and architecture material to explain the connection behavior and the no-sudo boundary.
5. Validate `pymobiledevice3` 11.15.1 in the project environment, then run the full 77-test suite, 89-action headless GUI smoke, CLI discovery, and every command-catalog live-help route. Review the diff before handoff.

## Continuous improvement log

| Date | Improvement | Verification | Follow-up boundary |
| --- | --- | --- | --- |
| 2026-09-21 | Moved backup transport imports out of desktop startup; fixed terminal output draining for usbmux discovery; added privacy-safe connection diagnostics. | 75 tests, headless GUI smoke, source launcher verification, and deterministic QProcess tests passed. | Real-device discovery remains separately opt-in and time-specific. |
| 2026-09-21 | Upgraded the pinned `pymobiledevice3` runtime to 11.15.1 and reconciled source, bundle, citation, packaging, and third-party source metadata. | CLI version reports 11.15.1; 77 tests and all 49 catalog live-help routes passed locally. | The next packaged artifact must be built by CI before distribution. |
| 2026-09-21 | Added a dual-architecture frozen-artifact smoke workflow, CI command-catalog verification, and a native Mach-O minimum-version gate. | A clean local build passed its full 77-test suite and produced a signed arm64 app; the host's Homebrew Python targets macOS 26, so the new 13.0 gate correctly stopped that incompatible local artifact before ZIP creation. | GitHub Actions runs with `MACOSX_DEPLOYMENT_TARGET=13.0`; its first Apple Silicon and Intel runs remain required before distribution. |
| 2026-09-21 | Added a reusable typed finite-process controller and migrated device discovery to it. | Real child-process tests cover terminal stdout/stderr, fast completion, launch failure, cancellation, timeout, and one-result semantics; the full suite now contains 81 tests. | Migrate other finite QProcess workflows incrementally; long-running streams retain their separate lifecycle. |
| 2026-09-21 | Made live-help drift checks accept successful help emitted on either standard output or standard error. | A clean GitHub runner exposed two false option mismatches while the same pinned CLI passed locally; the channel-specific regression test now preserves strict option matching without assuming a help stream. | Re-run CI on a clean runner and retain failure for genuinely absent routes or options. |
| 2026-09-21 | Added an opt-in physical-device protocol with staged read-only, developer-service, and state-changing checks. | The current host check found no Apple mobile USB device, no usbmux device, and no CoreDevice result, so no physical compatibility claim was made. | Run the protocol with an authorized connected device and retain identifiers and raw evidence locally. |
| 2026-09-21 | Added contextual readiness for every guided command and corrected support-bundle capability aggregation. | Command-specific tests cover untested, ready, not-applicable, and attention states; the GUI smoke verifies the new control by stable object ID. | Readiness remains a point-in-time local probe and never substitutes for an actual command result. |
| 2026-09-21 | Migrated Man Pages live help to the shared finite-operation controller and normalized styled CLI help for command-drift checks. | The GUI smoke now completes a real live-help request; controller relaunch tests reject stale output, and ANSI-split option tokens remain strictly verifiable. | Sequential command drift and other finite workflows remain incremental migrations. |
| 2026-09-21 | Corrected the macOS compatibility gate and bounded native-build timing. | The first clean dual-architecture run proved arm64 produced a macOS 11-compatible executable, which is compatible with the advertised macOS 13 floor; Intel exceeded the original 45-minute job limit. | Re-run both native builders with reusable Nuitka caches and a 90-minute cap before merging. |
| 2026-09-21 | Expanded compatibility validation from the launcher to every bundled Mach-O and pinned a genuinely compatible Qt line. | PySide6 6.11.2 wheel filenames advertise macOS 13, but direct `otool` inspection found Shiboken load commands requiring macOS 15; PySide6 6.9.3 Shiboken binaries declare macOS 12. | The dual-native CI build must pass the full-bundle architecture and deployment-floor scan before release. |

## Research sources

* Apple: [Developer Mode guidance](https://developer.apple.com/documentation/xcode/enabling-developer-mode-on-a-device), [Xcode command-line tools](https://developer.apple.com/documentation/xcode/xcode-command-line-tool-reference), and [RVI packet capture](https://developer.apple.com/documentation/network/recording-a-packet-trace).
* `pymobiledevice3`: [repository and documentation](https://github.com/doronz88/pymobiledevice3), [iOS 17+ tunnel guide](https://github.com/doronz88/pymobiledevice3/blob/master/docs/guides/ios17-tunnels.md), and [protocol-layer overview](https://github.com/doronz88/pymobiledevice3/blob/master/misc/understanding_idevice_protocol_layers.md).
* Complementary tools: [libimobiledevice](https://github.com/libimobiledevice/libimobiledevice), [go-ios](https://github.com/danielpaulus/go-ios), [Facebook idb](https://github.com/facebook/idb), [MVT](https://github.com/mvt-project/mvt), and [ipsw](https://github.com/blacktop/ipsw).
