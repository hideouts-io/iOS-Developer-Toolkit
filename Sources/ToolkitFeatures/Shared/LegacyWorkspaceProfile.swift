import DeviceKit
import Foundation
import ToolkitCore

/// Workspace profiles exported by the 0.3.x Python app (`schema_version` 1), translated into the
/// current profile. The file is checked as strictly as 0.3.x checked it; settings with no exact
/// counterpart are mapped to the closest one and explained in `WorkspaceProfile.Import.notes`.
enum LegacyWorkspaceProfile {
    static let schemaVersion = 1

    struct File: Decodable {
        var schemaVersion: Int
        var createdWithVersion: String
        var name: String
        var description: String
        var defaultWorkspace: String
        var settings: Settings

        struct Settings: Decodable {
            var ddiSource: String
            var command: Command
            var appWorkflow: AppWorkflow
            var backupWorkflow: BackupWorkflow
            var evidenceWorkflow: EvidenceWorkflow
            var locationWorkflow: LocationWorkflow

            enum CodingKeys: String, CodingKey {
                case ddiSource = "ddi_source", command
                case appWorkflow = "app_workflow", backupWorkflow = "backup_workflow"
                case evidenceWorkflow = "evidence_workflow", locationWorkflow = "location_workflow"
            }
        }

        struct Command: Decodable {
            var category: String
            var preset: String
        }

        struct AppWorkflow: Decodable {
            var calculateAppSizes: Bool
            var installAsDeveloperPackage: Bool
            enum CodingKeys: String, CodingKey {
                case calculateAppSizes = "calculate_app_sizes", installAsDeveloperPackage = "install_as_developer_package"
            }
        }

        struct BackupWorkflow: Decodable {
            var forceFullBackup: Bool
            var requireEncryption: Bool
            enum CodingKeys: String, CodingKey {
                case forceFullBackup = "force_full_backup", requireEncryption = "require_encryption"
            }
        }

        struct EvidenceWorkflow: Decodable {
            var captureDurationSeconds: Int
            var includeSyslog: Bool
            var includeOSLog: Bool
            var includePcap: Bool
            var includeScreenshot: Bool
            var includeCrashPull: Bool
            enum CodingKeys: String, CodingKey {
                case captureDurationSeconds = "capture_duration_seconds", includeSyslog = "include_syslog", includeOSLog = "include_oslog"
                case includePcap = "include_pcap", includeScreenshot = "include_screenshot", includeCrashPull = "include_crash_pull"
            }
        }

        struct LocationWorkflow: Decodable {
            var timingRandomnessMilliseconds: Int
            var ignoreTimingDelays: Bool
            var routeSpeedPresetKmh: Int
            var routeSpeedKmh: Int
            var routeIntervalSeconds: Int
            var routeTraversals: Int
            enum CodingKeys: String, CodingKey {
                case timingRandomnessMilliseconds = "timing_randomness_ms", ignoreTimingDelays = "ignore_timing_delays"
                case routeSpeedPresetKmh = "route_speed_preset_kmh", routeSpeedKmh = "route_speed_kmh"
                case routeIntervalSeconds = "route_interval_seconds", routeTraversals = "route_traversals"
            }
        }

        enum CodingKeys: String, CodingKey {
            case schemaVersion = "schema_version", createdWithVersion = "created_with_version", name, description
            case defaultWorkspace = "default_workspace", settings
        }
    }

    /// 0.3.x workspace names.
    static let workspaces: [String: Workspace] = [
        "Home": .overview,
        "Device & DDI": .device,
        "Capability Matrix": .readiness,
        "Location Lab": .location,
        "Live Logs": .liveLogs,
        "Command Center": .actions,
        "Installed Apps": .apps,
        "Backup": .backup,
        "Sideload IPA": .installApp,
        "Evidence Capture": .evidence,
        "Ecosystem Tools": .externalTools,
        "Man Pages": .help,
        "Scope & Safety": .safety,
    ]

    static let categories = ["Device Basics", "Apps & Files", "Logging & Capture", "Developer & DVT", "Web & Discovery", "Device Actions"]

    /// Each 0.3.x Command Center preset and the action that replaces it (MIGRATION.md §9.1).
    /// `nil` means the preset moved to a workspace or has no replacement.
    static let presets: [String: String?] = [
        "devices": nil, "lockdown": "lockdown-values", "activation": "activation-state", "developer-mode": "developer-mode-status",
        "diagnostics": "diagnostics", "battery": "battery", "ioregistry": "ioregistry", "mobilegestalt": "mobilegestalt",
        "processes": "processes", "profiles": "configuration-profiles", "provisioning": "provisioning-profiles",
        "orientation": "orientation", "icon-metrics": "icon-metrics",
        "apps-list": nil, "apps-query": "app-query", "afc-list": "media-list", "dvt-list": nil,
        "crash-list": "crash-list", "crash-pull": "crash-pull",
        "syslog": nil, "oslog": nil, "pcap": "packet-capture", "btlogger": "bluetooth-capture",
        "dvt-device": "device-details", "dvt-proclist": "processes", "dvt-applist": nil, "dvt-netstat": "instruments",
        "dvt-pid-check": "processes", "dvt-energy": "instruments", "sysmon-system": "instruments", "sysmon-process": "instruments",
        "graphics": "instruments", "notifications": nil, "core-profile": "instruments", "screenshot": "screenshot",
        "core-device-info": "device-details", "core-display": "displays", "core-lock": "lock-state", "core-processes": "processes",
        "core-apps": nil, "mounted-images": "mounted-images", "personalization": "personalization",
        "bonjour-rsd": "bonjour", "remote-browse": nil, "web-tabs": "web-tabs",
        "launch-app": "launch-app", "open-url": "open-url", "location-set": "set-location", "location-clear": "clear-location",
    ]

