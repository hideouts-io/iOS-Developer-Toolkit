import DeviceKit
import Foundation
import ToolkitCore

/// Instruments recordings with `xcrun xctrace record`, the Apple-supported replacement for the
/// DVT telemetry streams (sysmon, energy, graphics, network activity, KDebug) of earlier versions.
public enum InstrumentsRecorder {
    public struct Template: Sendable, Hashable, Identifiable {
        public var id: String { name }
        public var name: String
        public var purpose: String
        public var replaces: String
    }

    public static let templates: [Template] = [
        Template(name: "Activity Monitor", purpose: "CPU, memory, disk, and network use per process.", replaces: "DVT sysmon system/process metrics"),
        Template(name: "Network", purpose: "Network connections and traffic per process.", replaces: "DVT network activity (netstat)"),
        Template(name: "Power Profiler", purpose: "Energy use by CPU, GPU, display, and networking.", replaces: "DVT energy monitor"),
        Template(name: "Time Profiler", purpose: "Where processes spend CPU time.", replaces: "DVT process sampling"),
        Template(name: "System Trace", purpose: "Threads, system calls, and scheduling (kernel trace).", replaces: "CoreProfile / KDebug tracing"),
        Template(name: "Animation Hitches", purpose: "Frame timing and hitches.", replaces: "DVT graphics monitor"),
        Template(name: "Logging", purpose: "os_log and signpost activity alongside other data.", replaces: "DVT OSLog stream"),
    ]

    public static func request(template: String, target: DeviceTarget, durationSeconds: Int, output: URL) throws -> CommandRequest {
        guard templates.contains(where: { $0.name == template }) else {
            throw ToolkitError.invalidInput("Choose one of the listed Instruments templates.")
        }
        guard (1...3600).contains(durationSeconds) else {
            throw ToolkitError.invalidInput("Recording length must be between 1 second and 1 hour.")
        }
        guard output.pathExtension == "trace" else {
            throw ToolkitError.invalidInput("Instruments recordings are saved as .trace files.")
        }
        guard !FileManager.default.fileExists(atPath: output.path) else {
            throw ToolkitError.invalidInput("A recording with that name already exists. Choose a new name.")
        }
        guard target.kind != .demo else { throw ToolkitError(.unsupported, message: "Recordings are not available for the demo device.") }
        return try XcodeTool.xctrace.request(
            ["record", "--template", template, "--device", target.udid, "--all-processes", "--time-limit", "\(durationSeconds)s", "--output", output.path, "--no-prompt"],
            timeout: TimeInterval(durationSeconds + 180),
            displayName: "xctrace record (\(template))"
        )
    }
}

/// Whether Instruments can record from a device, read from `xcrun xctrace list devices` (the
/// supported replacement for the DVT reachability check of earlier versions).
public enum InstrumentsDeviceList {
    public enum Presence: Sendable, Equatable {
        case available
        /// Listed under an “Offline” heading: connected, but Instruments cannot record from it yet.
        case offline
        case notListed
    }

    public static func request() throws -> CommandRequest {
        try XcodeTool.xctrace.request(["list", "devices"], timeout: 60, displayName: "xctrace list devices")
    }

    /// Finds the device by the identifier in parentheses at the end of its line, e.g.
    /// `iPhone (26.3.1) (00008150-…)`, under the `== Devices ==`, `== Devices Offline ==`, and
    /// `== Simulators ==` headings.
    public static func presence(of udid: String, in output: String) -> Presence {
        var offline = false
        for rawLine in output.split(whereSeparator: \.isNewline) {
            let line = rawLine.trimmingCharacters(in: .whitespaces)
            if line.hasPrefix("=="), line.hasSuffix("==") {
                offline = line.localizedCaseInsensitiveContains("offline")
                continue
            }
            guard line.hasSuffix(")"), let open = line.lastIndex(of: "(") else { continue }
            let identifier = line[line.index(after: open)..<line.index(before: line.endIndex)]
            if identifier.caseInsensitiveCompare(udid) == .orderedSame {
                return offline ? .offline : .available
            }
        }
        return .notListed
    }
}

/// Apple developer-tool handoffs.
public enum XcodeHandoff {
    public static func openProject(_ url: URL) throws -> CommandRequest {
        let name = url.lastPathComponent
        guard ["xcodeproj", "xcworkspace"].contains(url.pathExtension) || name == "Package.swift" else {
            throw ToolkitError.invalidInput("Choose an .xcodeproj, .xcworkspace, or Package.swift.")
        }
        guard FileManager.default.fileExists(atPath: url.path) else { throw ToolkitError.fileSystem("The project does not exist.", path: url.path) }
        return try XcodeTool.xed.request([url.path], timeout: 60, displayName: "xed")
    }

    public static func openResult(_ url: URL) throws -> CommandRequest {
        guard ["xcresult", "trace"].contains(url.pathExtension) else {
            throw ToolkitError.invalidInput("Choose an .xcresult or .trace bundle.")
        }
        guard FileManager.default.fileExists(atPath: url.path) else { throw ToolkitError.fileSystem("The bundle does not exist.", path: url.path) }
        return CommandRequest(executable: try AppleTool.open.locate(), arguments: [url.path], timeout: 60, displayName: "open \(url.pathExtension)")
    }

    public static func remoteVirtualInterfaces() throws -> CommandRequest {
        CommandRequest(executable: try AppleTool.rvictl.locate(), arguments: ["-l"], timeout: 30, displayName: "rvictl -l")
    }
}
