import DeviceTestSupport
import Foundation
import Testing
@testable import DeviceKit
@testable import ToolkitFeatures
import ToolkitCore

/// Registers the lockdown services used by collection and readiness checks on a fake device.
func registerStandardServices(_ server: FakeDeviceServer, afc: FakeAFCFileSystem, crashes: FakeAFCFileSystem) {
    server.register(service: ConfigurationProfileService.serviceName) { channel in
        let messages = PlistMessageConnection(channel: channel)
        guard (try await messages.receive(timeout: 5))["RequestType"]?.stringValue == "GetProfileList" else { return }
        try await messages.send([
            "Status": "Acknowledged",
            "OrderedIdentifiers": ["com.example.wifi"],
            "ProfileMetadata": ["com.example.wifi": ["PayloadDisplayName": "Office Wi-Fi", "PayloadOrganization": "Example Corp", "PayloadRemovalDisallowed": false]],
            "ProfileManifest": ["com.example.wifi": ["IsActive": true]],
        ])
    }
    server.register(service: OSTraceRelay.serviceName) { channel in
        // PidList: one leading byte, then a big-endian length and the plist.
        let request = try await PlistMessageConnection(channel: channel).receive(timeout: 5)
        guard request["Request"]?.stringValue == "PidList" else { return }
        let reply = try PlistValue.dictionary(["Status": "RequestSuccessful", "Payload": ["1": ["ProcessName": "launchd"], "250": ["ProcessName": "SpringBoard"]]]).encoded(format: .binary)
        var header = Data([0x01])
        header.appendBigEndian(UInt32(reply.count))
        try await channel.write(header + reply)
    }
    server.domainValues["com.apple.security.mac.amfi"] = ["DeveloperModeStatus": true]
    server.domainValues["com.apple.mobile.backup"] = ["WillEncrypt": false]
    server.register(service: DiagnosticsRelay.serviceName) { channel in
        let messages = PlistMessageConnection(channel: channel)
        while let request = try? await messages.receive(timeout: 5) {
            if request["Request"]?.stringValue == "Goodbye" { break }
            try await messages.send(["Status": "Success", "Diagnostics": ["IORegistry": ["CurrentCapacity": 50], "MobileGestalt": ["ProductType": "iPhone16,1"], "GasGauge": ["CycleCount": 10]]])
        }
    }
    server.register(service: InstallationProxy.serviceName) { channel in
        let messages = PlistMessageConnection(channel: channel)
        _ = try await messages.receive(timeout: 5)
        try await messages.send(["Status": "BrowsingApplications", "CurrentList": [["CFBundleIdentifier": "com.example.demo", "CFBundleName": "Demo", "ApplicationType": "User"]]])
        try await messages.send(["Status": "Complete"])
    }
    server.register(service: ImageMounter.serviceName) { channel in
        let messages = PlistMessageConnection(channel: channel)
        while let request = try? await messages.receive(timeout: 5) {
            if request["Command"]?.stringValue == "Hangup" { break }
            try await messages.send(["EntryList": []])
        }
    }
    server.register(service: ProvisioningProfileService.serviceName) { channel in
        let messages = PlistMessageConnection(channel: channel)
        _ = try await messages.receive(timeout: 5)
        try await messages.send(["Status": 0, "Payload": []])
    }
    server.register(service: AFCClient.crashReportMoverServiceName) { channel in
        try await channel.write(Data("ping".utf8))
    }
    server.register(service: AFCClient.crashReportServiceName) { channel in try await crashes.serve(channel) }
    server.register(service: AFCClient.mediaServiceName) { channel in try await afc.serve(channel) }
    server.register(service: SyslogRelay.serviceName) { channel in
        try await channel.write(Data("kernel: collected line\u{0}".utf8))
        while (try? await channel.hasMoreData()) == true { _ = try? await channel.readSome() }
    }
}

