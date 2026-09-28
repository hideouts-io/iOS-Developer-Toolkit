import Compression
import Foundation
import ToolkitCore

/// A read-only ZIP reader for inspecting IPA packages.
///
/// The archive is treated as untrusted: member names are validated before anything is written,
/// symbolic links and encrypted members are refused, declared sizes are enforced while
/// inflating (so a "zip bomb" cannot exceed them), and total expansion is capped.
public final class ZipArchive: @unchecked Sendable {
    public struct Entry: Sendable, Hashable {
        public let name: String
        public let compressionMethod: UInt16
        public let flags: UInt16
        public let compressedSize: UInt64
        public let uncompressedSize: UInt64
        public let localHeaderOffset: UInt64
        public let externalAttributes: UInt32
        public let versionMadeBy: UInt16

        public var isDirectory: Bool { name.hasSuffix("/") }
        public var isEncrypted: Bool { flags & 0x1 != 0 }
        public var unixMode: UInt16 { UInt16(truncatingIfNeeded: externalAttributes >> 16) }
        public var isSymbolicLink: Bool { (versionMadeBy >> 8) == 3 && (unixMode & 0o170000) == 0o120000 }
    }

    public static let maximumTotalUncompressedBytes: UInt64 = 8 * 1024 * 1024 * 1024
    public static let maximumEntries = 500_000

    public let url: URL
    public let entries: [Entry]
    private let handle: FileHandle
    private let fileSize: UInt64

    public init(url: URL) throws {
        guard let handle = try? FileHandle(forReadingFrom: url) else {
            throw ToolkitError.fileSystem("The package could not be opened.", path: url.path)
        }
        self.url = url
        self.handle = handle
        fileSize = (try? handle.seekToEnd()) ?? 0
        entries = try ZipArchive.readCentralDirectory(handle: handle, fileSize: fileSize)
        try ZipArchive.validate(entries)
    }

    deinit {
        try? handle.close()
    }

    // MARK: Validation

    public static func validate(_ entries: [Entry]) throws {
        var seen = Set<String>()
        var total: UInt64 = 0
        for entry in entries {
            guard seen.insert(entry.name).inserted else {
                throw ToolkitError.invalidInput("The package contains a duplicate entry: \(entry.name)")
            }
            try validateName(entry.name)
            if entry.isSymbolicLink {
                throw ToolkitError.invalidInput("The package contains a symbolic link, which cannot be inspected safely: \(entry.name)")
            }
            if entry.isEncrypted {
                throw ToolkitError.invalidInput("The package contains an encrypted entry: \(entry.name)")
            }
            guard entry.compressionMethod == 0 || entry.compressionMethod == 8 else {
                throw ToolkitError.invalidInput("The package uses an unsupported compression method (\(entry.compressionMethod)).")
            }
            total += entry.uncompressedSize
            if total > maximumTotalUncompressedBytes {
                throw ToolkitError.invalidInput("The package expands beyond the 8 GB inspection limit.")
            }
        }
    }

    static func validateName(_ name: String) throws {
        let unsafe = name.isEmpty || name.hasPrefix("/") || name.contains("\\") || name.contains("\0")
            || name.split(separator: "/", omittingEmptySubsequences: false).contains("..")
        if unsafe {
            throw ToolkitError.invalidInput("The package contains an unsafe path: \(name)")
        }
    }

    public func entry(named name: String) -> Entry? {
        entries.first { $0.name == name }
    }

    // MARK: Reading

    /// Reads a (small) entry fully into memory, enforcing `limit`.
    public func data(for entry: Entry, limit: UInt64) throws -> Data {
        guard entry.uncompressedSize <= limit else {
            throw ToolkitError.invalidInput("\(entry.name) is larger than the \(limit / 1_048_576) MB inspection limit.")
        }
        var output = Data()
        try stream(entry) { output.append($0) }
        return output
    }

    /// Extracts entries under `prefix` into `destination` (which must be a private folder).
    /// File permissions are limited to 0755/0644 so no set-id bits survive extraction.
    public func extract(prefix: String, to destination: URL) throws {
        for entry in entries where entry.name == prefix || entry.name.hasPrefix(prefix) {
            let target = try SecureFileIO.safeChild(of: destination, relativePath: entry.name)
            if entry.isDirectory {
                try SecureFileIO.createPrivateDirectory(at: target)
                continue
            }
            try SecureFileIO.createPrivateDirectory(at: target.deletingLastPathComponent())
            let executable = entry.unixMode & 0o111 != 0
            try SecureFileIO.writeNewFile(Data(), to: target, mode: executable ? 0o755 : 0o644)
            let output = try FileHandle(forWritingTo: target)
            defer { try? output.close() }
            try stream(entry) { try output.write(contentsOf: $0) }
        }
    }

