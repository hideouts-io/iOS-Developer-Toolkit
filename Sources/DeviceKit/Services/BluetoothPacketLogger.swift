import Foundation
import ToolkitCore

/// One Apple PacketLogger record: a 4-byte big-endian length, seconds and microseconds (big-endian),
/// a record type, and the HCI payload.
public struct PacketLoggerRecord: Sendable, Hashable {
    public static let headerSize = 13

    public var seconds: UInt32
    public var microseconds: UInt32
    public var type: UInt8
    public var payload: Data
    /// The record exactly as received; concatenated records form a `.pklg` file.
    public var raw: Data

    public init?(_ raw: Data) {
        let bytes = [UInt8](raw)
        guard bytes.count >= Self.headerSize else { return nil }
        func be32(_ offset: Int) -> UInt32 {
            UInt32(bytes[offset]) << 24 | UInt32(bytes[offset + 1]) << 16 | UInt32(bytes[offset + 2]) << 8 | UInt32(bytes[offset + 3])
        }
        seconds = be32(4)
        microseconds = be32(8)
        type = bytes[12]
        payload = Data(bytes[Self.headerSize...])
        self.raw = raw
    }

    public var typeLabel: String { Self.label(for: type) }

    public static func label(for type: UInt8) -> String {
        switch type {
        case 0x00: return "HCI command"
        case 0x01: return "HCI event"
        case 0x02: return "ACL data sent"
        case 0x03: return "ACL data received"
        case 0x08: return "SCO data sent"
        case 0x09: return "SCO data received"
        default: return "Other (0x" + String(type, radix: 16) + ")"
        }
    }
}

/// `com.apple.bluetooth.BTPacketLogger`: live Bluetooth HCI traffic, over lockdown without Xcode.
///
/// A private service that streams only after Apple's Bluetooth logging profile is installed on the
/// device. Each record arrives with a 2-byte little-endian length prefix (a zero length is a
/// keep-alive) and is an Apple PacketLogger record; written back to back, the records are a
/// `.pklg` file that PacketLogger (Additional Tools for Xcode) and Wireshark open.
public enum BluetoothPacketLogger {
    public static let serviceName = "com.apple.bluetooth.BTPacketLogger"
    static let maximumRecordSize = 64 * 1024

    public static func records(_ session: DeviceSession) async throws -> AsyncThrowingStream<PacketLoggerRecord, Error> {
        let service: ServiceConnection
        do {
            service = try await session.openService(serviceName)
        } catch let error as ToolkitError {
            throw ToolkitError(.serviceUnavailable, message: "The device did not start Bluetooth logging.", recovery: "Install Apple's Bluetooth logging profile on the device (Apple Developer › Profiles and Logs › Bluetooth), restart Bluetooth, and try again.", technicalDetail: error.technicalDetail ?? error.message)
        }
        return AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    while !Task.isCancelled, try await service.channel.hasMoreData() {
                        let prefix = [UInt8](try await service.channel.read(exactly: 2, timeout: nil))
                        let length = Int(prefix[0]) | Int(prefix[1]) << 8
                        if length == 0 { continue }
                        guard length >= PacketLoggerRecord.headerSize, length <= maximumRecordSize else {
                            throw ToolkitError(.protocolViolation, message: "The Bluetooth log stream lost synchronization.", technicalDetail: "record length \(length)")
                        }
                        let raw = try await service.channel.read(exactly: length, timeout: 60)
                        if let record = PacketLoggerRecord(raw) { continuation.yield(record) }
                    }
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
                await service.close()
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }
}

/// Writes PacketLogger records to a new `.pklg` file (owner-only, never overwriting).
public final class PacketLoggerFileWriter: @unchecked Sendable {
    public let url: URL
    private let handle: FileHandle
    private let lock = NSLock()
    private var hasher = StreamingHasher()
    public private(set) var recordCount = 0
    public private(set) var countsByType: [UInt8: Int] = [:]

    public init(creatingNewFileAt url: URL) throws {
        try SecureFileIO.writeNewFile(Data(), to: url)
        guard let handle = try? FileHandle(forWritingTo: url) else {
            throw ToolkitError.fileSystem("Could not open the capture file.", path: url.path)
        }
        self.url = url
        self.handle = handle
    }

    public func write(_ record: PacketLoggerRecord) throws {
        try lock.withLock {
            try handle.write(contentsOf: record.raw)
            hasher.update(record.raw)
            recordCount += 1
            countsByType[record.type, default: 0] += 1
        }
    }

    /// Closes the file and returns its SHA-256.
    public func finish() throws -> String {
        try lock.withLock {
            try handle.synchronize()
            try handle.close()
            return hasher.finalizeHex()
        }
    }
}
