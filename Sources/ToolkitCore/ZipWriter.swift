import Compression
import Foundation

/// Writes a standard ZIP archive (stored or deflated entries) for support bundles and exports.
public struct ZipWriter {
    public enum Method: UInt16 {
        case stored = 0
        case deflated = 8
    }

    private struct Record {
        var name: Data
        var method: Method
        var crc: UInt32
        var compressedSize: UInt32
        var uncompressedSize: UInt32
        var offset: UInt32
        var dosTime: UInt16
        var dosDate: UInt16
        var mode: UInt32
    }

    private var body = Data()
    private var records: [Record] = []
    private let date: Date

    public init(date: Date = Date()) {
        self.date = date
    }

    public mutating func add(name: String, data: Data, method: Method = .deflated, mode: UInt32 = 0o600) throws {
        guard !name.isEmpty, !name.hasPrefix("/"), !name.split(separator: "/").contains(".."), !name.contains("\\") else {
            throw ToolkitError.invalidInput("Invalid archive entry name: \(name)")
        }
        guard data.count < Int(UInt32.max) else {
            throw ToolkitError.invalidInput("\(name) is too large for this archive format.")
        }
        var payload = data
        var chosen = method
        if method == .deflated {
            if let compressed = Self.deflate(data), compressed.count < data.count {
                payload = compressed
            } else {
                chosen = .stored
            }
        }
        let (time, day) = Self.dosDateTime(date)
        let record = Record(
            name: Data(name.utf8),
            method: chosen,
            crc: CRC32.checksum(data),
            compressedSize: UInt32(payload.count),
            uncompressedSize: UInt32(data.count),
            offset: UInt32(body.count),
            dosTime: time,
            dosDate: day,
            mode: mode
        )
        var header = Data()
        header.appendLE32(0x0403_4B50)
        header.appendLE16(20)
        header.appendLE16(0x0800) // UTF-8 names
        header.appendLE16(record.method.rawValue)
        header.appendLE16(record.dosTime)
        header.appendLE16(record.dosDate)
        header.appendLE32(record.crc)
        header.appendLE32(record.compressedSize)
        header.appendLE32(record.uncompressedSize)
        header.appendLE16(UInt16(record.name.count))
        header.appendLE16(0)
        body.append(header)
        body.append(record.name)
        body.append(payload)
        records.append(record)
    }

    public func finalized() -> Data {
        var archive = body
        let directoryOffset = UInt32(archive.count)
        var directory = Data()
        for record in records {
            directory.appendLE32(0x0201_4B50)
            directory.appendLE16(0x0314) // made by Unix, spec 2.0
            directory.appendLE16(20)
            directory.appendLE16(0x0800)
            directory.appendLE16(record.method.rawValue)
            directory.appendLE16(record.dosTime)
            directory.appendLE16(record.dosDate)
            directory.appendLE32(record.crc)
            directory.appendLE32(record.compressedSize)
            directory.appendLE32(record.uncompressedSize)
            directory.appendLE16(UInt16(record.name.count))
            directory.appendLE16(0)
            directory.appendLE16(0)
            directory.appendLE16(0)
            directory.appendLE16(0)
            directory.appendLE32((0o100000 | record.mode) << 16)
            directory.appendLE32(record.offset)
            directory.append(record.name)
        }
        archive.append(directory)
        var end = Data()
        end.appendLE32(0x0605_4B50)
        end.appendLE16(0)
        end.appendLE16(0)
        end.appendLE16(UInt16(records.count))
        end.appendLE16(UInt16(records.count))
        end.appendLE32(UInt32(directory.count))
        end.appendLE32(directoryOffset)
        end.appendLE16(0)
        archive.append(end)
        return archive
    }

    static func deflate(_ data: Data) -> Data? {
        guard !data.isEmpty else { return nil }
        let capacity = data.count + 1024
        let destination = UnsafeMutablePointer<UInt8>.allocate(capacity: capacity)
        defer { destination.deallocate() }
        let written = data.withUnsafeBytes { raw -> Int in
            guard let source = raw.bindMemory(to: UInt8.self).baseAddress else { return 0 }
            return compression_encode_buffer(destination, capacity, source, data.count, nil, COMPRESSION_ZLIB)
        }
        return written > 0 ? Data(bytes: destination, count: written) : nil
    }

    static func dosDateTime(_ date: Date) -> (UInt16, UInt16) {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "UTC") ?? .current
        let parts = calendar.dateComponents([.year, .month, .day, .hour, .minute, .second], from: date)
        let year = max(1980, parts.year ?? 1980) - 1980
        let time = UInt16(((parts.hour ?? 0) << 11) | ((parts.minute ?? 0) << 5) | ((parts.second ?? 0) / 2))
        let day = UInt16((year << 9) | ((parts.month ?? 1) << 5) | (parts.day ?? 1))
        return (time, day)
    }
}

public enum CRC32 {
    private static let table: [UInt32] = (0..<256).map { index -> UInt32 in
        var value = UInt32(index)
        for _ in 0..<8 {
            value = value & 1 == 1 ? 0xEDB8_8320 ^ (value >> 1) : value >> 1
        }
        return value
    }

    public static func checksum(_ data: Data) -> UInt32 {
        var crc: UInt32 = 0xFFFF_FFFF
        for byte in data {
            crc = table[Int((crc ^ UInt32(byte)) & 0xFF)] ^ (crc >> 8)
        }
        return crc ^ 0xFFFF_FFFF
    }
}

extension Data {
    mutating func appendLE16(_ value: UInt16) {
        append(UInt8(value & 0xFF))
        append(UInt8(value >> 8))
    }

    mutating func appendLE32(_ value: UInt32) {
        for shift in stride(from: 0, to: 32, by: 8) { append(UInt8((value >> UInt32(shift)) & 0xFF)) }
    }
}
