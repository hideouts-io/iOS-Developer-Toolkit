import Foundation
import OSLog

/// Structured logging categories. Every log line goes through `Logger`, so it appears in
/// Console.app under the subsystem below and can be exported by the diagnostic log viewer.
///
/// Privacy: device identifiers, names, paths, and command arguments are logged with
/// `privacy: .private` (redacted unless a logging profile is installed). Only coarse state
/// (counts, outcomes, durations, error kinds) is logged publicly.
public enum LogCategory: String, CaseIterable, Sendable {
    case application = "Application"
    case deviceDiscovery = "DeviceDiscovery"
    case deviceCommunication = "DeviceCommunication"
    case commands = "Commands"
    case diagnostics = "Diagnostics"
    case security = "Security"
    case networking = "Networking"
    case filesystem = "Filesystem"
    case backup = "Backup"
    case location = "Location"
    case logs = "LiveLogs"
    case evidence = "Evidence"
}

public enum ToolkitLog {
    public static let subsystem = "io.hideouts.iOSDeveloperToolkit"

    public static func logger(_ category: LogCategory) -> Logger {
        Logger(subsystem: subsystem, category: category.rawValue)
    }

    public static let application = logger(.application)
    public static let deviceDiscovery = logger(.deviceDiscovery)
    public static let deviceCommunication = logger(.deviceCommunication)
    public static let commands = logger(.commands)
    public static let diagnostics = logger(.diagnostics)
    public static let security = logger(.security)
    public static let networking = logger(.networking)
    public static let filesystem = logger(.filesystem)
    public static let backup = logger(.backup)
    public static let location = logger(.location)
    public static let logs = logger(.logs)
    public static let evidence = logger(.evidence)
}

/// One entry read back from the unified log for the in-app diagnostic viewer.
public struct DiagnosticLogEntry: Identifiable, Sendable, Hashable {
    public let id: UUID
    public let date: Date
    public let category: String
    public let level: String
    public let message: String

    public init(id: UUID = UUID(), date: Date, category: String, level: String, message: String) {
        self.id = id
        self.date = date
        self.category = category
        self.level = level
        self.message = message
    }
}

/// Reads this process's own log entries through `OSLogStore`. The current-process scope needs
/// no entitlement or elevated privilege.
public enum DiagnosticLogReader {
    public static func recentEntries(since interval: TimeInterval = 3600, limit: Int = 5_000) throws -> [DiagnosticLogEntry] {
        let store = try OSLogStore(scope: .currentProcessIdentifier)
        let position = store.position(date: Date().addingTimeInterval(-interval))
        let predicate = NSPredicate(format: "subsystem == %@", ToolkitLog.subsystem)
        var entries: [DiagnosticLogEntry] = []
        for case let entry as OSLogEntryLog in try store.getEntries(at: position, matching: predicate) {
            entries.append(
                DiagnosticLogEntry(
                    date: entry.date,
                    category: entry.category,
                    level: levelName(entry.level),
                    message: entry.composedMessage
                )
            )
            if entries.count >= limit { break }
        }
        return entries
    }

    static func levelName(_ level: OSLogEntryLog.Level) -> String {
        switch level {
        case .debug: return "Debug"
        case .info: return "Info"
        case .notice: return "Notice"
        case .error: return "Error"
        case .fault: return "Fault"
        case .undefined: return "Default"
        @unknown default: return "Default"
        }
    }

    /// Renders entries as plain text suitable for a support bundle after sanitization.
    public static func render(_ entries: [DiagnosticLogEntry]) -> String {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return entries.map { entry in
            "\(formatter.string(from: entry.date)) [\(entry.level)] \(entry.category): \(entry.message)"
        }.joined(separator: "\n")
    }
}
