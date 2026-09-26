import Foundation
import Testing
@testable import DeviceKit
import DeviceTestSupport
import ToolkitCore

private func runWithServer(_ configure: (FakeDeviceServer) -> Void, _ body: (FakeDeviceServer) async throws -> Void) async throws {
    let server = try FakeDeviceServer()
    configure(server)
    try await server.start()
    do {
        try await body(server)
    } catch {
        await server.stop()
        throw error
    }
    await server.stop()
}

/// Builds an os_trace_relay record with the documented layout.
func makeTraceRecord(pid: UInt32, seconds: UInt32, microseconds: UInt32, level: UInt8, filename: String, image: String, message: String, subsystem: String?, category: String?) -> Data {
    var bytes = [UInt8](repeating: 0, count: 129)
    func put32(_ value: UInt32, _ offset: Int) {
        for index in 0..<4 { bytes[offset + index] = UInt8((value >> (8 * UInt32(index))) & 0xFF) }
    }
    func put16(_ value: UInt16, _ offset: Int) {
        bytes[offset] = UInt8(value & 0xFF)
        bytes[offset + 1] = UInt8(value >> 8)
    }
    put32(pid, 9)
    put32(seconds, 55)
    put32(microseconds, 63)
    bytes[68] = level
    let imageBytes = Array(image.utf8) + [0]
    let messageBytes = Array(message.utf8) + [0]
    put16(UInt16(imageBytes.count), 107)
    put16(UInt16(messageBytes.count), 109)
    let subsystemBytes = subsystem.map { Array($0.utf8) + [0] } ?? []
    let categoryBytes = category.map { Array($0.utf8) + [0] } ?? []
    put32(UInt32(subsystemBytes.count), 117)
    put32(UInt32(categoryBytes.count), 121)
    bytes += Array(filename.utf8) + [0]
    bytes += imageBytes + messageBytes + subsystemBytes + categoryBytes
    return Data(bytes)
}

@Suite("Lockdown services (fake device)", .serialized)
struct ServiceTests {
    @Test func syslogRelaySplitsRecordsAndKeepsRawBytes() async throws {
        let raw = Data("Sep 26 10:00:00 iPhone kernel[0] <Notice>: one\u{0}Sep 26 10:00:01 iPhone SpringBoard[55] <Error>: two\nthree\u{0}".utf8)
        try await runWithServer({ _ in }) { server in
            server.register(service: SyslogRelay.serviceName) { channel in
                try await channel.write(raw.prefix(20))
                try await channel.write(raw.dropFirst(20))
            }
            try await DeviceSession.with(server.target, usbmux: server.client) { session in
                var spooled = Data()
                var lines: [String] = []
                for try await chunk in try await SyslogRelay.stream(session) {
                    spooled.append(chunk.spoolBytes)
                    lines += chunk.lines.map(\.message)
                }
                #expect(spooled == raw)
                #expect(lines.count == 3)
                #expect(lines[0].hasSuffix("<Notice>: one"))
                #expect(lines[2] == "three")
            }
        }
    }

    @Test func syslogParserBoundsUnterminatedRecords() {
        var parser = SyslogRecordParser()
        parser.maximumRecordLength = 10
        let lines = parser.consume(Data(repeating: 0x41, count: 25))
        #expect(lines.count == 1)
        #expect(parser.flush().isEmpty)
    }

