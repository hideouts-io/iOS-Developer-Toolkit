import DeviceKit
import Foundation
import ToolkitCore

/// How much an action can change. Drives the confirmation the user must give.
public enum ActionRisk: String, Codable, Sendable, CaseIterable, Comparable {
    case readOnly = "read-only"
    case hostWrite = "host-write"
    case deviceChange = "device-change"
    case highImpact = "high-impact"

    public var label: String {
        switch self {
        case .readOnly: return "Read-only"
        case .hostWrite: return "Saves files on this Mac"
        case .deviceChange: return "Changes the device"
        case .highImpact: return "High impact"
        }
    }

    public var explanation: String {
        switch self {
        case .readOnly: return "Only reads information. Nothing on the device or this Mac is changed."
        case .hostWrite: return "Copies information from the device into a file you choose on this Mac. Review the file before sharing it."
        case .deviceChange: return "Changes app, process, location, or developer state on the target device."
        case .highImpact: return "Can restart the device or remove data. Make sure you have a current backup."
        }
    }

    public var symbolName: String {
        switch self {
        case .readOnly: return "eye"
        case .hostWrite: return "square.and.arrow.down"
        case .deviceChange: return "exclamationmark.triangle"
        case .highImpact: return "exclamationmark.octagon"
        }
    }

    private var order: Int {
        switch self {
        case .readOnly: return 0
        case .hostWrite: return 1
        case .deviceChange: return 2
        case .highImpact: return 3
        }
    }

    public static func < (lhs: ActionRisk, rhs: ActionRisk) -> Bool { lhs.order < rhs.order }
}

/// The acknowledgement required before an action runs.
public struct ConfirmationRequirement: Sendable, Hashable {
    public var risk: ActionRisk
    /// The exact phrase to type, bound to the target's UDID suffix; nil when none is needed.
    public var phrase: String?
    public var requiresBackupAcknowledgement: Bool
    public var requiresReview: Bool

    public static func make(for risk: ActionRisk, target: DeviceTarget?) -> ConfirmationRequirement {
        let suffix = target?.confirmationSuffix ?? "LOCAL"
        switch risk {
        case .readOnly:
            return ConfirmationRequirement(risk: risk, phrase: nil, requiresBackupAcknowledgement: false, requiresReview: false)
        case .hostWrite:
            return ConfirmationRequirement(risk: risk, phrase: nil, requiresBackupAcknowledgement: false, requiresReview: true)
        case .deviceChange:
            return ConfirmationRequirement(risk: risk, phrase: "RUN \(suffix)", requiresBackupAcknowledgement: false, requiresReview: true)
        case .highImpact:
            return ConfirmationRequirement(risk: risk, phrase: "IRREVERSIBLE \(suffix)", requiresBackupAcknowledgement: true, requiresReview: true)
        }
    }

    /// Whether the user's input satisfies the requirement. Comparison is exact apart from
    /// surrounding whitespace, so a different device's suffix never matches.
    public func isSatisfied(typedPhrase: String, backupAcknowledged: Bool) -> Bool {
        if requiresBackupAcknowledgement && !backupAcknowledged { return false }
        guard let phrase else { return true }
        return typedPhrase.trimmingCharacters(in: .whitespacesAndNewlines) == phrase
    }
}

/// Splits an Advanced Mode argument string into a vector without invoking a shell. Supports
/// single quotes, double quotes, and backslash escapes; pipes, redirects, and substitutions are
/// treated as plain characters.
public enum ArgumentSplitter {
    public static func split(_ text: String) throws -> [String] {
        var arguments: [String] = []
        var current = ""
        var inSingle = false
        var inDouble = false
        var escaping = false
        var hasToken = false
        for character in text {
            if escaping {
                current.append(character)
                escaping = false
                hasToken = true
                continue
            }
            switch character {
            case "\\" where !inSingle:
                escaping = true
            case "'" where !inDouble:
                inSingle.toggle()
                hasToken = true
            case "\"" where !inSingle:
                inDouble.toggle()
                hasToken = true
            case let whitespace where whitespace.isWhitespace && !inSingle && !inDouble:
                if hasToken {
                    arguments.append(current)
                    current = ""
                    hasToken = false
                }
            default:
                current.append(character)
                hasToken = true
            }
        }
        if escaping || inSingle || inDouble {
            throw ToolkitError.invalidInput("The arguments have an unfinished quote or escape.")
        }
        if hasToken { arguments.append(current) }
        return arguments
    }
}

/// Classifies free-form `devicectl` arguments for Advanced Mode and binds them to the selected
/// device.
public enum AdvancedCommandPolicy {
    static let readOnlyPrefixes: [[String]] = [["list"], ["device", "info"], ["help"], ["device", "orientation", "get"], ["device", "profile", "list"], ["device", "pasteboard", "info"], ["device", "simulate", "location", "list"]]
    static let hostWritePrefixes: [[String]] = [["device", "copy", "from"], ["device", "capture"], ["device", "sysdiagnose"], ["diagnose"]]
    static let highImpactPrefixes: [[String]] = [
        ["device", "reboot"], ["device", "settings", "reset"], ["device", "uninstall"], ["device", "profile", "remove"], ["device", "profile", "install"],
        ["device", "pairings", "unpair"], ["manage", "unpair"], ["manage", "ddis", "clean"], ["device", "rename"],
    ]

    public static func risk(for arguments: [String]) -> ActionRisk {
        let words = arguments.filter { !$0.hasPrefix("-") }
        func matches(_ prefixes: [[String]]) -> Bool { prefixes.contains { Array(words.prefix($0.count)) == $0 } }
        if matches(highImpactPrefixes) { return .highImpact }
        if matches(hostWritePrefixes) { return .hostWrite }
        if matches(readOnlyPrefixes) { return .readOnly }
        return .deviceChange
    }

    /// Adds `--device <selected>` for device commands and refuses any attempt to address a
    /// different device, or to redirect JSON output (the toolkit manages that).
    public static func bind(_ arguments: [String], to target: DeviceTarget?) throws -> [String] {
        guard let first = arguments.first else { throw ToolkitError.invalidInput("Enter a devicectl command, for example: device info apps") }
        guard ["device", "list", "manage", "help", "diagnose"].contains(first) else {
            throw ToolkitError.invalidInput("Advanced Mode runs devicectl subcommands only (device, list, manage, help, diagnose).")
        }
        for forbidden in ["--json-output", "-j", "--log-output", "-l"] where arguments.contains(forbidden) {
            throw ToolkitError.invalidInput("\(forbidden) is managed by the toolkit and cannot be set in Advanced Mode.")
        }
        var bound = arguments
        let deviceFlags = ["--device", "-d", "--devices"]
        if let index = arguments.firstIndex(where: deviceFlags.contains) {
            guard let target, index + 1 < arguments.count, arguments[index + 1] == target.coreDeviceSelector else {
                throw ToolkitError.invalidInput("Advanced Mode always targets the selected device. Remove the --device option; the toolkit adds it for you.")
            }
        } else if first == "device" || (first == "manage" && arguments.count > 1 && arguments[1] != "ddis") {
            guard let target, target.kind == .physical else {
                throw ToolkitError.invalidInput("Select a physical device first; device commands always name the selected device.")
            }
            bound += ["--device", target.coreDeviceSelector]
        }
        return bound
    }
}
