import Foundation
import ToolkitCore

public struct SimulatorRuntime: Sendable, Hashable, Identifiable {
    public var id: String { identifier }
    public var identifier: String
    public var name: String
    public var platform: String?
    public var version: String?
    public var buildVersion: String?
    public var isAvailable: Bool
}

public struct SimulatorRecord: Sendable, Hashable, Identifiable {
    public var id: String { udid }
    public var udid: String
    public var name: String
    public var state: SimulatorState
    public var isAvailable: Bool
    public var availabilityError: String?
    public var deviceTypeIdentifier: String?
    public var runtime: SimulatorRuntime?
    public var runtimeIdentifier: String
    public var dataPath: String?
    public var logPath: String?
    /// The device type's display name, such as "iPad Air 11-inch (M4)".
    public var deviceTypeName: String?
    /// The hardware identifier the simulator models, such as "iPhone18,1".
    public var modelIdentifier: String? = nil

    /// Simulators run natively on the Mac's processor.
    static var hostArchitecture: String {
        #if arch(arm64)
        return "arm64 (this Mac)"
        #else
        return "x86_64 (this Mac)"
        #endif
    }

    public var device: Device {
        let typeName = deviceTypeName ?? deviceTypeIdentifier?.components(separatedBy: ".").last?.replacingOccurrences(of: "-", with: " ")
        return Device(
            kind: .simulator,
            udid: udid,
            name: name,
            productType: modelIdentifier,
            marketingName: typeName,
            family: DeviceFamily.from(productType: typeName),
            osName: runtime?.platform ?? SimulatorClient.platform(fromRuntimeIdentifier: runtimeIdentifier),
            osVersion: runtime?.version ?? SimulatorClient.version(fromRuntimeIdentifier: runtimeIdentifier),
            buildVersion: runtime?.buildVersion,
            architecture: SimulatorRecord.hostArchitecture,
            transports: [.local],
            pairingState: .notApplicable,
            developerMode: .notApplicable,
            simulatorState: state,
            simulatorRuntime: runtime?.name ?? runtimeIdentifier,
            sources: [.simctl]
        )
    }
}

public struct SimulatorApp: Sendable, Hashable, Identifiable {
    public var id: String { bundleIdentifier }
    public var bundleIdentifier: String
    public var name: String
    public var version: String?
    public var build: String?
    public var applicationType: String?
    public var bundlePath: String?
}

/// Typed access to `xcrun simctl`. Every call targets an explicit simulator UDID.
public struct SimulatorClient: Sendable {
    public let runner: CommandRunning

    public init(runner: CommandRunning = ProcessCommandRunner()) {
        self.runner = runner
    }

    // MARK: Discovery

    public func list() async throws -> [SimulatorRecord] {
        async let devicesResult = run(["list", "devices", "--json"], timeout: 60, name: "simctl list devices")
        async let runtimesResult = run(["list", "runtimes", "--json"], timeout: 60, name: "simctl list runtimes")
        async let typesResult = run(["list", "devicetypes", "--json"], timeout: 60, name: "simctl list devicetypes")
        let devicesJSON = try JSONValue.parse(try await devicesResult.standardOutput)
        let runtimesJSON = (try? await runtimesResult).flatMap { try? JSONValue.parse($0.standardOutput) }
        let typesJSON = (try? await typesResult).flatMap { try? JSONValue.parse($0.standardOutput) }
        return Self.parse(devices: devicesJSON, runtimes: runtimesJSON, deviceTypes: typesJSON)
    }