@Suite("Evidence capture (fake device)", .serialized)
struct EvidenceTests {
    func physicalDevice(_ server: FakeDeviceServer) -> Device {
        Device(kind: .physical, udid: server.udid, name: "Test iPhone", productType: "iPhone16,1", osVersion: "18.2", buildVersion: "22C152", transports: [.usb], pairingState: .paired, developerMode: .enabled, usbmuxDeviceID: server.deviceID, sources: [.usbmux])
    }

    @Test func guidedCaseIntakeAndValidation() throws {
        let root = try SecureFileIO.makeTemporaryDirectory(prefix: "cases")
        defer { try? FileManager.default.removeItem(at: root) }
        let target = DeviceTarget(kind: .physical, udid: "00008110-001234560ABC801E", name: "Phone", osVersion: nil, usbmuxDeviceID: 1, coreDeviceIdentifier: nil, transport: .usb)
        #expect(throws: ToolkitError.self) { try CaseWorkflow.createGuidedCase(in: root, target: target, title: "x", purpose: "", authorized: false) }
        #expect(throws: ToolkitError.self) { try CaseWorkflow.createGuidedCase(in: root, target: target, title: "   ", purpose: "", authorized: true) }
        let (folder, intake) = try CaseWorkflow.createGuidedCase(in: root, target: target, title: "  Lost   phone ", purpose: "Authorized review", authorized: true)
        #expect(intake.title == "Lost phone")
        #expect(folder.lastPathComponent.hasPrefix("ios-case-"))
        #expect(folder.lastPathComponent.hasSuffix("001234560ABC801E".suffix(12)))
        try CaseWorkflow.validateForCollection(folder, target: target)
        let other = DeviceTarget(kind: .physical, udid: "OTHER-UDID-000000", name: "Other", osVersion: nil, usbmuxDeviceID: 2, coreDeviceIdentifier: nil, transport: .usb)
        #expect(throws: ToolkitError.self) { try CaseWorkflow.validateForCollection(folder, target: other) }
        try SecureFileIO.writeNewFile(Data("{}".utf8), to: folder.appendingPathComponent("manifest.json"))
        #expect(throws: ToolkitError.self) { try CaseWorkflow.validateForCollection(folder, target: target) }
        let permissions = try FileManager.default.attributesOfItem(atPath: folder.path)[.posixPermissions] as? NSNumber
        #expect(permissions?.intValue == 0o700)
    }

