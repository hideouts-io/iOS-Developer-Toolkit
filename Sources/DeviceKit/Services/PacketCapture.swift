import Foundation
import ToolkitCore

/// One packet from `com.apple.pcapd`.
public struct CapturedPacket: Sendable, Hashable {
    public var timestamp: Date
    public var interfaceName: String
    public var processName: String?
    public var pid: Int?
    public var isOutbound: Bool
    public var protocolFamily: UInt32
    /// Link-layer frame ready to write to a DLT_EN10MB pcap file.
    public var frame: Data
}

/// Decodes pcapd records and writes classic libpcap files.
///
/// pcapd delivers one plist per packet whose root is a data blob: a big-endian header (header
/// length, version, payload length, type, unit, direction, protocol family, frame pre/post
/// lengths, interface name, process identity, timestamp) followed by the packet. When the
/// packet has no link-layer header (pre-length 0), an Ethernet header is synthesized from the
/// protocol family so standard tools can open the file.
public enum PacketCaptureService {
    public static let serviceName = "com.apple.pcapd"

    public static func stream(_ session: DeviceSession) async throws -> AsyncThrowingStream<CapturedPacket, Error> {
        let service = try await session.openService(serviceName)
        return AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    while !Task.isCancelled, try await service.channel.hasMoreData() {
                        let message = try await service.messages.receive(timeout: nil)
                        guard let blob = message.dataValue else { continue }
                        if let packet = PcapdRecordParser.parse(blob) {
                            continuation.yield(packet)
                        }
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

public enum PcapdRecordParser {
    public static let minimumHeaderLength = 95

    public static func parse(_ blob: Data) -> CapturedPacket? {
        let bytes = [UInt8](blob)
        guard bytes.count >= 4 else { return nil }
        let headerLength = Int(be32(bytes, 0))
        guard headerLength >= 23, headerLength <= bytes.count else { return nil }
        let payloadLength = Int(be32(bytes, 5))
        let direction = bytes[12]
        let family = be32(bytes, 13)
        let preLength = Int(be32(bytes, 17))
        let interface = cString(bytes, 25, 16)
        var pid: Int?
        var process: String?
        var timestamp = Date()
        if headerLength >= minimumHeaderLength {
            pid = Int(le32(bytes, 41))
            process = cString(bytes, 45, 17)
            let seconds = TimeInterval(be32(bytes, 87))
            let microseconds = TimeInterval(be32(bytes, 91))
            if seconds > 0 { timestamp = Date(timeIntervalSince1970: seconds + microseconds / 1_000_000) }
        }
        let available = bytes.count - headerLength
        let length = min(max(0, payloadLength), available)
        let payload = Data(bytes[headerLength..<(headerLength + length)])
        let frame: Data
        if preLength == 0 {
            frame = ethernetHeader(family: family) + payload
        } else {
            frame = payload
        }
        return CapturedPacket(
            timestamp: timestamp,
            interfaceName: interface,
            processName: process?.isEmpty == true ? nil : process,
            pid: pid,
            isOutbound: direction == 0x01,
            protocolFamily: family,
            frame: frame
        )
    }

    static func ethernetHeader(family: UInt32) -> Data {
        var header = Data(repeating: 0, count: 12)
        switch family {
        case 30: header.append(contentsOf: [0x86, 0xDD]) // AF_INET6
        default: header.append(contentsOf: [0x08, 0x00]) // AF_INET
        }
        return header
    }

    static func be32(_ bytes: [UInt8], _ offset: Int) -> UInt32 {
        guard offset + 4 <= bytes.count else { return 0 }
        return UInt32(bytes[offset]) << 24 | UInt32(bytes[offset + 1]) << 16 | UInt32(bytes[offset + 2]) << 8 | UInt32(bytes[offset + 3])
    }

    static func le32(_ bytes: [UInt8], _ offset: Int) -> UInt32 {
        guard offset + 4 <= bytes.count else { return 0 }
        return UInt32(bytes[offset]) | UInt32(bytes[offset + 1]) << 8 | UInt32(bytes[offset + 2]) << 16 | UInt32(bytes[offset + 3]) << 24
    }

    static func cString(_ bytes: [UInt8], _ offset: Int, _ length: Int) -> String {
        guard offset < bytes.count else { return "" }
        let end = min(bytes.count, offset + length)
        let slice = bytes[offset..<end].prefix { $0 != 0 }
        return String(decoding: slice, as: UTF8.self)
    }
}

/// Writes a classic libpcap file (microsecond timestamps, Ethernet link type).
public final class PcapFileWriter: @unchecked Sendable {
    public let url: URL
    private let handle: FileHandle
    private let lock = NSLock()
    private var hasher = StreamingHasher()
    public private(set) var packetCount = 0

    public init(creatingNewFileAt url: URL) throws {
        var header = Data()
        header.appendLittleEndian(UInt32(0xA1B2_C3D4))
        header.append(contentsOf: [0x02, 0x00, 0x04, 0x00]) // version 2.4
        header.appendLittleEndian(UInt32(0)) // thiszone
        header.appendLittleEndian(UInt32(0)) // sigfigs
        header.appendLittleEndian(UInt32(262_144)) // snaplen
        header.appendLittleEndian(UInt32(1)) // LINKTYPE_ETHERNET
        try SecureFileIO.writeNewFile(header, to: url)
        guard let handle = try? FileHandle(forWritingTo: url) else {
            throw ToolkitError.fileSystem("Could not open the capture file.", path: url.path)
        }
        handle.seekToEndOfFile()
        self.url = url
        self.handle = handle
        hasher.update(header)
    }

    public func write(_ packet: CapturedPacket) throws {
        let seconds = packet.timestamp.timeIntervalSince1970
        var record = Data()
        record.appendLittleEndian(UInt32(truncatingIfNeeded: Int(seconds)))
        record.appendLittleEndian(UInt32(truncatingIfNeeded: Int((seconds - floor(seconds)) * 1_000_000)))
        let captured = packet.frame.prefix(262_144)
        record.appendLittleEndian(UInt32(captured.count))
        record.appendLittleEndian(UInt32(packet.frame.count))
        record.append(captured)
        try lock.withLock {
            try handle.write(contentsOf: record)
            hasher.update(record)
            packetCount += 1
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