    private func stream(_ entry: Entry, _ sink: (Data) throws -> Void) throws {
        let dataOffset = try localDataOffset(for: entry)
        try handle.seek(toOffset: dataOffset)
        var remaining = entry.compressedSize
        var produced: UInt64 = 0

        func emit(_ chunk: Data) throws {
            produced += UInt64(chunk.count)
            guard produced <= entry.uncompressedSize else {
                throw ToolkitError.invalidInput("\(entry.name) expands beyond its declared size; the package may be malicious.")
            }
            try sink(chunk)
        }

        if entry.compressionMethod == 0 {
            while remaining > 0 {
                let count = Int(min(remaining, 1 << 20))
                guard let chunk = try handle.read(upToCount: count), !chunk.isEmpty else { break }
                remaining -= UInt64(chunk.count)
                try emit(chunk)
            }
        } else {
            try inflate(compressedSize: entry.compressedSize, emit: emit)
        }
        guard produced == entry.uncompressedSize else {
            throw ToolkitError.invalidInput("\(entry.name) is truncated or corrupt.")
        }
    }

    private func inflate(compressedSize: UInt64, emit: (Data) throws -> Void) throws {
        let bufferSize = 1 << 16
        let destination = UnsafeMutablePointer<UInt8>.allocate(capacity: bufferSize)
        defer { destination.deallocate() }
        let streamPointer = UnsafeMutablePointer<compression_stream>.allocate(capacity: 1)
        defer { streamPointer.deallocate() }
        guard compression_stream_init(streamPointer, COMPRESSION_STREAM_DECODE, COMPRESSION_ZLIB) == COMPRESSION_STATUS_OK else {
            throw ToolkitError(.internalInconsistency, message: "Decompression could not start.")
        }
        defer { compression_stream_destroy(streamPointer) }

        var remaining = compressedSize
        var input = Data()
        var finished = false
        while !finished {
            if input.isEmpty && remaining > 0 {
                let count = Int(min(remaining, 1 << 20))
                input = try handle.read(upToCount: count) ?? Data()
                remaining -= UInt64(input.count)
                if input.isEmpty { remaining = 0 }
            }
            let isLast = remaining == 0
            let status: compression_status = input.withUnsafeBytes { raw in
                let base = raw.bindMemory(to: UInt8.self).baseAddress
                streamPointer.pointee.src_ptr = base ?? UnsafePointer(destination)
                streamPointer.pointee.src_size = input.count
                streamPointer.pointee.dst_ptr = destination
                streamPointer.pointee.dst_size = bufferSize
                return compression_stream_process(streamPointer, isLast ? Int32(COMPRESSION_STREAM_FINALIZE.rawValue) : 0)
            }
            let consumed = input.count - streamPointer.pointee.src_size
            input.removeFirst(consumed)
            let producedCount = bufferSize - streamPointer.pointee.dst_size
            if producedCount > 0 { try emit(Data(bytes: destination, count: producedCount)) }
            switch status {
            case COMPRESSION_STATUS_END:
                finished = true
            case COMPRESSION_STATUS_OK:
                if isLast && input.isEmpty && producedCount == 0 {
                    throw ToolkitError.invalidInput("A compressed entry is truncated.")
                }
            default:
                throw ToolkitError.invalidInput("A compressed entry is corrupt.")
            }
        }
    }

    private func localDataOffset(for entry: Entry) throws -> UInt64 {
        try handle.seek(toOffset: entry.localHeaderOffset)
        guard let header = try handle.read(upToCount: 30), header.count == 30, header.readUInt32LE(0) == 0x0403_4B50 else {
            throw ToolkitError.invalidInput("The package has a damaged entry header: \(entry.name)")
        }
        let nameLength = UInt64(header.readUInt16LE(26))
        let extraLength = UInt64(header.readUInt16LE(28))
        let offset = entry.localHeaderOffset + 30 + nameLength + extraLength
        guard offset + entry.compressedSize <= fileSize else {
            throw ToolkitError.invalidInput("The package is truncated.")
        }
        return offset
    }

    // MARK: Central directory

