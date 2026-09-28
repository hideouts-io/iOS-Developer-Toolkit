import Foundation
import Testing
@testable import DeviceKit
@testable import ToolkitFeatures
import ToolkitCore

let xcodeAvailable = FileManager.default.isExecutableFile(atPath: "/usr/bin/xcrun") && FileManager.default.fileExists(atPath: "/Library/Developer/PrivateFrameworks/CoreDevice.framework")

@Suite("Toolchain and reference")
struct ToolchainTests {
    /// Every devicectl/simctl/xctrace route and option the app relies on exists in this Xcode.
    @Test(.enabled(if: xcodeAvailable), .timeLimit(.minutes(3)))
    func installedXcodeProvidesEveryRoute() async throws {
        let results = await ToolchainCheck.run(runner: ProcessCommandRunner())
        #expect(results.count == ToolchainCheck.routes.count)
        // Routes every supported Xcode must have are required; routes newer Xcode versions add are
        // reported as “needs a newer Xcode” (for example on the Xcode 26.6 CI runner).
        let problems = results.filter { $0.state != .available && $0.state != .needsNewerXcode }
        #expect(problems.isEmpty, "\(problems.map { "\($0.route.id): \($0.state.rawValue) \($0.detail)" })")
        let unexpectedNewer = results.filter { $0.state == .needsNewerXcode && !$0.route.needsRecentXcode }
        #expect(unexpectedNewer.isEmpty)
    }

    @Test func evaluationDetectsMissingOptionsAndRoutes() {
        let route = ToolchainCheck.Route(tool: .devicectl, path: ["device", "x"], requiredOptions: ["--alpha", "--beta"], usedFor: "test")
        #expect(ToolchainCheck.evaluate(route, helpText: "USAGE --alpha --beta", succeeded: true).state == .available)
        #expect(ToolchainCheck.evaluate(route, helpText: "USAGE --alpha", succeeded: true).state == .changed)
        #expect(ToolchainCheck.evaluate(route, helpText: "Error: Unknown subcommand", succeeded: false).state == .missing)
        var newer = route
        newer.needsRecentXcode = true
        #expect(ToolchainCheck.evaluate(newer, helpText: "USAGE --alpha", succeeded: true).state == .needsNewerXcode)
        #expect(ToolchainCheck.evaluate(newer, helpText: "Error: Unknown subcommand", succeeded: false).state == .needsNewerXcode)
        #expect(ToolchainCheck.evaluate(newer, helpText: "USAGE --alpha --beta", succeeded: true).state == .available)
        let report = ToolchainCheck.render([ToolchainCheck.evaluate(route, helpText: "--alpha", succeeded: true)])
        #expect(report.contains("Changed (1)"))
        #expect(report.contains("--beta"))
    }

    @Test func missingXcodeIsReportedAsTheCause() {
        let missing = DeveloperToolsStatus.Availability.missing(reason: "xcrun: error: unable to find utility \"devicectl\"")
        let commandLineToolsOnly = DeveloperToolsStatus(developerDirectory: "/Library/Developer/CommandLineTools", xcodeVersion: nil, devicectl: missing, simctl: missing, xctrace: missing)
        #expect(ToolchainCheck.unavailableReason(.devicectl, in: commandLineToolsOnly)?.hasPrefix("Xcode is not installed") == true)
        #expect(ToolchainCheck.unavailableReason(.xed, in: commandLineToolsOnly) == nil)

        let brokenTool = DeveloperToolsStatus(developerDirectory: "/Applications/Xcode.app/Contents/Developer", xcodeVersion: "Xcode 27.0", devicectl: missing, simctl: .available(version: nil), xctrace: .available(version: nil))
        #expect(ToolchainCheck.unavailableReason(.devicectl, in: brokenTool)?.hasPrefix("devicectl could not run") == true)
        #expect(ToolchainCheck.unavailableReason(.simctl, in: brokenTool) == nil)

        // A busy Mac is not a missing Xcode.
        let busy = DeveloperToolsStatus(developerDirectory: "/Applications/Xcode.app/Contents/Developer", xcodeVersion: nil, devicectl: .unresponsive(reason: "devicectl did not finish within 45 seconds."), simctl: .available(version: nil), xctrace: .available(version: nil))
        #expect(ToolchainCheck.unavailableReason(.devicectl, in: busy)?.contains("did not answer in time") == true)
    }

