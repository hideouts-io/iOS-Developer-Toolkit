import DeviceKit
import Foundation
import OSLog
import ToolkitCore

public struct CollectionOptions: Codable, Sendable, Hashable {
    /// Stream duration in seconds (0 = no streams).
    public var durationSeconds: Int
    public var includeClassicSyslog: Bool
    public var includeUnifiedLogs: Bool
    public var includePacketCapture: Bool
    public var includeScreenshot: Bool
    public var includeCrashReports: Bool

    public init(durationSeconds: Int = 60, includeClassicSyslog: Bool = false, includeUnifiedLogs: Bool = true, includePacketCapture: Bool = false, includeScreenshot: Bool = false, includeCrashReports: Bool = false) {
        self.durationSeconds = durationSeconds
        self.includeClassicSyslog = includeClassicSyslog
        self.includeUnifiedLogs = includeUnifiedLogs
        self.includePacketCapture = includePacketCapture
        self.includeScreenshot = includeScreenshot
        self.includeCrashReports = includeCrashReports
    }

    public func validated() throws -> CollectionOptions {
        guard (0...3600).contains(durationSeconds) else { throw ToolkitError.invalidInput("The stream duration must be between 0 and 3,600 seconds.") }
        return self
    }

    var hasStreams: Bool { durationSeconds > 0 && (includeClassicSyslog || includeUnifiedLogs || includePacketCapture) }
}

public enum StepStatus: String, Codable, Sendable {
    case succeeded, failed, unavailable, cancelled
}

public struct CollectionStep: Codable, Sendable, Hashable, Identifiable {
    public var id: String
    public var title: String
    public var mechanism: String
    public var required: Bool
    public var status: StepStatus
    public var attempts: Int
    public var startedAt: Date
    public var finishedAt: Date
    public var outputPath: String?
    public var detail: String

    enum CodingKeys: String, CodingKey {
        case id, title, mechanism, required, status, attempts, detail
        case startedAt = "started_at"
        case finishedAt = "finished_at"
        case outputPath = "output_path"
    }
}

public enum CollectionOutcome: String, Codable, Sendable {
    /// Every step succeeded.
    case complete
    /// Required identification succeeded; some optional coverage is missing.
    case partial
    /// Required identification failed.
    case failed

    /// Exit codes kept from earlier releases' collector (0 complete, 2 partial, 1 failed).
    public var exitCode: Int32 {
        switch self {
        case .complete: return 0
        case .partial: return 2
        case .failed: return 1
        }
    }
}

public struct CollectionManifest: Codable, Sendable {
    public var schemaVersion = 2
    public var application = "iOS Developer Toolkit"
    public var applicationVersion: String
    public var targetUDID: String
    public var targetName: String
    public var targetModel: String?
    public var targetOSVersion: String?
    public var targetBuild: String?
    public var options: CollectionOptions
    public var startedAt: Date
    public var finishedAt: Date
    public var outcome: CollectionOutcome
    public var stoppedEarly: Bool
    public var steps: [CollectionStep]
    public var limitations: [String] = [
        "A failed or unavailable step is a coverage gap, not proof of absence.",
        "Hashes detect later changes to finalized files; they do not by themselves prove when, where, or by whom evidence was acquired.",
        "Service views (lockdown, AFC, CoreDevice) are Apple-defined and are not a full file-system acquisition.",
    ]

    enum CodingKeys: String, CodingKey {
        case application, options, outcome, steps, limitations
        case schemaVersion = "schema_version"
        case applicationVersion = "application_version"
        case targetUDID = "target_udid"
        case targetName = "target_name"
        case targetModel = "target_model"
        case targetOSVersion = "target_os_version"
        case targetBuild = "target_build"
        case startedAt = "started_at"
        case finishedAt = "finished_at"
        case stoppedEarly = "stopped_early"
    }
}

public enum CollectionEvent: Sendable {
    case stepStarted(String)
    case stepFinished(CollectionStep)
    case streaming(secondsRemaining: Int)
    case finalizing
}

