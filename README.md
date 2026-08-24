# iOS Developer Toolkit

A small macOS GUI for recognizing connected iPhones and iPads, explaining Developer Mode, mounting a modern Developer Disk Image, and collecting reviewable diagnostic evidence through `pymobiledevice3`.

## Run

From the repository root:

```bash
./script/build_and_run.sh
```

The script creates `IOSDeveloperToolkit/.venv`, installs the pinned project and dependencies there, stages `IOSDeveloperToolkit/dist/iOS Developer Toolkit.app`, and opens the app. Nothing is installed globally.

## Device workflow

1. Connect the iPhone or iPad by USB, unlock it, and tap **Trust**.
2. Follow the displayed instructions for **Settings → Privacy & Security → Developer Mode**. Enabling it requires a device restart and passcode confirmation.
3. Choose one DDI source:
   - **Downloaded personalized DDI:** `pymobiledevice3 mounter auto-mount` downloads the image, BuildManifest, and trust cache from [DeveloperDiskImage](https://github.com/doronz88/DeveloperDiskImage), obtains an Apple TSS personalization ticket, and mounts it at `/System/Developer`.
   - **Local Apple/Xcode DDI:** the app attaches `/Library/Developer/CoreDevice/CandidateDDIs/iOS_DDI.dmg` read-only, passes its `Restore` directory to `pymobiledevice3 cryptex auto-install`, and detaches the temporary Mac-side mount afterward. The outer candidate DMG is not uploaded directly to iOS.
4. Use **Collect Evidence** or the built-in `pymobiledevice3` console.
5. Unmount the personalized image or uninstall the local DDI Cryptex when finished.

Both modern paths are personalized for the selected device and normally contact Apple TSS. The repository-backed path additionally downloads from GitHub.

## Evidence collection

Every run creates a new timestamped case directory containing:

- Lockdown and usbmux device inventory
- Mounted image and Cryptex state
- Diagnostics, MobileGestalt, IORegistry, and battery snapshots
- App, process, profile, provisioning, AFC, and crash inventories
- DVT device information, detailed sysmon process data, and `developer dvt ls /`
- Optional screenshot and complete crash-report pull
- Timed classic syslog, DVT structured `oslog`, and PCAP streams
- `manifest.json` with commands, times, versions, exits, and coverage gaps
- `SHA256SUMS.txt` for collected files

The collector can also be run without the GUI:

```bash
IOSDeveloperToolkit/.venv/bin/ios-developer-collect \
  --udid DEVICE_UDID \
  --output-root "$HOME/Documents/iOS Developer Toolkit Cases" \
  --duration 300 \
  --include-syslog \
  --include-oslog \
  --include-pcap
```

## Scope and privacy

- A DDI enables Apple developer services; it is not a jailbreak or passcode bypass.
- The DVT root listing is not unrestricted raw filesystem acquisition.
- PCAP does not decrypt TLS and cannot by itself prove why an app contacted a service.
- Failed or empty commands are recorded as coverage gaps rather than interpreted as proof of absence.
- Logs, PCAPs, screenshots, app inventories, crash reports, and UDIDs may be sensitive. Review them before sharing.
- Enabling Developer Mode and mounting a DDI change device state. For evidence-sensitive work, collect a backup or sysdiagnose first and record the time of every action.
