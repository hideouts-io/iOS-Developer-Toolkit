import Foundation
import ToolkitCore

/// One device as reported by `devicectl list devices` / `device info details`.
public struct CoreDeviceRecord: Sendable, Hashable {
    public var identifier: String
    public var udid: String?
    public var name: String
    public var productType: String?
    public var marketingName: String?
    public var deviceType: String?
    public var platform: String?
    public var reality: String?
    public var hardwareModel: String?
    public var architecture: String?
    public var serialNumber: String?
    public var ecid: String?
    public var osVersion: String?
    public var buildVersion: String?
    public var developerMode: DeveloperModeState
    public var ddiServicesAvailable: Bool?
    public var bootState: String?
    public var transportType: String?
    public var pairingState: PairingState
    public var tunnelState: String?
    public var visibilityClass: String?
    public var capabilities: [String]
    public var raw: JSONValue

    public var isPhysical: Bool {
        guard let reality else { return true }
        return reality.lowercased() == "physical"
    }

    public var transport: DeviceTransport? {
        guard let transportType = transportType?.lowercased() else { return nil }
        if transportType.contains("wired") || transportType.contains("usb") { return .usb }
        if transportType.contains("network") || transportType.contains("wireless") || transportType.contains("wifi") { return .network }
        return nil
    }

    /// A merged `Device` for the UI.
    public func device(lastSeen: Date = Date()) -> Device {
        Device(
            kind: .physical,
            udid: udid ?? identifier,
            name: name,
            productType: productType,
            marketingName: marketingName,
            family: DeviceFamily.from(productType: productType, deviceType: deviceType),
            osName: platform.map { $0 == "iOS" && deviceType == "iPad" ? "iPadOS" : $0 },
            osVersion: osVersion,
            buildVersion: buildVersion,
            architecture: architecture,
            hardwareModel: hardwareModel,
            serialNumber: serialNumber,
            ecid: ecid,
            transports: transport.map { [$0] } ?? [],
            pairingState: pairingState,
            developerMode: developerMode,
            ddiServicesAvailable: ddiServicesAvailable,
            tunnelState: tunnelState,
            coreDeviceIdentifier: identifier,
            sources: [.coreDevice],
            lastSeen: lastSeen
        )
    }

    /// Parses a device object. Both the classic property groups and the newer flattened
    /// `properties` dictionary are read, so the parser works across devicectl JSON versions.
    public init?(json: JSONValue) {
        guard json.object != nil else { return nil }
        let hardware = json["hardwareProperties"]
        let device = json["deviceProperties"]
        let connection = json["connectionProperties"]
        let flattened = CoreDeviceRecord.flatten(json["properties"])

        func pick(_ group: JSONValue?, _ key: String) -> JSONValue? {
            group?[key] ?? flattened[key]
        }

        guard let identifier = json["identifier"]?.nonEmptyString ?? pick(hardware, "udid")?.nonEmptyString else { return nil }
        self.identifier = identifier
        udid = pick(hardware, "udid")?.nonEmptyString
        name = pick(device, "name")?.nonEmptyString ?? pick(hardware, "marketingName")?.nonEmptyString ?? "Unnamed device"
        productType = pick(hardware, "productType")?.nonEmptyString
        marketingName = pick(hardware, "marketingName")?.nonEmptyString
        deviceType = pick(hardware, "deviceType")?.nonEmptyString
        platform = pick(hardware, "platform")?.nonEmptyString
        reality = pick(hardware, "reality")?.nonEmptyString
        hardwareModel = pick(hardware, "hardwareModel")?.nonEmptyString
        architecture = pick(hardware, "cpuType")?["name"]?.nonEmptyString
        serialNumber = pick(hardware, "serialNumber")?.nonEmptyString
        ecid = pick(hardware, "ecid")?.string
        osVersion = pick(device, "osVersionNumber")?.nonEmptyString
        buildVersion = pick(device, "osBuildUpdate")?.nonEmptyString
        developerMode = DeveloperModeState.fromCoreDevice(pick(device, "developerModeStatus")?.string)
        ddiServicesAvailable = pick(device, "ddiServicesAvailable")?.bool
        bootState = pick(device, "bootState")?.nonEmptyString
        transportType = pick(connection, "transportType")?.nonEmptyString
        pairingState = PairingState.fromCoreDevice(pick(connection, "pairingState")?.string)
        tunnelState = pick(connection, "tunnelState")?.nonEmptyString
        visibilityClass = json["visibilityClass"]?.nonEmptyString ?? flattened["visibilityClass"]?.nonEmptyString
        capabilities = json["capabilities"]?.array?.compactMap { $0["name"]?.nonEmptyString } ?? []
        raw = json
    }

