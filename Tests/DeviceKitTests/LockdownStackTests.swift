import Foundation
import Testing
@testable import DeviceKit
import DeviceTestSupport
import ToolkitCore

@Suite("usbmuxd + lockdown stack (fake device)", .serialized)
struct LockdownStackTests {
    func withServer(_ configure: (FakeDeviceServer) -> Void = { _ in }, _ body: (FakeDeviceServer) async throws -> Void) async throws {
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

    @Test func listsDevicesWithNormalizedUDID() async throws {
        try await withServer { server in
            let devices = try await server.client.listDevices()
            #expect(devices.count == 1)
            #expect(devices[0].udid == "00008110-001234560ABC801E")
            #expect(devices[0].serialNumber == server.serial)
            #expect(devices[0].transport == .usb)
            #expect(devices[0].deviceID == 7)
        }
    }

    @Test func readsPairRecord() async throws {
        try await withServer { server in
            let device = try await server.client.listDevices()[0]
            let record = try await server.client.readPairRecord(for: device)
            #expect(record.hostID == TestPairing.hostID)
            #expect(record.systemBUID == TestPairing.systemBUID)
            #expect(record.escrowBag == Data([0xE5, 0xC0]))
        }
    }

    @Test func missingPairRecordIsNotPaired() async throws {
        try await withServer({ $0.pairRecordAvailable = false }) { server in
            let device = try await server.client.listDevices()[0]
            do {
                _ = try await server.client.readPairRecord(for: device)
                Issue.record("expected notPaired")
            } catch let error as ToolkitError {
                #expect(error.kind == .notPaired)
                #expect(error.recovery?.contains("Trust") == true)
            }
        }
    }

    @Test func opensTLSSessionAndVerifiesIdentity() async throws {
        try await withServer { server in
            let session = try await DeviceSession.open(target: server.target, usbmux: server.client)
            let name = try await session.getValue(key: "DeviceName")
            #expect(name?.stringValue == "Test iPhone")
            let all = try await session.getValue()
            #expect(all?["ProductType"]?.stringValue == "iPhone16,1")
            await session.close()
        }
    }

    @Test func refusesWhenAnotherDeviceAnswers() async throws {
        try await withServer({ $0.reportedUDID = "00008110-00FFFFFFFFFFFFFF" }) { server in
            do {
                _ = try await DeviceSession.open(target: server.target, usbmux: server.client)
                Issue.record("expected identity mismatch")
            } catch let error as ToolkitError {
                #expect(error.kind == .internalInconsistency)
                #expect(error.message.contains("not the one you selected"))
            }
        }
    }

    @Test func rejectsUnpinnedDeviceCertificate() async throws {
        try await withServer({ $0.serverCertificate = "other" }) { server in
            do {
                _ = try await DeviceSession.open(target: server.target, usbmux: server.client)
                Issue.record("expected TLS pinning failure")
            } catch let error as ToolkitError {
                #expect(error.kind == .notPaired || error.kind == .serviceUnavailable || error.kind == .deviceDisconnected)
            }
        }
    }

    @Test func missingDeviceIsActionable() async throws {
        try await withServer { server in
            let other = DeviceTarget(kind: .physical, udid: "00008030-000000000000002E", name: "Other iPad", osVersion: nil, usbmuxDeviceID: 99, coreDeviceIdentifier: nil, transport: .usb)
            do {
                _ = try await DeviceSession.open(target: other, usbmux: server.client)
                Issue.record("expected deviceNotFound")
            } catch let error as ToolkitError {
                #expect(error.kind == .deviceNotFound)
                #expect(error.message.contains("Other iPad"))
            }
        }
    }

    @Test func lockdownErrorsAreTranslated() async throws {
        try await withServer({ $0.lockdownErrors["StartSession"] = "PasswordProtected" }) { server in
            do {
                _ = try await DeviceSession.open(target: server.target, usbmux: server.client)
                Issue.record("expected locked")
            } catch let error as ToolkitError {
                #expect(error.kind == .deviceLocked)
                #expect(error.message == "The device is locked.")
            }
        }
    }

    @Test func opensServicesWithAndWithoutTLS() async throws {
        for useTLS in [false, true] {
            try await withServer({ $0.serviceTLS = useTLS }) { server in
                server.register(service: "com.example.echo") { channel in
                    let bytes = try await channel.read(exactly: 4, timeout: 5)
                    try await channel.write(bytes + Data("!".utf8))
                }
                try await DeviceSession.with(server.target, usbmux: server.client) { session in
                    let service = try await session.openService("com.example.echo")
                    try await service.channel.write(Data("ping".utf8))
                    let reply = try await service.channel.read(exactly: 5, timeout: 5)
                    #expect(String(decoding: reply, as: UTF8.self) == "ping!")
                }
            }
        }
    }

    @Test func unknownServiceIsUnavailable() async throws {
        try await withServer { server in
            do {
                try await DeviceSession.with(server.target, usbmux: server.client) { session in
                    _ = try await session.openService("com.apple.not.a.service")
                }
                Issue.record("expected failure")
            } catch let error as ToolkitError {
                #expect(error.kind == .serviceUnavailable)
            }
        }
    }

    @Test func escrowBagIsSentOnlyWhenRequested() async throws {
        try await withServer { server in
            server.register(service: "com.apple.mobilebackup2") { _ in }
            server.register(service: "com.apple.syslog_relay") { _ in }
            try await DeviceSession.with(server.target, usbmux: server.client) { session in
                _ = try await session.openService("com.apple.syslog_relay")
                _ = try await session.openService("com.apple.mobilebackup2", useEscrowBag: true)
            }
            let requests = server.receivedServiceRequests
            #expect(requests.count == 2)
            #expect(requests[0]["EscrowBag"] == nil)
            #expect(requests[1]["EscrowBag"]?.dataValue == Data([0xE5, 0xC0]))
        }
    }

    @Test func listenDeliversAttachAndDetachEvents() async throws {
        try await withServer { server in
            var events: [USBMuxEvent] = []
            for try await event in server.client.listen() {
                events.append(event)
                if events.count == 2 { break }
            }
            guard case .attached(let device) = events[0] else {
                Issue.record("expected attached")
                return
            }
            #expect(device.udid == server.udid)
            #expect(events[1] == .detached(deviceID: 7))
        }
    }

    @Test func absentSocketIsReportedClearly() async throws {
        let client = USBMuxClient(socketPath: "/tmp/definitely-not-usbmuxd-\(UUID().uuidString)")
        #expect(!client.isSocketPresent)
        do {
            _ = try await client.listDevices()
            Issue.record("expected failure")
        } catch let error as ToolkitError {
            #expect(error.kind == .serviceUnavailable)
            #expect(error.message.contains("usbmuxd"))
        }
    }
}

@Suite("usbmuxd wire format")
struct USBMuxProtocolTests {
    @Test func headerRoundTrip() throws {
        let data = try USBMuxProtocol.encode(USBMuxProtocol.request("ListDevices"), tag: 42)
        let header = try USBMuxProtocol.decodeHeader(data.prefix(16))
        #expect(Int(header.length) == data.count)
        #expect(header.version == 1)
        #expect(header.messageType == 8)
        #expect(header.tag == 42)
        let payload = try PlistValue.decode(data.dropFirst(16))
        #expect(payload["MessageType"]?.stringValue == "ListDevices")
        #expect(payload["kLibUSBMuxVersion"]?.intValue == 3)
    }

