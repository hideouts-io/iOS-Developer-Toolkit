import Foundation
import NIOCore
import NIOPosix
import NIOSSL
@testable import DeviceKit
import ToolkitCore

/// Test-only pairing material (generated for this test suite; not real device keys).
enum TestPairing {
    static func fixture(_ name: String) -> Data {
        let url = Bundle.module.url(forResource: "Fixtures/pairing/\(name)", withExtension: nil)!
        return try! Data(contentsOf: url)
    }

    static let hostID = "5F6C1AF2-0000-4000-8000-00000000C0DE"
    static let systemBUID = "B0D5E3A1-0000-4000-8000-0000000B01D0"

    static var pairRecord: PlistValue {
        [
            "HostID": .string(hostID),
            "SystemBUID": .string(systemBUID),
            "HostCertificate": .data(fixture("host.pem")),
            "HostPrivateKey": .data(fixture("host.key")),
            "DeviceCertificate": .data(fixture("device.pem")),
            "RootCertificate": .data(fixture("root.pem")),
            "EscrowBag": .data(Data([0xE5, 0xC0])),
        ]
    }
}

/// A scripted usbmuxd + lockdownd + service implementation over a temporary Unix socket.
///
/// It speaks the same wire formats as the real services so the production client code
/// (framing, pairing, TLS with client certificates and pinning, identity checks, services)
/// is exercised end to end without hardware.
final class FakeDeviceServer: @unchecked Sendable {
    typealias ServiceHandler = @Sendable (DeviceChannel) async throws -> Void

    let socketPath: String
    let directory: URL
    let serial = "00008110001234560ABC801E"
    var udid: String { USBMuxDevice.normalizedUDID(serial) }
    let deviceID = 7

    var reportedUDID: String?
    var pairRecordAvailable = true
    var serverCertificate = "device"
    var serviceTLS = false
    var listenSendsDetach = true
    var lockdownValues: [String: PlistValue] = [:]
    var domainValues: [String: [String: PlistValue]] = [:]
    var lockdownErrors: [String: String] = [:]
    private var services: [String: ServiceHandler] = [:]
    private var servicePorts: [UInt16: String] = [:]
    private var nextPort: UInt16 = 49_152
    private let lock = NSLock()
    private var serverChannel: Channel?
    private(set) var receivedServiceRequests: [PlistValue] = []

    init() throws {
        directory = try SecureFileIO.makeTemporaryDirectory(prefix: "fake-usbmuxd")
        socketPath = directory.appendingPathComponent("usbmuxd").path
        lockdownValues = [
            "DeviceName": "Test iPhone",
            "ProductType": "iPhone16,1",
            "ProductVersion": "18.2",
            "BuildVersion": "22C152",
            "DeviceClass": "iPhone",
            "HardwareModel": "D83AP",
            "CPUArchitecture": "arm64e",
        ]
    }

    var client: USBMuxClient { USBMuxClient(socketPath: socketPath) }

    var target: DeviceTarget {
        DeviceTarget(kind: .physical, udid: udid, name: "Test iPhone", osVersion: "18.2", usbmuxDeviceID: deviceID, coreDeviceIdentifier: nil, transport: .usb)
    }

    func register(service name: String, handler: @escaping ServiceHandler) {
        lock.withLock { services[name] = handler }
    }

    func start() async throws {
        let bootstrap = ServerBootstrap(group: MultiThreadedEventLoopGroup.singleton)
            .childChannelInitializer { [self] child in
                child.eventLoop.makeCompletedFuture {
                    let inbound = InboundBuffer()
                    try child.pipeline.syncOperations.addHandler(inbound)
                    let connection = DeviceChannel(channel: child, inbound: inbound, description: "fake peer")
                    Task { await self.handleUSBMux(connection) }
                }
            }
        serverChannel = try await bootstrap.bind(unixDomainSocketPath: socketPath).get()
    }

    func stop() async {
        try? await serverChannel?.close().get()
        try? FileManager.default.removeItem(at: directory)
    }

    // MARK: usbmuxd

    private var attachedMessage: PlistValue {
        [
            "MessageType": "Attached",
            "DeviceID": .integer(Int64(deviceID)),
            "Properties": [
                "SerialNumber": .string(serial),
                "ConnectionType": "USB",
                "DeviceID": .integer(Int64(deviceID)),
                "ProductID": 4776,
                "LocationID": 1,
            ],
        ]
    }

    private func send(_ value: PlistValue, on connection: DeviceChannel) async throws {
        try await connection.write(try USBMuxProtocol.encode(value, tag: 1))
    }