    @Test func osTraceRelayDecodesStructuredRecords() async throws {
        let record = makeTraceRecord(pid: 321, seconds: 1_700_000_000, microseconds: 250_000, level: 0x10, filename: "/usr/libexec/locationd", image: "CoreLocation", message: "Location updated", subsystem: "com.apple.locationd", category: "Core")
        try await runWithServer({ _ in }) { server in
            server.register(service: OSTraceRelay.serviceName) { channel in
                let request = try await PlistMessageConnection(channel: channel).receive(timeout: 5)
                #expect(request["Request"]?.stringValue == "StartActivity")
                let reply = try PlistValue(dictionaryLiteral: ("Status", "RequestSuccessful")).encoded(format: .xml)
                var header = Data()
                header.appendLittleEndian(UInt32(4))
                header.appendLittleEndian(UInt32(reply.count))
                try await channel.write(header + reply)
                var frame = Data([0x02])
                frame.appendLittleEndian(UInt32(record.count))
                try await channel.write(frame + record)
            }
            try await DeviceSession.with(server.target, usbmux: server.client) { session in
                var lines: [LogLine] = []
                for try await chunk in try await OSTraceRelay.stream(session) {
                    lines += chunk.lines
                    #expect(!chunk.spoolBytes.isEmpty)
                }
                #expect(lines.count == 1)
                let line = try #require(lines.first)
                #expect(line.pid == 321)
                #expect(line.process == "locationd")
                #expect(line.level == "Error")
                #expect(line.subsystem == "com.apple.locationd")
                #expect(line.category == "Core")
                #expect(line.message == "Location updated")
                #expect(line.timestamp == Date(timeIntervalSince1970: 1_700_000_000.25))
            }
        }
    }

    @Test func osTraceParserFallsBackOnMalformedRecords() {
        let line = OSTraceRecordParser.parse(Data("short but readable text".utf8))
        #expect(line.level == "Undecoded")
        #expect(line.message.contains("readable"))
        let truncated = makeTraceRecord(pid: 1, seconds: 1, microseconds: 0, level: 0, filename: "x", image: "img", message: "hello", subsystem: nil, category: nil).prefix(135)
        #expect(OSTraceRecordParser.parse(truncated).level == "Undecoded")
    }

    @Test func pcapdPacketsBecomeAValidPcapFile() async throws {
        var header = [UInt8](repeating: 0, count: 95)
        let payload: [UInt8] = [0x45, 0x00, 0x00, 0x14] + [UInt8](repeating: 0xAB, count: 16)
        func be32(_ value: UInt32, _ offset: Int) {
            header[offset] = UInt8(value >> 24); header[offset + 1] = UInt8((value >> 16) & 0xFF)
            header[offset + 2] = UInt8((value >> 8) & 0xFF); header[offset + 3] = UInt8(value & 0xFF)
        }
        be32(95, 0)
        be32(UInt32(payload.count), 5)
        header[12] = 0x01
        be32(2, 13)
        for (index, byte) in Array("en0".utf8).enumerated() { header[25 + index] = byte }
        header[41] = 42
        for (index, byte) in Array("Safari".utf8).enumerated() { header[45 + index] = byte }
        be32(1_700_000_000, 87)
        be32(5, 91)
        let blob = Data(header + payload)

        let packet = try #require(PcapdRecordParser.parse(blob))
        #expect(packet.interfaceName == "en0")
        #expect(packet.processName == "Safari")
        #expect(packet.pid == 42)
        #expect(packet.isOutbound)
        #expect(packet.frame.count == 14 + payload.count)
        #expect(packet.frame[12] == 0x08)

        try await runWithServer({ _ in }) { server in
            server.register(service: PacketCaptureService.serviceName) { channel in
                try await PlistMessageConnection(channel: channel).send(.data(blob), format: .binary)
            }
            let directory = try SecureFileIO.makeTemporaryDirectory(prefix: "pcap-test")
            defer { try? FileManager.default.removeItem(at: directory) }
            let file = directory.appendingPathComponent("capture.pcap")
            let writer = try PcapFileWriter(creatingNewFileAt: file)
            try await DeviceSession.with(server.target, usbmux: server.client) { session in
                for try await packet in try await PacketCaptureService.stream(session) {
                    try writer.write(packet)
                }
            }
            let digest = try writer.finish()
            let data = try Data(contentsOf: file)
            #expect(data.readLittleEndianUInt32(at: 0) == 0xA1B2_C3D4)
            #expect(data.readLittleEndianUInt32(at: 20) == 1)
            #expect(data.readLittleEndianUInt32(at: 24) == 1_700_000_000)
            #expect(Int(data.readLittleEndianUInt32(at: 32)) == 14 + payload.count)
            #expect(writer.packetCount == 1)
            #expect(digest == (try SecureFileIO.sha256(of: file)))
        }
    }

