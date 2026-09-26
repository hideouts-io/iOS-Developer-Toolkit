import Foundation
import OSLog
import ToolkitCore

/// The pairing material macOS stores after a device trusts this Mac. It is read through
/// usbmuxd (`ReadPairRecord`), which does not require root, and is only held in memory.
public struct PairRecord: Sendable {
    public let hostID: String
    public let systemBUID: String
    public let hostCertificatePEM: Data
    public let hostPrivateKeyPEM: Data
    public let deviceCertificatePEM: Data?
    public let rootCertificatePEM: Data?
    public let escrowBag: Data?

    public init(hostID: String, systemBUID: String, hostCertificatePEM: Data, hostPrivateKeyPEM: Data, deviceCertificatePEM: Data?, rootCertificatePEM: Data?, escrowBag: Data?) {
        self.hostID = hostID
        self.systemBUID = systemBUID
        self.hostCertificatePEM = hostCertificatePEM
        self.hostPrivateKeyPEM = hostPrivateKeyPEM
        self.deviceCertificatePEM = deviceCertificatePEM
        self.rootCertificatePEM = rootCertificatePEM
        self.escrowBag = escrowBag
    }

    public init(plist: PlistValue) throws {
        guard let hostID = plist["HostID"]?.stringValue,
              let systemBUID = plist["SystemBUID"]?.stringValue,
              let certificate = plist["HostCertificate"]?.dataValue,
              let key = plist["HostPrivateKey"]?.dataValue
        else {
            throw ToolkitError(
                .notPaired,
                message: "This Mac's pairing record for the device is incomplete.",
                recovery: "Disconnect the device, reconnect it, and tap Trust when asked."
            )
        }
        self.init(
            hostID: hostID,
            systemBUID: systemBUID,
            hostCertificatePEM: certificate,
            hostPrivateKeyPEM: key,
            deviceCertificatePEM: plist["DeviceCertificate"]?.dataValue,
            rootCertificatePEM: plist["RootCertificate"]?.dataValue,
            escrowBag: plist["EscrowBag"]?.dataValue
        )
    }

    public var tlsCredentials: TLSCredentials {
        TLSCredentials(certificatePEM: hostCertificatePEM, privateKeyPEM: hostPrivateKeyPEM, pinnedDeviceCertificatePEM: deviceCertificatePEM)
    }
}

/// A client for the macOS usbmuxd socket.
public struct USBMuxClient: Sendable {
    public static let defaultSocketPath = "/var/run/usbmuxd"
    public let socketPath: String
    private let logger = ToolkitLog.deviceDiscovery

    public init(socketPath: String = USBMuxClient.defaultSocketPath) {
        self.socketPath = socketPath
    }

    public var isSocketPresent: Bool {
        var info = stat()
        return stat(socketPath, &info) == 0 && (info.st_mode & S_IFMT) == S_IFSOCK
    }

    // MARK: Requests

    public func listDevices() async throws -> [USBMuxDevice] {
        let reply = try await exchange(USBMuxProtocol.request("ListDevices"), operation: "listing devices")
        guard let list = reply["DeviceList"]?.arrayValue else {
            throw ToolkitError(.protocolViolation, message: "usbmuxd returned an unexpected device list.")
        }
        return list.compactMap(USBMuxDevice.init(message:))
    }

    public func readPairRecord(for device: USBMuxDevice) async throws -> PairRecord {
        var lastError: Error?
        for identifier in Array(Set([device.serialNumber, device.udid])).sorted() {
            do {
                let reply = try await exchange(USBMuxProtocol.request("ReadPairRecord", extra: ["PairRecordID": .string(identifier)]), operation: "reading the pairing record", interpretResult: false)
                if let number = reply["Number"]?.intValue, number != 0 {
                    lastError = ToolkitError(.notPaired, message: "This device has not trusted this Mac.", technicalDetail: "ReadPairRecord result \(number)")
                    continue
                }
                guard let data = reply["PairRecordData"]?.dataValue else {
                    lastError = ToolkitError(.protocolViolation, message: "usbmuxd returned a pairing record without data.")
                    continue
                }
                return try PairRecord(plist: try PlistValue.decode(data))
            } catch {
                lastError = error
            }
        }
        if let toolkitError = lastError as? ToolkitError, toolkitError.kind != .notPaired {
            throw toolkitError
        }
        throw ToolkitError(
            .notPaired,
            message: "This device has not trusted this Mac yet.",
            recovery: "Unlock the device, connect it with a USB cable, and tap Trust when asked. You may need to enter the device passcode.",
            technicalDetail: (lastError as? ToolkitError)?.technicalDetail
        )
    }

