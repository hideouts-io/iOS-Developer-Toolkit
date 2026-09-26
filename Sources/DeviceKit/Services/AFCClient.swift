import Foundation
import ToolkitCore

/// Apple File Conduit (`com.apple.afc`, also used by `com.apple.crashreportcopymobile`).
///
/// AFC exposes an Apple-defined view (the Media folder for `com.apple.afc`, crash reports for the
/// crash service). It is not access to the device's full file system.
public actor AFCClient {
    public static let mediaServiceName = "com.apple.afc"
    public static let crashReportServiceName = "com.apple.crashreportcopymobile"
    public static let crashReportMoverServiceName = "com.apple.crashreportmover"

    enum Operation: UInt64 {
        case status = 0x01
        case data = 0x02
        case readDirectory = 0x03
        case removePath = 0x08
        case makeDirectory = 0x09
        case getFileInfo = 0x0A
        case fileOpen = 0x0D
        case fileOpenResult = 0x0E
        case fileRead = 0x0F
        case fileWrite = 0x10
        case fileClose = 0x14
        case fileLock = 0x1B
    }

    public enum OpenMode: UInt64, Sendable {
        case readOnly = 1
        case readWrite = 2
        case writeTruncate = 3
    }

    public enum LockOperation: UInt64, Sendable {
        case shared = 5
        case exclusive = 6
        case unlock = 12
    }

    public struct FileInfo: Sendable, Hashable {
        public var size: Int64?
        public var isDirectory: Bool
        public var isSymbolicLink: Bool
        public var modified: Date?
        public var raw: [String: String]
    }

    static let magic = Data("CFA6LPAA".utf8)
    static let headerLength = 40
    static let maximumPacketLength = 64 * 1024 * 1024

    private let connection: ServiceConnection
    private var packetNumber: UInt64 = 0

    public init(connection: ServiceConnection) {
        self.connection = connection
    }

    public static func openMedia(_ session: DeviceSession) async throws -> AFCClient {
        AFCClient(connection: try await session.openService(mediaServiceName))
    }

    /// Asks the device to move pending crash reports into the copy area, then opens the crash
    /// report AFC view.
    public static func openCrashReports(_ session: DeviceSession) async throws -> AFCClient {
        if let mover = try? await session.openService(crashReportMoverServiceName) {
            _ = try? await mover.channel.read(exactly: 4, timeout: 30)
            await mover.close()
        }
        return AFCClient(connection: try await session.openService(crashReportServiceName))
    }

    // MARK: Packets

    static func encode(operation: UInt64, packetNumber: UInt64, header: Data, payload: Data) -> Data {
        var data = Data(capacity: headerLength + header.count + payload.count)
        data.append(magic)
        data.appendLittleEndian(UInt64(headerLength + header.count + payload.count))
        data.appendLittleEndian(UInt64(headerLength + header.count))
        data.appendLittleEndian(packetNumber)
        data.appendLittleEndian(operation)
        data.append(header)
        data.append(payload)
        return data
    }

    private func transact(_ operation: Operation, header: Data = Data(), payload: Data = Data()) async throws -> (operation: UInt64, body: Data) {
        packetNumber &+= 1
        try await connection.channel.write(Self.encode(operation: operation.rawValue, packetNumber: packetNumber, header: header, payload: payload))
        let packetHeader = try await connection.channel.read(exactly: Self.headerLength, timeout: 60)
        guard packetHeader.prefix(8) == Self.magic else {
            throw ToolkitError(.protocolViolation, message: "The device's file service sent an invalid reply.")
        }
        let entireLength = Int(clamping: packetHeader.readLittleEndianUInt64(at: 8))
        let replyOperation = packetHeader.readLittleEndianUInt64(at: 32)
        guard entireLength >= Self.headerLength, entireLength <= Self.maximumPacketLength else {
            throw ToolkitError(.protocolViolation, message: "The device's file service sent an invalid length.", technicalDetail: "\(entireLength)")
        }
        let body = try await connection.channel.read(exactly: entireLength - Self.headerLength, timeout: 120)
        if replyOperation == Operation.status.rawValue {
            let code = body.count >= 8 ? body.readLittleEndianUInt64(at: 0) : 1
            if code != 0 { throw Self.error(code: code) }
        }
        return (replyOperation, body)
    }

    static func error(code: UInt64) -> ToolkitError {
        switch code {
        case 8: return ToolkitError(.fileSystem, message: "The item does not exist on the device.", technicalDetail: "AFC error 8 (object not found)")
        case 9: return ToolkitError(.fileSystem, message: "The item is a folder.", technicalDetail: "AFC error 9 (is a directory)")
        case 10: return ToolkitError(.permissionDenied, message: "The device did not allow access to this item.", technicalDetail: "AFC error 10 (permission denied)")
        case 7: return ToolkitError(.invalidInput, message: "The device rejected the file request.", technicalDetail: "AFC error 7 (invalid argument)")
        default: return ToolkitError(.fileSystem, message: "The device's file service reported an error.", technicalDetail: "AFC error \(code)")
        }
    }

    static func path(_ path: String) -> Data {
        var data = Data(path.utf8)
        data.append(0)
        return data
    }

    static func nulSeparated(_ data: Data) -> [String] {
        data.split(separator: 0, omittingEmptySubsequences: true).map { String(decoding: $0, as: UTF8.self) }
    }

    // MARK: Operations

    public func listDirectory(_ path: String) async throws -> [String] {
        let reply = try await transact(.readDirectory, header: Self.path(path))
        return Self.nulSeparated(reply.body).filter { $0 != "." && $0 != ".." }.sorted()
    }

    public func fileInfo(_ path: String) async throws -> FileInfo {
        let reply = try await transact(.getFileInfo, header: Self.path(path))
        let values = Self.nulSeparated(reply.body)
        var raw: [String: String] = [:]
        var index = 0
        while index + 1 < values.count {
            raw[values[index]] = values[index + 1]
            index += 2
        }
        let modifiedNanoseconds = raw["st_mtime"].flatMap(Double.init)
        return FileInfo(
            size: raw["st_size"].flatMap(Int64.init),
            isDirectory: raw["st_ifmt"] == "S_IFDIR",
            isSymbolicLink: raw["st_ifmt"] == "S_IFLNK",
            modified: modifiedNanoseconds.map { Date(timeIntervalSince1970: $0 / 1_000_000_000) },
            raw: raw
        )
    }

    public func makeDirectory(_ path: String) async throws {
        _ = try await transact(.makeDirectory, header: Self.path(path))
    }

    public func remove(_ path: String) async throws {
        _ = try await transact(.removePath, header: Self.path(path))
    }

    public func open(_ path: String, mode: OpenMode) async throws -> UInt64 {
        var header = Data()
        header.appendLittleEndian(mode.rawValue)
        header.append(Self.path(path))
        let reply = try await transact(.fileOpen, header: header)
        guard reply.operation == Operation.fileOpenResult.rawValue, reply.body.count >= 8 else {
            throw ToolkitError(.protocolViolation, message: "The device did not open the file.")
        }
        return reply.body.readLittleEndianUInt64(at: 0)
    }

    public func read(handle: UInt64, count: Int) async throws -> Data {
        var header = Data()
        header.appendLittleEndian(handle)
        header.appendLittleEndian(UInt64(count))
        return try await transact(.fileRead, header: header).body
    }

    public func write(handle: UInt64, data: Data) async throws {
        var header = Data()
        header.appendLittleEndian(handle)
        _ = try await transact(.fileWrite, header: header, payload: data)
    }

    public func close(handle: UInt64) async throws {
        var header = Data()
        header.appendLittleEndian(handle)
        _ = try await transact(.fileClose, header: header)
    }

    public func lock(handle: UInt64, operation: LockOperation) async throws {
        var header = Data()
        header.appendLittleEndian(handle)
        header.appendLittleEndian(operation.rawValue)
        _ = try await transact(.fileLock, header: header)
    }

    /// Copies a device file to a new local file (never overwriting).
    public func download(_ path: String, to destination: URL, chunkSize: Int = 1 << 20) async throws -> Int64 {
        try SecureFileIO.writeNewFile(Data(), to: destination)
        guard let output = try? FileHandle(forWritingTo: destination) else {
            throw ToolkitError.fileSystem("Could not create the local copy.", path: destination.path)
        }
        defer { try? output.close() }
        let handle = try await open(path, mode: .readOnly)
        var total: Int64 = 0
        do {
            while true {
                try Task.checkCancellation()
                let chunk = try await read(handle: handle, count: chunkSize)
                if chunk.isEmpty { break }
                try output.write(contentsOf: chunk)
                total += Int64(chunk.count)
            }
            try await close(handle: handle)
        } catch {
            try? await close(handle: handle)
            try? FileManager.default.removeItem(at: destination)
            throw error
        }
        return total
    }

    /// Uploads a local file (used to stage an IPA in `PublicStaging`).
    public func upload(_ source: URL, to path: String, chunkSize: Int = 1 << 20, progress: @Sendable (Int64, Int64) -> Void = { _, _ in }) async throws {
        guard let input = try? FileHandle(forReadingFrom: source) else {
            throw ToolkitError.fileSystem("Could not read the file to upload.", path: source.path)
        }
        defer { try? input.close() }
        let totalSize = SecureFileIO.fileSize(source) ?? 0
        let handle = try await open(path, mode: .writeTruncate)
        var sent: Int64 = 0
        do {
            while let chunk = try input.read(upToCount: chunkSize), !chunk.isEmpty {
                try Task.checkCancellation()
                try await write(handle: handle, data: chunk)
                sent += Int64(chunk.count)
                progress(sent, totalSize)
            }
            try await close(handle: handle)
        } catch {
            try? await close(handle: handle)
            throw error
        }
    }

    /// Recursively lists files beneath `path` (bounded to avoid unbounded traversal).
    public func walk(_ path: String, maximumEntries: Int = 20_000) async throws -> [String] {
        var results: [String] = []
        var queue = [path]
        while let current = queue.first, results.count < maximumEntries {
            queue.removeFirst()
            for name in try await listDirectory(current) {
                let child = current.hasSuffix("/") ? current + name : current + "/" + name
                let info = try? await fileInfo(child)
                if info?.isDirectory == true {
                    queue.append(child)
                } else {
                    results.append(child)
                }
                if results.count >= maximumEntries { break }
            }
        }
        return results
    }

    public func close() async {
        await connection.close()
    }
}
