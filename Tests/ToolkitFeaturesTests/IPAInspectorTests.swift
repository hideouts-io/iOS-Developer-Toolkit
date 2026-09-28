import Foundation
import Testing
@testable import ToolkitFeatures
import ToolkitCore

@Suite("IPA inspection")
struct IPAInspectorTests {
    func infoPlist(bundle: String = "com.example.demo") throws -> Data {
        let plist: PlistValue = [
            "CFBundleIdentifier": .string(bundle),
            "CFBundleName": "Demo",
            "CFBundleShortVersionString": "1.2.3",
            "CFBundleVersion": "45",
            "CFBundleExecutable": "Demo",
            "MinimumOSVersion": "17.0",
            "CFBundleSupportedPlatforms": ["iPhoneOS"],
        ]
        return try plist.encoded(format: .xml)
    }

    func makeIPA(_ entries: [(String, Data)], in directory: URL, name: String = "Demo.ipa") throws -> URL {
        var writer = ZipWriter()
        for (entryName, data) in entries { try writer.add(name: entryName, data: data) }
        let url = directory.appendingPathComponent(name)
        try SecureFileIO.writeNewFile(writer.finalized(), to: url)
        return url
    }

    func run(_ executable: String, _ arguments: [String], in directory: URL) async throws -> CommandResult {
        try await ProcessCommandRunner().run(CommandRequest(executable: URL(fileURLWithPath: executable), arguments: arguments, workingDirectory: directory, timeout: 60))
    }

    @Test func unsignedPackageIsInspectedButNotInstallable() throws {
        let directory = try SecureFileIO.makeTemporaryDirectory(prefix: "ipa")
        defer { try? FileManager.default.removeItem(at: directory) }
        let ipa = try makeIPA([("Payload/Demo.app/Info.plist", try infoPlist()), ("Payload/Demo.app/Demo", Data(repeating: 1, count: 4096))], in: directory)
        let inspection = try IPAInspector.inspect(ipa)
        #expect(inspection.appName == "Demo")
        #expect(inspection.bundleIdentifier == "com.example.demo")
        #expect(inspection.version == "1.2.3")
        #expect(inspection.build == "45")
        #expect(inspection.minimumOSVersion == "17.0")
        #expect(inspection.signature.status == .missing)
        #expect(inspection.provisioning.status == .absent)
        #expect(!inspection.isInstallable)
        #expect(inspection.installabilityExplanation.contains("not signed"))
        #expect(inspection.packageSHA256.count == 64)
        #expect(inspection.report.contains("Bundle identifier: com.example.demo"))
    }

    @Test(arguments: ["Payload/../../evil", "/Payload/Demo.app/Info.plist", "Payload\\Demo.app\\x"])
    func unsafeEntryNamesAreRejected(name: String) throws {
        #expect(throws: ToolkitError.self) { try ZipArchive.validateName(name) }
    }

    @Test func requiresExactlyOneAppInfoPlist() throws {
        let directory = try SecureFileIO.makeTemporaryDirectory(prefix: "ipa")
        defer { try? FileManager.default.removeItem(at: directory) }
        let none = try makeIPA([("Payload/readme.txt", Data("x".utf8))], in: directory, name: "none.ipa")
        #expect(throws: ToolkitError.self) { try IPAInspector.inspect(none) }
        let two = try makeIPA([("Payload/A.app/Info.plist", try infoPlist()), ("Payload/B.app/Info.plist", try infoPlist())], in: directory, name: "two.ipa")
        #expect(throws: ToolkitError.self) { try IPAInspector.inspect(two) }
        let notZip = directory.appendingPathComponent("fake.ipa")
        try SecureFileIO.writeNewFile(Data("not a zip at all, just text".utf8), to: notZip)
        #expect(throws: ToolkitError.self) { try IPAInspector.inspect(notZip) }
        #expect(throws: ToolkitError.self) { try IPAInspector.inspect(directory.appendingPathComponent("file.zip")) }
    }

