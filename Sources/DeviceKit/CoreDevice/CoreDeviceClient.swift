import Foundation
import OSLog
import ToolkitCore

/// Typed access to Apple's CoreDevice service through `xcrun devicectl`.
///
/// Only the documented, versioned JSON output (`--json-output`) is parsed; human-readable
/// output is kept for display only. Every call names its target explicitly with `--device`.
public struct CoreDeviceClient: Sendable {
    public let runner: CommandRunning
    private let logger = ToolkitLog.deviceCommunication

    public init(runner: CommandRunning = ProcessCommandRunner()) {
        self.runner = runner
    }

    /// The raw outcome of one devicectl call.
    public struct Response: Sendable {
        public let json: JSONValue
        public let command: CommandResult

        public var result: JSONValue? { json["result"] }
    }

    // MARK: Discovery and information

    public func listDevices(timeout: TimeInterval = 30) async throws -> [CoreDeviceRecord] {
        let response = try await invoke(["list", "devices"], timeout: timeout, displayName: "devicectl list devices")
        return Self.parseDevices(response.json)
    }

    public func details(_ target: DeviceTarget, timeout: TimeInterval = 30) async throws -> (record: CoreDeviceRecord?, response: Response) {
        let response = try await invoke(["device", "info", "details", "--device", target.coreDeviceSelector], timeout: timeout, displayName: "devicectl device info details")
        let record = response.result.flatMap(CoreDeviceRecord.init(json:))
        return (record, response)
    }

    public func apps(_ target: DeviceTarget, includeSystemApps: Bool = true) async throws -> [CoreDeviceApp] {
        var arguments = ["device", "info", "apps", "--device", target.coreDeviceSelector]
        if includeSystemApps { arguments.append("--include-default-apps") }
        let response = try await invoke(arguments, timeout: 90, displayName: "devicectl device info apps")
        return Self.parseApps(response.json)
    }

    public func processes(_ target: DeviceTarget) async throws -> [CoreDeviceProcess] {
        let response = try await invoke(["device", "info", "processes", "--device", target.coreDeviceSelector], timeout: 60, displayName: "devicectl device info processes")
        return Self.parseProcesses(response.json)
    }

    public func lockState(_ target: DeviceTarget) async throws -> CoreDeviceLockState {
        let response = try await invoke(["device", "info", "lockState", "--device", target.coreDeviceSelector], timeout: 30, displayName: "devicectl device info lockState")
        return Self.parseLockState(response.json)
    }

    /// Queries developer disk image services. With `autoMount`, CoreDevice installs the
    /// correct personalized DDI first (the Apple-supported replacement for manual mounting).
    public func ddiServices(_ target: DeviceTarget, autoMount: Bool) async throws -> Response {
        try await invoke(
            ["device", "info", "ddiServices", "--device", target.coreDeviceSelector, autoMount ? "--auto-mount-ddis" : "--no-auto-mount-ddis"],
            timeout: autoMount ? 600 : 60,
            displayName: autoMount ? "devicectl device info ddiServices (mount)" : "devicectl device info ddiServices"
        )
    }

    public func displays(_ target: DeviceTarget) async throws -> Response {
        try await invoke(["device", "info", "displays", "--device", target.coreDeviceSelector], timeout: 30, displayName: "devicectl device info displays")
    }

    public func profiles(_ target: DeviceTarget, type: String? = nil) async throws -> Response {
        var arguments = ["device", "profile", "list", "--device", target.coreDeviceSelector]
        if let type { arguments += ["--type", type] }
        return try await invoke(arguments, timeout: 60, displayName: "devicectl device profile list")
    }

    public func orientation(_ target: DeviceTarget) async throws -> Response {
        try await invoke(["device", "orientation", "get", "--device", target.coreDeviceSelector], timeout: 30, displayName: "devicectl device orientation get")
    }

    public func crashLogs(_ target: DeviceTarget) async throws -> [CoreDeviceFile] {
        let response = try await invoke(
            ["device", "info", "files", "--device", target.coreDeviceSelector, "--domain-type", "systemCrashLogs"],
            timeout: 120,
            displayName: "devicectl device info files (crash logs)"
        )
        return Self.parseFiles(response.json)
    }

