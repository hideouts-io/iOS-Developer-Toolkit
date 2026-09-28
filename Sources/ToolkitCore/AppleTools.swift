import Foundation

/// Fixed locations of the Apple tools the toolkit uses. Tools are never resolved through
/// `PATH`, so a same-named executable elsewhere cannot be substituted.
public enum AppleTool: String, CaseIterable, Sendable {
    case xcrun
    case xcodeSelect = "xcode-select"
    case codesign
    case security
    case hdiutil
    case open
    case ditto
    case rvictl
    case swVers = "sw_vers"

    public var candidates: [URL] {
        switch self {
        case .xcrun: return [URL(fileURLWithPath: "/usr/bin/xcrun")]
        case .xcodeSelect: return [URL(fileURLWithPath: "/usr/bin/xcode-select")]
        case .codesign: return [URL(fileURLWithPath: "/usr/bin/codesign")]
        case .security: return [URL(fileURLWithPath: "/usr/bin/security")]
        case .hdiutil: return [URL(fileURLWithPath: "/usr/bin/hdiutil")]
        case .open: return [URL(fileURLWithPath: "/usr/bin/open")]
        case .ditto: return [URL(fileURLWithPath: "/usr/bin/ditto")]
        case .rvictl: return [URL(fileURLWithPath: "/Library/Apple/usr/bin/rvictl"), URL(fileURLWithPath: "/usr/bin/rvictl")]
        case .swVers: return [URL(fileURLWithPath: "/usr/bin/sw_vers")]
        }
    }

    /// Whether the tool ships with Xcode (as opposed to macOS itself).
    public var requiresXcode: Bool {
        switch self {
        case .rvictl: return true
        default: return false
        }
    }

    public func locate() throws -> URL {
        for candidate in candidates where FileManager.default.isExecutableFile(atPath: candidate.path) {
            return candidate
        }
        throw ToolkitError(
            .toolMissing,
            message: "\(rawValue) is not available on this Mac.",
            recovery: requiresXcode
                ? "Install Xcode from the App Store and open it once to finish installing its components."
                : "Reinstall the macOS Command Line Tools with xcode-select --install.",
            technicalDetail: "Checked: " + candidates.map(\.path).joined(separator: ", ")
        )
    }
}

/// Builders for `xcrun`-dispatched Xcode tools (`devicectl`, `simctl`, `xctrace`, `xed`).
public enum XcodeTool: String, Sendable, CaseIterable {
    case devicectl
    case simctl
    case xctrace
    case xed

    public func request(
        _ arguments: [String],
        timeout: TimeInterval? = 60,
        displayName: String? = nil,
        standardInput: Data? = nil,
        outputLimit: Int = 32 * 1024 * 1024
    ) throws -> CommandRequest {
        let xcrun = try AppleTool.xcrun.locate()
        return CommandRequest(
            executable: xcrun,
            arguments: [rawValue] + arguments,
            standardInput: standardInput,
            timeout: timeout,
            outputLimit: outputLimit,
            displayName: displayName ?? CommandRequest.defaultDisplayName(tool: rawValue, arguments: arguments)
        )
    }
}

/// Describes which developer tooling is present on the host.
public struct DeveloperToolsStatus: Sendable, Hashable {
    public enum Availability: Sendable, Hashable {
        case available(version: String?)
        case missing(reason: String)
        /// The tool exists but did not answer in time (a busy Mac), which is not the same as
        /// Xcode being missing.
        case unresponsive(reason: String)

        public var isAvailable: Bool {
            if case .available = self { return true }
            return false
        }
    }

    public var developerDirectory: String?
    public var xcodeVersion: String?
    public var devicectl: Availability
    public var simctl: Availability
    public var xctrace: Availability

    public init(developerDirectory: String?, xcodeVersion: String?, devicectl: Availability, simctl: Availability, xctrace: Availability) {
        self.developerDirectory = developerDirectory
        self.xcodeVersion = xcodeVersion
        self.devicectl = devicectl
        self.simctl = simctl
        self.xctrace = xctrace
    }

    /// Only the Command Line Tools (no Xcode.app) are selected.
    public var isCommandLineToolsOnly: Bool {
        developerDirectory?.hasPrefix("/Library/Developer/CommandLineTools") ?? false
    }

    public static func probe(runner: CommandRunning) async -> DeveloperToolsStatus {
        var developerDirectory: String?
        if let selectURL = try? AppleTool.xcodeSelect.locate(),
           let result = try? await runner.run(CommandRequest(executable: selectURL, arguments: ["-p"], timeout: 10, displayName: "xcode-select -p")),
           result.succeeded {
            developerDirectory = result.standardOutputText.trimmingCharacters(in: .whitespacesAndNewlines)
        }

        async let devicectl = availability(runner: runner, tool: .devicectl, arguments: ["--version"])
        async let simctl = availability(runner: runner, tool: .simctl, arguments: ["help"])
        async let xctrace = availability(runner: runner, tool: .xctrace, arguments: ["version"])
        async let xcodeVersion = xcodeBuildVersion(runner: runner)

        return DeveloperToolsStatus(
            developerDirectory: developerDirectory,
            xcodeVersion: await xcodeVersion,
            devicectl: await devicectl,
            simctl: await simctl,
            xctrace: await xctrace
        )
    }

    private static func availability(runner: CommandRunning, tool: XcodeTool, arguments: [String]) async -> Availability {
        do {
            let result = try await runner.run(try tool.request(arguments, timeout: probeTimeout))
            guard result.succeeded else {
                return .missing(reason: "xcrun could not run \(tool.rawValue) (exit \(result.exitCode ?? -1)). \(result.standardErrorText.prefix(300))")
            }
            let firstLine = (result.standardOutputText + result.standardErrorText)
                .split(separator: "\n").first.map(String.init)?
                .trimmingCharacters(in: .whitespaces)
            return .available(version: tool == .simctl ? nil : firstLine)
        } catch let error as ToolkitError where error.kind == .timedOut {
            return .unresponsive(reason: error.message)
        } catch {
            return .missing(reason: (error as? ToolkitError)?.message ?? error.localizedDescription)
        }
    }

    /// Normally well under a second; a Mac busy booting a simulator can take much longer.
    static let probeTimeout: TimeInterval = 45

    private static func xcodeBuildVersion(runner: CommandRunning) async -> String? {
        guard let xcrun = try? AppleTool.xcrun.locate(),
              let result = try? await runner.run(CommandRequest(executable: xcrun, arguments: ["xcodebuild", "-version"], timeout: probeTimeout, displayName: "xcodebuild -version")),
              result.succeeded
        else { return nil }
        return result.standardOutputText.split(separator: "\n").map(String.init).joined(separator: " · ")
    }
}
