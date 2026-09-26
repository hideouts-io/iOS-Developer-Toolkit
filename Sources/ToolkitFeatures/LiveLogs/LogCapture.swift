import DeviceKit
import Foundation
import ToolkitCore

/// A text filter for the working view: literal or regular expression, optionally case-sensitive.
public struct LogFilter: Sendable, Hashable, Codable {
    public var text: String
    public var isRegularExpression: Bool
    public var isCaseSensitive: Bool

    public init(text: String = "", isRegularExpression: Bool = false, isCaseSensitive: Bool = false) {
        self.text = text
        self.isRegularExpression = isRegularExpression
        self.isCaseSensitive = isCaseSensitive
    }

    public var isEmpty: Bool { text.isEmpty }

    /// Returns a matcher, or throws an actionable error for an invalid expression.
    public func matcher() throws -> @Sendable (String) -> Bool {
        guard !text.isEmpty else { return { _ in true } }
        if isRegularExpression {
            let expression: NSRegularExpression
            do {
                expression = try NSRegularExpression(pattern: text, options: isCaseSensitive ? [] : [.caseInsensitive])
            } catch {
                throw ToolkitError.invalidInput("The regular expression is not valid.")
            }
            let box = UncheckedSendable(expression)
            return { line in box.value.firstMatch(in: line, range: NSRange(line.startIndex..<line.endIndex, in: line)) != nil }
        }
        let needle = text
        let sensitive = isCaseSensitive
        return { line in sensitive ? line.contains(needle) : line.range(of: needle, options: .caseInsensitive) != nil }
    }
}

/// NSRegularExpression is immutable and thread-safe but not annotated Sendable.
struct UncheckedSendable<Value>: @unchecked Sendable {
    let value: Value
    init(_ value: Value) { self.value = value }
}

public enum FindingAssessment: String, Codable, Sendable, CaseIterable, Identifiable {
    case observation
    case lead
    case needsCorroboration = "needs-corroboration"

    public var id: String { rawValue }

    public var label: String {
        switch self {
        case .observation: return "Observation"
        case .lead: return "Lead to correlate"
        case .needsCorroboration: return "Needs corroboration"
        }
    }
}

/// An analyst annotation on selected log text. It is never mixed into the raw capture.
public struct LiveLogFinding: Codable, Sendable, Hashable, Identifiable {
    public var id: String { createdAt.timeIntervalSince1970.description + note }
    public var createdAt: Date
    public var note: String
    public var selectedText: String
    public var stream: String
    public var deviceIdentifier: String
    public var rawBytesObserved: Int64
    public var filterExpression: String
    public var filterIsRegex: Bool
    public var filterCaseSensitive: Bool
    public var assessment: FindingAssessment
    public var tags: [String]

    enum CodingKeys: String, CodingKey {
        case note, stream, assessment, tags
        case createdAt = "created_at"
        case selectedText = "selected_text"
        case deviceIdentifier = "device_identifier"
        case rawBytesObserved = "raw_bytes_observed"
        case filterExpression = "filter_expression"
        case filterIsRegex = "filter_is_regex"
        case filterCaseSensitive = "filter_case_sensitive"
    }

    public static let maximumSelectedTextLength = 20_000
    public static let maximumTags = 12
    public static let maximumTagLength = 48

    public static func parseTags(_ text: String) throws -> [String] {
        var tags: [String] = []
        for raw in text.split(separator: ",") {
            let tag = raw.trimmingCharacters(in: .whitespaces).lowercased()
            if tag.isEmpty { continue }
            guard tag.count <= maximumTagLength else { throw ToolkitError.invalidInput("Tags must be \(maximumTagLength) characters or fewer.") }
            guard tag.range(of: #"^[a-z0-9][a-z0-9_-]*$"#, options: .regularExpression) != nil else {
                throw ToolkitError.invalidInput("Tags may use lowercase letters, numbers, hyphens, and underscores, and must start with a letter or number.")
            }
            if !tags.contains(tag) { tags.append(tag) }
        }
        guard tags.count <= maximumTags else { throw ToolkitError.invalidInput("A finding can have at most \(maximumTags) tags.") }
        return tags
    }