    /// Flattens `properties.<category>.<field>` into `field → value`.
    static func flatten(_ properties: JSONValue?) -> [String: JSONValue] {
        guard let categories = properties?.object else { return [:] }
        var flattened: [String: JSONValue] = [:]
        for (_, category) in categories {
            guard let fields = category.object else { continue }
            for (key, value) in fields where flattened[key] == nil {
                flattened[key] = value
            }
        }
        return flattened
    }
}

public struct CoreDeviceApp: Sendable, Hashable, Identifiable {
    public var id: String { bundleIdentifier }
    public var name: String
    public var bundleIdentifier: String
    public var version: String?
    public var bundleVersion: String?
    public var isRemovable: Bool?
    public var isBuiltByDeveloper: Bool?
    public var isAppClip: Bool?
    public var isHidden: Bool?
    public var isDefaultApp: Bool?
    public var url: String?

    public init?(json: JSONValue) {
        guard let bundleIdentifier = json["bundleIdentifier"]?.nonEmptyString else { return nil }
        self.bundleIdentifier = bundleIdentifier
        name = json["name"]?.nonEmptyString ?? bundleIdentifier
        version = json["version"]?.nonEmptyString
        bundleVersion = json["bundleVersion"]?.nonEmptyString
        isRemovable = json["removable"]?.bool
        isBuiltByDeveloper = json["builtByDeveloper"]?.bool
        isAppClip = json["appClip"]?.bool
        isHidden = json["hidden"]?.bool
        isDefaultApp = json["defaultApp"]?.bool
        url = json["url"]?.nonEmptyString
    }
}

public struct CoreDeviceProcess: Sendable, Hashable, Identifiable {
    public var id: Int { pid }
    public var pid: Int
    public var executablePath: String?

    public var name: String {
        guard let executablePath else { return "PID \(pid)" }
        let trimmed = executablePath.hasSuffix("/") ? String(executablePath.dropLast()) : executablePath
        return URL(string: trimmed)?.lastPathComponent.removingPercentEncoding
            ?? (trimmed as NSString).lastPathComponent
    }

    public init?(json: JSONValue) {
        guard let pid = json["processIdentifier"]?.int else { return nil }
        self.pid = pid
        executablePath = json["executable"]?.nonEmptyString
    }

    public init(pid: Int, executablePath: String?) {
        self.pid = pid
        self.executablePath = executablePath
    }
}

public struct CoreDeviceLockState: Sendable, Hashable {
    public var passcodeRequired: Bool?
    public var unlockedSinceBoot: Bool?

    public init(passcodeRequired: Bool?, unlockedSinceBoot: Bool?) {
        self.passcodeRequired = passcodeRequired
        self.unlockedSinceBoot = unlockedSinceBoot
    }

    public var summary: String {
        switch (passcodeRequired, unlockedSinceBoot) {
        case (.some(true), _): return "Locked — unlock the device to continue."
        case (.some(false), .some(true)): return "Unlocked"
        case (.some(false), .some(false)): return "Unlocked, but not unlocked since restart"
        case (.some(false), .none): return "Unlocked"
        case (.none, .some(false)): return "Not unlocked since restart — unlock it once to continue."
        default: return "Unknown"
        }
    }
}

public struct CoreDeviceFile: Sendable, Hashable, Identifiable {
    public var id: String { path }
    public var path: String
    public var size: Int64?
    public var modified: Date?

    public init?(json: JSONValue) {
        guard let path = json["relativePath"]?.nonEmptyString ?? json["path"]?.nonEmptyString ?? json["name"]?.nonEmptyString else { return nil }
        self.path = path
        size = (json["size"] ?? json["fileSize"])?.double.map { Int64($0) }
        modified = (json["lastModDate"] ?? json["modificationDate"])?.string.flatMap(ISO8601.parse)
    }
}
