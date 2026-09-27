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
