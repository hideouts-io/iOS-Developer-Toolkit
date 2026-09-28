import Foundation

/// How a device or host operation ended.
public enum OperationOutcome: String, Codable, Sendable, CaseIterable {
    case succeeded
    case failed
    case cancelled
    case timedOut = "timed-out"
    case launchFailed = "launch-failed"

    public var label: String {
        switch self {
        case .succeeded: return "Succeeded"
        case .failed: return "Failed"
        case .cancelled: return "Cancelled"
        case .timedOut: return "Timed out"
        case .launchFailed: return "Could not start"
        }
    }

    public static func from(_ error: Error) -> OperationOutcome {
        guard let toolkitError = error as? ToolkitError else {
            return error is CancellationError ? .cancelled : .failed
        }
        switch toolkitError.kind {
        case .cancelled: return .cancelled
        case .timedOut: return .timedOut
        case .toolMissing: return .launchFailed
        default: return .failed
        }
    }
}

/// One completed operation in the session journal ("Session Activity").
///
/// Raw output is never stored; only its size and SHA-256, so the journal can be exported
/// without embedding device content.
public struct OperationRecord: Identifiable, Codable, Sendable, Hashable {
    public let id: String
    public let title: String
    public let workspace: String
    public let target: String
    public let transport: String
    public let argv: [String]
    public let startedAt: Date
    public let finishedAt: Date
    public let outcome: OperationOutcome
    public let exitCode: Int32?
    public let errorMessage: String?
    public let outputBytes: Int
    public let outputSHA256: String
    public let errorOutputBytes: Int
    public let errorOutputSHA256: String
    public let prerequisites: [String]
    public let outputPaths: [String]

    public var durationMilliseconds: Int {
        max(0, Int((finishedAt.timeIntervalSince(startedAt) * 1000).rounded()))
    }

    public init(
        title: String,
        workspace: String,
        target: String,
        transport: String,
        argv: [String] = [],
        startedAt: Date,
        finishedAt: Date,
        outcome: OperationOutcome,
        exitCode: Int32? = nil,
        errorMessage: String? = nil,
        output: Data = Data(),
        errorOutput: Data = Data(),
        prerequisites: [String] = [],
        outputPaths: [String] = []
    ) {
        let identity = ([workspace, title, ISO8601.string(startedAt), ISO8601.string(finishedAt), UUID().uuidString] + argv).joined(separator: "\u{0}")
        self.id = String(SecureFileIO.sha256(of: Data(identity.utf8)).prefix(16))
        self.title = title
        self.workspace = workspace
        self.target = target
        self.transport = transport
        self.argv = argv
        self.startedAt = startedAt
        self.finishedAt = max(finishedAt, startedAt)
        self.outcome = outcome
        self.exitCode = exitCode
        self.errorMessage = errorMessage
        self.outputBytes = output.count
        self.outputSHA256 = SecureFileIO.sha256(of: output)
        self.errorOutputBytes = errorOutput.count
        self.errorOutputSHA256 = SecureFileIO.sha256(of: errorOutput)
        self.prerequisites = prerequisites
        self.outputPaths = outputPaths
    }

    /// The explicit-export manifest. It never embeds raw output.
    public func manifestJSON() throws -> Data {
        let manifest: [String: Any] = [
            "schema_version": 2,
            "operation_id": id,
            "title": title,
            "workspace": workspace,
            "target": target,
            "transport": transport,
            "argv": argv,
            "timing": [
                "started_at": ISO8601.string(startedAt),
                "finished_at": ISO8601.string(finishedAt),
                "duration_milliseconds": durationMilliseconds,
            ],
            "result": [
                "outcome": outcome.rawValue,
                "exit_code": exitCode.map { NSNumber(value: $0) } ?? NSNull(),
                "error_message": errorMessage ?? NSNull(),
            ] as [String: Any],
            "captured_output": [
                "stdout_bytes": outputBytes,
                "stdout_sha256": outputSHA256,
                "stderr_bytes": errorOutputBytes,
                "stderr_sha256": errorOutputSHA256,
                "raw_output_included": false,
            ] as [String: Any],
            "prerequisites": prerequisites,
            "output_paths": outputPaths,
            "privacy_notice": "This user-exported manifest omits raw output but may contain device identifiers, local paths, and other sensitive values from the argument vector and target label.",
        ]
        return try JSONSerialization.data(withJSONObject: manifest, options: [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes])
    }
}

/// The in-memory session journal. Nothing is written to disk automatically.
public actor OperationJournal {
    public static let defaultCapacity = 250
    private let capacity: Int
    private var storage: [OperationRecord] = []
    private var observers: [UUID: AsyncStream<[OperationRecord]>.Continuation] = [:]

    public init(capacity: Int = OperationJournal.defaultCapacity) {
        self.capacity = max(1, capacity)
    }

    public var records: [OperationRecord] { storage }

    public func append(_ record: OperationRecord) {
        guard !storage.contains(where: { $0.id == record.id }) else { return }
        storage.append(record)
        if storage.count > capacity {
            storage.removeFirst(storage.count - capacity)
        }
        for observer in observers.values { observer.yield(storage) }
    }

    public func clear() {
        storage.removeAll()
        for observer in observers.values { observer.yield(storage) }
    }

    public func updates() -> AsyncStream<[OperationRecord]> {
        let identifier = UUID()
        let (stream, continuation) = AsyncStream<[OperationRecord]>.makeStream(bufferingPolicy: .bufferingNewest(1))
        continuation.yield(storage)
        observers[identifier] = continuation
        continuation.onTermination = { [weak self] _ in
            Task { await self?.removeObserver(identifier) }
        }
        return stream
    }

    private func removeObserver(_ identifier: UUID) {
        observers[identifier] = nil
    }
}

public enum ISO8601 {
    public static func string(_ date: Date) -> String {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter.string(from: date)
    }

    public static func compactUTC(_ date: Date) -> String {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = TimeZone(identifier: "UTC")
        formatter.dateFormat = "yyyyMMdd'T'HHmmss'Z'"
        return formatter.string(from: date)
    }

    public static func parse(_ value: String) -> Date? {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = formatter.date(from: value) { return date }
        formatter.formatOptions = [.withInternetDateTime]
        return formatter.date(from: value)
    }
}

public enum ByteFormatting {
    /// Decimal units, matching Finder.
    public static func string(_ bytes: Int64?) -> String {
        guard let bytes else { return "—" }
        return ByteCountFormatter.string(fromByteCount: bytes, countStyle: .file)
    }
}

public enum JSONOutput {
    /// Deterministic pretty JSON for files the toolkit writes.
    public static func encode<T: Encodable>(_ value: T) throws -> Data {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
        encoder.dateEncodingStrategy = .iso8601
        var data = try encoder.encode(value)
        data.append(0x0A)
        return data
    }

    public static func decoder() -> JSONDecoder {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        return decoder
    }
}