    public func preferredDDI(platform: String = "iOS") async throws -> Response {
        try await invoke(["list", "preferredDDI", "--platform", platform], timeout: 60, displayName: "devicectl list preferredDDI")
    }

    // MARK: Host-side operations

    public func copyCrashLogs(_ target: DeviceTarget, source: String = "/", to destination: URL) async throws -> Response {
        try await invoke(
            ["device", "copy", "from", "--device", target.coreDeviceSelector, "--domain-type", "systemCrashLogs", "--source", source, "--destination", destination.path],
            timeout: 900,
            displayName: "devicectl device copy from (crash logs)"
        )
    }

    public func screenshot(_ target: DeviceTarget, to destination: URL) async throws -> Response {
        guard destination.pathExtension.lowercased() == "png" else {
            throw ToolkitError.invalidInput("Screenshots are saved as PNG files. Choose a file name ending in .png.")
        }
        return try await invoke(["device", "capture", "screenshot", "--device", target.coreDeviceSelector, "--destination", destination.path], timeout: 120, displayName: "devicectl device capture screenshot")
    }

    public func sysdiagnose(_ target: DeviceTarget, destination: URL, fullLogs: Bool) async throws -> Response {
        var arguments = ["device", "sysdiagnose", "--device", target.coreDeviceSelector, "--destination", destination.path]
        if fullLogs { arguments.append("--gather-full-logs") }
        return try await invoke(arguments, timeout: 1_800, displayName: "devicectl device sysdiagnose")
    }

    public func updateHostDDIs() async throws -> Response {
        try await invoke(["manage", "ddis", "update"], timeout: 900, displayName: "devicectl manage ddis update")
    }

    public func pair(_ target: DeviceTarget) async throws -> Response {
        try await invoke(["manage", "pair", "--device", target.coreDeviceSelector], timeout: 180, displayName: "devicectl manage pair")
    }

    // MARK: Device-changing operations

    public func install(appAt path: URL, on target: DeviceTarget) async throws -> Response {
        try await invoke(["device", "install", "app", "--device", target.coreDeviceSelector, path.path], timeout: 900, displayName: "devicectl device install app")
    }

    public func uninstall(bundleIdentifier: String, on target: DeviceTarget) async throws -> Response {
        try BundleIdentifier.validate(bundleIdentifier)
        return try await invoke(["device", "uninstall", "app", "--device", target.coreDeviceSelector, bundleIdentifier], timeout: 300, displayName: "devicectl device uninstall app")
    }

    public func launch(bundleIdentifier: String, on target: DeviceTarget, terminateExisting: Bool) async throws -> Response {
        try BundleIdentifier.validate(bundleIdentifier)
        var arguments = ["device", "process", "launch", "--device", target.coreDeviceSelector]
        if terminateExisting { arguments.append("--terminate-existing") }
        arguments.append(bundleIdentifier)
        return try await invoke(arguments, timeout: 120, displayName: "devicectl device process launch")
    }

    public func terminate(pid: Int, on target: DeviceTarget, force: Bool) async throws -> Response {
        guard pid > 0 else { throw ToolkitError.invalidInput("The process identifier must be a positive number.") }
        var arguments = ["device", "process", "terminate", "--device", target.coreDeviceSelector, "--pid", String(pid)]
        if force { arguments.append("--kill") }
        return try await invoke(arguments, timeout: 60, displayName: "devicectl device process terminate")
    }

    public func openURL(_ url: URL, on target: DeviceTarget) async throws -> Response {
        guard let scheme = url.scheme, !scheme.isEmpty else {
            throw ToolkitError.invalidInput("Enter a complete URL including its scheme, for example https://example.com.")
        }
        return try await invoke(["device", "process", "openURL", "--device", target.coreDeviceSelector, url.absoluteString], timeout: 60, displayName: "devicectl device process openURL")
    }

