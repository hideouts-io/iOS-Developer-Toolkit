import Foundation
import ToolkitCore

/// Physical hardware versus an Xcode simulator. The UI keeps these visibly separate.
public enum DeviceKind: String, Codable, Sendable, CaseIterable {
    case physical
    case simulator
    case demo

    public var label: String {
        switch self {
        case .physical: return "Physical Device"
        case .simulator: return "Simulator"
        case .demo: return "Demo Device"
        }
    }
}

public enum DeviceFamily: String, Codable, Sendable {
    case iPhone, iPad, iPod, appleTV = "Apple TV", appleWatch = "Apple Watch", vision = "Apple Vision", mac = "Mac", unknown = "Device"

    public static func from(productType: String?, deviceType: String? = nil) -> DeviceFamily {
        let value = (deviceType ?? productType ?? "").lowercased()
        if value.hasPrefix("iphone") { return .iPhone }
        if value.hasPrefix("ipad") { return .iPad }
        if value.hasPrefix("ipod") { return .iPod }
        if value.hasPrefix("appletv") || value.contains("apple tv") { return .appleTV }
        if value.hasPrefix("watch") || value.contains("watch") { return .appleWatch }
        if value.hasPrefix("reality") || value.contains("vision") { return .vision }
        if value.hasPrefix("mac") { return .mac }
        return .unknown
    }

    public var symbolName: String {
        switch self {
        case .iPhone: return "iphone"
        case .iPad: return "ipad"
        case .iPod: return "ipodtouch"
        case .appleTV: return "appletv"
        case .appleWatch: return "applewatch"
        case .vision: return "visionpro"
        case .mac: return "desktopcomputer"
        case .unknown: return "questionmark.square.dashed"
        }
    }
}

/// How the Mac currently reaches a device.
public enum DeviceTransport: String, Codable, Sendable, CaseIterable, Comparable {
    case usb
    case network
    case local

    public var label: String {
        switch self {
        case .usb: return "USB"
        case .network: return "Wi-Fi / Network"
        case .local: return "On this Mac"
        }
    }

    public static func < (lhs: DeviceTransport, rhs: DeviceTransport) -> Bool {
        lhs.rawValue < rhs.rawValue
    }
}

public enum PairingState: String, Codable, Sendable {
    case paired
    case unpaired
    case pairingInProgress
    case unknown
    case notApplicable

    public var label: String {
        switch self {
        case .paired: return "Trusted"
        case .unpaired: return "Not trusted"
        case .pairingInProgress: return "Waiting for Trust"
        case .unknown: return "Unknown"
        case .notApplicable: return "Not needed"
        }
    }

    public static func fromCoreDevice(_ value: String?) -> PairingState {
        switch value?.lowercased() {
        case "paired": return .paired
        case "unpaired": return .unpaired
        case "pairinginprogress", "pairing": return .pairingInProgress
        default: return .unknown
        }
    }
}

public enum DeveloperModeState: String, Codable, Sendable {
    case enabled
    case disabled
    case unknown
    case notApplicable

    public var label: String {
        switch self {
        case .enabled: return "On"
        case .disabled: return "Off"
        case .unknown: return "Unknown"
        case .notApplicable: return "Not needed"
        }
    }

    public static func fromCoreDevice(_ value: String?) -> DeveloperModeState {
        switch value?.lowercased() {
        case "enabled": return .enabled
        case "disabled": return .disabled
        default: return .unknown
        }
    }
}

public enum SimulatorState: String, Codable, Sendable {
    case booted = "Booted"
    case shutdown = "Shutdown"
    case booting = "Booting"
    case shuttingDown = "Shutting Down"
    case creating = "Creating"
    case unknown

    public init(simctlValue: String?) {
        self = SimulatorState(rawValue: simctlValue ?? "") ?? .unknown
    }

    public var label: String {
        switch self {
        case .booted: return "Running"
        case .shutdown: return "Shut down"
        case .booting: return "Starting"
        case .shuttingDown: return "Shutting down"
        case .creating: return "Being created"
        case .unknown: return "Unknown"
        }
    }
}

/// Where a device record came from. A physical device can be reported by both usbmuxd
/// (lockdown services, no Xcode needed) and CoreDevice (developer services, Xcode needed).
public enum DiscoverySource: String, Codable, Sendable, CaseIterable {
    case usbmux
    case coreDevice
    case simctl
    case demo
}

/// A merged, display-ready device record.
public struct Device: Identifiable, Hashable, Sendable, Codable {
    public var id: String { "\(kind.rawValue):\(udid)" }

    public var kind: DeviceKind
    public var udid: String
    public var name: String
    public var productType: String?
    public var marketingName: String?
    public var family: DeviceFamily
    public var osName: String?
    public var osVersion: String?
    public var buildVersion: String?
    public var architecture: String?
    public var hardwareModel: String?
    public var serialNumber: String?
    public var ecid: String?
    public var transports: Set<DeviceTransport>
    public var pairingState: PairingState
    public var developerMode: DeveloperModeState
    public var ddiServicesAvailable: Bool?
    public var tunnelState: String?
    public var coreDeviceIdentifier: String?
    public var usbmuxDeviceID: Int?
    public var simulatorState: SimulatorState?
    public var simulatorRuntime: String?
    public var sources: Set<DiscoverySource>
    public var lastSeen: Date

