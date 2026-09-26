import Foundation
import ToolkitCore

/// Length-prefixed property-list messaging (4-byte big-endian length + plist), used by lockdown
/// and most lockdown services.
public struct PlistMessageConnection: Sendable {
    public let channel: DeviceChannel
    public let maximumMessageLength: Int

    public init(channel: DeviceChannel, maximumMessageLength: Int = 64 * 1024 * 1024) {
        self.channel = channel
        self.maximumMessageLength = maximumMessageLength
    }

    public static func frame(_ value: PlistValue, format: PropertyListSerialization.PropertyListFormat = .xml) throws -> Data {
        let body = try value.encoded(format: format)
        var data = Data(capacity: body.count + 4)
        data.appendBigEndian(UInt32(body.count))
        data.append(body)
        return data
    }

    public func send(_ value: PlistValue, format: PropertyListSerialization.PropertyListFormat = .xml) async throws {
        try await channel.write(try Self.frame(value, format: format))
    }

    public func receive(timeout: TimeInterval? = 30) async throws -> PlistValue {
        let header = try await channel.read(exactly: 4, timeout: timeout)
        let length = Int(header.readBigEndianUInt32(at: 0))
        guard length > 0, length <= maximumMessageLength else {
            throw ToolkitError(.protocolViolation, message: "The device sent a message with an invalid length.", technicalDetail: "length=\(length)")
        }
        let body = try await channel.read(exactly: length, timeout: timeout ?? 60)
        return try PlistValue.decode(body)
    }

    public func request(_ value: PlistValue, timeout: TimeInterval? = 30) async throws -> PlistValue {
        try await send(value)
        return try await receive(timeout: timeout)
    }

    public func close() async {
        await channel.close()
    }
}
