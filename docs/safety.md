# Safety and privacy

## Authorization comes first

Use the toolkit only on devices and data you own or are explicitly authorized to develop against, administer, test, back up, or examine. A DDI, trust relationship, profile, entitlement, or available service does not establish authorization.

The canonical [Scope and Safety workspace guide](https://github.com/hideouts-io/iOS-Developer-Toolkit#scope-and-safety) lists the product's technical limits. The [security policy](https://github.com/hideouts-io/iOS-Developer-Toolkit/security/policy) explains private vulnerability reporting and the data that must never be placed in a public issue.

## Action classes

| Class | Examples | Review boundary |
|---|---|---|
| Read-oriented | Discovery, status, inventory, help | Exact target and command remain visible |
| Host write | Export, capture, backup, analysis output | Destination and sensitive-output warning |
| Device change | Install, uninstall, mount, launch, location | Device-bound typed acknowledgement |
| High impact | Restore, erase, activation, restart, shutdown | Backup acknowledgement plus irreversible phrase |

The Action Palette exposes only operations currently eligible in the visible state and rechecks eligibility at activation.

## Evidence and interpretation

Raw logs, packet captures, backups, app inventories, screenshots, profiles, crash reports, MVT results, and external-tool inventories can contain sensitive device, account, application, location, or network data. Store them outside a public checkout on access-controlled storage.

Hashes detect later changes; they do not prove acquisition time, custody, authorship, completeness, or truth. Empty output is not proof of absence. A successful command is not proof that its view is complete. Analyst annotations remain separate from raw capture facts.

For collection structure and retention guidance, use the [canonical evidence-case reference](https://github.com/hideouts-io/iOS-Developer-Toolkit#evidence-case-contents).