    @Test func slowXcodeToolsAreNotReportedAsMissing() async {
        final class TimingOutRunner: CommandRunning, @unchecked Sendable {
            func run(_ request: CommandRequest) async throws -> CommandResult {
                throw ToolkitError.timedOut(request.displayName, after: 45)
            }
            func stream(_ request: CommandRequest) -> AsyncThrowingStream<CommandStreamEvent, Error> {
                AsyncThrowingStream { $0.finish(throwing: ToolkitError.timedOut(request.displayName, after: 45)) }
            }
        }
        let tools = await DeveloperToolsStatus.probe(runner: TimingOutRunner())
        guard case .unresponsive = tools.devicectl else {
            Issue.record("expected unresponsive, got \(tools.devicectl)")
            return
        }
        let simulator = Device(kind: .simulator, udid: "SIM", name: "iPhone", transports: [.local], sources: [.simctl])
        let results = await CapabilityProbe(runner: TimingOutRunner()).run(for: simulator)
        let xcode = results.first { $0.id == "xcode-tools" }
        #expect(xcode?.state == .attention)
        #expect(xcode?.summary.contains("did not answer in time") == true)
        #expect(results.first { $0.id == "simulator-running" }?.summary == "Xcode's tools did not answer in time.")
    }

    @Test(.enabled(if: xcodeAvailable))
    func referenceDiscoversDevicectlSubcommands() async throws {
        let root = ToolReference.roots[0]
        let text = try await ToolReference.helpText(root, runner: ProcessCommandRunner())
        let children = ToolReference.children(of: root, helpText: text).map { $0.path.last ?? "" }
        #expect(children.contains("device"))
        #expect(children.contains("list"))
        #expect(children.contains("manage"))
        let device = try #require(ToolReference.children(of: root, helpText: text).first { $0.path == ["device"] })
        let deviceChildren = ToolReference.children(of: device, helpText: try await ToolReference.helpText(device, runner: ProcessCommandRunner()))
        #expect(deviceChildren.contains { $0.path == ["device", "info"] })
        #expect(deviceChildren.contains { $0.path == ["device", "process"] })
    }
}

@Suite("Profiles, support bundle, and compatibility")
struct SharedFeatureTests {
    @Test func workspaceProfileRoundTripAndValidation() throws {
        var profile = WorkspaceProfile(name: "QA lab", description: "Defaults for release testing", defaultWorkspace: .location, actionCategory: "Device Actions")
        profile.location.routeSpeedKmh = 40
        let decoded = try WorkspaceProfile.decode(try profile.encoded())
        #expect(decoded == profile)
        #expect(decoded.preview.contains("Opens to: Location Lab"))

        var leaky = profile
        leaky.description = "Copied from /Users/alice/Desktop"
        #expect(throws: ToolkitError.self) { try leaky.validated() }
        leaky.description = "UDID 00008110-001234560ABC801E"
        #expect(throws: ToolkitError.self) { try leaky.validated() }
        var bad = profile
        bad.location.routeTraversals = 99
        #expect(throws: ToolkitError.self) { try bad.validated() }
        bad = profile
        bad.actionCategory = "Unknown"
        #expect(throws: ToolkitError.self) { try bad.validated() }
        #expect(throws: ToolkitError.self) { try WorkspaceProfile.decode(Data("{\"name\":1}".utf8)) }
        #expect(throws: ToolkitError.self) { try WorkspaceProfile.decode(Data(repeating: 0x20, count: 70_000)) }
    }