    @Test func entriesThatExceedTheirDeclaredSizeAreStopped() throws {
        let directory = try SecureFileIO.makeTemporaryDirectory(prefix: "ipa")
        defer { try? FileManager.default.removeItem(at: directory) }
        var writer = ZipWriter()
        try writer.add(name: "Payload/Demo.app/Info.plist", data: try infoPlist())
        try writer.add(name: "Payload/Demo.app/big", data: Data(repeating: 0, count: 100_000))
        var archive = writer.finalized()
        // Lie about the uncompressed size of "big" in both the local and central headers.
        for signature in [Data([0x50, 0x4B, 0x03, 0x04]), Data([0x50, 0x4B, 0x01, 0x02])] {
            var searchStart = archive.startIndex
            while let range = archive.range(of: signature, in: searchStart..<archive.endIndex) {
                let nameOffset = signature[2] == 0x03 ? 30 : 46
                let nameLengthOffset = signature[2] == 0x03 ? 26 : 28
                let sizeOffset = signature[2] == 0x03 ? 22 : 24
                let nameLength = Int(archive[range.lowerBound + nameLengthOffset]) | Int(archive[range.lowerBound + nameLengthOffset + 1]) << 8
                let name = String(decoding: archive[(range.lowerBound + nameOffset)..<(range.lowerBound + nameOffset + nameLength)], as: UTF8.self)
                if name.hasSuffix("/big") {
                    archive[range.lowerBound + sizeOffset] = 0x10
                    archive[range.lowerBound + sizeOffset + 1] = 0x00
                    archive[range.lowerBound + sizeOffset + 2] = 0x00
                    archive[range.lowerBound + sizeOffset + 3] = 0x00
                }
                searchStart = range.upperBound
            }
        }
        let url = directory.appendingPathComponent("bomb.ipa")
        try SecureFileIO.writeNewFile(archive, to: url)
        let zip = try ZipArchive(url: url)
        let entry = try #require(zip.entry(named: "Payload/Demo.app/big"))
        #expect(entry.uncompressedSize == 16)
        #expect(throws: ToolkitError.self) { _ = try zip.data(for: entry, limit: 1_000_000) }
        let target = directory.appendingPathComponent("out")
        try SecureFileIO.createPrivateDirectory(at: target)
        #expect(throws: ToolkitError.self) { try zip.extract(prefix: "Payload/", to: target) }
    }

    @Test(.enabled(if: FileManager.default.isExecutableFile(atPath: "/usr/bin/zip")))
    func symbolicLinksAreRefused() async throws {
        let directory = try SecureFileIO.makeTemporaryDirectory(prefix: "ipa")
        defer { try? FileManager.default.removeItem(at: directory) }
        let app = directory.appendingPathComponent("Payload/Demo.app")
        try FileManager.default.createDirectory(at: app, withIntermediateDirectories: true)
        try infoPlist().write(to: app.appendingPathComponent("Info.plist"))
        try FileManager.default.createSymbolicLink(atPath: app.appendingPathComponent("escape").path, withDestinationPath: "/etc/passwd")
        let result = try await run("/usr/bin/zip", ["-qry", "linked.ipa", "Payload"], in: directory)
        #expect(result.succeeded)
        do {
            _ = try IPAInspector.inspect(directory.appendingPathComponent("linked.ipa"))
            Issue.record("expected refusal")
        } catch let error as ToolkitError {
            #expect(error.message.contains("symbolic link"))
        }
    }