    /// Where presets without an action went.
    static let movedPresets: [String: String] = [
        "devices": "Devices are discovered automatically.",
        "apps-list": "The app inventory is on the Apps page.",
        "core-apps": "The app inventory is on the Apps page.",
        "dvt-applist": "The app inventory is on the Apps page.",
        "syslog": "Classic syslog is on the Live Logs page.",
        "oslog": "Unified Logging is on the Live Logs page.",
    ]

    static func translate(_ data: Data) throws -> WorkspaceProfile.Import {
        let file: File
        do {
            file = try JSONDecoder().decode(File.self, from: data)
        } catch {
            throw ToolkitError.invalidInput("The file looks like a 0.3.x workspace profile but is incomplete or damaged.")
        }
        guard file.schemaVersion == schemaVersion else {
            throw ToolkitError.invalidInput("This profile was made by an unsupported version (schema \(file.schemaVersion)).")
        }
        guard let workspace = workspaces[file.defaultWorkspace] else {
            throw ToolkitError.invalidInput("The profile names an unknown 0.3.x workspace.")
        }
        let settings = file.settings
        guard settings.command.category == "All categories" || categories.contains(settings.command.category) else {
            throw ToolkitError.invalidInput("The profile names an unknown 0.3.x command category.")
        }
        guard let replacement = presets[settings.command.preset] else {
            throw ToolkitError.invalidInput("The profile names an unknown 0.3.x command preset.")
        }
        guard ["personalized", "local-xcode"].contains(settings.ddiSource) else {
            throw ToolkitError.invalidInput("The profile names an unknown 0.3.x developer-image source.")
        }
        let evidence = settings.evidenceWorkflow
        // 0.3.x required at least 10 seconds of capture.
        guard (10...3600).contains(evidence.captureDurationSeconds) else {
            throw ToolkitError.invalidInput("Capture duration must be 10–3,600 seconds.")
        }
        guard [5, 10, 20, 40, 100].contains(settings.locationWorkflow.routeSpeedPresetKmh) else {
            throw ToolkitError.invalidInput("The profile names an unknown 0.3.x route speed preset.")
        }

        var notes: [String] = []
        let action = replacement.flatMap(ActionCatalog.descriptor)
        let actionCategory = settings.command.category == "All categories" ? "All" : (action?.category ?? "All")
        if let action {
            notes.append("Command Center preset “\(settings.command.preset)” → action “\(action.title)” (\(action.category)).")
        } else {
            notes.append("Command Center preset “\(settings.command.preset)” has no action here. \(movedPresets[settings.command.preset] ?? "See MIGRATION.md §9.1 for why.")")
        }
        notes.append("Developer-image source “\(settings.ddiSource)” → \(DeveloperImageMechanism.native.label). Images come from Xcode or a folder you add; nothing is downloaded.")
        if evidence.includeOSLog {
            notes.append("DVT OSLog → Unified Logging (no developer image needed).")
        }

        var profile = WorkspaceProfile(
            name: file.name,
            description: file.description,
            defaultWorkspace: workspace,
            actionCategory: actionCategory,
            selectedAction: action?.id,
            developerImageMechanism: .native,
            apps: .init(calculateSizes: settings.appWorkflow.calculateAppSizes, includeSystemApps: false, installAsDeveloperPackage: settings.appWorkflow.installAsDeveloperPackage),
            backup: .init(forceFullBackup: settings.backupWorkflow.forceFullBackup, requireEncryption: settings.backupWorkflow.requireEncryption),
            evidence: CollectionOptions(durationSeconds: evidence.captureDurationSeconds, includeClassicSyslog: evidence.includeSyslog, includeUnifiedLogs: evidence.includeOSLog, includePacketCapture: evidence.includePcap, includeScreenshot: evidence.includeScreenshot, includeCrashReports: evidence.includeCrashPull),
            location: .init(timingJitterMilliseconds: settings.locationWorkflow.timingRandomnessMilliseconds, ignoreRecordedTiming: settings.locationWorkflow.ignoreTimingDelays, routeSpeedKmh: settings.locationWorkflow.routeSpeedKmh, routeIntervalSeconds: settings.locationWorkflow.routeIntervalSeconds, routeTraversals: settings.locationWorkflow.routeTraversals)
        )
        profile.createdWithVersion = file.createdWithVersion
        return WorkspaceProfile.Import(profile: try profile.validated(), legacyVersion: try WorkspaceProfile.text(file.createdWithVersion, label: "version", maximum: 40, allowEmpty: false), notes: notes)
    }
}