    @Test func workspaceProfileCarriesActionAndDeveloperImageChoices() throws {
        let profile = WorkspaceProfile(name: "Capture lab", actionCategory: "Capture & Instruments", selectedAction: "bluetooth-capture", developerImageMechanism: .coreDevice)
        let decoded = try WorkspaceProfile.importing(try profile.encoded())
        #expect(decoded.profile == profile)
        #expect(decoded.legacyVersion == nil && decoded.notes.isEmpty)
        #expect(profile.preview.contains("Selected action: Bluetooth capture"))
        #expect(profile.preview.contains("Developer image: mount with Xcode device service (devicectl)"))
        var bad = profile
        bad.selectedAction = "no-such-action"
        #expect(throws: ToolkitError.self) { try bad.validated() }
        bad.selectedAction = "battery"  // Device Basics, not Capture & Instruments
        #expect(throws: ToolkitError.self) { try bad.validated() }
        // Profiles exported before these fields existed still import.
        let older = try JSONSerialization.jsonObject(with: try WorkspaceProfile(name: "Older").encoded()) as? [String: Any]
        var trimmed = try #require(older)
        trimmed["selectedAction"] = nil
        trimmed["developerImageMechanism"] = nil
        #expect(try WorkspaceProfile.importing(try JSONSerialization.data(withJSONObject: trimmed)).profile.selectedAction == nil)
    }

    /// Written by 0.3.4's own `render_workspace_profile_json` (ios_developer_toolkit/workspace_profile.py).
    static let legacyProfile = """
    {
      "created_with_version": "0.3.4",
      "default_workspace": "Command Center",
      "description": "Shared settings for the device lab",
      "name": "Lab defaults",
      "privacy": {
        "schema_excludes": ["device identity and targets", "credentials and authorization acknowledgements", "local paths and coordinates", "command parameters", "case text and capture output"],
        "user_supplied_text_fields": ["name", "description"],
        "warning": "Review the user-supplied name and description before sharing."
      },
      "schema_version": 1,
      "settings": {
        "app_workflow": {"calculate_app_sizes": false, "install_as_developer_package": true},
        "backup_workflow": {"force_full_backup": true, "require_encryption": true},
        "command": {"category": "Logging & Capture", "preset": "btlogger"},
        "ddi_source": "local-xcode",
        "evidence_workflow": {"capture_duration_seconds": 120, "include_crash_pull": true, "include_oslog": true, "include_pcap": false, "include_screenshot": false, "include_syslog": true},
        "location_workflow": {"ignore_timing_delays": true, "route_interval_seconds": 3, "route_speed_kmh": 35, "route_speed_preset_kmh": 20, "route_traversals": 4, "timing_randomness_ms": 250}
      }
    }
    """