    public func setLocation(latitude: Double, longitude: Double, on target: DeviceTarget) async throws -> Response {
        try Coordinate.validate(latitude: latitude, longitude: longitude)
        return try await invoke(
            ["device", "simulate", "location", "coordinate", "--device", target.coreDeviceSelector, "--latitude", Coordinate.format(latitude), "--longitude", Coordinate.format(longitude)],
            timeout: 120,
            displayName: "devicectl device simulate location coordinate"
        )
    }

    /// Starts constant-speed movement along waypoints (CoreDevice route simulation).
    public func simulateRoute(_ waypoints: [(latitude: Double, longitude: Double)], speedMetresPerSecond: Double, updateIntervalSeconds: Double, on target: DeviceTarget) async throws -> Response {
        guard waypoints.count >= 2 else { throw ToolkitError.invalidInput("A route needs at least two waypoints.") }
        guard speedMetresPerSecond.isFinite, speedMetresPerSecond > 0, speedMetresPerSecond <= 100 else {
            throw ToolkitError.invalidInput("Route speed must be between 0 and 100 metres per second.")
        }
        guard updateIntervalSeconds.isFinite, updateIntervalSeconds >= 0.5, updateIntervalSeconds <= 60 else {
            throw ToolkitError.invalidInput("The update interval must be between 0.5 and 60 seconds.")
        }
        for point in waypoints { try Coordinate.validate(latitude: point.latitude, longitude: point.longitude) }
        let directory = try SecureFileIO.makeTemporaryDirectory(prefix: "idt-route")
        defer { try? FileManager.default.removeItem(at: directory) }
        let routeFile = directory.appendingPathComponent("route.json")
        let document: [String: Any] = [
            "mode": "interval",
            "interval": updateIntervalSeconds,
            "speed": speedMetresPerSecond,
            "waypoints": waypoints.map { ["latitude": $0.latitude, "longitude": $0.longitude] },
        ]
        try SecureFileIO.writeNewFile(try JSONSerialization.data(withJSONObject: document, options: [.sortedKeys]), to: routeFile)
        return try await invoke(
            ["device", "simulate", "location", "route", "--device", target.coreDeviceSelector, "--route-file", routeFile.path],
            timeout: 120,
            displayName: "devicectl device simulate location route"
        )
    }

    public func clearLocation(on target: DeviceTarget) async throws -> Response {
        try await invoke(["device", "simulate", "location", "clear", "--device", target.coreDeviceSelector], timeout: 120, displayName: "devicectl device simulate location clear")
    }

    public func reboot(_ target: DeviceTarget) async throws -> Response {
        try await invoke(["device", "reboot", "--device", target.coreDeviceSelector], timeout: 180, displayName: "devicectl device reboot")
    }

    // MARK: Help (for the in-app help browser and the toolchain check)

    public func help(_ route: [String]) async throws -> String {
        let request = try XcodeTool.devicectl.request(["help"] + route, timeout: 20, displayName: "devicectl help \(route.joined(separator: " "))")
        let result = try await runner.run(request)
        let text = result.standardOutputText.isEmpty ? result.standardErrorText : result.standardOutputText
        guard result.succeeded || !text.isEmpty else {
            throw ToolkitError(.commandFailed, message: "Help for devicectl \(route.joined(separator: " ")) is unavailable.", technicalDetail: result.technicalSummary)
        }
        return text
    }

    // MARK: Invocation

    /// Runs devicectl with `--json-output` to a private temporary file, then parses and
    /// interprets the documented result envelope.
    public func invoke(_ arguments: [String], timeout: TimeInterval, displayName: String) async throws -> Response {
        let directory = try SecureFileIO.makeTemporaryDirectory(prefix: "idt-devicectl")
        defer { try? FileManager.default.removeItem(at: directory) }
        let jsonFile = directory.appendingPathComponent("result.json")
        let devicectlTimeout = max(5, Int(timeout.rounded()))
        let request = try XcodeTool.devicectl.request(
            arguments + ["--timeout", String(devicectlTimeout), "--json-output", jsonFile.path],
            timeout: timeout + 20,
            displayName: displayName
        )
        let result = try await runner.run(request)
        let data = try? Data(contentsOf: jsonFile)
        let json = data.flatMap { try? JSONValue.parse($0) }
        if let json, json["info"]?["outcome"]?.string == "success", result.succeeded {
            return Response(json: json, command: result)
        }
        let error = CoreDeviceErrorInterpreter.interpret(json: json, command: result, operation: displayName)
        logger.error("\(displayName, privacy: .public) failed: \(error.kind.rawValue, privacy: .public)")
        throw error
    }

