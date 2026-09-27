# Safety and privacy

## Authorization comes first

Use the toolkit only on devices and data you own or are explicitly authorized to develop against,
administer, test, back up, or examine. Trust, Developer Mode, a developer disk image, a profile,
or an available service does not establish authorization.

The app's **Scope & Safety** page lists its technical limits. The [security policy](../SECURITY.md)
explains private vulnerability reporting and what must never go into a public issue.

## Confirmation levels

| Level | Examples | Before it runs |
|---|---|---|
| Read-only | Device details, battery, lock state, app list | Runs immediately; the target is always visible |
| Saves files on this Mac | Screenshot, crash reports, backup, capture | Review sheet with the destination; files are never overwritten |
| Changes the device | Install or launch an app, set a location, mount or unmount the developer image | Type `RUN` and the last six characters of the target's UDID |
| High impact | Restart, remove an app, erase a simulator | Confirm a current backup, then type `IRREVERSIBLE` and the same six characters |

The Command Palette and Actions list only what is available for the selected target and
re-check eligibility when you run it. Advanced Mode classifies `devicectl` subcommands the same
way, treats anything it does not recognize as a device change, always adds the selected device,
and rejects attempts to address a different one.

## Evidence and interpretation

Logs, packet captures, backups, app lists, screenshots, profiles, crash reports, and MVT results
can contain sensitive device, account, app, location, and network data. Keep them on
access-controlled storage, outside any public repository. The app writes them with owner-only
permissions.

Hashes detect later changes; they do not prove when something was collected, by whom, or that
it is complete or true. Empty output is not proof of absence. Findings and notes are recorded
separately from the raw capture they refer to.
