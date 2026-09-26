import Foundation
import Testing
@testable import DeviceKit
import ToolkitCore

private func withFakeDevice(_ configure: (FakeDeviceServer) -> Void, _ body: (FakeDeviceServer) async throws -> Void) async throws {
    let server = try FakeDeviceServer()
    configure(server)
    try await server.start()
    do {
        try await body(server)
    } catch {
        await server.stop()
        throw error
    }
    await server.stop()
}

/// Scripted DeviceLink peer.
struct FakeDeviceLink {
    let channel: DeviceChannel
    var messages: PlistMessageConnection { PlistMessageConnection(channel: channel) }

    func handshake() async throws -> PlistValue {
        try await messages.send(["DLMessageVersionExchange", 300, 0], format: .binary)
        let versions = try await messages.receive(timeout: 5)
        #expect(versions[1]?.stringValue == "DLVersionsOk")
        try await messages.send(["DLMessageDeviceReady"], format: .binary)
        let hello = try await messages.receive(timeout: 5)
        #expect(hello[1]?["MessageName"]?.stringValue == "Hello")
        try await messages.send(["DLMessageProcessMessage", ["MessageName": "Response", "ErrorCode": 0, "ProtocolVersion": 2.1]], format: .binary)
        return try await messages.receive(timeout: 5)
    }

    func expectStatus(_ code: Int) async throws -> PlistValue {
        let status = try await messages.receive(timeout: 5)
        #expect(status[0]?.stringValue == "DLMessageStatusResponse")
        #expect(status[1]?.intValue == code)
        return status
    }

    func length(_ value: Int) -> Data {
        var data = Data()
        data.appendBigEndian(UInt32(value))
        return data
    }

    func upload(_ files: [(String, Data)]) async throws {
        for (name, content) in files {
            try await channel.write(length(name.utf8.count) + Data(name.utf8))
            try await channel.write(length(name.utf8.count) + Data(name.utf8))
            if !content.isEmpty {
                try await channel.write(length(content.count + 1) + Data([0x0C]) + content)
            }
            try await channel.write(length(1) + Data([0x00]))
        }
        try await channel.write(length(0))
    }

    /// Reads the host's reply to DLMessageDownloadFiles.
    func receiveDownloads() async throws -> [String: Data?] {
        var results: [String: Data?] = [:]
        while true {
            let nameLength = Int(try await channel.read(exactly: 4, timeout: 5).readBigEndianUInt32(at: 0))
            if nameLength == 0 { break }
            let name = String(decoding: try await channel.read(exactly: nameLength, timeout: 5), as: UTF8.self)
            var content = Data()
            var failed = false
            while true {
                let blockLength = Int(try await channel.read(exactly: 4, timeout: 5).readBigEndianUInt32(at: 0))
                let code = try await channel.read(exactly: 1, timeout: 5)[0]
                let payload = blockLength > 1 ? try await channel.read(exactly: blockLength - 1, timeout: 5) : Data()
                if code == 0x0C { content.append(payload); continue }
                failed = code != 0x00
                break
            }
            results[name] = failed ? nil : content
        }
        return results
    }
}

@Suite("AFC and MobileBackup2 (fake device)", .serialized)
struct BackupAndAFCTests {
    @Test func afcListsReadsAndWrites() async throws {
        let fileSystem = FakeAFCFileSystem(files: ["/DCIM/100APPLE/IMG_0001.JPG": Data("jpeg".utf8), "/Downloads/readme.txt": Data(repeating: 0x61, count: 3000)])
        try await withFakeDevice({ _ in }) { server in
            server.register(service: AFCClient.mediaServiceName) { channel in
                try await fileSystem.serve(channel)
            }
            let directory = try SecureFileIO.makeTemporaryDirectory(prefix: "afc-test")
            defer { try? FileManager.default.removeItem(at: directory) }
            try await DeviceSession.with(server.target, usbmux: server.client) { session in
                let afc = try await AFCClient.openMedia(session)
                #expect(try await afc.listDirectory("/") == ["DCIM", "Downloads"])
                let info = try await afc.fileInfo("/Downloads/readme.txt")
                #expect(info.size == 3000)
                #expect(!info.isDirectory)
                #expect(try await afc.fileInfo("/DCIM").isDirectory)
                let destination = directory.appendingPathComponent("readme.txt")
                #expect(try await afc.download("/Downloads/readme.txt", to: destination, chunkSize: 1024) == 3000)
                #expect(try Data(contentsOf: destination).count == 3000)
                let source = directory.appendingPathComponent("upload.ipa")
                try SecureFileIO.writeNewFile(Data(repeating: 7, count: 2500), to: source)
                try await afc.makeDirectory("/PublicStaging")
                try await afc.upload(source, to: "/PublicStaging/upload.ipa", chunkSize: 1000)
                let walk = try await afc.walk("/")
                #expect(walk.contains("/PublicStaging/upload.ipa"))
                do {
                    _ = try await afc.fileInfo("/missing")
                    Issue.record("expected not found")
                } catch let error as ToolkitError {
                    #expect(error.kind == .fileSystem)
                    #expect(error.message.contains("does not exist"))
                }
                await afc.close()
            }
            #expect(await fileSystem.files["/PublicStaging/upload.ipa"]?.count == 2500)
        }
    }