    @Test func importsPythonWorkspaceProfiles() throws {
        let imported = try WorkspaceProfile.importing(Data(Self.legacyProfile.utf8))
        let profile = imported.profile
        #expect(imported.legacyVersion == "0.3.4")
        #expect(profile.schemaVersion == WorkspaceProfile.currentSchemaVersion)
        #expect(profile.name == "Lab defaults" && profile.description == "Shared settings for the device lab")
        #expect(profile.defaultWorkspace == .actions)
        #expect(profile.actionCategory == "Capture & Instruments")
        #expect(profile.selectedAction == "bluetooth-capture")
        #expect(profile.developerImageMechanism == .native)
        #expect(profile.apps == .init(calculateSizes: false, includeSystemApps: false, installAsDeveloperPackage: true))
        #expect(profile.backup == .init(forceFullBackup: true, requireEncryption: true))
        #expect(profile.evidence == CollectionOptions(durationSeconds: 120, includeClassicSyslog: true, includeUnifiedLogs: true, includePacketCapture: false, includeScreenshot: false, includeCrashReports: true))
        #expect(profile.location == .init(timingJitterMilliseconds: 250, ignoreRecordedTiming: true, routeSpeedKmh: 35, routeIntervalSeconds: 3, routeTraversals: 4))
        #expect(imported.notes.contains { $0.contains("“btlogger” → action “Bluetooth capture”") })
        #expect(imported.notes.contains { $0.contains("nothing is downloaded") })
        #expect(imported.notes.contains { $0.contains("DVT OSLog → Unified Logging") })
        // Once imported it is an ordinary profile.
        #expect(try WorkspaceProfile.decode(try profile.encoded()) == profile)

        func variant(_ edit: (inout [String: Any]) -> Void) throws -> WorkspaceProfile.Import {
            var object = try #require(try JSONSerialization.jsonObject(with: Data(Self.legacyProfile.utf8)) as? [String: Any])
            edit(&object)
            return try WorkspaceProfile.importing(try JSONSerialization.data(withJSONObject: object))
        }
        func setting(_ path: [String], _ value: Any) -> (inout [String: Any]) -> Void {
            { object in
                var settings = object["settings"] as! [String: Any]
                if path.count == 1 { settings[path[0]] = value } else {
                    var inner = settings[path[0]] as! [String: Any]
                    inner[path[1]] = value
                    settings[path[0]] = inner
                }
                object["settings"] = settings
            }
        }
        // A preset that moved to a workspace, with “All categories”.
        let moved = try variant { object in
            setting(["command", "category"], "All categories")(&object)
            setting(["command", "preset"], "syslog")(&object)
        }
        #expect(moved.profile.actionCategory == "All" && moved.profile.selectedAction == nil)
        #expect(moved.notes.contains { $0.contains("Live Logs page") })
        #expect(try variant { $0["default_workspace"] = "Man Pages" }.profile.defaultWorkspace == .help)

        // Checked as strictly as 0.3.x checked it.
        #expect(throws: ToolkitError.self) { try variant { $0["default_workspace"] = "Nowhere" } }
        #expect(throws: ToolkitError.self) { try variant(setting(["command", "preset"], "rm-rf")) }
        #expect(throws: ToolkitError.self) { try variant(setting(["ddi_source"], "download")) }
        #expect(throws: ToolkitError.self) { try variant(setting(["evidence_workflow", "capture_duration_seconds"], 5)) }
        #expect(throws: ToolkitError.self) { try variant(setting(["evidence_workflow", "include_pcap"], "yes")) }
        #expect(throws: ToolkitError.self) { try variant(setting(["location_workflow", "route_speed_preset_kmh"], 7)) }
        #expect(throws: ToolkitError.self) { try variant { $0["description"] = "From /Users/alice/Desktop" } }
        #expect(throws: ToolkitError.self) { try variant { $0["schema_version"] = 3 } }
        #expect(throws: ToolkitError.self) { try variant { $0["settings"] = nil } }
    }

    @Test func everyPythonPresetHasATranslation() throws {
        // The 49 Command Center presets of 0.3.4 (command_catalog.py).
        #expect(LegacyWorkspaceProfile.presets.count == 49)
        for case let (preset, action?) in LegacyWorkspaceProfile.presets {
            #expect(ActionCatalog.descriptor(action) != nil, "\(preset) → \(action)")
        }
        for (preset, action) in LegacyWorkspaceProfile.presets where action == nil && LegacyWorkspaceProfile.movedPresets[preset] == nil {
            #expect(["dvt-list", "notifications", "remote-browse"].contains(preset), "\(preset) has neither an action nor a destination")
        }
    }

    @Test func supportBundleIsSanitizedAndHashed() throws {
        let context = SupportBundleContext(
            workspace: "Device",
            detectedDeviceCount: 2,
            selectedDeviceKind: "physical",
            discoveryStatus: ["usbmux": "1 device", "coreDevice": "Alice's iPhone at 192.168.1.4"],
            capabilityCounts: ["ready": 5, "attention": 1],
            toolchainReport: "Missing: devicectl device x — /Users/alice/Library",
            developerTools: ["xcode": "Xcode 27.0"],
            statuses: ["device": "Connected to Alice's iPhone 00008110-001234560ABC801E"],
            redactions: ["Alice's iPhone"],
            diagnosticLog: [DiagnosticLogEntry(date: Date(), category: "Commands", level: "Info", message: "Started devicectl for alice@example.com")]
        )
        let entries = try SupportBundle.entries(for: context)
        let combined = entries.map { String(decoding: $0.1, as: UTF8.self) }.joined(separator: "\n")
        for secret in ["Alice's iPhone", "192.168.1.4", "00008110-001234560ABC801E", "/Users/alice", "alice@example.com"] {
            #expect(!combined.contains(secret), "\(secret) leaked")
        }
        #expect(entries.map(\.0) == ["README.txt", "environment.json", "context.json", "toolchain-check.txt", "diagnostic-log.txt", "SHA256SUMS.json"])
        let hashes = try JSONValue.parse(entries.last!.1)
        #expect(hashes["entries"]?["context.json"]?.string == SecureFileIO.sha256(of: entries[2].1))

        let directory = try SecureFileIO.makeTemporaryDirectory(prefix: "bundle")
        defer { try? FileManager.default.removeItem(at: directory) }
        let zip = directory.appendingPathComponent("support.zip")
        try SupportBundle.write(to: zip, context: context)
        let archive = try ZipArchive(url: zip)
        #expect(archive.entries.count == 6)
        #expect(throws: ToolkitError.self) { try SupportBundle.write(to: zip, context: context) }
        #expect(throws: ToolkitError.self) { try SupportBundle.write(to: directory.appendingPathComponent("x.txt"), context: context) }
    }