    static func readCentralDirectory(handle: FileHandle, fileSize: UInt64) throws -> [Entry] {
        guard fileSize >= 22 else { throw ToolkitError.invalidInput("The file is not a valid package (too small).") }
        let tailLength = min(fileSize, 65_557)
        try handle.seek(toOffset: fileSize - tailLength)
        let tail = try handle.read(upToCount: Int(tailLength)) ?? Data()
        guard let eocd = tail.lastRange(of: Data([0x50, 0x4B, 0x05, 0x06]))?.lowerBound else {
            throw ToolkitError.invalidInput("The file is not a valid ZIP-based package.")
        }
        let eocdRelative = eocd - tail.startIndex
        var entryCount = UInt64(tail.readUInt16LE(eocdRelative + 10))
        var directorySize = UInt64(tail.readUInt32LE(eocdRelative + 12))
        var directoryOffset = UInt64(tail.readUInt32LE(eocdRelative + 16))

        if entryCount == 0xFFFF || directorySize == 0xFFFF_FFFF || directoryOffset == 0xFFFF_FFFF {
            // ZIP64: locate the ZIP64 end-of-central-directory record.
            guard eocdRelative >= 20, tail.readUInt32LE(eocdRelative - 20) == 0x0706_4B50 else {
                throw ToolkitError.invalidInput("The package's ZIP64 directory is missing.")
            }
            let zip64Offset = tail.readUInt64LE(eocdRelative - 12)
            try handle.seek(toOffset: zip64Offset)
            guard let record = try handle.read(upToCount: 56), record.count == 56, record.readUInt32LE(0) == 0x0606_4B50 else {
                throw ToolkitError.invalidInput("The package's ZIP64 directory is damaged.")
            }
            entryCount = record.readUInt64LE(32)
            directorySize = record.readUInt64LE(40)
            directoryOffset = record.readUInt64LE(48)
        }
        guard entryCount <= UInt64(maximumEntries), directoryOffset + directorySize <= fileSize, directorySize <= 512 * 1024 * 1024 else {
            throw ToolkitError.invalidInput("The package directory is invalid or too large.")
        }
        try handle.seek(toOffset: directoryOffset)
        let directory = try handle.read(upToCount: Int(directorySize)) ?? Data()
        var entries: [Entry] = []
        var cursor = 0
        for _ in 0..<entryCount {
            guard cursor + 46 <= directory.count, directory.readUInt32LE(cursor) == 0x0201_4B50 else {
                throw ToolkitError.invalidInput("The package directory is damaged.")
            }
            let versionMadeBy = directory.readUInt16LE(cursor + 4)
            let flags = directory.readUInt16LE(cursor + 8)
            let method = directory.readUInt16LE(cursor + 10)
            var compressed = UInt64(directory.readUInt32LE(cursor + 20))
            var uncompressed = UInt64(directory.readUInt32LE(cursor + 24))
            let nameLength = Int(directory.readUInt16LE(cursor + 28))
            let extraLength = Int(directory.readUInt16LE(cursor + 30))
            let commentLength = Int(directory.readUInt16LE(cursor + 32))
            let external = directory.readUInt32LE(cursor + 38)
            var localOffset = UInt64(directory.readUInt32LE(cursor + 42))
            let nameStart = cursor + 46
            guard nameStart + nameLength + extraLength + commentLength <= directory.count else {
                throw ToolkitError.invalidInput("The package directory is damaged.")
            }
            let nameData = directory.subdata(in: (directory.startIndex + nameStart)..<(directory.startIndex + nameStart + nameLength))
            let name = String(decoding: nameData, as: UTF8.self)
            // ZIP64 extended information extra field.
            var extraCursor = nameStart + nameLength
            let extraEnd = extraCursor + extraLength
            while extraCursor + 4 <= extraEnd {
                let tag = directory.readUInt16LE(extraCursor)
                let size = Int(directory.readUInt16LE(extraCursor + 2))
                var field = extraCursor + 4
                if tag == 0x0001 {
                    if uncompressed == 0xFFFF_FFFF, field + 8 <= extraEnd { uncompressed = directory.readUInt64LE(field); field += 8 }
                    if compressed == 0xFFFF_FFFF, field + 8 <= extraEnd { compressed = directory.readUInt64LE(field); field += 8 }
                    if localOffset == 0xFFFF_FFFF, field + 8 <= extraEnd { localOffset = directory.readUInt64LE(field) }
                }
                extraCursor += 4 + size
            }
            entries.append(Entry(name: name, compressionMethod: method, flags: flags, compressedSize: compressed, uncompressedSize: uncompressed, localHeaderOffset: localOffset, externalAttributes: external, versionMadeBy: versionMadeBy))
            cursor = nameStart + nameLength + extraLength + commentLength
        }
        return entries
    }
}

extension Data {
    func readUInt16LE(_ offset: Int) -> UInt16 {
        let base = startIndex + offset
        return UInt16(self[base]) | UInt16(self[base + 1]) << 8
    }

    func readUInt32LE(_ offset: Int) -> UInt32 {
        let base = startIndex + offset
        return UInt32(self[base]) | UInt32(self[base + 1]) << 8 | UInt32(self[base + 2]) << 16 | UInt32(self[base + 3]) << 24
    }

    func readUInt64LE(_ offset: Int) -> UInt64 {
        UInt64(readUInt32LE(offset)) | UInt64(readUInt32LE(offset + 4)) << 32
    }
}
