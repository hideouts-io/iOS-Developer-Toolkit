import Foundation
import ToolkitCore

/// What goes into a sanitized support bundle. Only aggregate, non-identifying state.
public struct SupportBundleContext: Sendable {
    public var workspace: String
    public var detectedDeviceCount: Int
    public var selectedDeviceKind: String?
    public var discoveryStatus: [String: String]
    public var capabilityCounts: [String: Int]
    public var toolchainReport: String
    public var developerTools: [String: String]
    public var statuses: [String: String]
    /// Literal values (device names, serials) to remove wherever they appear.
    public var redactions: [String]
    public var diagnosticLog: [DiagnosticLogEntry]

    public init(workspace: String, detectedDeviceCount: Int, selectedDeviceKind: String?, discoveryStatus: [String: String], capabilityCounts: [String: Int], toolchainReport: String, developerTools: [String: String], statuses: [String: String], redactions: [String], diagnosticLog: [DiagnosticLogEntry]) {
        self.workspace = workspace
        self.detectedDeviceCount = detectedDeviceCount
        self.selectedDeviceKind = selectedDeviceKind
        self.discoveryStatus = discoveryStatus
        self.capabilityCounts = capabilityCounts
        self.toolchainReport = toolchainReport
        self.developerTools = developerTools
        self.statuses = statuses
        self.redactions = redactions
        self.diagnosticLog = diagnosticLog
    }
}

/// Builds a local, reviewable ZIP for bug reports. Nothing is uploaded.
public enum SupportBundle {
    public static let readme = """
    iOS Developer Toolkit sanitized support bundle

    This archive was generated locally and is never uploaded by the application. It contains app and
    macOS version information, aggregate readiness states, sanitized status summaries, the toolchain
    check, and a sanitized copy of the app's own diagnostic log.

    It intentionally excludes device names, UDIDs, serial numbers, pairing records, backups, evidence
    cases, screenshots, packet captures, raw device logs, crash reports, app packages, passwords, and
    anything you typed. Common identifiers, paths, network addresses, and email addresses are redacted.
    Review the archive before sharing it.

    """

    public static func entries(for context: SupportBundleContext, now: Date = Date()) throws -> [(String, Data)] {
        func clean(_ text: String) -> String { Sanitizer.sanitize(text, redactions: context.redactions, limit: 200_000) }
        func json(_ object: Any) throws -> Data {
            var data = try JSONSerialization.data(withJSONObject: object, options: [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes])
            data.append(0x0A)
            return data
        }
        let info = ProcessInfo.processInfo
        #if arch(arm64)
        let architecture = "arm64"
        #else
        let architecture = "x86_64"
        #endif
        let environment: [String: Any] = [
            "toolkit_version": ToolkitVersion.current,
            "created_at": ISO8601.string(now),
            "macos_version": info.operatingSystemVersionString,
            "architecture": architecture,
            "developer_tools": context.developerTools.mapValues(clean),
        ]
        let summary: [String: Any] = [
            "workspace": context.workspace,
            "detected_device_count": context.detectedDeviceCount,
            "selected_device_kind": context.selectedDeviceKind ?? NSNull(),
            "discovery_status": context.discoveryStatus.mapValues(clean),
            "capability_state_counts": context.capabilityCounts,
            "statuses": context.statuses.mapValues { clean($0).prefix(2_000) }.mapValues(String.init),
        ]
        let log = context.diagnosticLog.isEmpty ? "No diagnostic log entries were available.\n" : clean(DiagnosticLogReader.render(context.diagnosticLog)) + "\n"
        var entries: [(String, Data)] = [
            ("README.txt", Data(readme.utf8)),
            ("environment.json", try json(environment)),
            ("context.json", try json(summary)),
            ("toolchain-check.txt", Data((context.toolchainReport.isEmpty ? "The toolchain check has not been run in this session.\n" : clean(context.toolchainReport)).utf8)),
            ("diagnostic-log.txt", Data(log.utf8)),
        ]
        let hashes = Dictionary(entries.map { ($0.0, SecureFileIO.sha256(of: $0.1)) }, uniquingKeysWith: { $1 })
        entries.append(("SHA256SUMS.json", try json(["created_at": ISO8601.string(now), "entries": hashes])))
        return entries
    }

    /// Writes the bundle to a new `.zip` file (never overwriting). Returns the entry names.
    @discardableResult
    public static func write(to destination: URL, context: SupportBundleContext) throws -> [String] {
        guard destination.pathExtension.lowercased() == "zip" else { throw ToolkitError.invalidInput("Support bundles are saved as .zip files.") }
        var writer = ZipWriter()
        let entries = try entries(for: context)
        for (name, data) in entries { try writer.add(name: name, data: data) }
        try SecureFileIO.writeNewFile(writer.finalized(), to: destination)
        return entries.map(\.0)
    }
}
