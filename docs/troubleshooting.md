# Troubleshooting

## Find the failing layer

| Symptom | Layer | What to check |
|---|---|---|
| Not in Finder either | USB / cable | Data-capable cable, direct port (no hub), device unlocked, **Allow accessory** approved on the Mac |
| In Finder, not in the app | macOS device service | **Connection diagnostics** on the Device page. If the device service is not answering, restart the Mac — the app never restarts system services |
| Listed, but “not trusted” | Pairing | Unlock, reconnect, tap **Trust**. If no prompt appears: *Settings › General › Transfer or Reset › Reset › Reset Location & Privacy* |
| Developer features unavailable | Developer Mode | *Settings › Privacy & Security › Developer Mode*. If the switch is missing, connect the device to Xcode once |
| “Needs Xcode” | Xcode | Install Xcode, open it once, and select it in *Xcode › Settings › Locations › Command Line Tools*. Run **Tool Reference › Toolchain Check** |
| Developer services not prepared | CoreDevice / DDI | Keep the device unlocked and on USB, make sure the Mac is online, then **Prepare Developer Services** on the Device page |
| Network device missing | CoreDevice | Pair it with Xcode over USB first, keep it on the same network, and refresh (⌘R) |
| Simulator missing | simctl | Install a simulator runtime in *Xcode › Settings › Components* |
| Location simulation fails on iOS 16 or earlier | Legacy service | Connect the device to Xcode once so Xcode mounts its developer disk image; this app does not mount images for iOS 16 and earlier |

The **Readiness Check** runs these checks in order and stops at the first one that fails.

## Specific problems

**Live Logs stay empty.** Check that the right stream was started (Unified or classic syslog for
devices, simulator log for simulators) and that the device is unlocked and in use. Filters change
only the view. The capture counter shows whether bytes are arriving.

**A backup stops.** Keep the device unlocked and awake, check free space on the Mac, and choose an
empty or previous backup folder for this device. If encryption is on, the backup password is the
one set on the device; the app cannot recover it.

**An `.ipa` will not install.** The inspection must show a valid signature, and the provisioning
profile must include the device (development and ad hoc profiles) or be an enterprise profile.

**A command times out.** Every external command has a timeout. Its message names the command;
the technical details are in **Help › Diagnostic Log**.

## Collect details for a report

1. Run the **Readiness Check** and copy its report.
2. **iOS Developer Toolkit › Create Support Bundle…** writes a sanitized ZIP (no names,
   identifiers, paths, addresses, or captured content). Open it and review it before sharing.
3. The unified log:

   ```bash
   log show --last 10m --predicate 'subsystem == "io.hideouts.iOSDeveloperToolkit"' --info
   ```

Ask in [GitHub Discussions](https://github.com/hideouts-io/iOS-Developer-Toolkit/discussions)
and follow [SUPPORT.md](../SUPPORT.md) before sharing any output.