    /// Builds a real ad-hoc-signed bundle, packages it with the system zip tool (deflate), and
    /// checks that SecStaticCode reports it valid — then tampers with it and expects invalid.
    @Test(.enabled(if: FileManager.default.isExecutableFile(atPath: "/usr/bin/zip") && FileManager.default.isExecutableFile(atPath: "/usr/bin/codesign")))
    func realSignatureIsVerifiedAndTamperingDetected() async throws {
        let directory = try SecureFileIO.makeTemporaryDirectory(prefix: "ipa-signed")
        defer { try? FileManager.default.removeItem(at: directory) }
        let app = directory.appendingPathComponent("Payload/Demo.app")
        try FileManager.default.createDirectory(at: app, withIntermediateDirectories: true)
        try infoPlist().write(to: app.appendingPathComponent("Info.plist"))
        try FileManager.default.copyItem(atPath: "/bin/echo", toPath: app.appendingPathComponent("Demo").path)
        try Data("resource".utf8).write(to: app.appendingPathComponent("resource.txt"))
        let signing = try await run("/usr/bin/codesign", ["--force", "--sign", "-", app.path], in: directory)
        #expect(signing.succeeded, "\(signing.standardErrorText)")
        #expect(try await run("/usr/bin/zip", ["-qr", "signed.ipa", "Payload"], in: directory).succeeded)

        let signed = try IPAInspector.inspect(directory.appendingPathComponent("signed.ipa"))
        #expect(signed.signature.status == .valid, "\(signed.signature.detail)")
        #expect(signed.signature.identifier == "com.example.demo")
        #expect(signed.isInstallable)

        try Data("tampered".utf8).write(to: app.appendingPathComponent("resource.txt"))
        #expect(try await run("/usr/bin/zip", ["-qr", "tampered.ipa", "Payload"], in: directory).succeeded)
        let tampered = try IPAInspector.inspect(directory.appendingPathComponent("tampered.ipa"))
        #expect(tampered.signature.status == .invalid)
        #expect(!tampered.isInstallable)
    }

    @Test func provisioningProfileSummaries() throws {
        let invalid = ProvisioningProfileDecoder.decode(Data("not cms".utf8))
        #expect(invalid.status == .invalid)
        let content: PlistValue = [
            "Name": "Team Dev",
            "UUID": "ABC",
            "TeamIdentifier": ["TEAM123"],
            "ProvisionedDevices": ["00008110-001234560ABC801E"],
            "ExpirationDate": .date(Date(timeIntervalSinceNow: -60)),
            "Entitlements": ["application-identifier": "TEAM123.com.example.demo", "get-task-allow": true],
            "DeveloperCertificates": [.data(Data([1]))],
        ]
        let summary = ProvisioningProfileDecoder.summarize(plist: try content.encoded(), signatureVerified: nil)
        #expect(summary.status == .decoded)
        #expect(summary.profileKind == "Development")
        #expect(summary.isExpired)
        #expect(summary.includes(udid: "00008110001234560abc801e"))
        #expect(!summary.includes(udid: "00008110-FFFFFFFFFFFFFFFF"))
        #expect(summary.allowedSignerCount == 1)
        // `idt inspect-ipa --json` keeps the 1.0 key.
        let json = try #require(try JSONSerialization.jsonObject(with: JSONOutput.encode(summary)) as? [String: Any])
        #expect(json["developerCertificateCount"] as? Int == 1)
        #expect(try JSONOutput.decoder().decode(ProvisioningProfileSummary.self, from: JSONOutput.encode(summary)) == summary)
    }

    @Test func zipWriterRoundTripsThroughReader() throws {
        let directory = try SecureFileIO.makeTemporaryDirectory(prefix: "zip")
        defer { try? FileManager.default.removeItem(at: directory) }
        let big = Data((0..<200_000).map { UInt8($0 % 7) })
        let url = try makeIPA([("a.txt", Data("hello".utf8)), ("dir/b.bin", big), ("empty", Data())], in: directory, name: "round.zip")
        let archive = try ZipArchive(url: url)
        #expect(archive.entries.map(\.name) == ["a.txt", "dir/b.bin", "empty"])
        #expect(try archive.data(for: archive.entry(named: "dir/b.bin")!, limit: 1 << 20) == big)
        #expect(try archive.data(for: archive.entry(named: "a.txt")!, limit: 100) == Data("hello".utf8))
        #expect(CRC32.checksum(Data("123456789".utf8)) == 0xCBF4_3926)
    }
}
