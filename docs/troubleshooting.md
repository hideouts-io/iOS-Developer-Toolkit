# Troubleshooting

## Start with the failing layer

| Visible symptom | First check | Next reference |
|---|---|---|
| No phone in the picker | Cable, unlock state, macOS accessory approval, Finder visibility, Trust | [Device not detected](https://github.com/hideouts-io/iOS-Developer-Toolkit#device-not-detected) |
| Paired but developer command fails | Developer Mode, DDI compatibility, tunnel, service-specific matrix row | [Capability Matrix](https://github.com/hideouts-io/iOS-Developer-Toolkit#device-capability-matrix) |
| Man Pages appears busy | Cancel the bounded request and retry from the current project environment | [Man Pages](https://github.com/hideouts-io/iOS-Developer-Toolkit#man-pages) |
| Stream has no lines | Confirm the correct stream family, prerequisites, app activity, and raw spool state | [Live Logs](https://github.com/hideouts-io/iOS-Developer-Toolkit#live-logs) |
| Backup fails | Encryption state, free space, destination freshness, unlock state | [Backup](https://github.com/hideouts-io/iOS-Developer-Toolkit#backup) |
| Optional adapter fails | Exact executable path/hash, reported version/build, upstream requirements, independent target state | [Ecosystem Tools](https://github.com/hideouts-io/iOS-Developer-Toolkit#ecosystem-tools) |

## Use the built-in diagnostics

1. Run **Retry Scan** for one immediate usbmux check.
2. Use **Reconnect & Retry…** for the guided 30-second physical reconnection window.
3. Run **Capability Matrix** and inspect the first non-ready prerequisite.
4. Use **Check Command Drift** when a guided command may no longer match the installed CLI.
5. Create a **Sanitized Support Bundle**, review it locally, and attach it only when appropriate.

The toolkit does not attempt to restart SIP-protected Apple services, delete pairing records, use `sudo`, or hide a failed prerequisite behind automatic recovery.

For support, use [GitHub Discussions](https://github.com/hideouts-io/iOS-Developer-Toolkit/discussions). Follow the [support policy](https://github.com/hideouts-io/iOS-Developer-Toolkit/blob/main/SUPPORT.md) before sharing any output.
