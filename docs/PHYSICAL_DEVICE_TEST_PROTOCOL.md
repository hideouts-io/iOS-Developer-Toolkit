# Physical-device test protocol

Use this protocol only with an iPhone or iPad that you own or are authorized to test. Keep raw UDIDs, device names, logs, captures, backups, screenshots, coordinates, and case evidence out of issues, pull requests, and compatibility reports.

## Required test context

Record these non-secret facts locally before testing:

* toolkit commit and application version;
* source or packaged build and native architecture;
* macOS, Xcode, Python, and `pymobiledevice3` versions;
* iPhone or iPad model, iOS version, build, and USB or network connection;
* whether Developer Mode, a DDI, and an RSD tunnel are expected for the tested workflow.

Do not record the raw UDID in a shared report. The application's Real-Device Compatibility view stores a one-way device fingerprint for local comparisons.

## Stage 1 — connection readiness

1. Connect the unlocked device directly with a known data-capable cable. Avoid hubs for the first test.
2. Accept **Allow accessory to connect** on macOS when shown.
3. Tap **Trust** on the device and enter its passcode when shown.
4. Confirm that Finder or Xcode lists the device.
5. Run `pymobiledevice3 usbmux list` in the project environment. Expect one JSON device record.
6. Run `xcrun devicectl list devices`. Expect the same device to be `available (paired)`.
7. Open the toolkit. Expect the physical device in the picker, an **Authorized device connected** banner, and a **devices-available** Connection diagnostic.
8. Disconnect and reconnect once. Expect the picker and banner to recover without restarting `usbmuxd`, deleting pairing records, or requiring `sudo`.

Failure boundaries:

* absent from the macOS USB tree: cable, port, lock state, or accessory-authorization problem;
* present in USB but absent from usbmux: pairing or Apple Mobile Device service problem;
* present in usbmux but absent from CoreDevice: Xcode/CoreDevice state problem;
* present in both CLIs but absent from the toolkit: application discovery regression; create a sanitized support bundle.

## Stage 2 — read-only application checks

1. Run the full **Capability Matrix** and save a local compatibility observation.
2. Verify that each row distinguishes ready, needs attention, unavailable, blocked, not tested, and not applicable.
3. Run finite read-only Command Center presets for device information, battery, date, mounted images, and installed applications.
4. Open Unified Log, syslog, and DVT OSLog separately. Confirm that each stream starts, receives data, pauses, filters, stops, and offers an explicit raw-save decision.
5. Run app inventory and local IPA inspection without installing or uninstalling an app.
6. Create a sanitized support bundle. Inspect the ZIP and confirm that it contains no raw device identity, command output, capture, log, credential, or user-entered value.

Expected result: every command either completes with bounded output or remains visibly identified as a stream with an enabled Stop control. No read-only check changes device state.

## Stage 3 — developer-service checks

Perform this stage only when Developer Mode is intentionally enabled.

1. Enable Developer Mode through iOS Settings and complete the required restart.
2. Mount the appropriate personalized DDI or use the Xcode candidate DDI when the selected workflow explicitly calls for it.
3. Re-run only the Developer Mode, DDI, RSD, DVT, and CoreDevice capability rows.
4. Verify DVT directory listing, application listing, and one bounded developer-service snapshot.
5. Start and stop DVT network activity. Confirm that the UI treats it as a stream rather than a finite snapshot.

Expected result: readiness changes are attributed to the correct layer. A DDI success does not imply that an RSD tunnel or every DVT service is available.

## Stage 4 — opt-in state-changing checks

These checks are not required for merge readiness. Run only when their device effect is acceptable and the displayed target is correct.

* **Location Lab:** set a harmless test coordinate, verify the visible simulated state, then use Clear and confirm that no simulation process remains.
* **IPA sideload:** inspect an eligible development-signed IPA first, install it with the device-bound acknowledgement, verify inventory, then uninstall only if planned.
* **Encrypted backup:** use protected local storage, verify the existing encryption state, understand that enabling backup encryption persists on the device, and confirm the resulting backup independently.
* **PCAP/RVI:** capture a short authorized trace, stop cleanly, open the file in an independent packet analyzer, and document encrypted-payload limitations.
* **Evidence case:** create a disposable case, collect one bounded artifact, finalize it, and independently verify its SHA-256 manifest.

## Completion record

Mark each stage as **passed**, **failed**, **not applicable**, or **not tested**. For failures, record the exact layer, command or button, exit status, sanitized error, and whether the failure reproduces in both the source and packaged app. Never convert **not tested** into a compatibility claim.