    @Test func diagnosticsBatteryAndFailures() async throws {
        try await runWithServer({ _ in }) { server in
            server.register(service: DiagnosticsRelay.serviceName) { channel in
                let messages = PlistMessageConnection(channel: channel)
                let request = try await messages.receive(timeout: 5)
                #expect(request["EntryClass"]?.stringValue == "IOPMPowerSource")
                try await messages.send(["Status": "Success", "Diagnostics": ["IORegistry": ["CurrentCapacity": 81, "IsCharging": true, "CycleCount": 312, "Temperature": 3050, "DesignCapacity": 3000, "AppleRawMaxCapacity": 2700]]])
                _ = try await messages.receive(timeout: 5)
                try await messages.send(["Status": "Failure"])
            }
            try await DeviceSession.with(server.target, usbmux: server.client) { session in
                let relay = try await DiagnosticsRelay.open(session)
                let summary = BatterySummary(registry: try await relay.battery())
                #expect(summary.percentage == 81)
                #expect(summary.isCharging == true)
                #expect(summary.cycleCount == 312)
                #expect(summary.temperatureCelsius == 30.5)
                #expect(summary.healthPercentage == 90)
                await #expect(throws: ToolkitError.self) { _ = try await relay.all() }
            }
        }
    }

    @Test func installationProxyBrowsesInBatches() async throws {
        try await runWithServer({ _ in }) { server in
            server.register(service: InstallationProxy.serviceName) { channel in
                let messages = PlistMessageConnection(channel: channel)
                let request = try await messages.receive(timeout: 5)
                #expect(request["Command"]?.stringValue == "Browse")
                try await messages.send(["Status": "BrowsingApplications", "CurrentList": [
                    ["CFBundleIdentifier": "com.example.zeta", "CFBundleDisplayName": "Zeta", "ApplicationType": "User", "StaticDiskUsage": 1000, "DynamicDiskUsage": 500],
                ]])
                try await messages.send(["Status": "BrowsingApplications", "CurrentList": [
                    ["CFBundleIdentifier": "com.apple.mobilesafari", "CFBundleName": "Safari", "ApplicationType": "System", "CFBundleShortVersionString": "18.2"],
                    ["CFBundleName": "Missing identifier"],
                ]])
                try await messages.send(["Status": "Complete"])
            }
            try await DeviceSession.with(server.target, usbmux: server.client) { session in
                let proxy = try await InstallationProxy.open(session)
                let apps = try await proxy.browse(includeSizes: true)
                #expect(apps.map(\.bundleIdentifier) == ["com.apple.mobilesafari", "com.example.zeta"])
                #expect(apps[1].totalBytes == 1500)
                #expect(apps[0].totalBytes == nil)
                #expect(apps[0].typeLabel == "Built-in")
            }
        }
    }

    @Test func installationProxyErrorsAreActionable() {
        let error = InstallationProxy.interpret(error: "ApplicationVerificationFailed", description: "bad sig", operation: "Installing")
        #expect(error.message.contains("signature"))
        #expect(error.recovery?.contains("provisioning profile") == true)
    }

    @Test func imageMounterQueries() async throws {
        try await runWithServer({ _ in }) { server in
            server.register(service: ImageMounter.serviceName) { channel in
                let messages = PlistMessageConnection(channel: channel)
                while let request = try? await messages.receive(timeout: 5) {
                    switch request["Command"]?.stringValue {
                    case "CopyDevices":
                        try await messages.send(["EntryList": [["MountPath": "/System/Developer", "DiskImageType": "Personalized", "IsMounted": true]]])
                    case "QueryDeveloperModeStatus":
                        try await messages.send(["DeveloperModeStatus": true])
                    case "UnmountImage":
                        try await messages.send(["Error": "UnmountImageFailed", "DetailedError": "image not mounted"])
                    default:
                        try await messages.send(["Status": "Complete"])
                    }
                }
            }
            try await DeviceSession.with(server.target, usbmux: server.client) { session in
                let mounter = try await ImageMounter.open(session)
                let images = try await mounter.mountedImages()
                #expect(images.first?.mountPath == "/System/Developer")
                #expect(images.first?.isMounted == true)
                #expect(try await mounter.developerModeStatus() == true)
                try await mounter.unmountDeveloperImage()
            }
        }
    }
}
