# Swift and Python feature comparison

This comparison uses the Swift and Python source inspected on October 9, 2026. The Swift reference was `cf9ac8e6ae5e51a083875fa3a8edbfdccb705cdd` plus its existing local edits; the original Python capability baseline was `11602f7f1d2379702c7f53478aab696d99820e2d`. The Python implementation column describes the `codex/python-swift-parity` review branch, based on `2d475039230900c97e8d789806bbe2a9cc63c89a`, rather than a published release. Source inspection establishes available workflows, not successful operation on every supported device.

## Differences and Python implementations

| Capability | Python before this comparison | Swift capability | Python implementation |
|---|---|---|---|
| Firmware workspace | Upstream restore commands and help | Dedicated catalog, inspection and installation workflow | New Firmware page with explicit targets, operations and reviewed installation plans |
| Apple firmware catalog | No dedicated browser | Current Apple releases and one-day cache | Model-specific release selection, a validated one-day private cache and explicit fresh refresh |
| Local IPSW inspection | No dedicated inspector | ZIP64 BuildManifest, supported models and variants | Bounded ZIP64 manifest inspection, exact board/variant selection |
| Firmware integrity | No dedicated workflow | Catalog SHA-1 and local SHA-256 | Local integrity action; catalog comparison rejects mismatches; SHA-256 records byte identity |
| Signing checks | No dedicated workflow | Remote release and device-bound installation preflight | Bounded HTTPS range manifest reading and fresh board/build/variant signing checks; the installer obtains its device-specific ticket through the separately patched HTTPS helper |
| Firmware downloads | No dedicated workflow | Download, progress, resume and cancellation | Bounded downloads with progress, explicit stop and validated resume state |
| Recovery/DFU | Command surface only | Query, watch, enter and exit | Exact helper-target queries; opt-in watch; confirmed enter/exit operations |
| Update versus Restore | Upstream command parameters | Reviewed Update or destructive Restore | Exact immutable plans bind target, variant, IPSW and helper hashes; Update never substitutes Erase |
| Installation lifetime | No dedicated protection | Progress and termination protection | Progress stages, blocked cancellation/close/Quit and macOS termination protection during installation |
| Firmware helper distribution | No packaged helpers | Separate universal `idevicerestore` and `irecovery` | Pinned universal helper recipe, licenses, file manifest, source archive and SBOM integration |
| Restore-helper network transport | No bundled helper transport | Native helper requests during firmware installation | HTTPS-only transport with certificate/hostname verification and explicit deadlines; helpers require schema 2, exact patch/recipe pins and matching file hashes before execution |
| Local Security Analysis | External MVT integration | Built-in investigator workspace | New Security Analysis page alongside the existing external MVT provider |
| Threat intelligence | External provider only | STIX/custom indicators and curated updates | Local typed imports, explicit curated commit-pinned updates, provenance and hashes |
| Artifact scanning | External provider only | Backup, sysdiagnose, files and imported outputs | Bounded read-only scans, including logical paths from decrypted backups and MVT outputs |
| Findings and reports | External provider output | Correlation, configuration review, investigator metadata and exports | Exact typed matches, conservative configuration leads, coverage warnings and private JSON/CSV/HTML exports with optional redaction |
| Simulator destinations | External idb/handoffs | First-class simulator workflows | New Simulators page with an independent UUID selector |
| Simulator lifecycle | External tooling | Boot, show, shutdown, appearance and erase | Native `simctl` operations; erase requires typed confirmation |
| Simulator apps and logs | External tooling | Inventory, `.app` install, launch, terminate, uninstall, URL and logs | Validated simulator `.app` installs and native operations; raw-spooling log viewer reused |
| Simulator location | Physical-device Location Lab only | Set, GPX playback and clear | Fixed/recorded timing playback, progress/cancellation and tracked-target Stop & Clear/close handling |
| Reachable CoreDevice targets | USB picker and individual handoffs | USB/network discovery | Independent validated CoreDevice picker in Xcode Tools; targets are explicit for every command |
| Physical `.app` installation | IPA workflow | Native `.app` installation | CoreDevice `.app` validation and installation in Xcode Tools; original IPA workflow retained |
| App-row launch and built-in filtering | Inventory, sizes and uninstall | Launch and include-system-app controls | Launch selected app and Include built-in apps controls |
| Instruments and sysdiagnose | Help/advanced commands | Guided native recordings and collection | Seven recording templates, explicit durations/targets/destinations, sysdiagnose, artifact opening and Logging XML export |
| Native log artifacts in cases | Streaming syslog/DVT OSLog | `.logarchive` and Logging trace collection | Optional OSLog archive and Instruments Logging artifacts included before case manifest and hashes |
| Local developer images | Downloaded personalized image and fixed local Cryptex candidate | Inventory, selected folders and identity matching | Xcode/local folder inventory, exact legacy version/build and personalized model/chip/board checks; pinned payload bytes and verified HTTPS personalization |
| CoreDevice developer images | Handoff only | Preparation, preferred DDI and host update | Explicit CoreDevice DDI mechanism, preferred-image inspection and host update without cleanup |
| Apple tool reference | pymobiledevice3 help | Apple-native help/reference and drift | Xcode Tools native help; existing 59-route PMD reference and 49-preset live checks retained |
| Navigation | Flat workspace list | Grouped sidebar and visibility/readiness shortcuts | Grouped sidebar, hide/show shortcut, refresh-readiness shortcut, palette destinations and session records |
| Workspace profiles | Python schema 1 | Swift schema 2 | Previewed Swift-to-Python translation for representable settings; native export also preserves new app/evidence preferences |