    public static func make(note: String, selectedText: String, stream: LogStreamKind, target: DeviceTarget, rawBytesObserved: Int64, filter: LogFilter, assessment: FindingAssessment, tags: [String]) throws -> LiveLogFinding {
        let trimmedNote = note.trimmingCharacters(in: .whitespacesAndNewlines)
        let trimmedSelection = selectedText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmedNote.isEmpty else { throw ToolkitError.invalidInput("A finding needs an analyst note.") }
        guard !trimmedSelection.isEmpty else { throw ToolkitError.invalidInput("Select one or more log lines before marking a finding.") }
        guard trimmedSelection.count <= maximumSelectedTextLength else {
            throw ToolkitError.invalidInput("The selected text must be \(maximumSelectedTextLength) characters or fewer.")
        }
        return LiveLogFinding(createdAt: Date(), note: trimmedNote, selectedText: trimmedSelection, stream: stream.rawValue, deviceIdentifier: target.udid, rawBytesObserved: rawBytesObserved, filterExpression: filter.text, filterIsRegex: filter.isRegularExpression, filterCaseSensitive: filter.isCaseSensitive, assessment: assessment, tags: tags)
    }
}

/// Sidecar metadata written next to each raw spool.
public struct LogCaptureMetadata: Codable, Sendable, Hashable {
    public var schemaVersion = 2
    public var stream: String
    public var streamTitle: String
    public var structured: Bool
    public var source: String
    public var deviceIdentifier: String
    public var deviceName: String
    public var deviceKind: String
    public var startedAt: Date
    public var finishedAt: Date?
    public var endReason: String?
    public var rawBytes: Int64
    public var decodedLines: Int
    public var rawPath: String
    public var rawSHA256: String?
    public var findingsPath: String?
    public var findingsCount: Int
    public var investigationReference: String
    public var interpretationBoundary = "Findings are analyst annotations, not device-generated facts or proof of causality."

    enum CodingKeys: String, CodingKey {
        case stream, structured, source
        case schemaVersion = "schema_version"
        case streamTitle = "stream_title"
        case deviceIdentifier = "device_identifier"
        case deviceName = "device_name"
        case deviceKind = "device_kind"
        case startedAt = "started_at"
        case finishedAt = "finished_at"
        case endReason = "end_reason"
        case rawBytes = "raw_bytes"
        case decodedLines = "decoded_lines"
        case rawPath = "raw_path"
        case rawSHA256 = "raw_sha256"
        case findingsPath = "findings_path"
        case findingsCount = "findings_count"
        case investigationReference = "investigation_reference"
        case interpretationBoundary = "interpretation_boundary"
    }
}

