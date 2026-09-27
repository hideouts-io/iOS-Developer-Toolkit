import Foundation
import ToolkitCore

// MARK: - Diagnostics relay

/// `com.apple.mobile.diagnostics_relay`: battery, IORegistry, MobileGestalt, and general
/// diagnostics. Restart/shutdown requests are intentionally not implemented.
public struct DiagnosticsRelay: Sendable {
    public static let serviceName = "com.apple.mobile.diagnostics_relay"
    let connection: ServiceConnection

    public static func open(_ session: DeviceSession) async throws -> DiagnosticsRelay {
        DiagnosticsRelay(connection: try await session.openService(serviceName))
    }

    public func ioRegistry(entryClass: String? = nil, entryName: String? = nil, plane: String? = nil) async throws -> PlistValue {
        var request: [String: PlistValue] = ["Request": "IORegistry"]
        if let entryClass { request["EntryClass"] = .string(entryClass) }
        if let entryName { request["EntryName"] = .string(entryName) }
        if let plane { request["CurrentPlane"] = .string(plane) }
        return try await diagnostics(.dictionary(request))["IORegistry"] ?? .dictionary([:])
    }

    /// A battery snapshot from the IOPMPowerSource registry entry.
    public func battery() async throws -> PlistValue {
        try await ioRegistry(entryClass: "IOPMPowerSource")
    }

    public func mobileGestalt(keys: [String]) async throws -> PlistValue {
        let reply = try await diagnostics(["Request": "MobileGestalt", "MobileGestaltKeys": .array(keys.map(PlistValue.string))])
        return reply["MobileGestalt"] ?? .dictionary([:])
    }

    public func all() async throws -> PlistValue {
        try await diagnostics(["Request": "All"])
    }

    func diagnostics(_ request: PlistValue) async throws -> PlistValue {
        let reply = try await connection.messages.request(request, timeout: 60)
        guard reply["Status"]?.stringValue == "Success" else {
            throw ToolkitError(.serviceUnavailable, message: "The device did not return diagnostics.", technicalDetail: "Status: \(reply["Status"]?.stringValue ?? "missing")")
        }
        return reply["Diagnostics"] ?? .dictionary([:])
    }

    public func close() async {
        _ = try? await connection.messages.request(["Request": "Goodbye"], timeout: 5)
        await connection.close()
    }

    /// Keys that are useful and still answered on current iOS versions.
    public static let defaultGestaltKeys = [
        "ActivationState", "BasebandFirmwareVersion", "BluetoothAddress", "BuildVersion", "CPUArchitecture",
        "DeviceClass", "DeviceColor", "DeviceName", "DiskUsage", "HardwareModel", "HasBaseband",
        "InternationalMobileEquipmentIdentity", "MLBSerialNumber", "ModelNumber", "PasswordProtected",
        "ProductName", "ProductType", "ProductVersion", "RegionCode", "RegionInfo", "SerialNumber",
        "UniqueChipID", "UniqueDeviceID", "WifiAddress",
    ]
}

/// Summarizes the IOPMPowerSource entry in plain language.
public struct BatterySummary: Sendable, Hashable {
    public var percentage: Int?
    public var isCharging: Bool?
    public var externalConnected: Bool?
    public var cycleCount: Int?
    public var temperatureCelsius: Double?
    public var designCapacity: Int?
    public var maximumCapacity: Int?

    public init(registry: PlistValue) {
        percentage = registry["CurrentCapacity"]?.intValue
        isCharging = registry["IsCharging"]?.boolValue
        externalConnected = registry["ExternalConnected"]?.boolValue
        cycleCount = registry["CycleCount"]?.intValue
        temperatureCelsius = registry["Temperature"]?.doubleValue.map { $0 / 100 }
        designCapacity = registry["DesignCapacity"]?.intValue
        maximumCapacity = registry["AppleRawMaxCapacity"]?.intValue ?? registry["NominalChargeCapacity"]?.intValue
    }

    /// Estimated health as a percentage of design capacity, when both values are present.
    public var healthPercentage: Int? {
        guard let designCapacity, let maximumCapacity, designCapacity > 0 else { return nil }
        return min(100, Int((Double(maximumCapacity) / Double(designCapacity) * 100).rounded()))
    }
}

// MARK: - Installation proxy

public struct InstalledApplication: Sendable, Hashable, Identifiable {
    public var id: String { bundleIdentifier }
    public var bundleIdentifier: String
    public var name: String
    public var version: String?
    public var build: String?
    public var applicationType: String
    public var staticBytes: Int64?
    public var dynamicBytes: Int64?
    public var path: String?

