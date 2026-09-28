import Foundation
import Testing
@testable import DeviceKit
@testable import ToolkitFeatures
import ToolkitCore

/// Read-only checks against a real iPhone or iPad connected by USB. Opt in with
/// IDT_DEVICE_TESTS=1. Nothing here changes the device: captures go to temporary files that are
/// deleted afterwards. Output lines (prefixed “[device]”) record what the device reported, without
/// identifiers.
@Suite("Real device (opt-in, read-only)", .serialized, .enabled(if: ProcessInfo.processInfo.environment["IDT_DEVICE_TESTS"] == "1"))
struct RealDeviceTests {
    func connectedTarget() async throws -> DeviceTarget {
        let device = try #require(try await USBMuxClient().listDevices().first { $0.transport == .usb }, "Connect an iPhone or iPad by USB")
        let version = try await DeviceSession.with(DeviceTarget(kind: .physical, udid: device.udid, name: "device", osVersion: nil, usbmuxDeviceID: device.deviceID, coreDeviceIdentifier: nil, transport: .usb)) { session in
            try await session.getValue(key: "ProductVersion")?.stringValue
        }
        return DeviceTarget(kind: .physical, udid: device.udid, name: "device", osVersion: version, usbmuxDeviceID: device.deviceID, coreDeviceIdentifier: nil, transport: .usb)
    }

    func report(_ text: String) { print("[device] \(text)") }

    @Test(.timeLimit(.minutes(5)))
    func lockdownServicesAnswer() async throws {
        let target = try await connectedTarget()
        try await DeviceSession.with(target) { session in
            let values = try #require(try await session.getValue())
            let product = values["ProductType"]?.stringValue ?? "?"
            report("lockdown: \(product), iOS \(values["ProductVersion"]?.stringValue ?? "?") (\(values["BuildVersion"]?.stringValue ?? "?")), \(values["CPUArchitecture"]?.stringValue ?? "?"), chip \(values["ChipID"]?.intValue.map { String($0, radix: 16) } ?? "?") board \(values["BoardId"]?.intValue.map(String.init) ?? "?")")
            #expect(values["UniqueDeviceID"]?.stringValue?.caseInsensitiveCompare(target.udid) == .orderedSame)

            let processes = try await OSTraceRelay.processList(session)
            report("process list (PidList): \(processes.count) processes; includes launchd: \(processes.contains { $0.pid == 1 })")
            #expect(processes.count > 20)
            #expect(processes.contains { $0.pid == 1 })

            let profiles = try await { () async throws -> [InstalledConfigurationProfile] in
                let service = try await ConfigurationProfileService.open(session)
                defer { Task { await service.close() } }
                return try await service.profiles()
            }()
            report("configuration profiles (MCInstall): \(profiles.count)")

            let provisioning = try await { () async throws -> [Data] in
                let service = try await ProvisioningProfileService.open(session)
                defer { Task { await service.close() } }
                return try await service.copyAll()
            }()
            report("provisioning profiles (misagent): \(provisioning.count)")

            let apps = try await { () async throws -> [InstalledApplication] in
                let proxy = try await InstallationProxy.open(session)
                defer { Task { await proxy.close() } }
                return try await proxy.browse(includeSizes: false)
            }()
            report("installed apps (installation_proxy): \(apps.count)")
            #expect(!apps.isEmpty)

            let diagnostics = try await { () async throws -> PlistValue in
                let relay = try await DiagnosticsRelay.open(session)
                defer { Task { await relay.close() } }
                return try await relay.all()
            }()
            report("diagnostics (diagnostics_relay): \((diagnostics.dictionaryValue ?? [:]).keys.sorted().joined(separator: ", "))")

            let mounter = try await ImageMounter.open(session)
            let images = try await mounter.mountedImages()
            let personalized = try await mounter.lookup(.personalized)
            await mounter.close()
            report("image mounter: \(images.count) mounted entries; personalized developer image mounted: \(!personalized.isEmpty)")
        }
    }