/// Spools every byte of a live stream to disk (independently of what the view shows), keeps an
/// incremental SHA-256, and manages findings and exports.
public actor LogCapture {
    public let kind: LogStreamKind
    public let target: DeviceTarget
    public let spoolURL: URL
    public let metadataURL: URL
    public let findingsURL: URL
    private let output: FileHandle
    private var hasher = StreamingHasher()
    private var metadata: LogCaptureMetadata
    private var findings: [LiveLogFinding] = []
    private var closed = false

    public static func defaultDirectory(home: URL = FileManager.default.homeDirectoryForCurrentUser) -> URL {
        home.appendingPathComponent("Library/Caches/iOS Developer Toolkit/Live Logs")
    }

    public init(kind: LogStreamKind, target: DeviceTarget, directory: URL = LogCapture.defaultDirectory(), now: Date = Date()) throws {
        try SecureFileIO.createPrivateDirectory(at: directory)
        let fragment = String(target.udid.filter { $0.isLetter || $0.isNumber || $0 == "-" }.prefix(40))
        let base = "\(ISO8601.compactUTC(now))-\(fragment.isEmpty ? "device" : fragment)-\(kind.rawValue)-\(UUID().uuidString.prefix(6))"
        spoolURL = directory.appendingPathComponent("\(base).\(kind.spoolExtension)")
        metadataURL = directory.appendingPathComponent("\(base).meta.json")
        findingsURL = directory.appendingPathComponent("\(base).\(kind.spoolExtension).findings.jsonl")
        try SecureFileIO.writeNewFile(Data(), to: spoolURL)
        guard let handle = try? FileHandle(forWritingTo: spoolURL) else {
            throw ToolkitError.fileSystem("The log spool could not be opened.", path: spoolURL.path)
        }
        output = handle
        self.kind = kind
        self.target = target
        let initial = LogCaptureMetadata(stream: kind.rawValue, streamTitle: kind.title, structured: kind.isStructured, source: kind.serviceDescription, deviceIdentifier: target.udid, deviceName: target.name, deviceKind: target.kind.rawValue, startedAt: now, rawBytes: 0, decodedLines: 0, rawPath: spoolURL.path, findingsCount: 0, investigationReference: "")
        metadata = initial
        try SecureFileIO.writeAtomically(try JSONOutput.encode(initial), to: metadataURL)
    }

    public var currentMetadata: LogCaptureMetadata { metadata }
    public var allFindings: [LiveLogFinding] { findings }
    public var isClosed: Bool { closed }

    public func append(_ chunk: LogChunk) throws {
        guard !closed else { return }
        if !chunk.spoolBytes.isEmpty {
            try output.write(contentsOf: chunk.spoolBytes)
            hasher.update(chunk.spoolBytes)
            metadata.rawBytes += Int64(chunk.spoolBytes.count)
        }
        metadata.decodedLines += chunk.lines.count
    }

    public func setInvestigationReference(_ reference: String) throws {
        metadata.investigationReference = String(reference.trimmingCharacters(in: .whitespacesAndNewlines).prefix(200))
        try writeMetadata()
    }

    /// Stops spooling, records why, and finalizes the hash. Safe to call more than once.
    public func finish(reason: String) throws {
        guard !closed else { return }
        closed = true
        try? output.synchronize()
        try? output.close()
        metadata.finishedAt = Date()
        metadata.endReason = reason
        metadata.rawSHA256 = hasher.finalizeHex()
        try writeMetadata()
    }

    public func addFinding(_ finding: LiveLogFinding) throws {
        var line = try JSONOutput.encode(finding)
        if line.last == 0x0A { line.removeLast() }
        line = Data(String(decoding: line, as: UTF8.self).replacingOccurrences(of: "\n", with: "").utf8) + Data([0x0A])
        try SecureFileIO.append(line, to: findingsURL, synchronize: true)
        findings.append(finding)
        metadata.findingsPath = findingsURL.path
        metadata.findingsCount = findings.count
        try writeMetadata()
    }

    private func writeMetadata() throws {
        try SecureFileIO.writeAtomically(try JSONOutput.encode(metadata), to: metadataURL)
    }

    // MARK: Exports

    /// Saves the complete raw capture plus metadata and findings (never overwriting).
    @discardableResult
    public func exportRaw(to destination: URL) throws -> String {
        try? output.synchronize()
        let data = try Data(contentsOf: spoolURL)
        try SecureFileIO.writeNewFile(data, to: destination)
        var saved = metadata
        saved.rawPath = destination.path
        saved.rawSHA256 = SecureFileIO.sha256(of: data)
        if FileManager.default.fileExists(atPath: findingsURL.path) {
            let findingsDestination = destination.appendingPathExtension("findings.jsonl")
            try SecureFileIO.writeNewFile(try Data(contentsOf: findingsURL), to: findingsDestination)
            saved.findingsPath = findingsDestination.path
        } else {
            saved.findingsPath = nil
        }
        try SecureFileIO.writeNewFile(try JSONOutput.encode(saved), to: destination.appendingPathExtension("meta.json"))
        return saved.rawSHA256 ?? ""
    }

    /// Saves only lines that match `filter`, rendered as text.
    public func exportFiltered(to destination: URL, filter: LogFilter) throws -> Int {
        try? output.synchronize()
        let matches = try filter.matcher()
        let data = try Data(contentsOf: spoolURL)
        var lines: [String] = []
        switch kind {
        case .classic:
            var parser = SyslogRecordParser()
            lines = (parser.consume(data) + parser.flush()).map(\.message)
        case .unified, .simulator:
            var splitter = LineSplitter()
            let raw = splitter.consume(data) + splitter.flush()
            lines = raw.compactMap { line -> String? in
                if kind == .simulator { return SimulatorLogParser.parse(line: line)?.rendered }
                guard let json = try? JSONValue.parse(Data(line.utf8)) else { return String(line) }
                return LogLine(timestamp: json["timestamp"]?.string.flatMap(ISO8601.parse), process: json["process"]?.string, pid: json["pid"]?.int, level: json["level"]?.string, subsystem: json["subsystem"]?.string, category: json["category"]?.string, message: json["message"]?.string ?? "").rendered
            }
        }
        let kept = lines.filter(matches)
        try SecureFileIO.writeNewFile(Data((kept.joined(separator: "\n") + (kept.isEmpty ? "" : "\n")).utf8), to: destination)
        return kept.count
    }

    /// Writes raw capture, metadata, findings, a readable report, and SHA256SUMS.txt into a new
    /// folder inside `parent`.
    public func exportEvidenceBundle(into parent: URL) throws -> URL {
        let folder = parent.appendingPathComponent(spoolURL.deletingPathExtension().lastPathComponent + "-investigation")
        try SecureFileIO.createNewPrivateDirectory(at: folder)
        let rawDestination = folder.appendingPathComponent(spoolURL.lastPathComponent)
        let rawHash = try exportRaw(to: rawDestination)
        let report = Self.renderReport(metadata: metadata, rawFilename: rawDestination.lastPathComponent, rawSHA256: rawHash, findings: findings)
        try SecureFileIO.writeNewFile(Data(report.utf8), to: folder.appendingPathComponent("investigation-report.md"))
        try HashManifest.write(for: folder)
        return folder
    }

    public static func renderReport(metadata: LogCaptureMetadata, rawFilename: String, rawSHA256: String?, findings: [LiveLogFinding]) -> String {
        var lines = [
            "# \(metadata.streamTitle) investigation report",
            "",
            "## Capture facts",
            "",
            "- Stream: `\(metadata.stream)` (\(metadata.source))",
            "- Device: `\(metadata.deviceName)` (`\(metadata.deviceIdentifier)`, \(metadata.deviceKind))",
            "- Capture started: `\(ISO8601.string(metadata.startedAt))`",
            "- Capture finished: `\(metadata.finishedAt.map(ISO8601.string) ?? "not finalized at export")`",
            "- Raw artifact: `\(rawFilename)`",
            "- Raw SHA-256: `\(rawSHA256 ?? "not finalized at export")`",
            "- Raw bytes observed: `\(metadata.rawBytes)`",
            "- Decoded lines observed: `\(metadata.decodedLines)`",
            "- Investigation reference: \(metadata.investigationReference.isEmpty ? "not provided" : metadata.investigationReference)",
            "",
            "## Analyst findings",
            "",
            "The records below are analyst annotations. They are not device-generated facts, proof of causality, or proof that a selected text fragment represents the complete event.",
            "",
        ]
        guard !findings.isEmpty else {
            lines.append("No analyst findings were recorded for this capture.")
            return lines.joined(separator: "\n") + "\n"
        }
        for (index, finding) in findings.enumerated() {
            lines += [
                "### Finding \(index + 1): \(finding.assessment.label)",
                "",
                "- Recorded: `\(ISO8601.string(finding.createdAt))`",
                "- Tags: \(finding.tags.isEmpty ? "none" : finding.tags.joined(separator: ", "))",
                "- Capture position: `\(finding.rawBytesObserved)` raw bytes observed",
                "- View filter: `\(finding.filterExpression.isEmpty ? "none" : finding.filterExpression)`; regex=`\(finding.filterIsRegex)`; case-sensitive=`\(finding.filterCaseSensitive)`",
                "- Analyst note: \(finding.note)",
                "",
                "Selected visible text:",
                "```text",
                finding.selectedText,
                "```",
                "",
            ]
        }
        return lines.joined(separator: "\n") + "\n"
    }
}

