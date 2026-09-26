import Foundation
import Testing
@testable import DeviceKit
@testable import ToolkitFeatures
import ToolkitCore

@Suite("Live log capture")
struct LiveLogTests {
    let target = DeviceTarget(kind: .physical, udid: "00008110-001234560ABC801E", name: "Test iPhone", osVersion: "26.0", usbmuxDeviceID: 1, coreDeviceIdentifier: nil, transport: .usb)

    @Test func filtersAreLiteralRegexAndCaseAware() throws {
        #expect(try LogFilter(text: "error").matcher()("An ERROR happened"))
        #expect(try !LogFilter(text: "error", isCaseSensitive: true).matcher()("An ERROR happened"))
        #expect(try LogFilter(text: "a.c").matcher()("xa.cx"))
        #expect(try !LogFilter(text: "a.c").matcher()("abc"))
        #expect(try LogFilter(text: "a.c", isRegularExpression: true).matcher()("abc"))
        #expect(try LogFilter().matcher()("anything"))
        #expect(throws: ToolkitError.self) { try LogFilter(text: "(", isRegularExpression: true).matcher() }
    }

    @Test func tagsAreNormalizedAndBounded() throws {
        #expect(try LiveLogFinding.parseTags(" Network, crash ,network,, ") == ["network", "crash"])
        #expect(throws: ToolkitError.self) { try LiveLogFinding.parseTags("bad tag") }
        #expect(throws: ToolkitError.self) { try LiveLogFinding.parseTags("-leading") }
        #expect(throws: ToolkitError.self) { try LiveLogFinding.parseTags((0..<13).map { "t\($0)" }.joined(separator: ",")) }
    }

    @Test func findingsRequireNoteAndSelection() throws {
        #expect(throws: ToolkitError.self) { try LiveLogFinding.make(note: " ", selectedText: "x", stream: .unified, target: target, rawBytesObserved: 0, filter: LogFilter(), assessment: .lead, tags: []) }
        #expect(throws: ToolkitError.self) { try LiveLogFinding.make(note: "n", selectedText: "", stream: .unified, target: target, rawBytesObserved: 0, filter: LogFilter(), assessment: .lead, tags: []) }
        #expect(throws: ToolkitError.self) { try LiveLogFinding.make(note: "n", selectedText: String(repeating: "x", count: 20_001), stream: .unified, target: target, rawBytesObserved: 0, filter: LogFilter(), assessment: .lead, tags: []) }
    }

    @Test func captureSpoolsHashesAndExports() async throws {
        let directory = try SecureFileIO.makeTemporaryDirectory(prefix: "logs")
        defer { try? FileManager.default.removeItem(at: directory) }
        let capture = try LogCapture(kind: .classic, target: target, directory: directory.appendingPathComponent("spool"))
        let raw = Data("kernel: first line\u{0}SpringBoard: second ERROR\u{0}".utf8)
        var parser = SyslogRecordParser()
        try await capture.append(LogChunk(spoolBytes: raw, lines: parser.consume(raw)))
        try await capture.setInvestigationReference("CASE-42")
        let finding = try LiveLogFinding.make(note: "Crash lead", selectedText: "SpringBoard: second ERROR", stream: .classic, target: target, rawBytesObserved: Int64(raw.count), filter: LogFilter(text: "error"), assessment: .lead, tags: ["crash"])
        try await capture.addFinding(finding)
        try await capture.finish(reason: "Stopped by user")
        try await capture.finish(reason: "second call is ignored")

        let metadata = await capture.currentMetadata
        #expect(metadata.rawBytes == Int64(raw.count))
        #expect(metadata.decodedLines == 2)
        #expect(metadata.rawSHA256 == SecureFileIO.sha256(of: raw))
        #expect(metadata.endReason == "Stopped by user")
        #expect(metadata.findingsCount == 1)
        #expect(metadata.investigationReference == "CASE-42")
        let spoolPermissions = try FileManager.default.attributesOfItem(atPath: await capture.spoolURL.path)[.posixPermissions] as? NSNumber
        #expect(spoolPermissions?.intValue == 0o600)

        let filtered = directory.appendingPathComponent("filtered.log")
        #expect(try await capture.exportFiltered(to: filtered, filter: LogFilter(text: "error")) == 1)
        #expect(try String(contentsOf: filtered, encoding: .utf8) == "SpringBoard: second ERROR\n")
        await #expect(throws: ToolkitError.self) { _ = try await capture.exportFiltered(to: filtered, filter: LogFilter()) }

        let bundle = try await capture.exportEvidenceBundle(into: directory)
        let files = Set(try FileManager.default.contentsOfDirectory(atPath: bundle.path))
        #expect(files.contains("investigation-report.md"))
        #expect(files.contains("SHA256SUMS.txt"))
        #expect(files.contains { $0.hasSuffix(".findings.jsonl") })
        #expect(files.contains { $0.hasSuffix(".meta.json") })
        #expect(try HashManifest.verify(folder: bundle).isEmpty)
        let report = try String(contentsOf: bundle.appendingPathComponent("investigation-report.md"), encoding: .utf8)
        #expect(report.contains("Finding 1: Lead to correlate"))
        #expect(report.contains("not device-generated facts"))
        #expect(report.contains(SecureFileIO.sha256(of: raw)))

        try Data("changed".utf8).write(to: bundle.appendingPathComponent("investigation-report.md"))
        #expect(try HashManifest.verify(folder: bundle) == ["investigation-report.md"])
        await #expect(throws: ToolkitError.self) { _ = try await capture.exportEvidenceBundle(into: directory) }
    }

    @Test func structuredFilteredExportRendersLines() async throws {
        let directory = try SecureFileIO.makeTemporaryDirectory(prefix: "logs")
        defer { try? FileManager.default.removeItem(at: directory) }
        let capture = try LogCapture(kind: .unified, target: target, directory: directory)
        for line in [LogLine(process: "locationd", pid: 5, level: "Error", message: "GPS lost"), LogLine(process: "backboardd", message: "touch")] {
            try await capture.append(LogChunk(spoolBytes: line.jsonLine, lines: [line]))
        }
        try await capture.finish(reason: "done")
        let output = directory.appendingPathComponent("out.log")
        #expect(try await capture.exportFiltered(to: output, filter: LogFilter(text: "locationd")) == 1)
        #expect(try String(contentsOf: output, encoding: .utf8).contains("locationd[5] <Error> GPS lost"))
    }

    @Test func lineSplitterKeepsPartialLines() {
        var splitter = LineSplitter()
        #expect(splitter.consume(Data("one\ntw".utf8)) == ["one"])
        #expect(splitter.consume(Data("o\nthree".utf8)) == ["two"])
        #expect(splitter.flush() == ["three"])
        #expect(splitter.flush().isEmpty)
    }

    @Test func streamKindsMatchDeviceKinds() {
        #expect(LogStreamKind.available(for: .physical) == [.unified, .classic])
        #expect(LogStreamKind.available(for: .simulator) == [.simulator])
        #expect(LogStreamKind.available(for: .demo).isEmpty)
    }
}
