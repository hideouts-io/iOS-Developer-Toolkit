import DeviceKit
import Foundation
import ToolkitCore

/// Verifies that every Apple command-line route the app depends on exists in the installed
/// Xcode, with the options the app passes. It runs only `help` commands and never contacts a
/// device. (This replaces the earlier "Guided Command Drift" check for pymobiledevice3.)
public enum ToolchainCheck {
    public struct Route: Sendable, Hashable, Identifiable {
        public var id: String { "\(tool.rawValue) \(path.joined(separator: " "))" }
        public var tool: XcodeTool
        public var path: [String]
        public var requiredOptions: [String]
        public var usedFor: String
    }

    public enum State: String, Sendable {
        case available = "Available"
        case changed = "Changed"
        case missing = "Missing"
        case failed = "Could not check"
    }

    public struct Result: Sendable, Hashable, Identifiable {
        public var id: String { route.id }
        public var route: Route
        public var state: State
        public var detail: String
    }

    public static let routes: [Route] = [
        Route(tool: .devicectl, path: ["list", "devices"], requiredOptions: ["--json-output", "--timeout"], usedFor: "Device discovery"),
        Route(tool: .devicectl, path: ["device", "info", "details"], requiredOptions: ["--device"], usedFor: "Device details, Readiness Check"),
        Route(tool: .devicectl, path: ["device", "info", "apps"], requiredOptions: ["--device", "--include-default-apps"], usedFor: "Apps"),
        Route(tool: .devicectl, path: ["device", "info", "processes"], requiredOptions: ["--device"], usedFor: "Running processes, Evidence"),
        Route(tool: .devicectl, path: ["device", "info", "lockState"], requiredOptions: ["--device"], usedFor: "Readiness Check"),
        Route(tool: .devicectl, path: ["device", "info", "ddiServices"], requiredOptions: ["--auto-mount-ddis", "--no-auto-mount-ddis"], usedFor: "Developer services"),
        Route(tool: .devicectl, path: ["device", "info", "displays"], requiredOptions: ["--device"], usedFor: "Displays action"),
        Route(tool: .devicectl, path: ["device", "profile", "list"], requiredOptions: ["--type"], usedFor: "Configuration profiles"),
        Route(tool: .devicectl, path: ["device", "capture", "screenshot"], requiredOptions: ["--destination"], usedFor: "Screenshot"),
        Route(tool: .devicectl, path: ["device", "sysdiagnose"], requiredOptions: ["--destination"], usedFor: "Sysdiagnose"),
        Route(tool: .devicectl, path: ["device", "install", "app"], requiredOptions: ["--device"], usedFor: "Install App"),
        Route(tool: .devicectl, path: ["device", "uninstall", "app"], requiredOptions: ["--device"], usedFor: "Remove app"),
        Route(tool: .devicectl, path: ["device", "process", "launch"], requiredOptions: ["--terminate-existing"], usedFor: "Launch app"),
        Route(tool: .devicectl, path: ["device", "process", "terminate"], requiredOptions: ["--pid"], usedFor: "Stop a process"),
        Route(tool: .devicectl, path: ["device", "process", "openURL"], requiredOptions: ["--device"], usedFor: "Open URL"),
        Route(tool: .devicectl, path: ["device", "simulate", "location", "coordinate"], requiredOptions: ["--latitude", "--longitude"], usedFor: "Location Lab"),
        Route(tool: .devicectl, path: ["device", "simulate", "location", "route"], requiredOptions: ["--route-file"], usedFor: "Location Lab routes"),
        Route(tool: .devicectl, path: ["device", "simulate", "location", "clear"], requiredOptions: ["--device"], usedFor: "Location Lab"),
        Route(tool: .devicectl, path: ["device", "reboot"], requiredOptions: ["--device"], usedFor: "Restart device"),
        Route(tool: .devicectl, path: ["manage", "ddis", "update"], requiredOptions: [], usedFor: "Update developer images"),
        Route(tool: .devicectl, path: ["list", "preferredDDI"], requiredOptions: ["--platform"], usedFor: "Preferred developer image"),
        Route(tool: .simctl, path: ["list"], requiredOptions: ["-j"], usedFor: "Simulator discovery"),
        Route(tool: .simctl, path: ["location"], requiredOptions: ["start", "clear", "set"], usedFor: "Simulator location"),
        Route(tool: .simctl, path: ["io"], requiredOptions: ["screenshot"], usedFor: "Simulator screenshot"),
        Route(tool: .simctl, path: ["bootstatus"], requiredOptions: ["-b"], usedFor: "Start simulator"),
        Route(tool: .simctl, path: ["launch"], requiredOptions: ["--terminate-running-process"], usedFor: "Simulator launch"),
        Route(tool: .simctl, path: ["spawn"], requiredOptions: [], usedFor: "Simulator logs"),
        Route(tool: .xctrace, path: ["record"], requiredOptions: ["--template", "--device", "--time-limit", "--all-processes", "--no-prompt"], usedFor: "Instruments recordings"),
    ]

    public static func helpRequest(_ route: Route) throws -> CommandRequest {
        switch route.tool {
        case .devicectl, .simctl: return try route.tool.request(["help"] + route.path, timeout: 15, displayName: "\(route.tool.rawValue) help \(route.path.joined(separator: " "))")
        case .xctrace: return try route.tool.request(["help"] + route.path, timeout: 15, displayName: "xctrace help \(route.path.joined(separator: " "))")
        case .xed: return try route.tool.request(["--help"], timeout: 15)
        }
    }