    @Test func fullBackupConversation() async throws {
        let statusPlist = try PlistValue(dictionaryLiteral: ("SnapshotState", "finished")).encoded()
        try await withFakeDevice({ $0.domainValues["com.apple.mobile.backup"] = ["WillEncrypt": true] }) { server in
            let udid = server.udid
            server.register(service: MobileBackup2.serviceName) { channel in
                let link = FakeDeviceLink(channel: channel)
                let request = try await link.handshake()
                #expect(request[1]?["MessageName"]?.stringValue == "Backup")
                #expect(request[1]?["TargetIdentifier"]?.stringValue == udid)
                #expect(request[1]?["Options"]?["ForceFullBackup"]?.boolValue == true)

                try await link.messages.send(["DLMessageCreateDirectory", .string("\(udid)/Snapshot"), 0, 5.0], format: .binary)
                _ = try await link.expectStatus(0)

                try await link.messages.send(["DLMessageUploadFiles", [], 0, 40.0], format: .binary)
                try await link.upload([
                    ("\(udid)/Snapshot/Status.plist", statusPlist),
                    ("\(udid)/Snapshot/ab/ab12cd", Data(repeating: 0x42, count: 70_000)),
                    ("\(udid)/Snapshot/empty", Data()),
                ])
                _ = try await link.expectStatus(0)

                try await link.messages.send(["DLMessageMoveFiles", .dictionary(["\(udid)/Snapshot/Status.plist": .string("\(udid)/Status.plist")]), 0, 70.0], format: .binary)
                _ = try await link.expectStatus(0)

                // A path that tries to leave the backup folder is refused, not followed.
                try await link.messages.send(["DLMessageCreateDirectory", "../../escape"], format: .binary)
                _ = try await link.expectStatus(-1)

                try await link.messages.send(["DLMessageGetFreeDiskSpace"], format: .binary)
                let space = try await link.expectStatus(0)
                #expect((space[3]?.int64Value ?? 0) > 0)

                try await link.messages.send(["DLMessageContentsOfDirectory", .string(udid)], format: .binary)
                let contents = try await link.expectStatus(0)
                #expect(contents[3]?["Status.plist"]?["DLFileType"]?.stringValue == "DLFileTypeRegular")

                try await link.messages.send(["DLMessageDownloadFiles", [.string("\(udid)/Status.plist"), .string("\(udid)/missing")], 0, 90.0], format: .binary)
                let downloads = try await link.receiveDownloads()
                #expect(downloads["\(udid)/Status.plist"] == .some(statusPlist))
                #expect(downloads["\(udid)/missing"] == .some(nil))
                let multi = try await link.expectStatus(-13)
                #expect(multi[3]?["\(udid)/missing"] != nil)

                try await link.messages.send(["DLMessageRemoveFiles", [.string("\(udid)/Snapshot/empty")], 0, 95.0], format: .binary)
                _ = try await link.expectStatus(0)

                try await link.messages.send(["DLMessageProcessMessage", ["ErrorCode": 0]], format: .binary)
                _ = try? await link.messages.receive(timeout: 2)
            }
            let root = try SecureFileIO.makeTemporaryDirectory(prefix: "backup-test")
            defer { try? FileManager.default.removeItem(at: root.deletingLastPathComponent().appendingPathComponent("escape")) }
            defer { try? FileManager.default.removeItem(at: root) }
            let events = LockedValue<[BackupEvent]>([])
            let result = try await DeviceSession.with(server.target, usbmux: server.client) { session in
                try await MobileBackup2.backup(session, options: BackupOptions(destinationRoot: root, forceFullBackup: true)) { event in
                    events.withLock { $0.append(event) }
                }
            }
            #expect(result.lastPathComponent == udid)
            let backup = root.appendingPathComponent(udid)
            #expect(FileManager.default.fileExists(atPath: backup.appendingPathComponent("Status.plist").path))
            #expect(FileManager.default.fileExists(atPath: backup.appendingPathComponent("Info.plist").path))
            #expect(try Data(contentsOf: backup.appendingPathComponent("Snapshot/ab/ab12cd")).count == 70_000)
            #expect(!FileManager.default.fileExists(atPath: backup.appendingPathComponent("Snapshot/empty").path))
            #expect(!FileManager.default.fileExists(atPath: root.deletingLastPathComponent().appendingPathComponent("escape").path))
            let info = try PlistValue.decode(try Data(contentsOf: backup.appendingPathComponent("Info.plist")))
            #expect(info["Target Identifier"]?.stringValue == udid)
            #expect(info["Device Name"]?.stringValue == "Test iPhone")
            let recorded = events.current
            #expect(recorded.contains(.encryption(true)))
            #expect(recorded.contains(.progress(40)))
            #expect(recorded.contains(.bytesReceived(Int64(statusPlist.count + 70_000))))
            guard case .finished(let finishedURL)? = recorded.last else {
                Issue.record("expected a finished event last")
                return
            }
            #expect(finishedURL.standardizedFileURL.path == backup.standardizedFileURL.path)
        }
    }