    // MARK: Parsers (static for unit tests)

    public static func parseDevices(_ json: JSONValue) -> [CoreDeviceRecord] {
        (json.value(at: "result.devices")?.array ?? []).compactMap(CoreDeviceRecord.init(json:))
    }

    public static func parseApps(_ json: JSONValue) -> [CoreDeviceApp] {
        (json.value(at: "result.apps")?.array ?? [])
            .compactMap(CoreDeviceApp.init(json:))
            .sorted { $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending }
    }

    public static func parseProcesses(_ json: JSONValue) -> [CoreDeviceProcess] {
        (json.value(at: "result.runningProcesses")?.array ?? json.value(at: "result.processes")?.array ?? [])
            .compactMap(CoreDeviceProcess.init(json:))
            .sorted { $0.pid < $1.pid }
    }

    public static func parseLockState(_ json: JSONValue) -> CoreDeviceLockState {
        CoreDeviceLockState(
            passcodeRequired: json.value(at: "result.passcodeRequired")?.bool,
            unlockedSinceBoot: json.value(at: "result.unlockedSinceBoot")?.bool
        )
    }

    public static func parseFiles(_ json: JSONValue) -> [CoreDeviceFile] {
        (json.value(at: "result.files")?.array ?? []).compactMap(CoreDeviceFile.init(json:))
    }
}

/// Turns devicectl failures into actionable, plain-language errors.
public enum CoreDeviceErrorInterpreter {
    public static func interpret(json: JSONValue?, command: CommandResult, operation: String) -> ToolkitError {
        let descriptions = json.map { collectDescriptions($0["error"]) } ?? []
        let deviceText = descriptions.joined(separator: " ")
        let combined = (deviceText + " " + command.standardErrorText).lowercased()
        let detailLines = [
            descriptions.isEmpty ? nil : "Device error: " + descriptions.joined(separator: " — "),
            json?["error"]?["domain"]?.string.map { "Domain: \($0)" },
            json?["error"]?["code"]?.string.map { "Code: \($0)" },
            command.technicalSummary,
        ].compactMap { $0 }
        let detail = detailLines.joined(separator: "\n")

        func error(_ kind: ToolkitError.Kind, _ message: String, _ recovery: String) -> ToolkitError {
            ToolkitError(kind, message: message, recovery: recovery, technicalDetail: detail)
        }

        if (json == nil && command.exitCode == 72) || combined.contains("unable to find utility \"devicectl\"") {
            return error(.toolMissing, "CoreDevice tools (devicectl) are not available.", "Install Xcode 15 or later from the App Store, open it once, and select it with xcode-select.")
        }
        // devicectl rejected the command line itself: the installed Xcode predates this command or
        // option (for example Xcode 26.6 has no `device simulate location`). Checked before the
        // phrase matches below, which could otherwise match words in the usage text.
        let syntaxPhrases = ["unknown option", "unexpected argument", "unknown subcommand", "unrecognized subcommand"]
        if json == nil, syntaxPhrases.contains(where: combined.contains) {
            return error(.unsupported, "The installed Xcode's device tool (devicectl) does not support this command.", "This feature needs a newer Xcode (the app is verified with Xcode 27). Update Xcode, or open Tool Reference › Toolchain Check to see which features your Xcode supports.")
        }
        let notFoundPhrases = ["no devices matched", "device was not found", "device not found", "unable to locate device", "no device found", "could not find device", "no such device"]
        if notFoundPhrases.contains(where: combined.contains) {
            return error(.deviceNotFound, "The selected device is no longer available to Xcode's device service.", "Reconnect the device with a USB cable, unlock it, and refresh the device list.")
        }
        if combined.contains("developer mode") && (combined.contains("disabled") || combined.contains("not enabled") || combined.contains("is off") || combined.contains("turned off")) {
            return error(.developerModeDisabled, "Developer Mode is turned off on the selected device.", "On the device, open Settings › Privacy & Security › Developer Mode, turn it on, restart, and confirm when asked.")
        }
        if combined.contains("locked") || combined.contains("passcode") || combined.contains("unlock") {
            return error(.deviceLocked, "The selected device is locked.", "Unlock the device and keep it awake, then try again.")
        }
        if combined.contains("not paired") || combined.contains("pairing") || combined.contains("trust") {
            return error(.notPaired, "The selected device has not trusted this Mac.", "Unlock the device, connect it with a USB cable, and tap Trust when asked. Then try again.")
        }
        if combined.contains("developer disk image") || combined.contains("ddi") {
            return error(.developerDiskImageUnavailable, "The developer image could not be mounted on the selected device.", "Keep the device unlocked and connected with USB, make sure this Mac is online, and use “Mount Developer Image” on the Device page (its Options menu can switch to the built-in mount).")
        }
        if combined.contains("timed out") || combined.contains("timeout") {
            return error(.timedOut, "The device did not respond in time.", "Make sure the device is unlocked, awake, and connected, then try again.")
        }
        if combined.contains("disconnected") || combined.contains("connection was invalidated") || combined.contains("not connected") || combined.contains("unavailable") {
            return error(.deviceDisconnected, "Unable to communicate with the selected device.", "Make sure the device is unlocked, connected, and has trusted this Mac.")
        }
        if combined.contains("not supported") || combined.contains("unsupported") {
            return error(.unsupported, "The selected device or its iOS version does not support this operation.", "Check the device's iOS version and Xcode version; newer features need both to be current.")
        }
        let summary = descriptions.first ?? "\(operation) did not complete."
        return error(.commandFailed, "The device reported a problem: \(summary)", "Review the technical details, make sure the device is unlocked and connected, and try again.")
    }