    public static func parse(devices: JSONValue, runtimes: JSONValue?, deviceTypes: JSONValue? = nil) -> [SimulatorRecord] {
        var typeNames: [String: String] = [:]
        var typeModels: [String: String] = [:]
        for item in deviceTypes?["devicetypes"]?.array ?? [] {
            guard let identifier = item["identifier"]?.nonEmptyString else { continue }
            typeNames[identifier] = item["name"]?.nonEmptyString
            typeModels[identifier] = item["modelIdentifier"]?.nonEmptyString
        }
        var runtimeTable: [String: SimulatorRuntime] = [:]
        for item in runtimes?["runtimes"]?.array ?? [] {
            guard let identifier = item["identifier"]?.nonEmptyString else { continue }
            runtimeTable[identifier] = SimulatorRuntime(
                identifier: identifier,
                name: item["name"]?.nonEmptyString ?? identifier,
                platform: item["platform"]?.nonEmptyString,
                version: item["version"]?.nonEmptyString,
                buildVersion: item["buildversion"]?.nonEmptyString,
                isAvailable: item["isAvailable"]?.bool ?? true
            )
        }
        var records: [SimulatorRecord] = []
        for (runtimeIdentifier, list) in devices["devices"]?.object ?? [:] {
            for item in list.array ?? [] {
                guard let udid = item["udid"]?.nonEmptyString, let name = item["name"]?.nonEmptyString else { continue }
                records.append(SimulatorRecord(
                    udid: udid,
                    name: name,
                    state: SimulatorState(simctlValue: item["state"]?.string),
                    isAvailable: item["isAvailable"]?.bool ?? true,
                    availabilityError: item["availabilityError"]?.nonEmptyString,
                    deviceTypeIdentifier: item["deviceTypeIdentifier"]?.nonEmptyString,
                    runtime: runtimeTable[runtimeIdentifier],
                    runtimeIdentifier: runtimeIdentifier,
                    dataPath: item["dataPath"]?.nonEmptyString,
                    logPath: item["logPath"]?.nonEmptyString,
                    deviceTypeName: item["deviceTypeIdentifier"]?.nonEmptyString.flatMap { typeNames[$0] },
                    modelIdentifier: item["deviceTypeIdentifier"]?.nonEmptyString.flatMap { typeModels[$0] }
                ))
            }
        }
        return records.sorted {
            if $0.state == .booted, $1.state != .booted { return true }
            if $1.state == .booted, $0.state != .booted { return false }
            if $0.runtimeIdentifier != $1.runtimeIdentifier { return $0.runtimeIdentifier > $1.runtimeIdentifier }
            return $0.name.localizedStandardCompare($1.name) == .orderedAscending
        }
    }

    static func platform(fromRuntimeIdentifier identifier: String) -> String? {
        let last = identifier.components(separatedBy: ".").last ?? ""
        return last.split(separator: "-").first.map(String.init)
    }

    static func version(fromRuntimeIdentifier identifier: String) -> String? {
        let last = identifier.components(separatedBy: ".").last ?? ""
        let parts = last.split(separator: "-").dropFirst()
        return parts.isEmpty ? nil : parts.joined(separator: ".")
    }

    // MARK: Lifecycle

    public func boot(_ target: DeviceTarget) async throws {
        try requireSimulator(target)
        let result = try await runner.run(try XcodeTool.simctl.request(["boot", target.udid], timeout: 180, displayName: "simctl boot"))
        // "Unable to boot device in current state: Booted" is not a failure.
        if !result.succeeded && !result.standardErrorText.contains("current state: Booted") {
            throw interpret(result, operation: "Starting the simulator")
        }
    }

    public func shutdown(_ target: DeviceTarget) async throws {
        try requireSimulator(target)
        let result = try await runner.run(try XcodeTool.simctl.request(["shutdown", target.udid], timeout: 120, displayName: "simctl shutdown"))
        if !result.succeeded && !result.standardErrorText.contains("current state: Shutdown") {
            throw interpret(result, operation: "Shutting down the simulator")
        }
    }

    /// Opens Simulator.app showing this simulator.
    public func showInSimulatorApp(_ target: DeviceTarget) async throws {
        try requireSimulator(target)
        let open = try AppleTool.open.locate()
        let result = try await runner.run(CommandRequest(executable: open, arguments: ["-a", "Simulator", "--args", "-CurrentDeviceUDID", target.udid], timeout: 30, displayName: "open Simulator"))
        guard result.succeeded else { throw interpret(result, operation: "Opening Simulator") }
    }

    public func erase(_ target: DeviceTarget) async throws {
        try await simple(["erase", target.udid], target: target, timeout: 300, operation: "Erasing the simulator")
    }

    // MARK: Apps

    public func apps(_ target: DeviceTarget) async throws -> [SimulatorApp] {
        try requireSimulator(target)
        let result = try await run(["listapps", target.udid], timeout: 60, name: "simctl listapps")
        return try Self.parseApps(result.standardOutput)
    }