    @Test func compatibilityHistoryIsFingerprintedAndSanitized() throws {
        let directory = try SecureFileIO.makeTemporaryDirectory(prefix: "compat")
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = CompatibilityStore(url: directory.appendingPathComponent("observations.jsonl"))
        let device = Device(kind: .physical, udid: "00008110-001234560ABC801E", name: "Alice's iPhone", productType: "iPhone16,1", osVersion: "26.0", buildVersion: "23A341", transports: [.usb])
        let results = [CapabilityRow.pairingTrust.result(.ready, "ok"), CapabilityRow.developerMode.result(.attention, "Off")]
        try store.append(CompatibilityObservation(device: device, results: results, observedAt: Date(timeIntervalSince1970: 1_000)))
        try store.append(CompatibilityObservation(device: device, results: [CapabilityRow.developerMode.result(.ready, "On")], observedAt: Date(timeIntervalSince1970: 2_000)))
        try SecureFileIO.append(Data("not json\n".utf8), to: store.url)
        let loaded = store.load()
        #expect(loaded.count == 2)
        let latest = CompatibilityStore.latest(loaded)
        #expect(latest.count == 1)
        #expect(latest[0].states["developer-mode"] == .ready)
        let raw = try String(contentsOf: store.url, encoding: .utf8)
        #expect(!raw.contains("00008110") && !raw.contains("Alice"))
        let json = String(decoding: try CompatibilityStore.renderJSON(loaded), as: UTF8.self)
        #expect(!json.contains(latest[0].fingerprint))
        #expect(json.contains("iPhone 15 Pro"))
        let markdown = CompatibilityStore.renderMarkdown(loaded)
        #expect(markdown.contains("| iPhone 15 Pro | 26.0 | 23A341 | USB |"))
    }

    @Test func workspacesAndDemoMode() {
        #expect(Set(Workspace.allCases.map(\.id)).count == Workspace.allCases.count)
        #expect(Workspace.allCases.allSatisfy { !$0.title.isEmpty && !$0.symbolName.isEmpty })
        #expect(!Workspace.evidence.supports(.simulator))
        #expect(Workspace.location.supports(.simulator))
        let demo = DemoMode.device
        #expect(demo.kind == .demo)
        #expect(demo.name.contains("simulated"))
        #expect(LogStreamKind.available(for: demo.kind).isEmpty)
    }
}

@Suite("Guided reconnect")
struct ReconnectGuideTests {
    func device(_ name: String, kind: DeviceKind = .physical, transports: Set<DeviceTransport> = [.usb], pairing: PairingState) -> Device {
        Device(kind: kind, udid: name, name: name, transports: transports, pairingState: pairing)
    }

