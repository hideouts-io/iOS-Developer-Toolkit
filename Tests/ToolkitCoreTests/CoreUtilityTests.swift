import Foundation
import Testing
@testable import ToolkitCore

@Suite("SecureFileIO")
struct SecureFileIOTests {
    @Test func writeNewFileRefusesToOverwrite() throws {
        let directory = try SecureFileIO.makeTemporaryDirectory(prefix: "secure-io")
        defer { try? FileManager.default.removeItem(at: directory) }
        let file = directory.appendingPathComponent("report.json")
        try SecureFileIO.writeNewFile(Data("one".utf8), to: file)
        #expect(throws: ToolkitError.self) {
            try SecureFileIO.writeNewFile(Data("two".utf8), to: file)
        }
        #expect(try String(contentsOf: file, encoding: .utf8) == "one")
        let permissions = try FileManager.default.attributesOfItem(atPath: file.path)[.posixPermissions] as? NSNumber
        #expect(permissions?.intValue == 0o600)
    }

    @Test func writeNewFileDoesNotFollowSymlinks() throws {
        let directory = try SecureFileIO.makeTemporaryDirectory(prefix: "secure-io")
        defer { try? FileManager.default.removeItem(at: directory) }
        let target = directory.appendingPathComponent("target")
        let link = directory.appendingPathComponent("link")
        try FileManager.default.createSymbolicLink(at: link, withDestinationURL: target)
        #expect(throws: ToolkitError.self) {
            try SecureFileIO.writeNewFile(Data("x".utf8), to: link)
        }
        #expect(!FileManager.default.fileExists(atPath: target.path))
    }

    @Test func temporaryDirectoryIsPrivate() throws {
        let directory = try SecureFileIO.makeTemporaryDirectory(prefix: "secure-io")
        defer { try? FileManager.default.removeItem(at: directory) }
        let permissions = try FileManager.default.attributesOfItem(atPath: directory.path)[.posixPermissions] as? NSNumber
        #expect(permissions?.intValue == 0o700)
    }

    @Test(arguments: ["../escape", "/etc/passwd", "a/../../b", "", "a\u{0}b", "Snapshots/../../x"])
    func safeChildRejectsTraversal(_ path: String) throws {
        let root = URL(fileURLWithPath: "/tmp/root", isDirectory: true)
        #expect(throws: ToolkitError.self) {
            _ = try SecureFileIO.safeChild(of: root, relativePath: path)
        }
    }

    @Test func safeChildAcceptsNestedPaths() throws {
        let root = URL(fileURLWithPath: "/tmp/root", isDirectory: true)
        let child = try SecureFileIO.safeChild(of: root, relativePath: "Snapshot/a/./b.plist")
        #expect(child.path == "/tmp/root/Snapshot/a/b.plist")
    }

    @Test func sha256MatchesKnownVector() throws {
        #expect(SecureFileIO.sha256(of: Data("abc".utf8)) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
        let directory = try SecureFileIO.makeTemporaryDirectory(prefix: "secure-io")
        defer { try? FileManager.default.removeItem(at: directory) }
        let file = directory.appendingPathComponent("abc")
        try SecureFileIO.writeNewFile(Data("abc".utf8), to: file)
        #expect(try SecureFileIO.sha256(of: file) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
        var hasher = StreamingHasher()
        hasher.update(Data("a".utf8))
        hasher.update(Data("bc".utf8))
        #expect(hasher.finalizeHex() == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
        #expect(hasher.byteCount == 3)
    }

    @Test func atomicWriteReplacesContent() throws {
        let directory = try SecureFileIO.makeTemporaryDirectory(prefix: "secure-io")
        defer { try? FileManager.default.removeItem(at: directory) }
        let file = directory.appendingPathComponent("state.json")
        try SecureFileIO.writeAtomically(Data("1".utf8), to: file)
        try SecureFileIO.writeAtomically(Data("2".utf8), to: file)
        #expect(try String(contentsOf: file, encoding: .utf8) == "2")
        let leftovers = try FileManager.default.contentsOfDirectory(atPath: directory.path).filter { $0.hasSuffix(".tmp") }
        #expect(leftovers.isEmpty)
    }
}