    public static func evaluate(_ route: Route, helpText: String, succeeded: Bool) -> Result {
        let text = helpText.lowercased()
        if !succeeded || text.contains("unknown subcommand") || text.contains("error: unexpected argument") || text.contains("unrecognized subcommand") {
            return Result(route: route, state: .missing, detail: "The installed \(route.tool.rawValue) does not offer this command.")
        }
        let missing = route.requiredOptions.filter { !helpText.contains($0) }
        if !missing.isEmpty {
            return Result(route: route, state: .changed, detail: "Missing option(s): \(missing.joined(separator: ", "))")
        }
        return Result(route: route, state: .available, detail: "")
    }

    /// Why a tool cannot be checked at all (Xcode missing or not selected), or nil when it runs.
    static func unavailableReason(_ tool: XcodeTool, in status: DeveloperToolsStatus) -> String? {
        let availability: DeveloperToolsStatus.Availability
        switch tool {
        case .devicectl: availability = status.devicectl
        case .simctl: availability = status.simctl
        case .xctrace: availability = status.xctrace
        case .xed: return nil
        }
        guard case .missing(let reason) = availability else { return nil }
        if status.developerDirectory == nil || status.isCommandLineToolsOnly {
            return "Xcode is not installed or not selected (only the Command Line Tools are available). Install Xcode and open it once."
        }
        return "\(tool.rawValue) could not run: \(reason)"
    }

    public static func run(runner: CommandRunning, progress: @Sendable (Int, Int) -> Void = { _, _ in }) async -> [Result] {
        let tools = await DeveloperToolsStatus.probe(runner: runner)
        var results: [Result] = []
        for (index, route) in routes.enumerated() {
            if Task.isCancelled { break }
            if let reason = unavailableReason(route.tool, in: tools) {
                results.append(Result(route: route, state: .missing, detail: reason))
                progress(index + 1, routes.count)
                continue
            }
            do {
                let output = try await runner.run(try helpRequest(route))
                results.append(evaluate(route, helpText: output.standardOutputText + output.standardErrorText, succeeded: output.succeeded || !output.standardOutputText.isEmpty))
            } catch {
                results.append(Result(route: route, state: .failed, detail: (error as? ToolkitError)?.message ?? error.localizedDescription))
            }
            progress(index + 1, routes.count)
        }
        return results
    }

    public static func render(_ results: [Result]) -> String {
        var lines = ["Toolchain check — \(ISO8601.string(Date()))", ""]
        for state in [State.missing, .changed, .failed, .available] {
            let matching = results.filter { $0.state == state }
            guard !matching.isEmpty else { continue }
            lines.append("\(state.rawValue) (\(matching.count)):")
            lines += matching.map { "  \($0.route.id) — \($0.route.usedFor)\($0.detail.isEmpty ? "" : " — \($0.detail)")" }
            lines.append("")
        }
        return lines.joined(separator: "\n")
    }
}

/// Built-in reference for the Apple tools the app uses.
public enum ToolReference {
    public struct Topic: Sendable, Hashable, Identifiable {
        public var id: String { "\(tool.rawValue) \(path.joined(separator: " "))" }
        public var tool: XcodeTool
        public var path: [String]
        public var title: String { path.isEmpty ? tool.rawValue : "\(tool.rawValue) \(path.joined(separator: " "))" }
    }

    public static let roots: [Topic] = [
        Topic(tool: .devicectl, path: []),
        Topic(tool: .simctl, path: []),
        Topic(tool: .xctrace, path: []),
    ]

    public static func helpText(_ topic: Topic, runner: CommandRunning) async throws -> String {
        let arguments: [String]
        switch topic.tool {
        case .xed: arguments = ["--help"]
        default: arguments = ["help"] + topic.path
        }
        let result = try await runner.run(try topic.tool.request(arguments, timeout: 15, displayName: topic.title + " help"))
        let text = result.standardOutputText.isEmpty ? result.standardErrorText : result.standardOutputText
        guard !text.isEmpty else {
            throw ToolkitError(.commandFailed, message: "No help is available for \(topic.title).", technicalDetail: result.technicalSummary)
        }
        return text
    }

    /// Parses the subcommand list from a help page (devicectl's SUBCOMMANDS section, simctl's
    /// "Subcommands:" list, or xctrace's command list).
    public static func children(of topic: Topic, helpText: String) -> [Topic] {
        var names: [String] = []
        var inSection = false
        for rawLine in helpText.split(separator: "\n", omittingEmptySubsequences: false) {
            let line = String(rawLine)
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            if ["SUBCOMMANDS:", "Subcommands:", "commands:"].contains(trimmed) {
                inSection = true
                continue
            }
            guard inSection else { continue }
            if trimmed.isEmpty { continue }
            if trimmed.hasPrefix("See ") || (!line.hasPrefix(" ") && !line.hasPrefix("\t")) {
                inSection = false
                continue
            }
            let indent = line.prefix { $0 == " " || $0 == "\t" }.count
            guard indent <= 8, let first = trimmed.split(separator: " ").first else { continue }
            let name = String(first)
            if name.range(of: #"^[A-Za-z][A-Za-z0-9-]*$"#, options: .regularExpression) != nil, name != "help", !names.contains(name) {
                names.append(name)
            }
        }
        return names.map { Topic(tool: topic.tool, path: topic.path + [$0]) }
    }
}
