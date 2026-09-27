import DeviceKit
import Foundation
import ToolkitCore

public enum CapabilityState: String, Codable, Sendable, CaseIterable {
    case ready
    case attention
    case unavailable
    case blocked
    case notTested = "not-tested"
    case notApplicable = "not-applicable"

    public var label: String {
        switch self {
        case .ready: return "Ready"
        case .attention: return "Needs attention"
        case .unavailable: return "Unavailable"
        case .blocked: return "Blocked"
        case .notTested: return "Not tested"
        case .notApplicable: return "Not applicable"
        }
    }

    public var symbolName: String {
        switch self {
        case .ready: return "checkmark.circle.fill"
        case .attention: return "exclamationmark.triangle.fill"
        case .unavailable: return "xmark.octagon.fill"
        case .blocked: return "slash.circle"
        case .notTested: return "circle.dashed"
        case .notApplicable: return "minus.circle"
        }
    }
}

public struct CapabilityResult: Codable, Sendable, Hashable, Identifiable {
    public var id: String
    public var layer: String
    public var title: String
    public var state: CapabilityState
    public var summary: String
    public var evidence: String
    public var remediation: String

    public init(id: String, layer: String, title: String, state: CapabilityState, summary: String, evidence: String = "", remediation: String = "") {
        self.id = id
        self.layer = layer
        self.title = title
        self.state = state
        self.summary = summary
        self.evidence = evidence
        self.remediation = remediation
    }
}

/// The rows of the matrix, in display order.
public enum CapabilityRow: String, CaseIterable, Sendable {
    case host = "host"
    case xcodeTools = "xcode-tools"
    case usbmux = "usbmuxd"
    case deviceConnection = "device-connection"
    case pairingTrust = "pairing-trust"
    case developerMode = "developer-mode"
    case coreDevice = "coredevice"
    case developerServices = "developer-services"
    case lockState = "lock-state"
    case lockdownServices = "lockdown-services"
    case backupService = "backup-service"
    case webInspector = "web-inspector"
    case simulatorRuntime = "simulator-runtime"
    case simulatorRunning = "simulator-running"

    public var layer: String {
        switch self {
        case .host, .xcodeTools, .usbmux: return "This Mac"
        case .deviceConnection, .pairingTrust: return "Connection"
        case .developerMode, .coreDevice, .developerServices: return "Developer readiness"
        case .lockState, .lockdownServices, .backupService, .webInspector: return "Device services"
        case .simulatorRuntime, .simulatorRunning: return "Simulator"
        }
    }

    public var title: String {
        switch self {
        case .host: return "macOS"
        case .xcodeTools: return "Xcode developer tools"
        case .usbmux: return "Device connection service (usbmuxd)"
        case .deviceConnection: return "Selected device is connected"
        case .pairingTrust: return "Device trusts this Mac"
        case .developerMode: return "Developer Mode"
        case .coreDevice: return "Xcode device service (CoreDevice)"
        case .developerServices: return "Developer services (DDI)"
        case .lockState: return "Device unlocked"
        case .lockdownServices: return "Logging and diagnostics services"
        case .backupService: return "Backup service"
        case .webInspector: return "Safari Web Inspector"
        case .simulatorRuntime: return "Simulator runtime installed"
        case .simulatorRunning: return "Simulator running"
        }
    }