## Existing shared capabilities

Both editions already provided the core device/DDI, readiness, apps, backup, IPA, physical location, live-log, guided command, evidence, external-tool, help and safety workspaces. Python already included offline map routes and saved places, raw log preservation with filtering/findings, case hashes, action palette, keyboard reference, connection diagnostics, demo mode, support bundles, session activity and shareable local workspace profiles. These were retained rather than reimplemented.

Python continues to use PySide6 and the pinned pymobiledevice3 backend, including its DVT and RemoteXPC capabilities. Swift uses SwiftUI and its own native clients and Apple-tool adapters. Equivalent Python workflows do not require replacing those language/framework or protocol choices. Separately installed MVT, UFADE, go-ios, idb and ipsw remain external providers; their original adapters and provider boundaries are retained on this review branch.

## Remaining differences and validation boundaries

- Swift profile settings with no exact Python representation are rejected with an explanation. These include automatic/unspecified DDI mechanisms, an absent or unmappable guided action, the Activity-only destination and capture duration zero. Imports do not invent a replacement action, change targets or start commands. Native DDI profiles require choosing the local folder after import because profiles omit local paths.
- Preflight signing requests use synthetic ECID/nonces to check board/build eligibility. A plan is separately bound to the selected physical target; the preflight result does not establish acceptance of that device's actual ticket. The helper obtains its actual device-specific ticket during installation.
- The application retains its macOS 13 floor. The optional universal firmware helpers have their own macOS 14 floor and are unavailable on macOS 13. Bundle verification applies the original strict application floor to all other native files, and validates the exact two manifest-bound helper paths separately.
- Local review-branch checks pass 194 Python tests, the 206-action offscreen GUI smoke check, all 49 guided live-help routes, compile/CLI validation and the strict documentation build. The internal backend command reports pymobiledevice3 11.15.1, and shell syntax checks pass for the build scripts. Earlier implementation checks covered all 17 destinations at two window sizes, installed Xcode image inspection and read-only public Apple catalog/range responses. Synthetic artifacts used in parsing tests establish parser behavior only; local checks are not hosted candidate-revision CI results.
- Separate implementation checks built the universal helpers and exercised both architectures on the Apple Silicon host. Their schema-2 inventory, 1,595-file source archive, extracted offline source validation and native SBOM records passed. Compiled host-only transport checks rejected HTTP, untrusted certificates, wrong hostnames and downgrade redirects; stalled connections stopped after approximately 30 seconds. These checks did not obtain a real device ticket or install firmware, and do not establish a complete frozen application build.
- Successful TSS acceptance, full IPSW download/resume, recovery on hardware, Update/Restore, device DDI mounting, network pairing, simulator mutations, real app installation, live capture and real evidence acquisition require the applicable authorized runtime checks. No firmware was written or device state changed during this implementation.
- An earlier local Apple Silicon frozen application build was blocked by a host pipe-readiness failure in Nuitka's import-detection subprocess. Complete frozen application/signing checks on Apple Silicon and Intel, plus hosted code scanning at the review revision, remain required. The universal helper build is separate from the full application build; no release was published.

Use the [physical-device protocol](PHYSICAL_DEVICE_TEST_PROTOCOL.md) for bounded runtime checks and [release verification](release-verification.md) for packaged builds.
