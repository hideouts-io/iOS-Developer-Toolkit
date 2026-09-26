import Foundation
import OSLog
import ToolkitCore

/// Progress and status reported during a backup.
public enum BackupEvent: Sendable, Equatable {
    case status(String)
    case progress(Double)
    case encryption(Bool)
    case bytesReceived(Int64)
    case finished(URL)
}

public struct BackupOptions: Sendable {
    /// The folder that will contain `<UDID>/` (the standard Finder/iTunes backup layout).
    public var destinationRoot: URL
    public var forceFullBackup: Bool

    public init(destinationRoot: URL, forceFullBackup: Bool) {
        self.destinationRoot = destinationRoot
        self.forceFullBackup = forceFullBackup
    }
}

/// A native client for `com.apple.mobilebackup2` (the DeviceLink protocol Finder uses).
///
/// Every path the device names is resolved beneath the chosen backup folder with
/// `SecureFileIO.safeChild`, so a malformed or hostile request cannot read or write outside it.
public enum MobileBackup2 {
    public static let serviceName = "com.apple.mobilebackup2"
    static let logger = ToolkitLog.backup

    // DeviceLink file-transfer codes.
    static let codeSuccess: UInt8 = 0x00
    static let codeErrorLocal: UInt8 = 0x06
    static let codeErrorRemote: UInt8 = 0x0B
    static let codeFileData: UInt8 = 0x0C
    static let emptyParameter = "___EmptyParameterString___"

    // MARK: Public operations

    /// Whether the device will encrypt local backups (a persistent device setting).
    public static func isEncryptionEnabled(_ session: DeviceSession) async throws -> Bool {
        guard let value = try await session.getValue(domain: "com.apple.mobile.backup", key: "WillEncrypt")?.boolValue else {
            throw ToolkitError(.serviceUnavailable, message: "The device did not report its backup encryption setting.")
        }
        return value
    }

    /// Runs a backup into `options.destinationRoot/<UDID>`.
    public static func backup(_ session: DeviceSession, options: BackupOptions, events: @escaping @Sendable (BackupEvent) -> Void) async throws -> URL {
        let target = session.target
        try SecureFileIO.createPrivateDirectory(at: options.destinationRoot)
        let backupDirectory = try SecureFileIO.safeChild(of: options.destinationRoot, relativePath: target.udid)
        try SecureFileIO.createPrivateDirectory(at: backupDirectory)

        let encrypted = try await isEncryptionEnabled(session)
        events(.encryption(encrypted))

        events(.status("Reading device information for the backup record…"))
        let infoPlist = try await makeInfoPlist(session)
        try SecureFileIO.writeAtomically(try infoPlist.encoded(format: .xml), to: backupDirectory.appendingPathComponent("Info.plist"))

        let isIncremental = !options.forceFullBackup && FileManager.default.fileExists(atPath: backupDirectory.appendingPathComponent("Status.plist").path)
        events(.status(isIncremental ? "Starting an incremental backup…" : "Starting a full backup…"))

        let lock = try await SyncLock.acquire(session)
        do {
            let link = try await DeviceLink.open(session)
            defer { Task { await link.close() } }
            var backupOptions: [String: PlistValue] = [:]
            if !isIncremental { backupOptions["ForceFullBackup"] = true }
            try await link.processMessage([
                "MessageName": "Backup",
                "TargetIdentifier": .string(target.udid),
                "SourceIdentifier": .string(target.udid),
                "Options": .dictionary(backupOptions),
            ])
            try await link.runMessageLoop(root: options.destinationRoot, events: events)
            await lock.release()
        } catch {
            await lock.release()
            throw error
        }
        events(.progress(100))
        events(.finished(backupDirectory))
        logger.info("Backup completed")
        return backupDirectory
    }