    public var remediation: String {
        switch self {
        case .host: return ""
        case .xcodeTools: return "Install Xcode from the App Store, open it once to finish installing components, and select it in Xcode › Settings › Locations. Features that use USB services still work without Xcode."
        case .usbmux: return "Reconnect the device. If macOS's device service still does not respond, restart the Mac."
        case .deviceConnection: return "Connect the device with a data-capable USB cable, unlock it, and wait a few seconds."
        case .pairingTrust: return "Unlock the device and tap Trust when asked. If no prompt appears, disconnect and reconnect the cable."
        case .developerMode: return "On the device open Settings › Privacy & Security › Developer Mode, turn it on, restart, and confirm. If the setting is missing, open Xcode › Window › Devices and Simulators once with the device connected."
        case .coreDevice: return "Open Xcode › Window › Devices and Simulators with the device connected and unlocked, and wait for Xcode to finish preparing it."
        case .developerServices: return "Use “Mount Developer Image” on the Device page. Keep the device unlocked; on iOS 17 and later this Mac must be online so Apple can personalize the image."
        case .lockState: return "Unlock the device and keep it awake while working."
        case .lockdownServices: return "Unlock the device; if it was just restarted, unlock it once."
        case .backupService: return "Unlock the device and make sure no other backup (Finder) is running."
        case .webInspector: return "Only needed to list Safari and web view tabs. Turn on Settings › Apps › Safari › Advanced › Web Inspector (Settings › Safari › Advanced before iOS 18)."
        case .simulatorRuntime: return "Install the simulator runtime in Xcode › Settings › Components."
        case .simulatorRunning: return "Start the simulator from the Simulator actions or from Xcode."
        }
    }

    public static func rows(for kind: DeviceKind) -> [CapabilityRow] {
        switch kind {
        case .physical: return [.host, .xcodeTools, .usbmux, .deviceConnection, .pairingTrust, .developerMode, .coreDevice, .developerServices, .lockState, .lockdownServices, .backupService, .webInspector]
        case .simulator: return [.host, .xcodeTools, .simulatorRuntime, .simulatorRunning]
        case .demo: return [.host]
        }
    }

    public func result(_ state: CapabilityState, _ summary: String, evidence: String = "") -> CapabilityResult {
        CapabilityResult(id: rawValue, layer: layer, title: title, state: state, summary: summary, evidence: evidence, remediation: state == .ready || state == .notApplicable ? "" : remediation)
    }

    public var untested: CapabilityResult {
        result(.notTested, "Run the check to test this.")
    }
}

/// Runs the readiness probes. Nothing here changes device state: no image is mounted, no
/// setting is changed, and no service other than a short-lived read is started.
public struct CapabilityProbe: Sendable {
    public let runner: CommandRunning
    public let coreDevice: CoreDeviceClient
    public let usbmux: USBMuxClient
    public let simulators: SimulatorClient
    public let developerImageFolders: [URL]

    public init(runner: CommandRunning = ProcessCommandRunner(), usbmux: USBMuxClient = USBMuxClient(), developerImageFolders: [URL] = []) {
        self.runner = runner
        coreDevice = CoreDeviceClient(runner: runner)
        simulators = SimulatorClient(runner: runner)
        self.usbmux = usbmux
        self.developerImageFolders = developerImageFolders
    }

    /// The readiness row for a natively evaluated developer-image state.
    static func developerImageResult(_ status: DeveloperImageStatus) -> CapabilityResult {
        let state: CapabilityState
        switch status.state {
        case .mounted: state = .ready
        case .notRequired: state = .notApplicable
        case .available, .personalizationRequired, .blocked: state = .attention
        case .missing, .incompatible, .failed: state = .unavailable
        }
        let summary = status.state == .mounted ? "Mounted." : "\(status.state.label): \(status.headline)"
        return CapabilityRow.developerServices.result(state, summary, evidence: ([status.explanation] + (status.technicalDetail.map { [$0] } ?? [])).joined(separator: " "))
    }

