import DeviceKit
import Foundation
import ToolkitCore

/// Shareable workflow defaults. A profile never contains device identity, credentials, paths,
/// coordinates, action parameters, case text, or captured output, and importing one only changes
/// control defaults — it never runs anything.
public struct WorkspaceProfile: Codable, Sendable, Hashable {
    public struct AppPreferences: Codable, Sendable, Hashable {
        public var calculateSizes: Bool
        public var includeSystemApps: Bool
        public var installAsDeveloperPackage: Bool
        public init(calculateSizes: Bool = true, includeSystemApps: Bool = false, installAsDeveloperPackage: Bool = false) {
            self.calculateSizes = calculateSizes
            self.includeSystemApps = includeSystemApps
            self.installAsDeveloperPackage = installAsDeveloperPackage
        }
    }

    public struct BackupPreferences: Codable, Sendable, Hashable {
        public var forceFullBackup: Bool
        public var requireEncryption: Bool
        public init(forceFullBackup: Bool = false, requireEncryption: Bool = true) {
            self.forceFullBackup = forceFullBackup
            self.requireEncryption = requireEncryption
        }
    }

    public struct LocationPreferences: Codable, Sendable, Hashable {
        public var timingJitterMilliseconds: Int
        public var ignoreRecordedTiming: Bool
        public var routeSpeedKmh: Int
        public var routeIntervalSeconds: Int
        public var routeTraversals: Int
        public init(timingJitterMilliseconds: Int = 0, ignoreRecordedTiming: Bool = false, routeSpeedKmh: Int = 5, routeIntervalSeconds: Int = 2, routeTraversals: Int = 1) {
            self.timingJitterMilliseconds = timingJitterMilliseconds
            self.ignoreRecordedTiming = ignoreRecordedTiming
            self.routeSpeedKmh = routeSpeedKmh
            self.routeIntervalSeconds = routeIntervalSeconds
            self.routeTraversals = routeTraversals
        }
    }

    public var schemaVersion: Int
    public var createdWithVersion: String
    public var name: String
    public var description: String
    public var defaultWorkspace: Workspace
    public var actionCategory: String
    /// The action selected on the Actions page (an `ActionCatalog` identifier).
    public var selectedAction: String?
    /// How the Device page mounts the developer image.
    public var developerImageMechanism: DeveloperImageMechanism?
    public var apps: AppPreferences
    public var backup: BackupPreferences
    public var evidence: CollectionOptions
    public var location: LocationPreferences

    public static let currentSchemaVersion = 2
    public static let maximumFileBytes = 64 * 1024

    public init(name: String, description: String = "", defaultWorkspace: Workspace = .overview, actionCategory: String = "All", selectedAction: String? = nil, developerImageMechanism: DeveloperImageMechanism? = nil, apps: AppPreferences = AppPreferences(), backup: BackupPreferences = BackupPreferences(), evidence: CollectionOptions = CollectionOptions(), location: LocationPreferences = LocationPreferences()) {
        schemaVersion = Self.currentSchemaVersion
        createdWithVersion = ToolkitVersion.current
        self.name = name
        self.description = description
        self.defaultWorkspace = defaultWorkspace
        self.actionCategory = actionCategory
        self.selectedAction = selectedAction
        self.developerImageMechanism = developerImageMechanism
        self.apps = apps
        self.backup = backup
        self.evidence = evidence
        self.location = location
    }

    /// Validates every field; returns a normalized copy.
    public func validated() throws -> WorkspaceProfile {
        var copy = self
        guard schemaVersion == Self.currentSchemaVersion else { throw ToolkitError.invalidInput("This profile was made by an unsupported version (schema \(schemaVersion)).") }
        copy.name = try Self.text(name, label: "name", maximum: 100, allowEmpty: false)
        copy.description = try Self.text(description, label: "description", maximum: 500, allowEmpty: true)
        copy.createdWithVersion = try Self.text(createdWithVersion, label: "version", maximum: 40, allowEmpty: false)
        guard actionCategory == "All" || ActionCatalog.categories.contains(actionCategory) else {
            throw ToolkitError.invalidInput("The profile names an unknown action category.")
        }
        if let selectedAction {
            guard let action = ActionCatalog.descriptor(selectedAction) else {
                throw ToolkitError.invalidInput("The profile names an unknown action.")
            }
            guard actionCategory == "All" || action.category == actionCategory else {
                throw ToolkitError.invalidInput("The profile's selected action is not in its action category.")
            }
        }
        copy.evidence = try evidence.validated()
        guard (0...60_000).contains(location.timingJitterMilliseconds) else { throw ToolkitError.invalidInput("Timing randomness must be 0–60,000 ms.") }
        guard (1...300).contains(location.routeSpeedKmh) else { throw ToolkitError.invalidInput("Route speed must be 1–300 km/h.") }
        guard (1...60).contains(location.routeIntervalSeconds) else { throw ToolkitError.invalidInput("Route interval must be 1–60 seconds.") }
        guard (1...20).contains(location.routeTraversals) else { throw ToolkitError.invalidInput("Route traversals must be 1–20.") }
        return copy
    }