    @Test func collectsSnapshotsStreamsAndHashes() async throws {
        let server = try FakeDeviceServer()
        registerStandardServices(server, afc: FakeAFCFileSystem(files: ["/DCIM/100APPLE/IMG_0001.JPG": Data("x".utf8)]), crashes: FakeAFCFileSystem(files: ["/JetsamEvent-2026.ips": Data("jetsam".utf8)]))
        try await server.start()
        defer { Task { await server.stop() } }
        let root = try SecureFileIO.makeTemporaryDirectory(prefix: "collect")
        defer { try? FileManager.default.removeItem(at: root) }
        let device = physicalDevice(server)
        let folder = try CaseWorkflow.createCaseFolder(in: root, target: device.target)
        let options = CollectionOptions(durationSeconds: 1, includeClassicSyslog: true, includeUnifiedLogs: false, includePacketCapture: false, includeScreenshot: true, includeCrashReports: true)
        let collector = try EvidenceCollector(device: device, caseFolder: folder, options: options, usbmux: server.client)
        let events = LockedValue<[String]>([])
        let manifest = await collector.run { event in
            if case .stepFinished(let step) = event { events.withLock { $0.append("\(step.id):\(step.status.rawValue)") } }
        }
        #expect(manifest.outcome == .partial, "\(manifest.steps.map { "\($0.id)=\($0.status.rawValue) \($0.detail)" })")
        let status = Dictionary(manifest.steps.map { ($0.id, $0.status) }, uniquingKeysWith: { $1 })
        #expect(status["lockdown-values"] == .succeeded)
        #expect(status["apps"] == .succeeded)
        #expect(status["battery"] == .succeeded)
        #expect(status["crash-inventory"] == .succeeded)
        #expect(status["media-root"] == .succeeded)
        #expect(status["stream-syslog"] == .succeeded)
        #expect(status["crash-reports"] == .succeeded)
        #expect(status["processes"] == .succeeded, "processes are read natively, without Xcode")
        #expect(status["configuration-profiles"] == .succeeded, "configuration profiles are read natively, without Xcode")
        #expect(status["coredevice-details"] == .unavailable)
        #expect(status["screenshot"] == .unavailable)
        #expect(FileManager.default.fileExists(atPath: folder.appendingPathComponent("artifacts/crashes/JetsamEvent-2026.ips").path))
        #expect(try String(contentsOf: folder.appendingPathComponent("streams/syslog.log"), encoding: .utf8).contains("collected line"))
        #expect(try String(contentsOf: folder.appendingPathComponent("snapshots/crash-list.txt"), encoding: .utf8).contains("JetsamEvent"))
        #expect(try String(contentsOf: folder.appendingPathComponent("snapshots/processes.txt"), encoding: .utf8).contains("250\tSpringBoard"))
        #expect(try HashManifest.verify(folder: folder, fileName: "SHA256SUMS").isEmpty)
        let manifestJSON = try JSONValue.parse(Data(contentsOf: folder.appendingPathComponent("manifest.json")))
        #expect(manifestJSON["target_udid"]?.string == server.udid)
        #expect(manifestJSON["outcome"]?.string == "partial")
        #expect(events.current.count == manifest.steps.count)
        #expect(manifest.outcome.exitCode == 2)
    }

    @Test func failsWhenTheDeviceCannotBeIdentified() async throws {
        let server = try FakeDeviceServer()
        server.pairRecordAvailable = false
        try await server.start()
        defer { Task { await server.stop() } }
        let root = try SecureFileIO.makeTemporaryDirectory(prefix: "collect-fail")
        defer { try? FileManager.default.removeItem(at: root) }
        let device = physicalDevice(server)
        let folder = try CaseWorkflow.createCaseFolder(in: root, target: device.target)
        let manifest = try await EvidenceCollector(device: device, caseFolder: folder, options: CollectionOptions(durationSeconds: 0), usbmux: server.client).run { _ in }
        #expect(manifest.outcome == .failed)
        #expect(manifest.outcome.exitCode == 1)
        #expect(manifest.steps.count == 1)
        #expect(manifest.steps[0].status == .failed)
        #expect(manifest.steps[0].attempts == 2)
        #expect(FileManager.default.fileExists(atPath: folder.appendingPathComponent("snapshots/lockdown-info.json.error.txt").path))
        #expect(FileManager.default.fileExists(atPath: folder.appendingPathComponent("SHA256SUMS").path))
    }

    @Test func rejectsSimulatorsAndInvalidDurations() throws {
        let simulator = Device(kind: .simulator, udid: "SIM", name: "Sim")
        #expect(throws: ToolkitError.self) { try EvidenceCollector(device: simulator, caseFolder: URL(fileURLWithPath: "/tmp"), options: CollectionOptions()) }
        #expect(throws: ToolkitError.self) { try CollectionOptions(durationSeconds: 7200).validated() }
    }
}

@Suite("Readiness and actions (fake device)", .serialized)
struct ReadinessAndActionTests {
    /// A runner where every Xcode tool is missing, as on a Mac without Xcode.
    final class NoXcodeRunner: CommandRunning, @unchecked Sendable {
        func run(_ request: CommandRequest) async throws -> CommandResult {
            CommandResult(request: request, termination: .exited(72), standardOutput: Data(), standardError: Data("xcrun: error: unable to find utility".utf8), startedAt: Date(), finishedAt: Date())
        }
        func stream(_ request: CommandRequest) -> AsyncThrowingStream<CommandStreamEvent, Error> {
            AsyncThrowingStream { $0.finish(throwing: ToolkitError(.toolMissing, message: "missing")) }
        }
    }