    public var totalBytes: Int64? {
        let sizes = [staticBytes, dynamicBytes].compactMap { $0 }
        return sizes.isEmpty ? nil : sizes.reduce(0, +)
    }

    public var typeLabel: String {
        switch applicationType {
        case "User": return "Installed by user"
        case "System": return "Built-in"
        case "Hidden": return "Hidden system app"
        default: return applicationType
        }
    }

    public init(bundleIdentifier: String, name: String, version: String?, build: String?, applicationType: String, staticBytes: Int64?, dynamicBytes: Int64?, path: String?) {
        self.bundleIdentifier = bundleIdentifier
        self.name = name
        self.version = version
        self.build = build
        self.applicationType = applicationType
        self.staticBytes = staticBytes
        self.dynamicBytes = dynamicBytes
        self.path = path
    }

    public init?(plist: PlistValue) {
        guard let identifier = plist["CFBundleIdentifier"]?.stringValue, !identifier.isEmpty else { return nil }
        bundleIdentifier = identifier
        name = plist["CFBundleDisplayName"]?.stringValue ?? plist["CFBundleName"]?.stringValue ?? identifier
        version = plist["CFBundleShortVersionString"]?.stringValue
        build = plist["CFBundleVersion"]?.stringValue
        applicationType = plist["ApplicationType"]?.stringValue ?? "Unknown"
        staticBytes = plist["StaticDiskUsage"]?.int64Value.flatMap { $0 >= 0 ? $0 : nil }
        dynamicBytes = plist["DynamicDiskUsage"]?.int64Value.flatMap { $0 >= 0 ? $0 : nil }
        path = plist["Path"]?.stringValue
    }
}

/// `com.apple.mobile.installation_proxy`.
public struct InstallationProxy: Sendable {
    public static let serviceName = "com.apple.mobile.installation_proxy"
    let connection: ServiceConnection

    public static func open(_ session: DeviceSession) async throws -> InstallationProxy {
        InstallationProxy(connection: try await session.openService(serviceName))
    }

    public func browse(includeSizes: Bool, applicationType: String = "Any") async throws -> [InstalledApplication] {
        var attributes = ["CFBundleIdentifier", "CFBundleDisplayName", "CFBundleName", "CFBundleShortVersionString", "CFBundleVersion", "ApplicationType", "Path"]
        if includeSizes { attributes += ["StaticDiskUsage", "DynamicDiskUsage"] }
        try await connection.messages.send([
            "Command": "Browse",
            "ClientOptions": ["ApplicationType": .string(applicationType), "ReturnAttributes": .array(attributes.map(PlistValue.string))],
        ])
        var apps: [InstalledApplication] = []
        while true {
            let reply = try await connection.messages.receive(timeout: 120)
            if let error = reply["Error"]?.stringValue {
                throw ToolkitError(.serviceUnavailable, message: "The device could not list its apps.", technicalDetail: "\(error): \(reply["ErrorDescription"]?.stringValue ?? "")")
            }
            for item in reply["CurrentList"]?.arrayValue ?? [] {
                if let app = InstalledApplication(plist: item) { apps.append(app) }
            }
            if reply["Status"]?.stringValue == "Complete" { break }
        }
        return apps.sorted { $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending }
    }

    /// Uninstalls an app, reporting progress (0–100).
    public func uninstall(bundleIdentifier: String, progress: @Sendable (Int) -> Void = { _ in }) async throws {
        try BundleIdentifier.validate(bundleIdentifier)
        try await connection.messages.send(["Command": "Uninstall", "ApplicationIdentifier": .string(bundleIdentifier)])
        try await awaitCompletion(operation: "Removing the app", progress: progress)
    }

    /// Installs a package previously uploaded to `PublicStaging` with AFC.
    public func install(stagedPackagePath: String, developerPackage: Bool, progress: @Sendable (Int) -> Void = { _ in }) async throws {
        var options: [String: PlistValue] = [:]
        if developerPackage { options["PackageType"] = "Developer" }
        try await connection.messages.send(["Command": "Install", "PackagePath": .string(stagedPackagePath), "ClientOptions": .dictionary(options)])
        try await awaitCompletion(operation: "Installing the app", progress: progress)
    }

    func awaitCompletion(operation: String, progress: @Sendable (Int) -> Void) async throws {
        while true {
            let reply = try await connection.messages.receive(timeout: 900)
            if let error = reply["Error"]?.stringValue {
                throw InstallationProxy.interpret(error: error, description: reply["ErrorDescription"]?.stringValue, operation: operation)
            }
            if let percent = reply["PercentComplete"]?.intValue { progress(max(0, min(100, percent))) }
            if reply["Status"]?.stringValue == "Complete" {
                progress(100)
                return
            }
        }
    }