    @Test func maliciousUploadPathAbortsBackup() async throws {
        try await withFakeDevice({ $0.domainValues["com.apple.mobile.backup"] = ["WillEncrypt": false] }) { server in
            server.register(service: MobileBackup2.serviceName) { channel in
                let link = FakeDeviceLink(channel: channel)
                _ = try await link.handshake()
                try await link.messages.send(["DLMessageUploadFiles", [], 0, 10.0], format: .binary)
                try await link.upload([("../../evil.txt", Data("x".utf8))])
                _ = try? await link.messages.receive(timeout: 2)
            }
            let root = try SecureFileIO.makeTemporaryDirectory(prefix: "backup-evil")
            defer { try? FileManager.default.removeItem(at: root) }
            do {
                _ = try await DeviceSession.with(server.target, usbmux: server.client) { session in
                    try await MobileBackup2.backup(session, options: BackupOptions(destinationRoot: root, forceFullBackup: true)) { _ in }
                }
                Issue.record("expected the backup to stop")
            } catch let error as ToolkitError {
                #expect(error.kind == .protocolViolation)
            }
            #expect(!FileManager.default.fileExists(atPath: root.deletingLastPathComponent().deletingLastPathComponent().appendingPathComponent("evil.txt").path))
        }
    }

    @Test func deviceReportedErrorsAreActionable() async throws {
        try await withFakeDevice({ $0.domainValues["com.apple.mobile.backup"] = ["WillEncrypt": false] }) { server in
            server.register(service: MobileBackup2.serviceName) { channel in
                let link = FakeDeviceLink(channel: channel)
                _ = try await link.handshake()
                try await link.messages.send(["DLMessageProcessMessage", ["ErrorCode": 208, "ErrorDescription": "Device locked"]], format: .binary)
                _ = try? await link.messages.receive(timeout: 2)
            }
            let root = try SecureFileIO.makeTemporaryDirectory(prefix: "backup-locked")
            defer { try? FileManager.default.removeItem(at: root) }
            do {
                _ = try await DeviceSession.with(server.target, usbmux: server.client) { session in
                    try await MobileBackup2.backup(session, options: BackupOptions(destinationRoot: root, forceFullBackup: false)) { _ in }
                }
                Issue.record("expected failure")
            } catch let error as ToolkitError {
                #expect(error.kind == .deviceLocked)
            }
        }
    }

    @Test func syncLockUsesNotificationsAndAFCLock() async throws {
        let fileSystem = FakeAFCFileSystem(files: [:])
        let notifications = LockedValue<[String]>([])
        try await withFakeDevice({ $0.domainValues["com.apple.mobile.backup"] = ["WillEncrypt": false] }) { server in
            server.register(service: AFCClient.mediaServiceName) { channel in try await fileSystem.serve(channel) }
            server.register(service: NotificationProxy.serviceName) { channel in
                let messages = PlistMessageConnection(channel: channel)
                while let message = try? await messages.receive(timeout: 5) {
                    if let name = message["Name"]?.stringValue { notifications.withLock { $0.append(name) } }
                    if message["Command"]?.stringValue == "Shutdown" { break }
                }
            }
            server.register(service: MobileBackup2.serviceName) { channel in
                let link = FakeDeviceLink(channel: channel)
                _ = try await link.handshake()
                try await link.messages.send(["DLMessageProcessMessage", ["ErrorCode": 0]], format: .binary)
                _ = try? await link.messages.receive(timeout: 2)
            }
            let root = try SecureFileIO.makeTemporaryDirectory(prefix: "backup-lock")
            defer { try? FileManager.default.removeItem(at: root) }
            _ = try await DeviceSession.with(server.target, usbmux: server.client) { session in
                try await MobileBackup2.backup(session, options: BackupOptions(destinationRoot: root, forceFullBackup: false)) { _ in }
            }
            try await Task.sleep(for: .milliseconds(200))
            #expect(await fileSystem.lockOperations == [AFCClient.LockOperation.exclusive.rawValue, AFCClient.LockOperation.unlock.rawValue])
            #expect(notifications.current == [
                "com.apple.itunes-mobdev.syncWillStart",
                "com.apple.itunes-mobdev.syncLockRequest",
                "com.apple.itunes-mobdev.syncDidStart",
                "com.apple.itunes-mobdev.syncDidFinish",
            ])
        }
    }

    @Test func progressExtraction() {
        #expect(DeviceLink.progress(in: ["DLMessageUploadFiles", [], 0, 42.5]) == 42.5)
        #expect(DeviceLink.progress(in: ["DLMessageUploadFiles", [], 12.0]) == 12.0)
        #expect(DeviceLink.progress(in: ["DLMessageUploadFiles", [], 0, 500.0]) == nil)
        #expect(DeviceLink.progress(in: ["DLMessageDisconnect"]) == nil)
    }
}