    @Test func rejectsOversizedOrTruncatedHeaders() {
        var oversized = Data()
        oversized.appendLittleEndian(UInt32(100 * 1024 * 1024))
        oversized.appendLittleEndian(UInt32(1))
        oversized.appendLittleEndian(UInt32(8))
        oversized.appendLittleEndian(UInt32(0))
        #expect(throws: ToolkitError.self) { try USBMuxProtocol.decodeHeader(oversized) }
        #expect(throws: ToolkitError.self) { try USBMuxProtocol.decodeHeader(Data([1, 2, 3])) }
        var tooSmall = Data()
        tooSmall.appendLittleEndian(UInt32(4))
        tooSmall.appendLittleEndian(UInt32(1))
        tooSmall.appendLittleEndian(UInt32(8))
        tooSmall.appendLittleEndian(UInt32(0))
        #expect(throws: ToolkitError.self) { try USBMuxProtocol.decodeHeader(tooSmall) }
    }

    @Test func portIsNetworkOrder() {
        #expect(USBMuxProtocol.networkOrderPort(62078) == 0x7EF2)
    }

    @Test func udidNormalization() {
        #expect(USBMuxDevice.normalizedUDID("00008110001234560ABC801E") == "00008110-001234560ABC801E")
        #expect(USBMuxDevice.normalizedUDID("00008110-001234560ABC801E") == "00008110-001234560ABC801E")
        let legacy = String(repeating: "a", count: 40)
        #expect(USBMuxDevice.normalizedUDID(legacy) == legacy)
    }