@Suite("Sanitizer")
struct SanitizerTests {
    @Test func redactsIdentifiersAndPaths() {
        let input = """
        device 00008110-001234560ABC801E at 192.168.1.20 mac aa:bb:cc:dd:ee:ff
        user me@example.com path /Users/alice/Documents/case.zip uuid 123E4567-E89B-12D3-A456-426614174000
        """
        let output = Sanitizer.sanitize(input, redactions: ["Alice's iPhone"])
        #expect(!output.contains("00008110"))
        #expect(!output.contains("192.168"))
        #expect(!output.contains("aa:bb"))
        #expect(!output.contains("example.com"))
        #expect(!output.contains("/Users/alice"))
        #expect(!output.contains("123E4567"))
        #expect(output.contains("<device-identifier>"))
        #expect(output.contains("<local-path>"))
    }

    @Test func redactsLiteralNamesAndLimitsLength() {
        let output = Sanitizer.sanitize("Alice's iPhone connected", redactions: ["Alice's iPhone"], limit: 12)
        #expect(output == "<redacted> c")
    }

    @Test func fingerprintIsStableAndOneWay() {
        let first = Sanitizer.fingerprint("00008110-001234560ABC801E")
        #expect(first == Sanitizer.fingerprint("00008110-001234560ABC801E"))
        #expect(first != Sanitizer.fingerprint("00008110-001234560ABC801F"))
        #expect(!first.contains("00008110"))
        #expect(first.count == 24)
    }
}

@Suite("PlistValue")
struct PlistValueTests {
    @Test func roundTripsThroughXMLAndBinary() throws {
        let value: PlistValue = [
            "Name": "iPhone",
            "Count": 3,
            "Enabled": true,
            "Ratio": 0.5,
            "Blob": .data(Data([1, 2, 3])),
            "Items": ["a", 1],
        ]
        for format: PropertyListSerialization.PropertyListFormat in [.xml, .binary] {
            let decoded = try PlistValue.decode(try value.encoded(format: format))
            #expect(decoded == value)
        }
    }

    @Test func distinguishesBooleansFromIntegers() throws {
        let data = Data("<?xml version=\"1.0\"?><plist version=\"1.0\"><dict><key>a</key><true/><key>b</key><integer>1</integer></dict></plist>".utf8)
        let decoded = try PlistValue.decode(data)
        #expect(decoded["a"] == .boolean(true))
        #expect(decoded["b"] == .integer(1))
        #expect(decoded["a"]?.intValue == nil)
    }

    @Test func malformedInputThrowsProtocolViolation() {
        do {
            _ = try PlistValue.decode(Data("not a plist".utf8))
            Issue.record("expected failure")
        } catch let error as ToolkitError {
            #expect(error.kind == .protocolViolation)
        } catch {
            Issue.record("unexpected error \(error)")
        }
    }

    @Test func accessorsNeverTrap() {
        let value: PlistValue = ["list": [1, 2]]
        #expect(value["missing"] == nil)
        #expect(value["list"]?[5] == nil)
        #expect(value["list"]?[1]?.intValue == 2)
        #expect(PlistValue.real(.nan).intValue == nil)
        #expect(PlistValue.unsignedInteger(UInt64.max).intValue == nil)
    }
}

@Suite("OperationJournal")
struct OperationJournalTests {
    func record(_ title: String) -> OperationRecord {
        OperationRecord(title: title, workspace: "Apps", target: "Test iPhone", transport: "CoreDevice", argv: ["devicectl", "x"], startedAt: Date(), finishedAt: Date().addingTimeInterval(1), outcome: .succeeded, exitCode: 0, output: Data("out".utf8))
    }

    @Test func boundsCapacity() async {
        let journal = OperationJournal(capacity: 3)
        for index in 0..<5 { await journal.append(record("op \(index)")) }
        let titles = await journal.records.map(\.title)
        #expect(titles == ["op 2", "op 3", "op 4"])
    }

    @Test func manifestOmitsRawOutput() throws {
        let manifest = try record("x").manifestJSON()
        let text = String(decoding: manifest, as: UTF8.self)
        #expect(text.contains("\"raw_output_included\" : false"))
        #expect(!text.contains("\"out\""))
        #expect(text.contains(SecureFileIO.sha256(of: Data("out".utf8))))
    }

    @Test func outcomeMapping() {
        #expect(OperationOutcome.from(ToolkitError.cancelled()) == .cancelled)
        #expect(OperationOutcome.from(ToolkitError.timedOut("x", after: 1)) == .timedOut)
        #expect(OperationOutcome.from(CancellationError()) == .cancelled)
        #expect(OperationOutcome.from(ToolkitError(.toolMissing, message: "")) == .launchFailed)
    }
}