    @Test func interpretsWhatDiscoverySees() {
        #expect(ReconnectGuide.evaluate(devices: [], timeElapsed: false) == .waiting)
        #expect(ReconnectGuide.evaluate(devices: [], timeElapsed: true) == .timedOut)
        // Only USB-connected physical devices count; simulators and network-only devices do not.
        #expect(ReconnectGuide.evaluate(devices: [device("Sim", kind: .simulator, transports: [.local], pairing: .notApplicable), device("Net", transports: [.network], pairing: .paired)], timeElapsed: true) == .timedOut)
        #expect(ReconnectGuide.evaluate(devices: [device("Phone", pairing: .paired)], timeElapsed: false) == .connected(name: "Phone"))
        #expect(ReconnectGuide.evaluate(devices: [device("Phone", pairing: .unpaired)], timeElapsed: false) == .awaitingTrust(name: "Phone"))
        // Pairing not known yet: keep waiting until the window ends.
        #expect(ReconnectGuide.evaluate(devices: [device("Phone", pairing: .unknown)], timeElapsed: false) == .waiting)
        #expect(ReconnectGuide.evaluate(devices: [device("Phone", pairing: .unknown)], timeElapsed: true) == .awaitingTrust(name: "Phone"))
        // A trusted device wins over an untrusted one.
        #expect(ReconnectGuide.evaluate(devices: [device("A", pairing: .unpaired), device("B", pairing: .paired)], timeElapsed: false) == .connected(name: "B"))
        #expect(ReconnectGuide.Outcome.timedOut.nextStep?.contains("restart the Mac") == true)
        #expect(ReconnectGuide.boundary.contains("never uses sudo"))
    }
}

@Suite("Keyboard navigation")
struct KeyboardNavigationTests {
    @Test func workspacesCycleInSidebarOrder() {
        #expect(Workspace.overview.next == .device)
        #expect(Workspace.device.previous == .overview)
        #expect(Workspace.allCases.last?.next == Workspace.allCases.first)
        #expect(Workspace.overview.previous == Workspace.allCases.last)
        for workspace in Workspace.allCases {
            #expect(workspace.next.previous == workspace)
        }
    }

    @Test func referenceListsTheMenuShortcuts() {
        let entries = KeyboardShortcutReference.sections.flatMap(\.entries)
        #expect(Set(entries.map(\.id)).count == entries.count)
        for (index, workspace) in Workspace.numbered.enumerated() {
            #expect(entries.contains { $0.keys == "⌘\(index + 1)" && $0.title == workspace.title })
        }
        #expect(Workspace.numbered.count == 9)
        for keys in ["⌘K", "⌥⌘←", "⌥⌘→", "⌘R", "⇧⌘R", "⌘/"] {
            #expect(entries.contains { $0.keys == keys }, "\(keys)")
        }
    }
}

@Suite("Readiness shortcuts and Advanced Mode")
struct ReadinessShortcutTests {
    @Test func evidenceCollectionsHaveTheirOwnPrerequisites() {
        let device = Device(kind: .physical, udid: "u", name: "Phone", transports: [.usb], sources: [.usbmux])
        var options = CollectionOptions()
        #expect(options.requirements == [.trustedDevice])
        options.includeScreenshot = true
        #expect(options.requirements == [.trustedDevice, .coreDevice])
        let trusted = CapabilityRow.pairingTrust.result(.ready, "Trusted")
        let noXcode = CapabilityRow.coreDevice.result(.blocked, "Needs Xcode.")
        #expect(ActionReadiness.evaluate(requirements: CollectionOptions().requirements, results: [trusted, noXcode], device: device) == .ready)
        #expect(ActionReadiness.evaluate(requirements: options.requirements, results: [trusted, noXcode], device: device) == .needsAttention(["Xcode device service (CoreDevice): Needs Xcode."]))
        #expect(ActionReadiness.evaluate(requirements: options.requirements, results: [], device: device) == .notTested)
    }

    @Test func toolReferenceTopicsFillInAdvancedMode() throws {
        #expect(ToolReference.advancedModeCommand(for: .init(tool: .devicectl, path: ["device", "info", "apps"])) == "device info apps")
        #expect(ToolReference.advancedModeCommand(for: .init(tool: .devicectl, path: [])) == nil)
        #expect(ToolReference.advancedModeCommand(for: .init(tool: .simctl, path: ["list"])) == nil)
        // What is filled in is still checked by Advanced Mode's own policy before anything runs.
        let target = DeviceTarget(kind: .physical, udid: "00008150-000B33334444002E", name: "Phone", osVersion: "26.0", usbmuxDeviceID: 1, coreDeviceIdentifier: nil, transport: .usb)
        let prepared = try ActionExecutor.prepareAdvanced(try #require(ToolReference.advancedModeCommand(for: .init(tool: .devicectl, path: ["device", "info", "apps"]))), target: target)
        #expect(prepared.risk == .readOnly)
    }
}