    @Test func matrixWithoutXcodeUsesNativeServices() async throws {
        let server = try FakeDeviceServer()
        registerStandardServices(server, afc: FakeAFCFileSystem(files: [:]), crashes: FakeAFCFileSystem(files: [:]))
        try await server.start()
        defer { Task { await server.stop() } }
        let device = Device(kind: .physical, udid: server.udid, name: "Test iPhone", osVersion: "18.2", transports: [.usb], usbmuxDeviceID: server.deviceID, sources: [.usbmux])
        let probe = CapabilityProbe(runner: NoXcodeRunner(), usbmux: server.client)
        let progress = LockedValue<Int>(0)
        let results = await probe.run(for: device) { _ in progress.withLock { $0 += 1 } }
        let states = Dictionary(results.map { ($0.id, $0.state) }, uniquingKeysWith: { $1 })
        #expect(results.map(\.id) == CapabilityRow.rows(for: .physical).map(\.rawValue))
        #expect(states["host"] == .ready)
        #expect(states["xcode-tools"] == .unavailable)
        #expect(states["usbmuxd"] == .ready)
        #expect(states["device-connection"] == .ready)
        #expect(states["pairing-trust"] == .ready)
        #expect(states["developer-mode"] == .ready)
        #expect(states["lockdown-services"] == .ready)
        #expect(states["backup-service"] == .ready)
        #expect(states["coredevice"] == .blocked)
        // Without Xcode the native check still reads the device; with no image on this Mac for it, the state is Missing.
        #expect(states["developer-services"] == .unavailable)
        #expect(results.first { $0.id == "developer-services" }?.summary.hasPrefix("Missing") == true)
        #expect(progress.current == results.count)
        #expect(results.first { $0.id == "xcode-tools" }?.remediation.contains("App Store") == true)

        let battery = try #require(ActionCatalog.descriptor("battery"))
        #expect(ActionReadiness.evaluate(battery, results: results, device: device) == .ready)
        // The process list no longer needs Xcode (it did in 1.0 before the parity audit); lock state still does.
        #expect(ActionReadiness.evaluate(try #require(ActionCatalog.descriptor("processes")), results: results, device: device) == .ready)
        let lockState = try #require(ActionCatalog.descriptor("lock-state"))
        guard case .needsAttention(let problems) = ActionReadiness.evaluate(lockState, results: results, device: device) else {
            Issue.record("expected attention")
            return
        }
        #expect(!problems.isEmpty)
    }

    @Test func matrixReportsUntrustedDevices() async throws {
        let server = try FakeDeviceServer()
        server.pairRecordAvailable = false
        try await server.start()
        defer { Task { await server.stop() } }
        let device = Device(kind: .physical, udid: server.udid, name: "Test iPhone", transports: [.usb], usbmuxDeviceID: server.deviceID, sources: [.usbmux])
        let results = await CapabilityProbe(runner: NoXcodeRunner(), usbmux: server.client).run(for: device)
        let states = Dictionary(results.map { ($0.id, $0.state) }, uniquingKeysWith: { $1 })
        #expect(states["pairing-trust"] == .unavailable)
        #expect(states["lockdown-services"] == .blocked)
        #expect(results.first { $0.id == "pairing-trust" }?.remediation.contains("Trust") == true)
    }

