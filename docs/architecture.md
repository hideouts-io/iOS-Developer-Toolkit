# Architecture

## Service layers

The toolkit keeps Apple service boundaries visible instead of reducing every failure to “device not connected.”

| Layer | Typical role | What readiness does not prove |
|---|---|---|
| USB / Wi-Fi and usbmux | Host discovery and transport | Trust, Developer Mode, or service access |
| Lockdown and paired services | Device information, apps, backup, diagnostics, AFC, classic syslog | Root access or unrestricted files |
| RemoteXPC / RSD | Modern service discovery and transport | That every advertised service accepts a request |
| Developer Mode and DDI | Enables compatible developer-service payloads | Jailbreak, bypass, or compromise |
| CoreDevice / DVT | Apple development and Instruments-style telemetry | Complete or stable forensic coverage |

The [README service-layer diagram](https://github.com/hideouts-io/iOS-Developer-Toolkit#how-the-service-layers-fit-together) is the canonical operational explanation.

## Process model

The GUI launches argument vectors directly rather than evaluating shell pipelines or substitutions. Finite operations use a shared controller with explicit timeout, terminal output draining, cancellation, and one typed result. Long-running streams use explicit Stop controls. Backup and evidence collection retain purpose-built protocols because password input and partial-artifact finalization have different safety requirements.

The key implementation surfaces are:

- [`runtime.py`](https://github.com/hideouts-io/iOS-Developer-Toolkit/blob/main/ios_developer_toolkit/runtime.py) for packaged/source command resolution;
- [`qt_process.py`](https://github.com/hideouts-io/iOS-Developer-Toolkit/blob/main/ios_developer_toolkit/qt_process.py) and [`interactive_process.py`](https://github.com/hideouts-io/iOS-Developer-Toolkit/blob/main/ios_developer_toolkit/interactive_process.py) for typed process lifecycles;
- [`command_catalog.py`](https://github.com/hideouts-io/iOS-Developer-Toolkit/blob/main/ios_developer_toolkit/command_catalog.py) for reviewed guided commands;
- [`operation_history.py`](https://github.com/hideouts-io/iOS-Developer-Toolkit/blob/main/ios_developer_toolkit/operation_history.py) for session-local operation records;
- [`collector.py`](https://github.com/hideouts-io/iOS-Developer-Toolkit/blob/main/ios_developer_toolkit/collector.py) for evidence coverage, manifests, and hashes.

## External-provider boundary

UFADE and MVT remain isolated external providers. go-ios, idb Companion, and ipsw are optional executable adapters with path, SHA-256, and version/build validation. Their dependencies, licenses, target selection, output semantics, and update cycles are not merged into the packaged application.

See the current [product audit and roadmap](PRODUCT_AUDIT_2026-09-21.md) for the evidence behind these boundaries.