    public func run(for device: Device, progress: @Sendable (CapabilityResult) -> Void = { _ in }) async -> [CapabilityResult] {
        var results: [CapabilityRow: CapabilityResult] = [:]
        func record(_ row: CapabilityRow, _ result: CapabilityResult) {
            results[row] = result
            progress(result)
        }
        let target = device.target
        let info = ProcessInfo.processInfo
        #if arch(arm64)
        let architecture = "Apple silicon"
        #else
        let architecture = "Intel"
        #endif
        record(.host, CapabilityRow.host.result(.ready, "macOS \(info.operatingSystemVersionString), \(architecture)"))

        let tools = await DeveloperToolsStatus.probe(runner: runner)
        let hasCoreDevice = tools.devicectl.isAvailable
        if tools.devicectl.isAvailable {
            record(.xcodeTools, CapabilityRow.xcodeTools.result(.ready, tools.xcodeVersion ?? "Xcode is installed.", evidence: tools.developerDirectory ?? ""))
        } else if tools.isCommandLineToolsOnly {
            record(.xcodeTools, CapabilityRow.xcodeTools.result(.attention, "Only the Command Line Tools are selected; developer services and simulators need Xcode.", evidence: tools.developerDirectory ?? ""))
        } else {
            record(.xcodeTools, CapabilityRow.xcodeTools.result(.unavailable, "Xcode is not installed or not selected.", evidence: {
                if case .missing(let reason) = tools.devicectl { return reason }
                return ""
            }()))
        }

        guard !Task.isCancelled else { return ordered(results, device.kind) }

        switch device.kind {
        case .demo:
            break
        case .simulator:
            guard hasCoreDevice || tools.simctl.isAvailable else {
                record(.simulatorRuntime, CapabilityRow.simulatorRuntime.result(.blocked, "Needs Xcode."))
                record(.simulatorRunning, CapabilityRow.simulatorRunning.result(.blocked, "Needs Xcode."))
                break
            }
            let records = (try? await simulators.list()) ?? []
            guard let simulator = records.first(where: { $0.udid == target.udid }) else {
                record(.simulatorRuntime, CapabilityRow.simulatorRuntime.result(.unavailable, "The simulator no longer exists."))
                record(.simulatorRunning, CapabilityRow.simulatorRunning.result(.blocked, "The simulator no longer exists."))
                break
            }
            record(.simulatorRuntime, simulator.isAvailable ? CapabilityRow.simulatorRuntime.result(.ready, simulator.runtime?.name ?? simulator.runtimeIdentifier) : CapabilityRow.simulatorRuntime.result(.unavailable, simulator.availabilityError ?? "The runtime is not installed."))
            record(.simulatorRunning, simulator.state == .booted ? CapabilityRow.simulatorRunning.result(.ready, "Running") : CapabilityRow.simulatorRunning.result(.attention, "The simulator is \(simulator.state.label.lowercased())."))
        case .physical:
            await probePhysical(device, hasCoreDevice: hasCoreDevice, record: record)
        }
        return ordered(results, device.kind)
    }