    public func readSystemBUID() async throws -> String {
        let reply = try await exchange(USBMuxProtocol.request("ReadBUID"), operation: "reading the host identifier")
        guard let buid = reply["BUID"]?.stringValue else {
            throw ToolkitError(.protocolViolation, message: "usbmuxd did not return the host identifier.")
        }
        return buid
    }

    /// Opens a raw TCP tunnel to `port` on the device. The returned channel speaks the device
    /// service's protocol directly.
    public func connect(to device: USBMuxDevice, port: UInt16) async throws -> DeviceChannel {
        let channel = try await DeviceChannel.connect(unixSocketPath: socketPath, description: "device port \(port)")
        do {
            let request = USBMuxProtocol.request("Connect", extra: [
                "DeviceID": .integer(Int64(device.deviceID)),
                "PortNumber": .integer(Int64(USBMuxProtocol.networkOrderPort(port))),
            ])
            try await channel.write(try USBMuxProtocol.encode(request, tag: 1))
            let reply = try await Self.readMessage(from: channel, timeout: 15)
            guard reply["MessageType"]?.stringValue == "Result", let number = reply["Number"]?.intValue else {
                throw ToolkitError(.protocolViolation, message: "usbmuxd sent an unexpected reply to a connection request.")
            }
            if let error = USBMuxProtocol.resultError(number, operation: "connecting to port \(port)") {
                throw error
            }
            return channel
        } catch {
            await channel.close()
            throw error
        }
    }

    /// Event-driven device notifications. The stream ends if usbmuxd closes the connection;
    /// the discovery coordinator reconnects with back-off.
    public func listen() -> AsyncThrowingStream<USBMuxEvent, Error> {
        let socketPath = self.socketPath
        let logger = self.logger
        return AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    let channel = try await DeviceChannel.connect(unixSocketPath: socketPath, description: "usbmuxd listener")
                    defer { Task { await channel.close() } }
                    try await channel.write(try USBMuxProtocol.encode(USBMuxProtocol.request("Listen"), tag: 1))
                    let reply = try await Self.readMessage(from: channel, timeout: 10)
                    if let number = reply["Number"]?.intValue, let error = USBMuxProtocol.resultError(number, operation: "listening for devices") {
                        throw error
                    }
                    logger.info("usbmuxd listener established")
                    while !Task.isCancelled {
                        let message = try await Self.readMessage(from: channel, timeout: nil)
                        if let event = USBMuxProtocol.event(from: message) {
                            continuation.yield(event)
                        }
                    }
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }

    // MARK: Wire helpers

    private func exchange(_ request: PlistValue, operation: String, interpretResult: Bool = true) async throws -> PlistValue {
        let channel = try await DeviceChannel.connect(unixSocketPath: socketPath, description: "usbmuxd")
        defer { Task { await channel.close() } }
        try await channel.write(try USBMuxProtocol.encode(request, tag: 1))
        let reply = try await Self.readMessage(from: channel, timeout: 15)
        if interpretResult,
           reply["MessageType"]?.stringValue == "Result",
           let number = reply["Number"]?.intValue,
           let error = USBMuxProtocol.resultError(number, operation: operation) {
            throw error
        }
        return reply
    }

    static func readMessage(from channel: DeviceChannel, timeout: TimeInterval?) async throws -> PlistValue {
        let header = try USBMuxProtocol.decodeHeader(try await channel.read(exactly: USBMuxProtocol.headerLength, timeout: timeout))
        let payload = try await channel.read(exactly: header.payloadLength, timeout: timeout ?? 30)
        return try PlistValue.decode(payload)
    }
}
