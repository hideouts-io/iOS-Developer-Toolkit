import Foundation
@testable import DeviceKit
import ToolkitCore

/// An in-memory AFC server for tests.
actor FakeAFCFileSystem {
    var files: [String: Data]
    var directories: Set<String>
    var handles: [UInt64: (path: String, offset: Int)] = [:]
    var nextHandle: UInt64 = 1
    var lockOperations: [UInt64] = []

    init(files: [String: Data], directories: Set<String> = ["/"]) {
        self.files = files
        var all = directories
        for path in files.keys {
            var parent = (path as NSString).deletingLastPathComponent
            while !parent.isEmpty && parent != "/" {
                all.insert(parent)
                parent = (parent as NSString).deletingLastPathComponent
            }
        }
        all.insert("/")
        self.directories = all
    }

    func children(of path: String) -> [String] {
        let prefix = path == "/" ? "/" : path + "/"
        let names = (Array(files.keys) + Array(directories)).compactMap { entry -> String? in
            guard entry.hasPrefix(prefix), entry != path else { return nil }
            let rest = entry.dropFirst(prefix.count)
            guard !rest.isEmpty, !rest.contains("/") else { return nil }
            return String(rest)
        }
        return Array(Set(names)).sorted()
    }

    func serve(_ channel: DeviceChannel) async throws {
        while try await channel.hasMoreData() {
            let header = try await channel.read(exactly: 40, timeout: 5)
            let entire = Int(header.readLittleEndianUInt64(at: 8))
            let this = Int(header.readLittleEndianUInt64(at: 16))
            let number = header.readLittleEndianUInt64(at: 24)
            let operation = header.readLittleEndianUInt64(at: 32)
            let headerData = try await channel.read(exactly: this - 40, timeout: 5)
            let payload = try await channel.read(exactly: entire - this, timeout: 5)
            let reply = handle(operation: operation, header: headerData, payload: payload)
            try await channel.write(AFCClient.encode(operation: reply.operation, packetNumber: number, header: reply.header, payload: reply.payload))
        }
    }

    private func status(_ code: UInt64) -> (operation: UInt64, header: Data, payload: Data) {
        var data = Data()
        data.appendLittleEndian(code)
        return (0x01, data, Data())
    }

    private func path(_ data: Data) -> String {
        String(decoding: data.prefix { $0 != 0 }, as: UTF8.self)
    }

    private func handle(operation: UInt64, header: Data, payload: Data) -> (operation: UInt64, header: Data, payload: Data) {
        switch operation {
        case 0x03:
            let directory = path(header)
            guard directories.contains(directory) else { return status(8) }
            let names = [".", ".."] + children(of: directory)
            return (0x02, Data(), Data(names.map { $0 + "\u{0}" }.joined().utf8))
        case 0x0A:
            let target = path(header)
            if let data = files[target] {
                return (0x02, Data(), Data("st_size\u{0}\(data.count)\u{0}st_ifmt\u{0}S_IFREG\u{0}st_mtime\u{0}1700000000000000000\u{0}".utf8))
            }
            if directories.contains(target) {
                return (0x02, Data(), Data("st_size\u{0}0\u{0}st_ifmt\u{0}S_IFDIR\u{0}".utf8))
            }
            return status(8)
        case 0x0D:
            let mode = header.readLittleEndianUInt64(at: 0)
            let target = path(header.dropFirst(8))
            if mode == 1 && files[target] == nil { return status(8) }
            if mode == 3 { files[target] = Data() }
            if mode == 2 && files[target] == nil { files[target] = Data() }
            let handle = nextHandle
            nextHandle += 1
            handles[handle] = (target, 0)
            var data = Data()
            data.appendLittleEndian(handle)
            return (0x0E, data, Data())
        case 0x0F:
            let handle = header.readLittleEndianUInt64(at: 0)
            let size = Int(header.readLittleEndianUInt64(at: 8))
            guard let state = handles[handle], let data = files[state.path] else { return status(7) }
            let chunk = data.dropFirst(state.offset).prefix(size)
            handles[handle] = (state.path, state.offset + chunk.count)
            return (0x02, Data(), Data(chunk))
        case 0x10:
            let handle = header.readLittleEndianUInt64(at: 0)
            guard let state = handles[handle] else { return status(7) }
            files[state.path, default: Data()].append(payload)
            return status(0)
        case 0x14:
            handles[header.readLittleEndianUInt64(at: 0)] = nil
            return status(0)
        case 0x1B:
            lockOperations.append(header.readLittleEndianUInt64(at: 8))
            return status(0)
        case 0x09:
            directories.insert(path(header))
            return status(0)
        case 0x08:
            files[path(header)] = nil
            return status(0)
        default:
            return status(2)
        }
    }
}