    @Test(.timeLimit(.minutes(5)))
    func developerImageStatusIsEvaluated() async throws {
        let target = try await connectedTarget()
        let status = await DeveloperImageManager().status(for: target)
        report("developer image: \(status.state.rawValue) — \(status.headline)")
        for (label, value) in status.detailRows where label != "Next step" { report("  \(label): \(value)") }
        #expect(status.state != .failed, "\(status.technicalDetail ?? "")")
        #expect(status.facts?.productVersion == target.osVersion)
        // Whether this Mac's images fit the device once Developer Mode is on (evaluation only).
        if var facts = status.facts, facts.developerModeEnabled == false {
            facts.developerModeEnabled = true
            let host = DeveloperImageHostInventory.discover(userFolders: [], coreDeviceAvailable: false)
            let assumed = DeveloperImageEvaluator.evaluate(facts: facts, observation: DeveloperImageObservation(), host: host)
            report("with Developer Mode on, this Mac's images would give: \(assumed.state.rawValue) — \(assumed.headline) [\(assumed.hostImage ?? "no host image")]")
            if let match = host.personalizedMatch(chipID: facts.chipID, boardID: facts.boardID) {
                report("  build identity: \(match.identity.productType ?? "?") \(match.identity.variant ?? "")")
            }
        }
    }

    /// Starts the Bluetooth logger for a few seconds. Without Apple's logging profile the device
    /// may refuse the service or send nothing; the outcome is reported, not asserted.
    @Test(.timeLimit(.minutes(2)))
    func bluetoothLoggerBehaviour() async throws {
        let target = try await connectedTarget()
        do {
            let count = try await DeviceSession.with(target) { session -> Int in
                let records = try await BluetoothPacketLogger.records(session)
                return try await withThrowingTaskGroup(of: Int.self) { group in
                    group.addTask {
                        var count = 0
                        do { for try await _ in records { count += 1 } } catch is CancellationError {}
                        return count
                    }
                    try await Task.sleep(for: .seconds(4))
                    group.cancelAll()
                    return try await group.next() ?? 0
                }
            }
            report("bluetooth logger: service started, \(count) records in 4 s")
        } catch let error as ToolkitError {
            report("bluetooth logger: \(error.message) (\(error.technicalDetail ?? ""))")
        }
    }

    @Test(.timeLimit(.minutes(5)))
    func streamsDeliverData() async throws {
        let target = try await connectedTarget()
        let directory = try SecureFileIO.makeTemporaryDirectory(prefix: "device-streams")
        defer { try? FileManager.default.removeItem(at: directory) }

        func collect(_ open: @escaping @Sendable (DeviceSession) async throws -> AsyncThrowingStream<LogChunk, Error>, seconds: Double) async throws -> (lines: Int, bytes: Int) {
            try await DeviceSession.with(target) { session in
                let stream = try await open(session)
                return try await withThrowingTaskGroup(of: (Int, Int).self) { group in
                    group.addTask {
                        var lines = 0, bytes = 0
                        do {
                            for try await chunk in stream { lines += chunk.lines.count; bytes += chunk.spoolBytes.count }
                        } catch is CancellationError {}
                        return (lines, bytes)
                    }
                    try await Task.sleep(for: .seconds(seconds))
                    group.cancelAll()
                    return try await group.next() ?? (0, 0)
                }
            }
        }
        let syslog = try await collect({ try await SyslogRelay.stream($0) }, seconds: 4)
        report("classic syslog (syslog_relay): \(syslog.lines) lines, \(syslog.bytes) bytes in 4 s")
        #expect(syslog.bytes > 0)
        let unified = try await collect({ try await OSTraceRelay.stream($0) }, seconds: 4)
        report("unified logging (os_trace_relay): \(unified.lines) records, \(unified.bytes) bytes in 4 s")
        #expect(unified.lines > 0)

        let pcapFile = directory.appendingPathComponent("capture.pcap")
        let writer = try PcapFileWriter(creatingNewFileAt: pcapFile)
        try await DeviceSession.with(target) { session in
            let stream = try await PacketCaptureService.stream(session)
            try await withThrowingTaskGroup(of: Void.self) { group in
                group.addTask {
                    do { for try await packet in stream { try writer.write(packet) } } catch is CancellationError {}
                }
                try await Task.sleep(for: .seconds(5))
                group.cancelAll()
            }
        }
        let digest = try writer.finish()
        let size = try Data(contentsOf: pcapFile).count
        report("packet capture (pcapd): \(writer.packetCount) packets, \(size) bytes in 5 s, sha256 \(digest.prefix(12))…")
        #expect(size >= 24, "a pcap file has at least its global header")
    }
}
