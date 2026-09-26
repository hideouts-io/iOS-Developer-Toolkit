import Foundation
import ToolkitCore

/// A device as reported by usbmuxd (`ListDevices` / `Attached`).
public struct USBMuxDevice: Sendable, Hashable, Identifiable {
    public var id: Int { deviceID }
    public var deviceID: Int
    public var udid: String
    /// The identifier exactly as usbmuxd reported it (used for pair-record lookups).
    public var serialNumber: String
    public var connectionType: String
    public var productID: Int?
    public var locationID: Int?

    public var transport: DeviceTransport {
        connectionType.lowercased() == "usb" ? .usb : .network
    }

    public init(deviceID: Int, udid: String, connectionType: String, productID: Int? = nil, locationID: Int? = nil) {
        self.deviceID = deviceID
        self.udid = USBMuxDevice.normalizedUDID(udid)
        self.serialNumber = udid
        self.connectionType = connectionType
        self.productID = productID
        self.locationID = locationID
    }

    /// Parses an `Attached` message or a `DeviceList` entry.
    public init?(message: PlistValue) {
        let properties = message["Properties"] ?? message
        guard let deviceID = (message["DeviceID"] ?? properties["DeviceID"])?.intValue,
              let udid = properties["SerialNumber"]?.stringValue, !udid.isEmpty
        else { return nil }
        self.deviceID = deviceID
        self.udid = USBMuxDevice.normalizedUDID(udid)
        serialNumber = udid
        connectionType = properties["ConnectionType"]?.stringValue ?? "USB"
        productID = properties["ProductID"]?.intValue
        locationID = properties["LocationID"]?.intValue
    }

    /// usbmuxd reports some modern UDIDs without the hyphen (24 hex digits). CoreDevice and
    /// Xcode use the hyphenated form, so normalize to it for merging.
    public static func normalizedUDID(_ value: String) -> String {
        if value.count == 24, !value.contains("-"), value.allSatisfy(\.isHexDigit) {
            let index = value.index(value.startIndex, offsetBy: 8)
            return String(value[..<index]) + "-" + String(value[index...])
        }
        return value
    }
}

public enum USBMuxEvent: Sendable, Hashable {
    case attached(USBMuxDevice)
    case detached(deviceID: Int)
    case paired(deviceID: Int)
}

/// usbmuxd wire format: a 16-byte little-endian header (length, version, message type, tag)
/// followed by an XML property list.
public enum USBMuxProtocol {
    public static let headerLength = 16
    public static let plistVersion: UInt32 = 1
    public static let plistMessageType: UInt32 = 8
    public static let maximumMessageLength = 16 * 1024 * 1024

    public static func encode(_ payload: PlistValue, tag: UInt32) throws -> Data {
        let body = try payload.encoded(format: .xml)
        var data = Data(capacity: headerLength + body.count)
        data.appendLittleEndian(UInt32(headerLength + body.count))
        data.appendLittleEndian(plistVersion)
        data.appendLittleEndian(plistMessageType)
        data.appendLittleEndian(tag)
        data.append(body)
        return data
    }

    public struct Header: Sendable, Hashable {
        public let length: UInt32
        public let version: UInt32
        public let messageType: UInt32
        public let tag: UInt32

        public var payloadLength: Int { Int(length) - USBMuxProtocol.headerLength }
    }

    public static func decodeHeader(_ data: Data) throws -> Header {
        guard data.count == headerLength else {
            throw ToolkitError(.protocolViolation, message: "usbmuxd sent a truncated message header.")
        }
        let header = Header(
            length: data.readLittleEndianUInt32(at: 0),
            version: data.readLittleEndianUInt32(at: 4),
            messageType: data.readLittleEndianUInt32(at: 8),
            tag: data.readLittleEndianUInt32(at: 12)
        )
        guard header.length >= UInt32(headerLength), header.payloadLength <= maximumMessageLength else {
            throw ToolkitError(.protocolViolation, message: "usbmuxd sent a message with an invalid length.", technicalDetail: "length=\(header.length)")
        }
        return header
    }

