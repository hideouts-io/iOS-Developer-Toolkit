import CryptoKit
import Foundation

/// File helpers that default to owner-only permissions, never follow symbolic links for new
/// files, and never overwrite unless explicitly asked to.
public enum SecureFileIO {
    public static let privateFileMode: mode_t = 0o600
    public static let privateDirectoryMode: mode_t = 0o700

    /// Creates a directory (and parents) with owner-only permissions. An existing directory is
    /// accepted only if it is a real directory (not a symbolic link).
    public static func createPrivateDirectory(at url: URL) throws {
        let manager = FileManager.default
        var isDirectory: ObjCBool = false
        if manager.fileExists(atPath: url.path, isDirectory: &isDirectory) {
            let attributes = try? manager.attributesOfItem(atPath: url.path)
            if attributes?[.type] as? FileAttributeType == .typeSymbolicLink || !isDirectory.boolValue {
                throw ToolkitError.fileSystem("The output location is not a regular folder.", path: url.path)
            }
            return
        }
        do {
            try manager.createDirectory(at: url, withIntermediateDirectories: true, attributes: [.posixPermissions: NSNumber(value: privateDirectoryMode)])
        } catch {
            throw ToolkitError.fileSystem("Could not create the folder.", path: url.path, underlying: error)
        }
    }

    /// Creates a new directory that must not already exist.
    public static func createNewPrivateDirectory(at url: URL) throws {
        let parent = url.deletingLastPathComponent()
        try createPrivateDirectory(at: parent)
        if mkdir(url.path, privateDirectoryMode) != 0 {
            let code = errno
            if code == EEXIST {
                throw ToolkitError.fileSystem("A folder with this name already exists. Choose a new name.", path: url.path)
            }
            throw ToolkitError.fileSystem("Could not create the folder.", path: url.path, underlying: POSIXError(POSIXErrorCode(rawValue: code) ?? .EIO))
        }
    }

    /// Writes a new file with owner-only permissions. Fails if anything exists at `url`.
    public static func writeNewFile(_ data: Data, to url: URL, mode: mode_t = privateFileMode) throws {
        let descriptor = open(url.path, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, mode)
        guard descriptor >= 0 else {
            let code = errno
            if code == EEXIST {
                throw ToolkitError.fileSystem("A file with this name already exists. Choose a new name; existing files are never overwritten.", path: url.path)
            }
            throw ToolkitError.fileSystem("Could not create the file.", path: url.path, underlying: POSIXError(POSIXErrorCode(rawValue: code) ?? .EIO))
        }
        let handle = FileHandle(fileDescriptor: descriptor, closeOnDealloc: true)
        do {
            try handle.write(contentsOf: data)
            try handle.synchronize()
            try handle.close()
        } catch {
            try? handle.close()
            try? FileManager.default.removeItem(at: url)
            throw ToolkitError.fileSystem("Could not write the file.", path: url.path, underlying: error)
        }
    }

    /// Replaces a file atomically (write to a unique sibling, fsync, rename).
    public static func writeAtomically(_ data: Data, to url: URL, mode: mode_t = privateFileMode) throws {
        let directory = url.deletingLastPathComponent()
        try createPrivateDirectory(at: directory)
        let temporary = directory.appendingPathComponent(".\(url.lastPathComponent).\(UUID().uuidString).tmp")
        try writeNewFile(data, to: temporary, mode: mode)
        if rename(temporary.path, url.path) != 0 {
            let code = errno
            try? FileManager.default.removeItem(at: temporary)
            throw ToolkitError.fileSystem("Could not save the file.", path: url.path, underlying: POSIXError(POSIXErrorCode(rawValue: code) ?? .EIO))
        }
    }