    static func text(_ value: String, label: String, maximum: Int, allowEmpty: Bool) throws -> String {
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        guard allowEmpty || !trimmed.isEmpty else { throw ToolkitError.invalidInput("The profile \(label) is required.") }
        guard trimmed.count <= maximum else { throw ToolkitError.invalidInput("The profile \(label) must be \(maximum) characters or fewer.") }
        guard !trimmed.unicodeScalars.contains(where: { CharacterSet.controlCharacters.contains($0) }) else {
            throw ToolkitError.invalidInput("The profile \(label) contains control characters.")
        }
        guard Sanitizer.sanitize(trimmed, limit: Int.max) == trimmed else {
            throw ToolkitError.invalidInput("The profile \(label) looks like it contains a path, account, device, or network identifier. Remove it before sharing.")
        }
        return trimmed
    }

    public func encoded() throws -> Data {
        try JSONOutput.encode(try validated())
    }

    public static func decode(_ data: Data) throws -> WorkspaceProfile {
        guard data.count <= maximumFileBytes else { throw ToolkitError.invalidInput("The profile file is too large.") }
        do {
            return try JSONOutput.decoder().decode(WorkspaceProfile.self, from: data).validated()
        } catch let error as ToolkitError {
            throw error
        } catch {
            throw ToolkitError.invalidInput("The file is not a valid workspace profile.")
        }
    }

    /// A profile read from a file, with notes on anything that was translated.
    public struct Import: Sendable {
        public var profile: WorkspaceProfile
        /// Set when the file was exported by the 0.3.x Python app.
        public var legacyVersion: String?
        /// Plain-language notes on how 0.3.x settings were translated.
        public var notes: [String]
    }

    /// Reads a profile exported by this app or by the 0.3.x Python app (schema 1).
    public static func importing(_ data: Data) throws -> Import {
        guard data.count <= maximumFileBytes else { throw ToolkitError.invalidInput("The profile file is too large.") }
        struct Schema: Decodable {
            var schemaVersion: Int?
            var legacySchemaVersion: Int?
            enum CodingKeys: String, CodingKey {
                case schemaVersion
                case legacySchemaVersion = "schema_version"
            }
        }
        guard let schema = try? JSONDecoder().decode(Schema.self, from: data) else {
            throw ToolkitError.invalidInput("The file is not a valid workspace profile.")
        }
        if schema.schemaVersion == nil, let legacy = schema.legacySchemaVersion {
            guard legacy == LegacyWorkspaceProfile.schemaVersion else {
                throw ToolkitError.invalidInput("This profile was made by an unsupported version (schema \(legacy)).")
            }
            return try LegacyWorkspaceProfile.translate(data)
        }
        return Import(profile: try decode(data), legacyVersion: nil, notes: [])
    }

    /// The exact, human-readable preview shown before import or export.
    public var preview: String {
        [
            "Profile: \(name)",
            description.isEmpty ? nil : "Description: \(description)",
            "Opens to: \(defaultWorkspace.title)",
            "Actions category: \(actionCategory)",
            selectedAction.flatMap(ActionCatalog.descriptor).map { "Selected action: \($0.title)" },
            developerImageMechanism.map { "Developer image: mount with \($0.label)" },
            "Apps: sizes \(apps.calculateSizes ? "on" : "off"), system apps \(apps.includeSystemApps ? "shown" : "hidden"), developer package installs \(apps.installAsDeveloperPackage ? "on" : "off")",
            "Backup: \(backup.forceFullBackup ? "always full" : "incremental when possible"), encryption \(backup.requireEncryption ? "required" : "optional")",
            "Evidence: \(evidence.durationSeconds)s streams; syslog \(evidence.includeClassicSyslog ? "on" : "off"), unified logs \(evidence.includeUnifiedLogs ? "on" : "off"), packet capture \(evidence.includePacketCapture ? "on" : "off"), screenshot \(evidence.includeScreenshot ? "on" : "off"), crash reports \(evidence.includeCrashReports ? "on" : "off")",
            "Location: route \(location.routeSpeedKmh) km/h every \(location.routeIntervalSeconds)s × \(location.routeTraversals); GPX timing \(location.ignoreRecordedTiming ? "ignored" : "kept") with ±\(location.timingJitterMilliseconds) ms",
        ].compactMap { $0 }.joined(separator: "\n")
    }
}