    public static func request(_ messageType: String, extra: [String: PlistValue] = [:]) -> PlistValue {
        var dictionary: [String: PlistValue] = [
            "MessageType": .string(messageType),
            "ClientVersionString": "iOSDeveloperToolkit",
            "ProgName": "iOSDeveloperToolkit",
            "kLibUSBMuxVersion": 3,
        ]
        for (key, value) in extra { dictionary[key] = value }
        return .dictionary(dictionary)
    }

    /// usbmuxd expects the TCP port in network byte order inside a host-order integer.
    public static func networkOrderPort(_ port: UInt16) -> Int {
        Int(port.bigEndian)
    }

    public static func event(from message: PlistValue) -> USBMuxEvent? {
        switch message["MessageType"]?.stringValue {
        case "Attached":
            return USBMuxDevice(message: message).map(USBMuxEvent.attached)
        case "Detached":
            return message["DeviceID"]?.intValue.map { USBMuxEvent.detached(deviceID: $0) }
        case "Paired":
            return message["DeviceID"]?.intValue.map { USBMuxEvent.paired(deviceID: $0) }
        default:
            return nil
        }
    }

    /// Maps a `Result` number to an actionable error (nil for success).
    public static func resultError(_ number: Int, operation: String) -> ToolkitError? {
        switch number {
        case 0:
            return nil
        case 2:
            return ToolkitError(.deviceDisconnected, message: "The device disconnected.", recovery: "Reconnect the device and try again.", technicalDetail: "usbmuxd result 2 (BadDevice) during \(operation)")
        case 3:
            return ToolkitError(
                .serviceUnavailable,
                message: "The device refused the connection.",
                recovery: "Unlock the device. If it was just restarted, unlock it once, then try again.",
                technicalDetail: "usbmuxd result 3 (ConnectionRefused) during \(operation)"
            )
        default:
            return ToolkitError(.protocolViolation, message: "The macOS device service rejected the request.", technicalDetail: "usbmuxd result \(number) during \(operation)")
        }
    }
}

extension Data {
    mutating func appendLittleEndian(_ value: UInt32) {
        var little = value.littleEndian
        Swift.withUnsafeBytes(of: &little) { append(contentsOf: $0) }
    }

    mutating func appendBigEndian(_ value: UInt32) {
        var big = value.bigEndian
        Swift.withUnsafeBytes(of: &big) { append(contentsOf: $0) }
    }

    mutating func appendLittleEndian(_ value: UInt64) {
        var little = value.littleEndian
        Swift.withUnsafeBytes(of: &little) { append(contentsOf: $0) }
    }

    func readLittleEndianUInt32(at offset: Int) -> UInt32 {
        var value: UInt32 = 0
        for index in 0..<4 {
            value |= UInt32(self[startIndex + offset + index]) << (8 * UInt32(index))
        }
        return value
    }

    func readBigEndianUInt32(at offset: Int) -> UInt32 {
        var value: UInt32 = 0
        for index in 0..<4 {
            value = (value << 8) | UInt32(self[startIndex + offset + index])
        }
        return value
    }

    func readLittleEndianUInt64(at offset: Int) -> UInt64 {
        var value: UInt64 = 0
        for index in 0..<8 {
            value |= UInt64(self[startIndex + offset + index]) << (8 * UInt64(index))
        }
        return value
    }

    func readLittleEndianUInt16(at offset: Int) -> UInt16 {
        UInt16(self[startIndex + offset]) | (UInt16(self[startIndex + offset + 1]) << 8)
    }

    func readBigEndianUInt16(at offset: Int) -> UInt16 {
        (UInt16(self[startIndex + offset]) << 8) | UInt16(self[startIndex + offset + 1])
    }
}