    /// Appends data to a file, creating it with owner-only permissions when needed.
    public static func append(_ data: Data, to url: URL, synchronize: Bool = false) throws {
        let descriptor = open(url.path, O_WRONLY | O_CREAT | O_APPEND | O_NOFOLLOW | O_CLOEXEC, privateFileMode)
        guard descriptor >= 0 else {
            throw ToolkitError.fileSystem("Could not open the file for appending.", path: url.path, underlying: POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO))
        }
        let handle = FileHandle(fileDescriptor: descriptor, closeOnDealloc: true)
        defer { try? handle.close() }
        do {
            try handle.write(contentsOf: data)
            if synchronize { try handle.synchronize() }
        } catch {
            throw ToolkitError.fileSystem("Could not append to the file.", path: url.path, underlying: error)
        }
    }

    /// Creates a unique owner-only temporary directory (mkdtemp).
    public static func makeTemporaryDirectory(prefix: String) throws -> URL {
        let base = URL(fileURLWithPath: NSTemporaryDirectory(), isDirectory: true)
        let sanitizedPrefix = prefix.filter { $0.isLetter || $0.isNumber || $0 == "-" }
        var template = Array(base.appendingPathComponent("\(sanitizedPrefix).XXXXXXXX").path.utf8CString)
        let created = template.withUnsafeMutableBufferPointer { buffer -> UnsafeMutablePointer<CChar>? in
            mkdtemp(buffer.baseAddress)
        }
        guard created != nil else {
            throw ToolkitError.fileSystem("Could not create a temporary folder.", path: base.path, underlying: POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO))
        }
        let path = template.withUnsafeBufferPointer { String(cString: $0.baseAddress!) }
        return URL(fileURLWithPath: path, isDirectory: true)
    }

    /// Resolves `relativePath` beneath `root`, rejecting absolute paths, `..` components,
    /// NUL bytes, and anything that would escape `root` after normalization.
    public static func safeChild(of root: URL, relativePath: String) throws -> URL {
        guard !relativePath.isEmpty, !relativePath.contains("\0") else {
            throw ToolkitError(.protocolViolation, message: "An unsafe file path was rejected.", technicalDetail: "Empty or NUL-containing path")
        }
        guard !relativePath.hasPrefix("/") else {
            throw ToolkitError(.protocolViolation, message: "An unsafe file path was rejected.", technicalDetail: "Absolute path: \(relativePath)")
        }
        let components = relativePath.split(separator: "/", omittingEmptySubsequences: true)
        guard !components.contains(where: { $0 == ".." }) else {
            throw ToolkitError(.protocolViolation, message: "An unsafe file path was rejected.", technicalDetail: "Parent traversal: \(relativePath)")
        }
        let standardizedRoot = root.standardizedFileURL.path
        var candidate = root.standardizedFileURL
        for component in components where component != "." {
            candidate.appendPathComponent(String(component))
        }
        let resolved = candidate.standardizedFileURL.path
        guard resolved == standardizedRoot || resolved.hasPrefix(standardizedRoot.hasSuffix("/") ? standardizedRoot : standardizedRoot + "/") else {
            throw ToolkitError(.protocolViolation, message: "An unsafe file path was rejected.", technicalDetail: "Escapes root: \(relativePath)")
        }
        return candidate
    }

    /// Streams a file through SHA-256.
    public static func sha256(of url: URL) throws -> String {
        guard let handle = try? FileHandle(forReadingFrom: url) else {
            throw ToolkitError.fileSystem("Could not open the file to calculate its hash.", path: url.path)
        }
        defer { try? handle.close() }
        var hasher = SHA256()
        while true {
            let chunk: Data?
            do {
                chunk = try handle.read(upToCount: 1 << 20)
            } catch {
                throw ToolkitError.fileSystem("Could not read the file to calculate its hash.", path: url.path, underlying: error)
            }
            guard let chunk, !chunk.isEmpty else { break }
            hasher.update(data: chunk)
        }
        return hasher.finalize().hexString
    }

    public static func sha256(of data: Data) -> String {
        SHA256.hash(data: data).hexString
    }

    /// Byte size of a regular file, or nil.
    public static func fileSize(_ url: URL) -> Int64? {
        (try? FileManager.default.attributesOfItem(atPath: url.path)[.size] as? NSNumber)?.int64Value
    }

    /// Lists regular files beneath `root` (not following symlinks), sorted by relative path.
    public static func regularFiles(under root: URL) -> [(relativePath: String, url: URL)] {
        guard let enumerator = FileManager.default.enumerator(
            at: root,
            includingPropertiesForKeys: [.isRegularFileKey, .isSymbolicLinkKey],
            options: [],
            errorHandler: { _, _ in true }
        ) else { return [] }
        let rootPath = root.standardizedFileURL.resolvingSymlinksInPath().path
        var files: [(String, URL)] = []
        for case let url as URL in enumerator {
            let values = try? url.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey])
            guard values?.isRegularFile == true, values?.isSymbolicLink != true else { continue }
            let path = url.standardizedFileURL.resolvingSymlinksInPath().path
            guard path.hasPrefix(rootPath + "/") else { continue }
            files.append((String(path.dropFirst(rootPath.count + 1)), url))
        }
        return files.sorted { $0.0 < $1.0 }
    }
}

extension Digest {
    public var hexString: String {
        map { String(format: "%02x", $0) }.joined()
    }
}

/// Incremental SHA-256 for data written progressively (live-log spools, PCAP files).
public struct StreamingHasher: Sendable {
    private var hasher = SHA256()
    public private(set) var byteCount: Int64 = 0

    public init() {}

    public mutating func update(_ data: Data) {
        hasher.update(data: data)
        byteCount += Int64(data.count)
    }

    public func finalizeHex() -> String {
        hasher.finalize().hexString
    }
}