    /// Collects localized descriptions from CoreDevice's error envelope, where values are
    /// often wrapped as `{"string": "..."}` and errors can be nested under NSUnderlyingError.
    static func collectDescriptions(_ error: JSONValue?, depth: Int = 0) -> [String] {
        guard let error, depth < 6 else { return [] }
        var results: [String] = []
        if let userInfo = error["userInfo"] {
            for key in ["NSLocalizedDescription", "NSLocalizedFailureReason", "NSLocalizedRecoverySuggestion"] {
                if let value = userInfo[key], let text = unwrapString(value), !results.contains(text) {
                    results.append(text)
                }
            }
            if let underlying = userInfo["NSUnderlyingError"] {
                let nested = underlying["error"] ?? underlying
                results += collectDescriptions(nested, depth: depth + 1).filter { !results.contains($0) }
            }
        }
        return results
    }

    static func unwrapString(_ value: JSONValue) -> String? {
        if let text = value.nonEmptyString { return text }
        if let text = value["string"]?.nonEmptyString { return text }
        return nil
    }
}

public enum BundleIdentifier {
    public static func validate(_ value: String) throws {
        let pattern = #"^[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$"#
        guard value.count <= 255, value.range(of: pattern, options: .regularExpression) != nil else {
            throw ToolkitError.invalidInput("“\(value)” is not a valid bundle identifier. Use a reverse-DNS name such as com.example.app.")
        }
    }
}

public enum Coordinate {
    public static func validate(latitude: Double, longitude: Double) throws {
        guard latitude.isFinite, (-90...90).contains(latitude) else {
            throw ToolkitError.invalidInput("Latitude must be between -90 and 90 degrees.")
        }
        guard longitude.isFinite, (-180...180).contains(longitude) else {
            throw ToolkitError.invalidInput("Longitude must be between -180 and 180 degrees.")
        }
    }

    public static func format(_ value: Double) -> String {
        String(format: "%.8f", value)
    }
}