    @Test func nativeActionsRunAgainstTheCapturedTarget() async throws {
        let server = try FakeDeviceServer()
        registerStandardServices(server, afc: FakeAFCFileSystem(files: ["/Downloads/a.txt": Data("a".utf8)]), crashes: FakeAFCFileSystem(files: [:]))
        try await server.start()
        defer { Task { await server.stop() } }
        let executor = ActionExecutor(runner: NoXcodeRunner(), usbmux: server.client)
        let target = server.target

        let battery = try await executor.execute(try #require(ActionCatalog.descriptor("battery")), target: target, values: [:])
        #expect(battery.summary == "Battery at 50%.")
        let values = try await executor.execute(try #require(ActionCatalog.descriptor("lockdown-values")), target: target, values: [:])
        #expect(values.details.contains { $0.0 == "DeviceName" && $0.1 == "Test iPhone" })
        let developerMode = try await executor.execute(try #require(ActionCatalog.descriptor("developer-mode-status")), target: target, values: [:])
        #expect(developerMode.summary == "Developer Mode is on.")
        let media = try await executor.execute(try #require(ActionCatalog.descriptor("media-list")), target: target, values: ["path": "/"])
        #expect(media.details.map(\.0) == ["Downloads"])
        let query = try await executor.execute(try #require(ActionCatalog.descriptor("app-query")), target: target, values: ["bundle": "com.example.demo"])
        #expect(query.summary.hasPrefix("Demo"))
        let processes = try await executor.execute(try #require(ActionCatalog.descriptor("processes")), target: target, values: [:])
        #expect(processes.summary == "2 processes running.")
        #expect(processes.details.map(\.1) == ["launchd", "SpringBoard"])
        let profiles = try await executor.execute(try #require(ActionCatalog.descriptor("configuration-profiles")), target: target, values: [:])
        #expect(profiles.summary == "1 configuration profile installed.")
        #expect(profiles.details.first?.0 == "Office Wi-Fi")
        #expect(profiles.details.first?.1 == "Example Corp")

        // A pcapd record with no link-layer header: 95-byte header + a 20-byte IPv4 packet.
        var record = [UInt8](repeating: 0, count: 95)
        record[3] = 95
        record[8] = 20
        record[16] = 2
        let blob = Data(record + [0x45] + [UInt8](repeating: 0, count: 19))
        server.register(service: PacketCaptureService.serviceName) { channel in
            try await PlistMessageConnection(channel: channel).send(.data(blob), format: .binary)
        }
        let directory = try SecureFileIO.makeTemporaryDirectory(prefix: "action-pcap")
        defer { try? FileManager.default.removeItem(at: directory) }
        let capture = directory.appendingPathComponent("capture.pcap")
        let pcap = try await executor.execute(try #require(ActionCatalog.descriptor("packet-capture")), target: target, values: ["duration": "5", "output": capture.path])
        #expect(pcap.summary == "The device ended the capture early. 1 packet saved.")
        #expect(try Data(contentsOf: capture).count == 24 + 16 + 14 + 20)
        await #expect(throws: ToolkitError.self) {
            _ = try await executor.execute(try #require(ActionCatalog.descriptor("packet-capture")), target: target, values: ["duration": "5", "output": capture.path])
        }

        await #expect(throws: ToolkitError.self) {
            _ = try await executor.execute(try #require(ActionCatalog.descriptor("app-query")), target: target, values: ["bundle": "not valid!"])
        }
        let simulatorTarget = DeviceTarget(kind: .simulator, udid: "SIM", name: "Sim", osVersion: nil, usbmuxDeviceID: nil, coreDeviceIdentifier: nil, transport: .local)
        await #expect(throws: ToolkitError.self) {
            _ = try await executor.execute(try #require(ActionCatalog.descriptor("battery")), target: simulatorTarget, values: [:])
        }
        let demo = DeviceTarget(kind: .demo, udid: "DEMO-IPHONE", name: "Demo", osVersion: nil, usbmuxDeviceID: nil, coreDeviceIdentifier: nil, transport: nil)
        await #expect(throws: ToolkitError.self) {
            _ = try await executor.execute(try #require(ActionCatalog.descriptor("screenshot")), target: demo, values: [:])
        }
    }
}