    /// `simctl listapps` prints an (OpenStep or XML) property list keyed by bundle identifier.
    public static func parseApps(_ data: Data) throws -> [SimulatorApp] {
        let plist = try PlistValue.decode(data)
        guard let dictionary = plist.dictionaryValue else {
            throw ToolkitError(.protocolViolation, message: "simctl returned an unexpected app list.")
        }
        return dictionary.compactMap { key, value -> SimulatorApp? in
            guard let info = value.dictionaryValue else { return nil }
            let identifier = info["CFBundleIdentifier"]?.stringValue ?? key
            return SimulatorApp(
                bundleIdentifier: identifier,
                name: info["CFBundleDisplayName"]?.stringValue ?? info["CFBundleName"]?.stringValue ?? identifier,
                version: info["CFBundleShortVersionString"]?.stringValue,
                build: info["CFBundleVersion"]?.stringValue,
                applicationType: info["ApplicationType"]?.stringValue,
                bundlePath: info["Path"]?.stringValue
            )
        }.sorted { $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending }
    }

    public func install(appAt path: URL, on target: DeviceTarget) async throws {
        guard path.pathExtension == "app" else {
            throw ToolkitError(.unsupported, message: "Simulators install .app bundles built for the simulator, not .ipa files.", recovery: "Build the app for an iOS Simulator destination in Xcode and choose the resulting .app bundle.")
        }
        try await simple(["install", target.udid, path.path], target: target, timeout: 600, operation: "Installing the app")
    }

    public func uninstall(bundleIdentifier: String, on target: DeviceTarget) async throws {
        try BundleIdentifier.validate(bundleIdentifier)
        try await simple(["uninstall", target.udid, bundleIdentifier], target: target, timeout: 120, operation: "Removing the app")
    }