    private func probePhysical(_ device: Device, hasCoreDevice: Bool, record: (CapabilityRow, CapabilityResult) -> Void) async {
        let target = device.target
        // usbmuxd and connection
        var muxDevice: USBMuxDevice?
        do {
            let devices = try await usbmux.listDevices()
            record(.usbmux, CapabilityRow.usbmux.result(.ready, "Responding"))
            muxDevice = devices.first { $0.udid.caseInsensitiveCompare(target.udid) == .orderedSame }
        } catch {
            record(.usbmux, CapabilityRow.usbmux.result(.unavailable, (error as? ToolkitError)?.message ?? error.localizedDescription))
        }
        if let muxDevice {
            record(.deviceConnection, CapabilityRow.deviceConnection.result(.ready, "Connected over \(muxDevice.transport.label)."))
        } else if device.supportsCoreDevice {
            record(.deviceConnection, CapabilityRow.deviceConnection.result(.attention, "Reachable only through Xcode's network connection; USB services (logs, backup, diagnostics) need a USB cable or Wi-Fi sync."))
        } else {
            record(.deviceConnection, CapabilityRow.deviceConnection.result(.unavailable, "The device is not connected."))
        }

        // Trust and lockdown-based checks
        var developerModeFromLockdown: Bool?
        var lockdownReady = false
        if muxDevice != nil {
            do {
                let probe = try await DeviceSession.with(target, usbmux: usbmux) { session -> (developerMode: Bool?, willEncrypt: Bool?) in
                    let developerMode = try? await session.developerModeEnabled()
                    let willEncrypt = try? await session.getValue(domain: "com.apple.mobile.backup", key: "WillEncrypt")?.boolValue
                    let syslog = try await session.openService(SyslogRelay.serviceName)
                    await syslog.close()
                    return (developerMode ?? nil, willEncrypt ?? nil)
                }
                developerModeFromLockdown = probe.developerMode
                record(.backupService, probe.willEncrypt.map { CapabilityRow.backupService.result(.ready, $0 ? "Available; backups are encrypted." : "Available; backup encryption is off.") } ?? CapabilityRow.backupService.result(.attention, "The device did not report its backup setting."))
                lockdownReady = true
                record(.pairingTrust, CapabilityRow.pairingTrust.result(.ready, "Trusted; a secure session was established."))
                record(.lockdownServices, CapabilityRow.lockdownServices.result(.ready, "Syslog relay started and closed successfully."))
            } catch let error as ToolkitError {
                let state: CapabilityState = [.deviceLocked, .pairingPending].contains(error.kind) ? .attention : .unavailable
                record(.pairingTrust, CapabilityRow.pairingTrust.result(state, error.message, evidence: error.technicalDetail ?? ""))
                record(.lockdownServices, CapabilityRow.lockdownServices.result(.blocked, "Needs a trusted connection."))
                record(.backupService, CapabilityRow.backupService.result(.blocked, "Needs a trusted connection."))
            } catch {
                record(.pairingTrust, CapabilityRow.pairingTrust.result(.unavailable, error.localizedDescription))
            }
        } else {
            let pairing = device.pairingState
            record(.pairingTrust, pairing == .paired ? CapabilityRow.pairingTrust.result(.ready, "Trusted (reported by Xcode).") : CapabilityRow.pairingTrust.result(.blocked, "Needs a connected device."))
            record(.lockdownServices, CapabilityRow.lockdownServices.result(.blocked, "Needs a USB or Wi-Fi sync connection."))
            record(.backupService, CapabilityRow.backupService.result(.blocked, "Needs a USB or Wi-Fi sync connection."))
        }

        // Web Inspector: a short probe, because it is off on most devices and a refusal only shows as
        // a dropped connection. “Not answering” is attention, not a failure.
        if lockdownReady {
            do {
                let applications = try await WebInspector.openPages(on: target, usbmux: usbmux, handshakeDeadline: .seconds(4), listingWindow: .seconds(1))
                let pages = applications.reduce(0) { $0 + $1.pages.count }
                record(.webInspector, CapabilityRow.webInspector.result(.ready, pages == 1 ? "Answering (1 inspectable page)." : "Answering (\(pages) inspectable pages)."))
            } catch {
                record(.webInspector, CapabilityRow.webInspector.result(.attention, "Not answering — Web Inspector is probably off.", evidence: (error as? ToolkitError)?.technicalDetail ?? error.localizedDescription))
            }
        } else {
            record(.webInspector, CapabilityRow.webInspector.result(.blocked, "Needs a trusted USB connection."))
        }

        // With a trusted USB session the developer-image row comes from the native check below.
        func recordImageFallback(_ result: CapabilityResult) {
            if !lockdownReady { record(.developerServices, result) }
        }

        // CoreDevice checks
        var coreDeviceRecord: CoreDeviceRecord?
        if !hasCoreDevice {
            record(.coreDevice, CapabilityRow.coreDevice.result(.blocked, "Needs Xcode."))
            recordImageFallback(CapabilityRow.developerServices.result(.blocked, "Needs Xcode."))
            record(.lockState, CapabilityRow.lockState.result(.blocked, "Needs Xcode."))
        } else {
            do {
                coreDeviceRecord = try await coreDevice.details(target).record
                record(.coreDevice, CapabilityRow.coreDevice.result(.ready, "Connected (tunnel: \(coreDeviceRecord?.tunnelState ?? "unknown"))."))
            } catch let error as ToolkitError {
                record(.coreDevice, CapabilityRow.coreDevice.result(error.kind == .deviceLocked ? .attention : .unavailable, error.message, evidence: error.technicalDetail ?? ""))
            } catch {
                record(.coreDevice, CapabilityRow.coreDevice.result(.unavailable, error.localizedDescription))
            }
            if coreDeviceRecord != nil {
                do {
                    let lock = try await coreDevice.lockState(target)
                    record(.lockState, CapabilityRow.lockState.result(lock.passcodeRequired == true ? .attention : .ready, lock.summary))
                } catch {
                    record(.lockState, CapabilityRow.lockState.result(.unavailable, (error as? ToolkitError)?.message ?? error.localizedDescription))
                }
                do {
                    _ = try await coreDevice.ddiServices(target, autoMount: false)
                    let available = coreDeviceRecord?.ddiServicesAvailable
                    recordImageFallback(available == false ? CapabilityRow.developerServices.result(.attention, "Not mounted yet.") : CapabilityRow.developerServices.result(.ready, "Available."))
                } catch let error as ToolkitError {
                    recordImageFallback(CapabilityRow.developerServices.result(.attention, error.message, evidence: error.technicalDetail ?? ""))
                } catch {
                    recordImageFallback(CapabilityRow.developerServices.result(.attention, error.localizedDescription))
                }
            } else {
                record(.lockState, CapabilityRow.lockState.result(.blocked, "Needs the Xcode device service."))
                recordImageFallback(CapabilityRow.developerServices.result(.blocked, "Needs the Xcode device service."))
            }
        }

        // With a trusted USB session, the native developer-image check is more precise than
        // CoreDevice's: it reads what is mounted and whether this Mac has a compatible image.
        if lockdownReady {
            let status = await DeveloperImageManager(usbmux: usbmux, coreDevice: coreDevice).status(for: target, userFolders: developerImageFolders)
            record(.developerServices, Self.developerImageResult(status))
        }

        // Developer Mode from whichever source answered.
        let developerMode: DeveloperModeState = developerModeFromLockdown.map { $0 ? .enabled : .disabled } ?? coreDeviceRecord?.developerMode ?? device.developerMode
        switch developerMode {
        case .enabled: record(.developerMode, CapabilityRow.developerMode.result(.ready, "On"))
        case .disabled: record(.developerMode, CapabilityRow.developerMode.result(.attention, "Off"))
        case .notApplicable: record(.developerMode, CapabilityRow.developerMode.result(.notApplicable, "Not used by this iOS version."))
        case .unknown:
            if let major = device.osMajorVersion, major < 16 {
                record(.developerMode, CapabilityRow.developerMode.result(.notApplicable, "iOS \(major) has no Developer Mode setting."))
            } else {
                record(.developerMode, CapabilityRow.developerMode.result(lockdownReady || coreDeviceRecord != nil ? .attention : .blocked, "The device did not report Developer Mode."))
            }
        }
    }

