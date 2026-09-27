# Physical-device test protocol

The native lockdown services in version 1.0 are verified against a protocol-accurate simulated
device and macOS's real device service, but not yet against physical hardware. This protocol is
how to verify them. Use it only with an iPhone or iPad you own or are authorized to test.

Keep UDIDs, device names, logs, captures, backups, screenshots, coordinates, and case evidence out
of issues and pull requests. Report results as **passed**, **failed**, **not applicable**, or
**not tested** per step — never turn “not tested” into a compatibility claim.

## Record first (locally)

- App version (**iOS Developer Toolkit › About**) and whether it is a release or a source build.
- Mac model and architecture, macOS version, Xcode version (or “no Xcode”).
- Device model, iOS version and build, USB or network connection.
- Developer Mode on or off.

## Stage 1 — discovery and trust (no Xcode needed)

1. Connect the unlocked device directly with a data cable; approve **Allow accessory** on the Mac.
2. Tap **Trust** and enter the passcode on the device.
3. Expect the device under **Physical Devices** within a few seconds, without pressing Refresh.
4. Disconnect it. Expect it to disappear (or show as network-only if Wi-Fi sync is on) without a
   restart. Reconnect and expect it back.
5. Connect a second device. Expect both listed separately; switching the selection must never
   redirect an operation that is already running.
6. Before trusting a new device, expect the Device page to say it is not trusted, with steps.

## Stage 2 — read-only checks

1. **Readiness Check**: every row shows ready, attention, unavailable, or not applicable, with a
   next step.
2. **Device** page: name, model, iOS version, build, Developer Mode status, and connection match
   *Settings › General › About*.
3. **Live Logs**: start **Unified** and **Classic syslog** separately. Each receives lines, pauses,
   filters (literal and regex), stops, and exports raw and filtered logs. Mark a finding and export
   an evidence bundle; verify it with `shasum -a 256 -c SHA256SUMS.txt`.
4. **Apps**: the list includes sizes. **Actions**: battery, diagnostics, IORegistry,
   provisioning profiles, crash report list, Media folder listing, mounted images.
5. **Actions › Packet capture**: capture 30 seconds and open the `.pcap` in Wireshark or with
   `tcpdump -r`.
6. **Actions › Safari and web view tabs** with Web Inspector on and a page open in Safari: the
   page title and address appear. With Web Inspector off: “Safari Web Inspector did not answer”.
7. **Create Support Bundle…**: unzip it and confirm it has no names, identifiers, paths, or
   captured content.

## Stage 3 — with Xcode and Developer Mode

1. **Developer image** card: note the state and details (iOS, build, model, chip/board). Expect
   *Personalization required* or *Available* on iOS 17+, *Available* or *Missing* on iOS 16 and earlier.
2. **Mount Developer Image** with *Mount with: Built-in*. Expect *Mounted*. Record whether Apple
   personalization was needed. Then **Mount Developer Image** again: nothing should be uploaded.
3. **Unmount**, then mount again with *Xcode device service*. Expect *Mounted*.
4. Lock the device and mount: expect “The device is locked.” With Developer Mode off: expect
   *Needs attention* and no upload.
5. iOS 16 or earlier: add a folder with the matching `DeveloperDiskImage.dmg` and `.signature`,
   mount, and confirm *Mounted* at `/Developer`.
6. Screenshot, running processes, lock state, launch an app, open a URL.
7. An Instruments recording of 10 seconds; open the `.trace` in Instruments.
8. On iOS 16 or earlier (after mounting the developer image): set and clear a location through
   the legacy service.

## Stage 4 — changes (opt in, one at a time)

- **Location Lab**: set a harmless coordinate, confirm it in Maps, then **Clear**. Quit the app with
  a location set and confirm it is cleared.
- **Install App**: inspect a development-signed `.ipa`, install it (`RUN` confirmation), confirm it
  launches, remove it (`IRREVERSIBLE` confirmation).
- **Backup**: an encrypted backup to an empty folder, then an incremental one to the same folder.
  Confirm it with Finder's backup list or a separate tool. Enabling encryption changes the device
  setting permanently until turned off with the same password.
- **Evidence Capture**: a 60-second collection with Unified Logs and packet capture; verify the
  manifest and `SHA256SUMS.txt`.

## Report

For each failure, record the step, the exact message, the technical details from **Help ›
Diagnostic Log**, and whether it reproduces after reconnecting. Open an issue with the sanitized
details, or add a row to the compatibility table in [MIGRATION.md](../MIGRATION.md#5-test-results).