    public init(
        kind: DeviceKind,
        udid: String,
        name: String,
        productType: String? = nil,
        marketingName: String? = nil,
        family: DeviceFamily? = nil,
        osName: String? = nil,
        osVersion: String? = nil,
        buildVersion: String? = nil,
        architecture: String? = nil,
        hardwareModel: String? = nil,
        serialNumber: String? = nil,
        ecid: String? = nil,
        transports: Set<DeviceTransport> = [],
        pairingState: PairingState = .unknown,
        developerMode: DeveloperModeState = .unknown,
        ddiServicesAvailable: Bool? = nil,
        tunnelState: String? = nil,
        coreDeviceIdentifier: String? = nil,
        usbmuxDeviceID: Int? = nil,
        simulatorState: SimulatorState? = nil,
        simulatorRuntime: String? = nil,
        sources: Set<DiscoverySource> = [],
        lastSeen: Date = Date()
    ) {
        self.kind = kind
        self.udid = udid
        self.name = name
        self.productType = productType
        self.marketingName = marketingName ?? productType.flatMap(ProductCatalog.marketingName(for:))
        self.family = family ?? DeviceFamily.from(productType: productType)
        self.osName = osName
        self.osVersion = osVersion
        self.buildVersion = buildVersion
        self.architecture = architecture
        self.hardwareModel = hardwareModel
        self.serialNumber = serialNumber
        self.ecid = ecid
        self.transports = transports
        self.pairingState = pairingState
        self.developerMode = developerMode
        self.ddiServicesAvailable = ddiServicesAvailable
        self.tunnelState = tunnelState
        self.coreDeviceIdentifier = coreDeviceIdentifier
        self.usbmuxDeviceID = usbmuxDeviceID
        self.simulatorState = simulatorState
        self.simulatorRuntime = simulatorRuntime
        self.sources = sources
        self.lastSeen = lastSeen
    }

    public var displayModel: String {
        marketingName ?? productType ?? family.rawValue
    }

    public var osMajorVersion: Int? {
        guard let osVersion, let major = osVersion.split(separator: ".").first else { return nil }
        return Int(major)
    }

    public var displayVersion: String {
        let os = osName ?? (family == .iPad ? "iPadOS" : "iOS")
        guard let osVersion else { return os }
        if let buildVersion { return "\(os) \(osVersion) (\(buildVersion))" }
        return "\(os) \(osVersion)"
    }

    public var primaryTransport: DeviceTransport? {
        if transports.contains(.usb) { return .usb }
        if transports.contains(.network) { return .network }
        if transports.contains(.local) { return .local }
        return nil
    }

    /// Whether lockdown (usbmuxd) services can be used right now.
    public var supportsLockdownServices: Bool {
        kind == .physical && usbmuxDeviceID != nil
    }

    /// Whether CoreDevice (`devicectl`) knows this device.
    public var supportsCoreDevice: Bool {
        kind == .physical && sources.contains(.coreDevice)
    }

    /// The immutable target captured when an operation starts.
    public var target: DeviceTarget {
        DeviceTarget(
            kind: kind,
            udid: udid,
            name: name,
            osVersion: osVersion,
            usbmuxDeviceID: usbmuxDeviceID,
            coreDeviceIdentifier: coreDeviceIdentifier,
            transport: primaryTransport
        )
    }
}

/// An immutable snapshot of the device an operation was started against. Operations never read
/// the "currently selected" device after they start, so changing the selection (or a second
/// device attaching) cannot redirect an in-flight operation.
public struct DeviceTarget: Hashable, Sendable, Codable {
    public let kind: DeviceKind
    public let udid: String
    public let name: String
    public let osVersion: String?
    public let usbmuxDeviceID: Int?
    public let coreDeviceIdentifier: String?
    public let transport: DeviceTransport?

    public init(kind: DeviceKind, udid: String, name: String, osVersion: String?, usbmuxDeviceID: Int?, coreDeviceIdentifier: String?, transport: DeviceTransport?) {
        self.kind = kind
        self.udid = udid
        self.name = name
        self.osVersion = osVersion
        self.usbmuxDeviceID = usbmuxDeviceID
        self.coreDeviceIdentifier = coreDeviceIdentifier
        self.transport = transport
    }

    /// The value passed to `devicectl --device`. The UDID is preferred; network-only devices
    /// without a UDID fall back to the CoreDevice identifier.
    public var coreDeviceSelector: String {
        udid.isEmpty ? (coreDeviceIdentifier ?? udid) : udid
    }

    /// The last six alphanumeric characters of the UDID, used in typed confirmations.
    public var confirmationSuffix: String {
        let cleaned = udid.uppercased().filter { $0.isLetter || $0.isNumber }
        return String(cleaned.suffix(6))
    }

    /// Short label for logs and session activity: "Name (…ABC123)".
    public var shortLabel: String {
        "\(name) (…\(confirmationSuffix))"
    }

    public var osMajorVersion: Int? {
        guard let osVersion, let major = osVersion.split(separator: ".").first else { return nil }
        return Int(major)
    }
}