/// Collects a bounded, hashed evidence case from one physical device.
public actor EvidenceCollector {
    public nonisolated let device: Device
    public nonisolated let caseFolder: URL
    public nonisolated let options: CollectionOptions
    private let runner: CommandRunning
    private let usbmux: USBMuxClient
    private let coreDevice: CoreDeviceClient
    private var steps: [CollectionStep] = []
    private var stopRequested = false
    private let logger = ToolkitLog.evidence

    public init(device: Device, caseFolder: URL, options: CollectionOptions, runner: CommandRunning = ProcessCommandRunner(), usbmux: USBMuxClient = USBMuxClient()) throws {
        guard device.kind == .physical else { throw ToolkitError(.unsupported, message: "Evidence collection is available for physical devices.") }
        self.device = device
        self.caseFolder = caseFolder
        self.options = try options.validated()
        self.runner = runner
        self.usbmux = usbmux
        coreDevice = CoreDeviceClient(runner: runner)
    }

    /// Ends timed streams early; collection still finalizes the manifest and hashes.
    public func requestStop() {
        stopRequested = true
    }

    public func run(events: @escaping @Sendable (CollectionEvent) -> Void) async -> CollectionManifest {
        let started = Date()
        let target = device.target
        logger.info("Evidence collection started")

        // 1. Required identification through an identity-verified lockdown session.
        await snapshot("lockdown-values", "Lockdown device information", "lockdownd GetValue (native)", required: true, file: "snapshots/lockdown-info.json", events: events) {
            try await DeviceSession.with(target, usbmux: self.usbmux) { try await $0.getValue() ?? .dictionary([:]) }.prettyJSONString()
        }
        try? SecureFileIO.writeNewFile(try JSONOutput.encode(device), to: caseFolder.appendingPathComponent("snapshots/device-record.json"))

        let identified = steps.first?.status == .succeeded
        if identified {
            await collectSnapshots(target, events: events)
            if options.hasStreams && !stopRequested { await collectStreams(target, events: events) }
            await collectArtifacts(target, events: events)
        }

        events(.finalizing)
        let succeeded = steps.allSatisfy { $0.status == .succeeded }
        let outcome: CollectionOutcome = !identified ? .failed : (succeeded ? .complete : .partial)
        let manifest = CollectionManifest(applicationVersion: ToolkitVersion.current, targetUDID: target.udid, targetName: target.name, targetModel: device.marketingName ?? device.productType, targetOSVersion: device.osVersion, targetBuild: device.buildVersion, options: options, startedAt: started, finishedAt: Date(), outcome: outcome, stoppedEarly: stopRequested, steps: steps)
        do {
            try SecureFileIO.writeNewFile(try JSONOutput.encode(manifest), to: caseFolder.appendingPathComponent(CaseWorkflow.manifestFileName))
            try HashManifest.write(for: caseFolder, fileName: "SHA256SUMS")
        } catch {
            logger.error("Could not finalize the case: \(error.localizedDescription, privacy: .public)")
        }
        logger.info("Evidence collection finished: \(outcome.rawValue, privacy: .public)")
        return manifest
    }

    // MARK: Snapshots

    private func collectSnapshots(_ target: DeviceTarget, events: @escaping @Sendable (CollectionEvent) -> Void) async {
        let usbmux = self.usbmux
        func lockdown(_ body: @escaping @Sendable (DeviceSession) async throws -> String) -> @Sendable () async throws -> String {
            { try await DeviceSession.with(target, usbmux: usbmux, body) }
        }
        let coreDevice = self.coreDevice
        let coreDeviceAvailable = device.supportsCoreDevice

        await snapshot("coredevice-details", "CoreDevice device information", "devicectl device info details", file: "snapshots/coredevice-details.json", unavailableUnless: coreDeviceAvailable, events: events) {
            try await coreDevice.details(target).response.json.prettyString()
        }
        await snapshot("developer-images", "Mounted developer images", "mobile_image_mounter CopyDevices", file: "snapshots/developer-images.json", events: events, lockdown { session in
            let mounter = try await ImageMounter.open(session)
            defer { Task { await mounter.close() } }
            return PlistValue.array(try await mounter.mountedImages().map(\.raw)).prettyJSONString()
        })
        await snapshot("ddi-services", "Developer services status", "devicectl device info ddiServices --no-auto-mount-ddis", file: "snapshots/ddi-services.json", unavailableUnless: coreDeviceAvailable, events: events) {
            try await coreDevice.ddiServices(target, autoMount: false).json.prettyString()
        }
        for (id, title, file, kind) in [("diagnostics", "Diagnostics overview", "snapshots/diagnostics.json", 0), ("mobilegestalt", "MobileGestalt values", "snapshots/mobilegestalt.json", 1), ("battery", "Battery snapshot", "snapshots/battery.json", 2), ("ioregistry", "IORegistry (device tree)", "snapshots/ioregistry.json", 3)] {
            await snapshot(id, title, "diagnostics_relay", file: file, events: events, lockdown { session in
                let relay = try await DiagnosticsRelay.open(session)
                defer { Task { await relay.close() } }
                switch kind {
                case 0: return try await relay.all().prettyJSONString()
                case 1: return try await relay.mobileGestalt(keys: DiagnosticsRelay.defaultGestaltKeys).prettyJSONString()
                case 2: return try await relay.battery().prettyJSONString()
                default: return try await relay.ioRegistry(plane: "IODeviceTree").prettyJSONString()
                }
            })
        }
        await snapshot("apps", "Installed applications", "installation_proxy Browse", file: "snapshots/apps.json", events: events, lockdown { session in
            let proxy = try await InstallationProxy.open(session)
            defer { Task { await proxy.close() } }
            let apps = try await proxy.browse(includeSizes: true)
            return String(decoding: try JSONOutput.encode(apps.map { ["bundle_identifier": $0.bundleIdentifier, "name": $0.name, "version": $0.version ?? "", "build": $0.build ?? "", "type": $0.applicationType, "total_bytes": $0.totalBytes.map(String.init) ?? ""] }), as: UTF8.self)
        })
        await snapshot("processes", "Running processes", "devicectl device info processes", file: "snapshots/processes.txt", unavailableUnless: coreDeviceAvailable, events: events) {
            try await coreDevice.processes(target).map { "\($0.pid)\t\($0.executablePath ?? "")" }.joined(separator: "\n") + "\n"
        }
        await snapshot("configuration-profiles", "Configuration profiles", "devicectl device profile list", file: "snapshots/configuration-profiles.json", unavailableUnless: coreDeviceAvailable, events: events) {
            try await coreDevice.profiles(target).json.prettyString()
        }
        await snapshot("provisioning-profiles", "Provisioning profiles", "misagent CopyAll", file: "snapshots/provisioning-profiles.json", events: events, lockdown { session in
            let service = try await ProvisioningProfileService.open(session)
            defer { Task { await service.close() } }
            return String(decoding: try JSONOutput.encode(try await service.copyAll().map(ProvisioningProfileDecoder.decode)), as: UTF8.self)
        })
        await snapshot("crash-inventory", "Crash report inventory", "crashreportcopymobile (AFC)", file: "snapshots/crash-list.txt", events: events, lockdown { session in
            let afc = try await AFCClient.openCrashReports(session)
            defer { Task { await afc.close() } }
            return try await afc.walk("/").joined(separator: "\n") + "\n"
        })
        await snapshot("media-root", "Media folder listing", "AFC", file: "snapshots/afc-root.txt", events: events, lockdown { session in
            let afc = try await AFCClient.openMedia(session)
            defer { Task { await afc.close() } }
            return try await afc.listDirectory("/").joined(separator: "\n") + "\n"
        })
    }

    /// Runs one snapshot, retrying once, and records the outcome.
    private func snapshot(_ id: String, _ title: String, _ mechanism: String, required: Bool = false, file: String, unavailableUnless available: Bool = true, events: @escaping @Sendable (CollectionEvent) -> Void, _ body: @escaping @Sendable () async throws -> String) async {
        events(.stepStarted(title))
        let started = Date()
        guard available else {
            record(CollectionStep(id: id, title: title, mechanism: mechanism, required: required, status: .unavailable, attempts: 0, startedAt: started, finishedAt: Date(), outputPath: nil, detail: "Needs Xcode's device service for this device."), events: events)
            return
        }
        var lastError = ""
        for attempt in 1...2 {
            if stopRequested && !required {
                record(CollectionStep(id: id, title: title, mechanism: mechanism, required: required, status: .cancelled, attempts: attempt - 1, startedAt: started, finishedAt: Date(), outputPath: nil, detail: "Stopped before this step ran."), events: events)
                return
            }
            do {
                let output = try await withTimeout(180, operation: title) { try await body() }
                let url = caseFolder.appendingPathComponent(file)
                try SecureFileIO.writeNewFile(Data(output.utf8), to: url)
                record(CollectionStep(id: id, title: title, mechanism: mechanism, required: required, status: .succeeded, attempts: attempt, startedAt: started, finishedAt: Date(), outputPath: file, detail: ""), events: events)
                return
            } catch {
                let toolkitError = error as? ToolkitError
                lastError = [toolkitError?.message ?? error.localizedDescription, toolkitError?.technicalDetail].compactMap { $0 }.joined(separator: " — ")
            }
        }
        try? SecureFileIO.writeNewFile(Data(lastError.utf8), to: caseFolder.appendingPathComponent(file + ".error.txt"))
        record(CollectionStep(id: id, title: title, mechanism: mechanism, required: required, status: .failed, attempts: 2, startedAt: started, finishedAt: Date(), outputPath: file + ".error.txt", detail: lastError), events: events)
    }

    private func record(_ step: CollectionStep, events: @Sendable (CollectionEvent) -> Void) {
        steps.append(step)
        events(.stepFinished(step))
    }

    // MARK: Streams

    private func collectStreams(_ target: DeviceTarget, events: @escaping @Sendable (CollectionEvent) -> Void) async {
        let duration = options.durationSeconds
        let folder = caseFolder
        let usbmux = self.usbmux
        var specs: [(id: String, title: String, mechanism: String, file: String, kind: Int)] = []
        if options.includeClassicSyslog { specs.append(("stream-syslog", "Classic syslog stream", SyslogRelay.serviceName, "streams/syslog.log", 0)) }
        if options.includeUnifiedLogs { specs.append(("stream-unified", "Unified Logging stream", OSTraceRelay.serviceName, "streams/unified.jsonl", 1)) }
        if options.includePacketCapture { specs.append(("stream-pcap", "Network packet capture", PacketCaptureService.serviceName, "streams/network.pcap", 2)) }

        let deadline = ContinuousClock.now + .seconds(duration)
        let stopFlag = LockedValue(false)
        let ticker = Task {
            var remaining = duration
            while remaining > 0 && !Task.isCancelled {
                events(.streaming(secondsRemaining: remaining))
                try? await Task.sleep(for: .seconds(1))
                remaining = max(0, Int((deadline - ContinuousClock.now).components.seconds))
                if await self.stopRequested { stopFlag.withLock { $0 = true } }
            }
        }
        let results = await withTaskGroup(of: CollectionStep.self) { group in
            for spec in specs {
                group.addTask {
                    let started = Date()
                    let url = folder.appendingPathComponent(spec.file)
                    do {
                        let bytes = try await EvidenceCollector.captureStream(kind: spec.kind, target: target, usbmux: usbmux, url: url, deadline: deadline, stopFlag: stopFlag)
                        return CollectionStep(id: spec.id, title: spec.title, mechanism: spec.mechanism, required: false, status: .succeeded, attempts: 1, startedAt: started, finishedAt: Date(), outputPath: spec.file, detail: "\(bytes) bytes captured")
                    } catch {
                        let message = (error as? ToolkitError)?.message ?? error.localizedDescription
                        return CollectionStep(id: spec.id, title: spec.title, mechanism: spec.mechanism, required: false, status: .failed, attempts: 1, startedAt: started, finishedAt: Date(), outputPath: FileManager.default.fileExists(atPath: url.path) ? spec.file : nil, detail: message)
                    }
                }
            }
            var collected: [CollectionStep] = []
            for await step in group { collected.append(step) }
            return collected
        }
        ticker.cancel()
        for step in results.sorted(by: { $0.id < $1.id }) { record(step, events: events) }
    }

    static func captureStream(kind: Int, target: DeviceTarget, usbmux: USBMuxClient, url: URL, deadline: ContinuousClock.Instant, stopFlag: LockedValue<Bool>) async throws -> Int64 {
        try await DeviceSession.with(target, usbmux: usbmux) { session in
            try await withThrowingTaskGroup(of: Int64.self) { group in
                group.addTask {
                    if kind == 2 {
                        let writer = try PcapFileWriter(creatingNewFileAt: url)
                        var bytes: Int64 = 0
                        do {
                            for try await packet in try await PacketCaptureService.stream(session) {
                                try writer.write(packet)
                                bytes += Int64(packet.frame.count)
                            }
                        } catch is CancellationError {}
                        _ = try? writer.finish()
                        return bytes
                    }
                    try SecureFileIO.writeNewFile(Data(), to: url)
                    let output = try FileHandle(forWritingTo: url)
                    defer { try? output.close() }
                    var bytes: Int64 = 0
                    let stream = kind == 0 ? try await SyslogRelay.stream(session) : try await OSTraceRelay.stream(session)
                    do {
                        for try await chunk in stream {
                            try output.write(contentsOf: chunk.spoolBytes)
                            bytes += Int64(chunk.spoolBytes.count)
                        }
                    } catch is CancellationError {}
                    return bytes
                }
                group.addTask {
                    while ContinuousClock.now < deadline && !stopFlag.current {
                        try await Task.sleep(for: .milliseconds(250))
                    }
                    return -1
                }
                // The timer finishes first in the normal case; cancelling the capture then lets
                // it flush and report its byte count.
                var captured: Int64 = 0
                while let value = try await group.next() {
                    if value == -1 {
                        group.cancelAll()
                    } else {
                        captured = value
                    }
                }
                return captured
            }
        }
    }

    // MARK: Artifacts

    private func collectArtifacts(_ target: DeviceTarget, events: @escaping @Sendable (CollectionEvent) -> Void) async {
        let coreDevice = self.coreDevice
        let usbmux = self.usbmux
        let folder = caseFolder
        if options.includeScreenshot {
            await snapshotFile("screenshot", "Screenshot", "devicectl device capture screenshot", file: "artifacts/screen.png", unavailableUnless: device.supportsCoreDevice, events: events) {
                _ = try await coreDevice.screenshot(target, to: folder.appendingPathComponent("artifacts/screen.png"))
            }
        }
        if options.includeCrashReports {
            await snapshotFile("crash-reports", "Crash reports", "crashreportcopymobile (AFC)", file: "artifacts/crashes", events: events) {
                let destination = folder.appendingPathComponent("artifacts/crashes")
                try SecureFileIO.createPrivateDirectory(at: destination)
                try await DeviceSession.with(target, usbmux: usbmux) { session in
                    let afc = try await AFCClient.openCrashReports(session)
                    defer { Task { await afc.close() } }
                    for path in try await afc.walk("/") {
                        let local = try SecureFileIO.safeChild(of: destination, relativePath: String(path.drop { $0 == "/" }))
                        try SecureFileIO.createPrivateDirectory(at: local.deletingLastPathComponent())
                        _ = try await afc.download(path, to: local)
                    }
                }
            }
        }
    }

    private func snapshotFile(_ id: String, _ title: String, _ mechanism: String, file: String, unavailableUnless available: Bool = true, events: @escaping @Sendable (CollectionEvent) -> Void, _ body: @escaping @Sendable () async throws -> Void) async {
        events(.stepStarted(title))
        let started = Date()
        guard available else {
            record(CollectionStep(id: id, title: title, mechanism: mechanism, required: false, status: .unavailable, attempts: 0, startedAt: started, finishedAt: Date(), outputPath: nil, detail: "Needs Xcode's device service for this device."), events: events)
            return
        }
        do {
            try await withTimeout(900, operation: title) { try await body() }
            record(CollectionStep(id: id, title: title, mechanism: mechanism, required: false, status: .succeeded, attempts: 1, startedAt: started, finishedAt: Date(), outputPath: file, detail: ""), events: events)
        } catch {
            record(CollectionStep(id: id, title: title, mechanism: mechanism, required: false, status: .failed, attempts: 1, startedAt: started, finishedAt: Date(), outputPath: nil, detail: (error as? ToolkitError)?.message ?? error.localizedDescription), events: events)
        }
    }
}