    func ordered(_ results: [CapabilityRow: CapabilityResult], _ kind: DeviceKind) -> [CapabilityResult] {
        CapabilityRow.rows(for: kind).map { results[$0] ?? $0.untested }
    }
}

/// Whether one action's prerequisites are met according to the latest matrix.
public enum ActionReadiness: Sendable, Equatable {
    case ready
    case notTested
    case needsAttention([String])

    public static func evaluate(_ action: ActionDescriptor, results: [CapabilityResult], device: Device?) -> ActionReadiness {
        guard device != nil else { return .notTested }
        let table = Dictionary(results.map { ($0.id, $0) }, uniquingKeysWith: { $1 })
        var problems: [String] = []
        var untested = false
        for requirement in action.requirements {
            let id: String
            switch requirement {
            case .trustedDevice: id = CapabilityRow.pairingTrust.rawValue
            case .lockdownConnection: id = CapabilityRow.deviceConnection.rawValue
            case .coreDevice: id = CapabilityRow.coreDevice.rawValue
            case .developerMode: id = CapabilityRow.developerMode.rawValue
            case .developerServices: id = CapabilityRow.developerServices.rawValue
            case .simulatorRunning: id = CapabilityRow.simulatorRunning.rawValue
            case .xcode: id = CapabilityRow.xcodeTools.rawValue
            }
            guard let result = table[id] else {
                if device?.kind == .simulator && [.trustedDevice, .lockdownConnection, .coreDevice, .developerMode, .developerServices].contains(requirement) { continue }
                untested = true
                continue
            }
            switch result.state {
            case .ready, .notApplicable: continue
            case .notTested: untested = true
            default: problems.append("\(requirement.label): \(result.summary)")
            }
        }
        if !problems.isEmpty { return .needsAttention(problems) }
        return untested ? .notTested : .ready
    }
}
