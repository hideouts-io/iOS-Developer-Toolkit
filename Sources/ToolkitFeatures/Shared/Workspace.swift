import DeviceKit
import Foundation

/// The app's navigation destinations. Shared by the UI, workspace profiles, and the support
/// bundle so their identifiers cannot drift apart.
public enum Workspace: String, CaseIterable, Codable, Sendable, Identifiable {
    case overview
    case device
    case readiness
    case apps
    case installApp
    case location
    case liveLogs
    case actions
    case backup
    case evidence
    case externalTools
    case activity
    case help
    case safety

    public var id: String { rawValue }

    public var title: String {
        switch self {
        case .overview: return "Overview"
        case .device: return "Device"
        case .readiness: return "Readiness Check"
        case .apps: return "Apps"
        case .installApp: return "Install App"
        case .location: return "Location Lab"
        case .liveLogs: return "Live Logs"
        case .actions: return "Actions"
        case .backup: return "Backup"
        case .evidence: return "Evidence Capture"
        case .externalTools: return "External Tools"
        case .activity: return "Session Activity"
        case .help: return "Tool Reference"
        case .safety: return "Scope & Safety"
        }
    }

    public var symbolName: String {
        switch self {
        case .overview: return "square.grid.2x2"
        case .device: return "iphone"
        case .readiness: return "checklist"
        case .apps: return "app.badge"
        case .installApp: return "square.and.arrow.down.on.square"
        case .location: return "location"
        case .liveLogs: return "text.alignleft"
        case .actions: return "bolt"
        case .backup: return "externaldrive.badge.timemachine"
        case .evidence: return "archivebox"
        case .externalTools: return "wrench.and.screwdriver"
        case .activity: return "clock.arrow.circlepath"
        case .help: return "book"
        case .safety: return "shield.lefthalf.filled"
        }
    }

    public var subtitle: String {
        switch self {
        case .overview: return "What you can do and where to start"
        case .device: return "Identity, trust, Developer Mode, and developer services"
        case .readiness: return "Check every prerequisite before you start"
        case .apps: return "Installed apps: search, sizes, launch, and remove"
        case .installApp: return "Inspect an .ipa or .app, then install it"
        case .location: return "Simulate coordinates, routes, and GPX tracks"
        case .liveLogs: return "Stream Unified Logs or syslog with filters and findings"
        case .actions: return "Guided device and developer actions"
        case .backup: return "Encrypted local backups and forensic handoffs"
        case .evidence: return "Hashed, documented evidence collection"
        case .externalTools: return "Optional tools you install separately"
        case .activity: return "Everything this session has run"
        case .help: return "Built-in help for the Apple tools the app uses"
        case .safety: return "What the app can and cannot do"
        }
    }

    public enum Group: String, CaseIterable, Sendable {
        case start = "Start"
        case device = "Device"
        case develop = "Develop"
        case data = "Data & Evidence"
        case reference = "Reference"
    }

    public var group: Group {
        switch self {
        case .overview: return .start
        case .device, .readiness, .apps, .installApp: return .device
        case .location, .liveLogs, .actions: return .develop
        case .backup, .evidence, .externalTools: return .data
        case .activity, .help, .safety: return .reference
        }
    }

    /// Whether the workspace can be used with this kind of device.
    public func supports(_ kind: DeviceKind?) -> Bool {
        switch self {
        case .backup, .evidence: return kind == .physical || kind == nil || kind == .demo
        default: return true
        }
    }
}

/// A clearly labelled simulated device for walkthroughs, screenshots, and UI tests. No device
/// operation can run against it.
public enum DemoMode {
    public static let identifier = "DEMO-IPHONE-17-PRO"
    public static let banner = "Demo Mode — this is a simulated iPhone for walkthroughs and screenshots. No device is connected and device actions are disabled."

    public static var device: Device {
        Device(
            kind: .demo,
            udid: identifier,
            name: "Demo iPhone (simulated)",
            productType: "iPhone18,1",
            marketingName: "iPhone 17 Pro",
            family: .iPhone,
            osName: "iOS",
            osVersion: "26.0",
            buildVersion: "23A341",
            architecture: "arm64e",
            hardwareModel: "D93AP",
            transports: [.usb],
            pairingState: .paired,
            developerMode: .enabled,
            ddiServicesAvailable: true,
            tunnelState: "connected",
            sources: [.demo]
        )
    }
}