    /// Turns on backup encryption with a new password. The password travels only inside the
    /// encrypted lockdown service connection; it is never logged or written to disk.
    public static func enableEncryption(_ session: DeviceSession, newPassword: String, events: @escaping @Sendable (BackupEvent) -> Void) async throws {
        guard !newPassword.isEmpty else { throw ToolkitError.invalidInput("Enter a new backup password.") }
        if try await isEncryptionEnabled(session) {
            events(.status("Backup encryption is already on; the existing password was not changed."))
            return
        }
        let scratch = try SecureFileIO.makeTemporaryDirectory(prefix: "idt-backup-password")
        defer { try? FileManager.default.removeItem(at: scratch) }
        let link = try await DeviceLink.open(session)
        defer { Task { await link.close() } }
        events(.status("Asking the device to turn on backup encryption. Enter the device passcode on the device if asked."))
        try await link.processMessage([
            "MessageName": "ChangePassword",
            "TargetIdentifier": .string(session.target.udid),
            "Options": ["NewPassword": .string(newPassword)],
        ])
        try await link.runMessageLoop(root: scratch, events: events)
        guard try await isEncryptionEnabled(session) else {
            throw ToolkitError(.commandFailed, message: "The device did not confirm that backup encryption is on.", recovery: "Unlock the device, enter the passcode if asked, and try again.")
        }
        events(.encryption(true))
    }

    // MARK: Info.plist

    static func makeInfoPlist(_ session: DeviceSession) async throws -> PlistValue {
        let values = try await session.getValue() ?? .dictionary([:])
        let udid = session.target.udid
        var info: [String: PlistValue] = [
            "Target Identifier": .string(udid),
            "Unique Identifier": .string(udid.uppercased()),
            "Target Type": "Device",
            "GUID": .string(UUID().uuidString.replacingOccurrences(of: "-", with: "")),
            "Last Backup Date": .date(Date()),
            "iTunes Version": "12.13.0",
            "Backup Tool": "iOS Developer Toolkit",
        ]
        let mapping = [
            "Device Name": "DeviceName", "Display Name": "DeviceName", "Product Type": "ProductType",
            "Product Version": "ProductVersion", "Build Version": "BuildVersion", "Serial Number": "SerialNumber",
        ]
        for (infoKey, lockdownKey) in mapping {
            if let value = values[lockdownKey]?.stringValue { info[infoKey] = .string(value) }
        }
        if let proxy = try? await InstallationProxy.open(session) {
            let apps = (try? await proxy.browse(includeSizes: false, applicationType: "User")) ?? []
            await proxy.close()
            info["Installed Applications"] = .array(apps.map { .string($0.bundleIdentifier) })
        }
        return .dictionary(info)
    }

    // MARK: Sync lock

    /// Prevents a concurrent sync while the backup runs, as Finder does.
    struct SyncLock: Sendable {
        let notifications: NotificationProxy?
        let afc: AFCClient?
        let handle: UInt64?

        static func acquire(_ session: DeviceSession) async throws -> SyncLock {
            let notifications = try? await NotificationProxy.open(session)
            try? await notifications?.post("com.apple.itunes-mobdev.syncWillStart")
            guard let afc = try? await AFCClient.openMedia(session),
                  let handle = try? await afc.open("/com.apple.itunes.lock_sync", mode: .readWrite)
            else {
                return SyncLock(notifications: notifications, afc: nil, handle: nil)
            }
            try? await notifications?.post("com.apple.itunes-mobdev.syncLockRequest")
            var locked = false
            for _ in 0..<50 {
                if (try? await afc.lock(handle: handle, operation: .exclusive)) != nil {
                    locked = true
                    break
                }
                try await Task.sleep(nanoseconds: 200_000_000)
            }
            guard locked else {
                try? await afc.close(handle: handle)
                await afc.close()
                try? await notifications?.post("com.apple.itunes-mobdev.syncDidFinish")
                await notifications?.close()
                throw ToolkitError(.serviceUnavailable, message: "Another sync or backup is using the device.", recovery: "Wait for Finder or another tool to finish, then try again.")
            }
            try? await notifications?.post("com.apple.itunes-mobdev.syncDidStart")
            return SyncLock(notifications: notifications, afc: afc, handle: handle)
        }

        func release() async {
            if let afc, let handle {
                try? await afc.lock(handle: handle, operation: .unlock)
                try? await afc.close(handle: handle)
                await afc.close()
            }
            try? await notifications?.post("com.apple.itunes-mobdev.syncDidFinish")
            await notifications?.close()
        }
    }
}