    @Test func eventsParse() {
        let attached: PlistValue = ["MessageType": "Attached", "DeviceID": 3, "Properties": ["SerialNumber": "abc", "ConnectionType": "Network", "DeviceID": 3]]
        guard case .attached(let device)? = USBMuxProtocol.event(from: attached) else {
            Issue.record("expected attached")
            return
        }
        #expect(device.transport == .network)
        #expect(USBMuxProtocol.event(from: ["MessageType": "Detached", "DeviceID": 3]) == .detached(deviceID: 3))
        #expect(USBMuxProtocol.event(from: ["MessageType": "Paired", "DeviceID": 3]) == .paired(deviceID: 3))
        #expect(USBMuxProtocol.event(from: ["MessageType": "Bogus"]) == nil)
        #expect(USBMuxProtocol.event(from: ["MessageType": "Attached"]) == nil)
    }

    @Test func resultNumbersMapToErrors() {
        #expect(USBMuxProtocol.resultError(0, operation: "x") == nil)
        #expect(USBMuxProtocol.resultError(2, operation: "x")?.kind == .deviceDisconnected)
        #expect(USBMuxProtocol.resultError(3, operation: "x")?.kind == .serviceUnavailable)
        #expect(USBMuxProtocol.resultError(6, operation: "x")?.kind == .protocolViolation)
    }

    @Test func lockdownErrorInterpretation() {
        #expect(LockdownErrorInterpreter.interpret("PasswordProtected", request: "x").kind == .deviceLocked)
        #expect(LockdownErrorInterpreter.interpret("InvalidHostID", request: "x").kind == .notPaired)
        #expect(LockdownErrorInterpreter.interpret("PairingDialogResponsePending", request: "x").kind == .pairingPending)
        #expect(LockdownErrorInterpreter.interpret("InvalidService", request: "x").kind == .serviceUnavailable)
        #expect(LockdownErrorInterpreter.interpret("SomethingNew", request: "x").kind == .commandFailed)
    }
}

@Suite("Real usbmuxd on this Mac")
struct RealUSBMuxTests {
    /// Verifies wire compatibility with the actual macOS daemon. Passes with zero devices.
    @Test(.enabled(if: USBMuxClient().isSocketPresent))
    func listDevicesAgainstSystemDaemon() async throws {
        let devices = try await USBMuxClient().listDevices()
        for device in devices {
            #expect(!device.udid.isEmpty)
        }
    }

    @Test(.enabled(if: USBMuxClient().isSocketPresent))
    func listenHandshakeAgainstSystemDaemon() async throws {
        let stream = USBMuxClient().listen()
        let task = Task {
            for try await _ in stream { break }
        }
        try await Task.sleep(for: .milliseconds(500))
        task.cancel()
        _ = await task.result
    }
}