    private func handleUSBMux(_ connection: DeviceChannel) async {
        do {
            let message = try await USBMuxClient.readMessage(from: connection, timeout: 10)
            switch message["MessageType"]?.stringValue {
            case "ListDevices":
                try await send(["DeviceList": [attachedMessage]], on: connection)
            case "ReadPairRecord":
                if pairRecordAvailable, message["PairRecordID"]?.stringValue == serial {
                    try await send(["PairRecordData": .data(try TestPairing.pairRecord.encoded(format: .binary))], on: connection)
                } else {
                    try await send(["MessageType": "Result", "Number": 2], on: connection)
                }
            case "ReadBUID":
                try await send(["BUID": .string(TestPairing.systemBUID)], on: connection)
            case "Listen":
                try await send(["MessageType": "Result", "Number": 0], on: connection)
                try await send(attachedMessage, on: connection)
                if listenSendsDetach {
                    try await send(["MessageType": "Detached", "DeviceID": .integer(Int64(deviceID))], on: connection)
                }
                _ = try? await connection.readSome()
            case "Connect":
                let networkPort = UInt16(truncatingIfNeeded: message["PortNumber"]?.intValue ?? 0)
                let port = UInt16(bigEndian: networkPort)
                guard message["DeviceID"]?.intValue == deviceID else {
                    try await send(["MessageType": "Result", "Number": 2], on: connection)
                    return
                }
                try await send(["MessageType": "Result", "Number": 0], on: connection)
                if port == LockdownClient.port {
                    try await handleLockdown(connection)
                } else if let name = lock.withLock({ servicePorts[port] }), let handler = lock.withLock({ services[name] }) {
                    if serviceTLS { try await acceptTLS(on: connection) }
                    try await handler(connection)
                }
            default:
                try await send(["MessageType": "Result", "Number": 1], on: connection)
            }
        } catch {
            // Client closed or test finished.
        }
        await connection.close()
    }

    // MARK: lockdownd

    private func serverTLSContext() throws -> NIOSSLContext {
        let certificate = try NIOSSLCertificate.fromPEMBytes(Array(TestPairing.fixture("\(serverCertificate).pem")))
        let key = try NIOSSLPrivateKey(bytes: Array(TestPairing.fixture("\(serverCertificate).key")), format: .pem)
        var configuration = TLSConfiguration.makeServerConfiguration(certificateChain: certificate.map { .certificate($0) }, privateKey: .privateKey(key))
        configuration.certificateVerification = .noHostnameVerification
        configuration.trustRoots = .certificates(try NIOSSLCertificate.fromPEMBytes(Array(TestPairing.fixture("root.pem"))))
        return try NIOSSLContext(configuration: configuration)
    }

    /// Writes the plaintext reply and inserts the TLS handler in the same event-loop tick, so
    /// the client's ClientHello cannot be read before TLS is in place.
    private func replyThenStartTLS(_ reply: PlistValue, on connection: DeviceChannel) async throws {
        let context = try serverTLSContext()
        let frame = try PlistMessageConnection.frame(reply)
        connection.inbound.prepareForTLS()
        let channel = connection.channel
        try await channel.eventLoop.submit {
            var buffer = channel.allocator.buffer(capacity: frame.count)
            buffer.writeBytes(frame)
            channel.writeAndFlush(buffer, promise: nil)
            try channel.pipeline.syncOperations.addHandler(NIOSSLServerHandler(context: context), position: .first)
        }.get()
        try await connection.inbound.waitForTLSHandshake(source: "fake server")
    }

    private func acceptTLS(on connection: DeviceChannel) async throws {
        let context = try serverTLSContext()
        connection.inbound.prepareForTLS()
        let channel = connection.channel
        try await channel.eventLoop.submit {
            try channel.pipeline.syncOperations.addHandler(NIOSSLServerHandler(context: context), position: .first)
        }.get()
        try await connection.inbound.waitForTLSHandshake(source: "fake service")
    }

    private func handleLockdown(_ connection: DeviceChannel) async throws {
        let messages = PlistMessageConnection(channel: connection)
        var sessionStarted = false
        while true {
            let request = try await messages.receive(timeout: nil)
            let name = request["Request"]?.stringValue ?? ""
            if let error = lockdownErrors[name] {
                try await messages.send(["Request": .string(name), "Error": .string(error)])
                continue
            }
            switch name {
            case "QueryType":
                try await messages.send(["Request": "QueryType", "Type": "com.apple.mobile.lockdown"])
            case "GetValue":
                var values = lockdownValues
                values["UniqueDeviceID"] = .string(reportedUDID ?? udid)
                if let domain = request["Domain"]?.stringValue {
                    values = domainValues[domain] ?? [:]
                }
                if let key = request["Key"]?.stringValue {
                    if let value = values[key] {
                        try await messages.send(["Request": "GetValue", "Key": .string(key), "Value": value])
                    } else {
                        try await messages.send(["Request": "GetValue", "Error": "MissingValue"])
                    }
                } else {
                    try await messages.send(["Request": "GetValue", "Value": .dictionary(values)])
                }
            case "StartSession":
                guard request["HostID"]?.stringValue == TestPairing.hostID else {
                    try await messages.send(["Request": "StartSession", "Error": "InvalidHostID"])
                    continue
                }
                sessionStarted = true
                try await replyThenStartTLS(["Request": "StartSession", "SessionID": "SESSION-1", "EnableSessionSSL": true], on: connection)
            case "StartService":
                guard sessionStarted else {
                    try await messages.send(["Request": "StartService", "Error": "SessionInactive"])
                    continue
                }
                lock.withLock { receivedServiceRequests.append(request) }
                let service = request["Service"]?.stringValue ?? ""
                let known = lock.withLock { services[service] != nil }
                guard known else {
                    try await messages.send(["Request": "StartService", "Error": "InvalidService"])
                    continue
                }
                let port: UInt16 = lock.withLock {
                    let port = nextPort
                    nextPort += 1
                    servicePorts[port] = service
                    return port
                }
                try await messages.send(["Request": "StartService", "Service": .string(service), "Port": .integer(Int64(port)), "EnableServiceSSL": .boolean(serviceTLS)])
            case "StopSession":
                try await messages.send(["Request": "StopSession"])
            default:
                try await messages.send(["Request": .string(name), "Error": "UnknownRequest"])
            }
        }
    }
}