/// Writes `SHA256SUMS.txt` for every regular file in a folder (recursively).
public enum HashManifest {
    public static let fileName = "SHA256SUMS.txt"

    @discardableResult
    public static func write(for folder: URL, fileName: String = HashManifest.fileName) throws -> URL {
        let manifest = folder.appendingPathComponent(fileName)
        let lines = try SecureFileIO.regularFiles(under: folder)
            .filter { $0.relativePath != fileName }
            .map { "\(try SecureFileIO.sha256(of: $0.url))  \($0.relativePath)" }
        try SecureFileIO.writeAtomically(Data((lines.joined(separator: "\n") + "\n").utf8), to: manifest)
        return manifest
    }

    /// Verifies a manifest; returns the relative paths that do not match.
    public static func verify(folder: URL, fileName: String = HashManifest.fileName) throws -> [String] {
        let text = try String(contentsOf: folder.appendingPathComponent(fileName), encoding: .utf8)
        var mismatches: [String] = []
        for line in text.split(separator: "\n") {
            let parts = line.split(separator: " ", maxSplits: 1)
            guard parts.count == 2 else { continue }
            let path = parts[1].trimmingCharacters(in: .whitespaces)
            let url = try SecureFileIO.safeChild(of: folder, relativePath: path)
            if (try? SecureFileIO.sha256(of: url)) != String(parts[0]) { mismatches.append(path) }
        }
        return mismatches
    }
}