/// The DeviceLink message loop shared by backup and password changes.
actor DeviceLink {
    private let connection: ServiceConnection
    private var messages: PlistMessageConnection { connection.messages }
    private var channel: DeviceChannel { connection.channel }

    init(connection: ServiceConnection) {
        self.connection = connection
    }

    static func open(_ session: DeviceSession) async throws -> DeviceLink {
        let connection = try await session.openService(MobileBackup2.serviceName, useEscrowBag: true)
        let link = DeviceLink(connection: connection)
        do {
            try await link.handshake()
        } catch {
            await link.close()
            throw error
        }
        return link
    }

    func handshake() async throws {
        let exchange = try await messages.receive(timeout: 30)
        guard exchange[0]?.stringValue == "DLMessageVersionExchange", let major = exchange[1] else {
            throw ToolkitError(.protocolViolation, message: "The backup service did not start correctly.", technicalDetail: exchange.prettyJSONString())
        }
        try await messages.send(["DLMessageVersionExchange", "DLVersionsOk", major], format: .binary)
        let ready = try await messages.receive(timeout: 30)
        guard ready[0]?.stringValue == "DLMessageDeviceReady" else {
            throw ToolkitError(.protocolViolation, message: "The backup service is not ready.", technicalDetail: ready.prettyJSONString())
        }
        try await processMessage(["MessageName": "Hello", "SupportedProtocolVersions": [2.0, 2.1]])
        let hello = try await messages.receive(timeout: 30)
        let code = hello[1]?["ErrorCode"]?.intValue ?? -1
        guard code == 0 else {
            throw ToolkitError(.unsupported, message: "The device does not support this backup protocol version.", technicalDetail: hello.prettyJSONString())
        }
    }

    func processMessage(_ body: PlistValue) async throws {
        try await messages.send(["DLMessageProcessMessage", body], format: .binary)
    }

    func sendStatus(_ code: Int, description: String? = nil, payload: PlistValue = .dictionary([:])) async throws {
        try await messages.send(["DLMessageStatusResponse", .integer(Int64(code)), .string(description ?? MobileBackup2.emptyParameter), payload], format: .binary)
    }

    /// Handles device requests until the final `DLMessageProcessMessage` or disconnect.
    func runMessageLoop(root: URL, events: @escaping @Sendable (BackupEvent) -> Void) async throws {
        var receivedBytes: Int64 = 0
        while true {
            try Task.checkCancellation()
            let message = try await messages.receive(timeout: 600)
            guard let name = message[0]?.stringValue else {
                throw ToolkitError(.protocolViolation, message: "The backup service sent an unreadable request.")
            }
            if let progress = Self.progress(in: message) { events(.progress(progress)) }
            switch name {
            case "DLMessageDownloadFiles":
                try await sendFiles(message[1]?.arrayValue?.compactMap(\.stringValue) ?? [], root: root)
            case "DLMessageUploadFiles":
                let count = try await receiveFiles(root: root)
                receivedBytes += count
                events(.bytesReceived(receivedBytes))
            case "DLMessageGetFreeDiskSpace":
                let values = try? root.resourceValues(forKeys: [.volumeAvailableCapacityForImportantUsageKey])
                try await sendStatus(0, payload: .integer(values?.volumeAvailableCapacityForImportantUsage ?? 0))
            case "DLMessageContentsOfDirectory":
                try await sendDirectoryContents(message[1]?.stringValue ?? "", root: root)
            case "DLMessageCreateDirectory":
                try await perform(root: root) {
                    let url = try SecureFileIO.safeChild(of: root, relativePath: message[1]?.stringValue ?? "")
                    try SecureFileIO.createPrivateDirectory(at: url)
                }
            case "DLMessageMoveFiles", "DLMessageMoveItems":
                let moves = message[1]?.dictionaryValue ?? [:]
                try await perform(root: root) {
                    for (source, destinationValue) in moves {
                        guard let destination = destinationValue.stringValue else { continue }
                        let from = try SecureFileIO.safeChild(of: root, relativePath: source)
                        let to = try SecureFileIO.safeChild(of: root, relativePath: destination)
                        if FileManager.default.fileExists(atPath: to.path) { try FileManager.default.removeItem(at: to) }
                        try SecureFileIO.createPrivateDirectory(at: to.deletingLastPathComponent())
                        try FileManager.default.moveItem(at: from, to: to)
                    }
                }
            case "DLMessageRemoveFiles", "DLMessageRemoveItems":
                let paths = message[1]?.arrayValue?.compactMap(\.stringValue) ?? []
                try await perform(root: root) {
                    for path in paths {
                        let url = try SecureFileIO.safeChild(of: root, relativePath: path)
                        if FileManager.default.fileExists(atPath: url.path) { try FileManager.default.removeItem(at: url) }
                    }
                }
            case "DLMessageCopyItem":
                try await perform(root: root) {
                    let from = try SecureFileIO.safeChild(of: root, relativePath: message[1]?.stringValue ?? "")
                    let to = try SecureFileIO.safeChild(of: root, relativePath: message[2]?.stringValue ?? "")
                    if FileManager.default.fileExists(atPath: to.path) { try FileManager.default.removeItem(at: to) }
                    try FileManager.default.copyItem(at: from, to: to)
                }
            case "DLMessageDisconnect":
                return
            case "DLMessageProcessMessage":
                let result = message[1] ?? .dictionary([:])
                let code = result["ErrorCode"]?.intValue ?? 0
                if code == 0 { return }
                throw MobileBackup2Errors.interpret(code: code, description: result["ErrorDescription"]?.stringValue)
            default:
                MobileBackup2.logger.error("Unhandled DeviceLink message \(name, privacy: .public)")
                try await sendStatus(-1, description: "Operation not supported")
            }
        }
    }

    static func progress(in message: PlistValue) -> Double? {
        guard let items = message.arrayValue, items.count > 2 else { return nil }
        for index in [3, 2] where index < items.count {
            if case .real(let value) = items[index], value.isFinite, (0...100).contains(value) { return value }
        }
        return nil
    }

    private func perform(root: URL, _ body: () throws -> Void) async throws {
        do {
            try body()
            try await sendStatus(0)
        } catch let error as ToolkitError where error.kind == .protocolViolation {
            MobileBackup2.logger.error("Rejected unsafe backup path")
            try await sendStatus(-1, description: "Rejected unsafe path")
        } catch {
            try await sendStatus(Self.deviceErrorCode(error), description: error.localizedDescription)
        }
    }

    static func deviceErrorCode(_ error: Error) -> Int {
        let nsError = error as NSError
        let posix = (nsError.userInfo[NSUnderlyingErrorKey] as? NSError)?.code ?? nsError.code
        switch Int32(truncatingIfNeeded: posix) {
        case ENOENT: return -6
        case EEXIST: return -7
        case ENOTDIR: return -8
        case EISDIR: return -9
        case ELOOP: return -10
        case EIO: return -11
        case ENOSPC: return -15
        default:
            if nsError.domain == NSCocoaErrorDomain, nsError.code == NSFileNoSuchFileError || nsError.code == NSFileReadNoSuchFileError { return -6 }
            return -1
        }
    }

    // MARK: File transfer

    private func writeLength(_ value: Int) async throws {
        var data = Data()
        data.appendBigEndian(UInt32(value))
        try await channel.write(data)
    }

    /// Host → device: send the requested files (for incremental backups).
    private func sendFiles(_ paths: [String], root: URL) async throws {
        var errors: [String: PlistValue] = [:]
        for path in paths {
            try await writeLength(path.utf8.count)
            try await channel.write(Data(path.utf8))
            let url = try? SecureFileIO.safeChild(of: root, relativePath: path)
            guard let url, let handle = try? FileHandle(forReadingFrom: url) else {
                let message = "No such file or directory"
                try await writeLength(message.utf8.count + 1)
                try await channel.write(Data([MobileBackup2.codeErrorLocal]) + Data(message.utf8))
                errors[path] = ["DLFileErrorString": .string(message), "DLFileErrorCode": -6]
                continue
            }
            defer { try? handle.close() }
            while let chunk = try handle.read(upToCount: 1 << 20), !chunk.isEmpty {
                try await writeLength(chunk.count + 1)
                try await channel.write(Data([MobileBackup2.codeFileData]) + chunk)
            }
            try await writeLength(1)
            try await channel.write(Data([MobileBackup2.codeSuccess]))
        }
        try await writeLength(0)
        if errors.isEmpty {
            try await sendStatus(0)
        } else {
            try await sendStatus(-13, description: "Multi status", payload: .dictionary(errors))
        }
    }

    private func readLength() async throws -> Int {
        Int(try await channel.read(exactly: 4, timeout: 600).readBigEndianUInt32(at: 0))
    }

    /// Device → host: receive files into the backup folder. Returns the number of bytes written.
    private func receiveFiles(root: URL) async throws -> Int64 {
        var total: Int64 = 0
        var errors: [String: PlistValue] = [:]
        while true {
            try Task.checkCancellation()
            let deviceNameLength = try await readLength()
            if deviceNameLength == 0 { break }
            guard deviceNameLength < 4096 else { throw ToolkitError(.protocolViolation, message: "The backup service sent an invalid file name.") }
            _ = try await channel.read(exactly: deviceNameLength, timeout: 600)
            let hostNameLength = try await readLength()
            guard hostNameLength > 0, hostNameLength < 4096 else { throw ToolkitError(.protocolViolation, message: "The backup service sent an invalid file name.") }
            let hostName = String(decoding: try await channel.read(exactly: hostNameLength, timeout: 600), as: UTF8.self)
            let destination = try SecureFileIO.safeChild(of: root, relativePath: hostName)
            try SecureFileIO.createPrivateDirectory(at: destination.deletingLastPathComponent())
            if FileManager.default.fileExists(atPath: destination.path) { try FileManager.default.removeItem(at: destination) }
            try SecureFileIO.writeNewFile(Data(), to: destination)
            let output = try FileHandle(forWritingTo: destination)
            defer { try? output.close() }
            while true {
                let blockLength = try await readLength()
                let code = try await channel.read(exactly: 1, timeout: 600)[0]
                let payloadLength = max(0, blockLength - 1)
                if code == MobileBackup2.codeFileData {
                    let payload = try await channel.read(exactly: payloadLength, timeout: 600)
                    try output.write(contentsOf: payload)
                    total += Int64(payload.count)
                } else if code == MobileBackup2.codeSuccess {
                    if payloadLength > 0 { _ = try await channel.read(exactly: payloadLength, timeout: 600) }
                    break
                } else if code == MobileBackup2.codeErrorRemote || code == MobileBackup2.codeErrorLocal {
                    let message = payloadLength > 0 ? String(decoding: try await channel.read(exactly: payloadLength, timeout: 600), as: UTF8.self) : "Unknown error"
                    errors[hostName] = ["DLFileErrorString": .string(message), "DLFileErrorCode": -1]
                    try? FileManager.default.removeItem(at: destination)
                    break
                } else {
                    throw ToolkitError(.protocolViolation, message: "The backup service sent an unexpected transfer code.", technicalDetail: "code=\(code)")
                }
            }
        }
        if errors.isEmpty {
            try await sendStatus(0)
        } else {
            try await sendStatus(-13, description: "Multi status", payload: .dictionary(errors))
        }
        return total
    }

    private func sendDirectoryContents(_ path: String, root: URL) async throws {
        guard let url = try? SecureFileIO.safeChild(of: root, relativePath: path.isEmpty ? "." : path) else {
            try await sendStatus(-1, description: "Rejected unsafe path")
            return
        }
        var entries: [String: PlistValue] = [:]
        let names = (try? FileManager.default.contentsOfDirectory(atPath: url.path)) ?? []
        for name in names {
            let child = url.appendingPathComponent(name)
            let values = try? child.resourceValues(forKeys: [.isDirectoryKey, .isRegularFileKey, .fileSizeKey, .contentModificationDateKey])
            let type = values?.isDirectory == true ? "DLFileTypeDirectory" : (values?.isRegularFile == true ? "DLFileTypeRegular" : "DLFileTypeUnknown")
            entries[name] = [
                "DLFileType": .string(type),
                "DLFileSize": .integer(Int64(values?.fileSize ?? 0)),
                "DLFileModificationDate": .date(values?.contentModificationDate ?? Date(timeIntervalSince1970: 0)),
            ]
        }
        try await sendStatus(0, payload: .dictionary(entries))
    }

    func close() async {
        try? await messages.send(["DLMessageDisconnect", "___EmptyParameterString___"], format: .binary)
        await connection.close()
    }
}

enum MobileBackup2Errors {
    static func interpret(code: Int, description: String?) -> ToolkitError {
        let detail = "MobileBackup2 error \(code): \(description ?? "")"
        switch code {
        case 208, -208:
            return ToolkitError(.deviceLocked, message: "The device must stay unlocked during the backup.", recovery: "Unlock the device, keep it awake, and try again.", technicalDetail: detail)
        case 105, -105:
            return ToolkitError(.fileSystem, message: "There is not enough space on this Mac for the backup.", recovery: "Free up space or choose a different backup folder.", technicalDetail: detail)
        case 207, -207:
            return ToolkitError(.commandFailed, message: "The backup password was not accepted.", recovery: "Enter the passcode on the device when asked, or check the backup password.", technicalDetail: detail)
        default:
            return ToolkitError(.commandFailed, message: "The device ended the backup with an error.", recovery: "Keep the device unlocked and connected, then try again.", technicalDetail: detail)
        }
    }
}