    public func launch(bundleIdentifier: String, on target: DeviceTarget, terminateExisting: Bool) async throws -> String {
        try BundleIdentifier.validate(bundleIdentifier)
        try requireSimulator(target)
        var arguments = ["launch"]
        if terminateExisting { arguments.append("--terminate-running-process") }
        arguments += [target.udid, bundleIdentifier]
        let result = try await runner.run(try XcodeTool.simctl.request(arguments, timeout: 60, displayName: "simctl launch"))
        guard result.succeeded else { throw interpret(result, operation: "Launching the app") }
        return result.standardOutputText.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    public func terminate(bundleIdentifier: String, on target: DeviceTarget) async throws {
        try BundleIdentifier.validate(bundleIdentifier)
        try await simple(["terminate", target.udid, bundleIdentifier], target: target, timeout: 60, operation: "Stopping the app")
    }

    public func openURL(_ url: URL, on target: DeviceTarget) async throws {
        guard url.scheme?.isEmpty == false else {
            throw ToolkitError.invalidInput("Enter a complete URL including its scheme, for example https://example.com.")
        }
        try await simple(["openurl", target.udid, url.absoluteString], target: target, timeout: 60, operation: "Opening the URL")
    }

    // MARK: Capture

    public func screenshot(_ target: DeviceTarget, to destination: URL) async throws {
        guard destination.pathExtension.lowercased() == "png" else {
            throw ToolkitError.invalidInput("Screenshots are saved as PNG files. Choose a file name ending in .png.")
        }
        try await simple(["io", target.udid, "screenshot", "--type=png", destination.path], target: target, timeout: 60, operation: "Taking the screenshot")
    }

    /// The command for a live unified-log stream (consumed with `CommandRunning.stream`).
    public func logStreamRequest(_ target: DeviceTarget, level: String = "info", predicate: String? = nil) throws -> CommandRequest {
        try requireSimulator(target)
        guard ["default", "info", "debug"].contains(level) else { throw ToolkitError.invalidInput("Unsupported log level \(level).") }
        var arguments = ["spawn", target.udid, "log", "stream", "--style", "ndjson", "--level", level]
        if let predicate, !predicate.isEmpty { arguments += ["--predicate", predicate] }
        return try XcodeTool.simctl.request(arguments, timeout: nil, displayName: "simctl log stream", outputLimit: 1 << 20)
    }

    // MARK: Location

    public func setLocation(latitude: Double, longitude: Double, on target: DeviceTarget) async throws {
        try Coordinate.validate(latitude: latitude, longitude: longitude)
        try await simple(["location", target.udid, "set", "\(Coordinate.format(latitude)),\(Coordinate.format(longitude))"], target: target, timeout: 60, operation: "Setting the simulated location")
    }

    public func simulateRoute(_ waypoints: [(latitude: Double, longitude: Double)], speedMetresPerSecond: Double, updateIntervalSeconds: Double, on target: DeviceTarget) async throws {
        guard waypoints.count >= 2 else { throw ToolkitError.invalidInput("A route needs at least two waypoints.") }
        guard speedMetresPerSecond.isFinite, speedMetresPerSecond > 0 else { throw ToolkitError.invalidInput("Route speed must be positive.") }
        guard updateIntervalSeconds.isFinite, updateIntervalSeconds > 0 else { throw ToolkitError.invalidInput("The update interval must be positive.") }
        for point in waypoints { try Coordinate.validate(latitude: point.latitude, longitude: point.longitude) }
        let pairs = waypoints.map { "\(Coordinate.format($0.latitude)),\(Coordinate.format($0.longitude))" }
        try await simple(
            ["location", target.udid, "start", "--speed=\(speedMetresPerSecond)", "--interval=\(updateIntervalSeconds)"] + pairs,
            target: target, timeout: 60, operation: "Starting the simulated route"
        )
    }

    public func clearLocation(on target: DeviceTarget) async throws {
        try await simple(["location", target.udid, "clear"], target: target, timeout: 60, operation: "Clearing the simulated location")
    }

    // MARK: Appearance

    public func setAppearance(dark: Bool, on target: DeviceTarget) async throws {
        try await simple(["ui", target.udid, "appearance", dark ? "dark" : "light"], target: target, timeout: 60, operation: "Changing the appearance")
    }

    // MARK: Help

    public func help(_ route: [String]) async throws -> String {
        let result = try await runner.run(try XcodeTool.simctl.request(["help"] + route, timeout: 20, displayName: "simctl help"))
        return result.standardOutputText.isEmpty ? result.standardErrorText : result.standardOutputText
    }

    // MARK: Helpers

    private func requireSimulator(_ target: DeviceTarget) throws {
        guard target.kind == .simulator else {
            throw ToolkitError(.internalInconsistency, message: "This action is only available for simulators.", technicalDetail: "Target kind: \(target.kind.rawValue)")
        }
    }

    private func simple(_ arguments: [String], target: DeviceTarget, timeout: TimeInterval, operation: String) async throws {
        try requireSimulator(target)
        let result = try await runner.run(try XcodeTool.simctl.request(arguments, timeout: timeout, displayName: "simctl \(arguments.first ?? "")"))
        guard result.succeeded else { throw interpret(result, operation: operation) }
    }

    private func run(_ arguments: [String], timeout: TimeInterval, name: String) async throws -> CommandResult {
        let result = try await runner.run(try XcodeTool.simctl.request(arguments, timeout: timeout, displayName: name))
        guard result.succeeded else { throw interpret(result, operation: name) }
        return result
    }

    func interpret(_ result: CommandResult, operation: String) -> ToolkitError {
        let text = result.standardErrorText.lowercased()
        if text.contains("unable to find utility") || text.contains("xcrun: error") {
            return ToolkitError(.toolMissing, message: "Simulator tools are not available.", recovery: "Install Xcode and open it once to install its components.", technicalDetail: result.technicalSummary)
        }
        if text.contains("invalid device") || text.contains("no devices are booted") || text.contains("device not found") {
            return ToolkitError(.deviceNotFound, message: "The selected simulator no longer exists.", recovery: "Refresh the device list and choose another simulator.", technicalDetail: result.technicalSummary)
        }
        if text.contains("current state: shutdown") || text.contains("unable to lookup in current state: shutdown") {
            return ToolkitError(.serviceUnavailable, message: "The simulator is not running.", recovery: "Start the simulator, then try again.", technicalDetail: result.technicalSummary)
        }
        if text.contains("found nothing to terminate") {
            return ToolkitError(.commandFailed, message: "The app is not running on the simulator.", technicalDetail: result.technicalSummary)
        }
        return ToolkitError(.commandFailed, message: "\(operation) failed on the simulator.", recovery: "Check that the simulator is running and try again.", technicalDetail: result.technicalSummary)
    }
}