    static func interpret(error: String, description: String?, operation: String) -> ToolkitError {
        let detail = "\(error): \(description ?? "")"
        switch error {
        case "ApplicationVerificationFailed":
            return ToolkitError(.commandFailed, message: "iOS rejected the app's signature.", recovery: "The app must be signed with a provisioning profile that includes this device. Re-sign or rebuild it in Xcode.", technicalDetail: detail)
        case "DeviceOSVersionTooLow":
            return ToolkitError(.unsupported, message: "The app requires a newer iOS version than the device has.", technicalDetail: detail)
        case "APIInternalError", "InstallProhibited":
            return ToolkitError(.commandFailed, message: "iOS did not allow the installation.", recovery: "Check device management restrictions and available storage, then try again.", technicalDetail: detail)
        default:
            return ToolkitError(.commandFailed, message: "\(operation) failed on the device.", recovery: "Review the technical details for the device's reason.", technicalDetail: detail)
        }
    }

    public func close() async { await connection.close() }
}

// MARK: - Provisioning profiles (misagent)

/// `com.apple.misagent`: installed provisioning profiles (CMS-signed payloads).
public struct ProvisioningProfileService: Sendable {
    public static let serviceName = "com.apple.misagent"
    let connection: ServiceConnection

    public static func open(_ session: DeviceSession) async throws -> ProvisioningProfileService {
        ProvisioningProfileService(connection: try await session.openService(serviceName))
    }

    public func copyAll() async throws -> [Data] {
        let reply = try await connection.messages.request(["MessageType": "CopyAll", "ProfileType": "Provisioning"], timeout: 60)
        if let status = reply["Status"]?.intValue, status != 0 {
            throw ToolkitError(.serviceUnavailable, message: "The device did not list provisioning profiles.", technicalDetail: "misagent status \(status)")
        }
        return (reply["Payload"]?.arrayValue ?? []).compactMap(\.dataValue)
    }

    public func close() async { await connection.close() }
}

// MARK: - SpringBoard services

public struct SpringBoardServices: Sendable {
    public static let serviceName = "com.apple.springboardservices"
    let connection: ServiceConnection

    public static func open(_ session: DeviceSession) async throws -> SpringBoardServices {
        SpringBoardServices(connection: try await session.openService(serviceName))
    }

    public enum Orientation: Int, Sendable {
        case unknown = 0, portrait = 1, portraitUpsideDown = 2, landscapeLeft = 3, landscapeRight = 4

        public var label: String {
            switch self {
            case .unknown: return "Unknown"
            case .portrait: return "Portrait"
            case .portraitUpsideDown: return "Portrait (upside down)"
            case .landscapeLeft: return "Landscape (left)"
            case .landscapeRight: return "Landscape (right)"
            }
        }
    }

    public func interfaceOrientation() async throws -> Orientation {
        let reply = try await connection.messages.request(["command": "getInterfaceOrientation"])
        return Orientation(rawValue: reply["interfaceOrientation"]?.intValue ?? 0) ?? .unknown
    }

    public func homeScreenIconMetrics() async throws -> PlistValue {
        try await connection.messages.request(["command": "getHomeScreenIconMetrics"])
    }

    public func iconPNG(bundleIdentifier: String) async throws -> Data? {
        try BundleIdentifier.validate(bundleIdentifier)
        return try await connection.messages.request(["command": "getIconPNGData", "bundleId": .string(bundleIdentifier)])["pngData"]?.dataValue
    }

    public func close() async { await connection.close() }
}

// MARK: - Notification proxy

public struct NotificationProxy: Sendable {
    public static let serviceName = "com.apple.mobile.notification_proxy"
    let connection: ServiceConnection

    public static func open(_ session: DeviceSession) async throws -> NotificationProxy {
        NotificationProxy(connection: try await session.openService(serviceName))
    }

    public func post(_ name: String) async throws {
        try await connection.messages.send(["Command": "PostNotification", "Name": .string(name)])
    }

    public func observe(_ name: String) async throws {
        try await connection.messages.send(["Command": "ObserveNotification", "Name": .string(name)])
    }

    /// Waits for the next relayed notification name.
    public func nextNotification(timeout: TimeInterval?) async throws -> String? {
        let message = try await connection.messages.receive(timeout: timeout)
        if message["Command"]?.stringValue == "RelayNotification" {
            return message["Name"]?.stringValue
        }
        if message["Command"]?.stringValue == "ProxyDeath" { return nil }
        return message["Name"]?.stringValue
    }

    public func close() async {
        try? await connection.messages.send(["Command": "Shutdown"])
        await connection.close()
    }
}
