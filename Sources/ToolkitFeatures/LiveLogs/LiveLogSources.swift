import DeviceKit
import Foundation
import ToolkitCore

/// The log streams the toolkit can open.
public enum LogStreamKind: String, CaseIterable, Sendable, Codable, Identifiable {
    case unified
    case classic
    case simulator

    public var id: String { rawValue }

    public var title: String {
        switch self {
        case .unified: return "Unified Logs"
        case .classic: return "Classic Syslog"
        case .simulator: return "Simulator Logs"
        }
    }

    public var summary: String {
        switch self {
        case .unified: return "Structured Unified Logging with process, level, subsystem, and category. Works on any trusted device; no developer image needed."
        case .classic: return "The older plain-text syslog relay. Useful for tools that expect traditional syslog lines."
        case .simulator: return "The simulator's Unified Log, streamed with `log stream`."
        }
    }

    public var isStructured: Bool { self != .classic }
    public var spoolExtension: String { isStructured ? "jsonl" : "log" }

    public var serviceDescription: String {
        switch self {
        case .unified: return "com.apple.os_trace_relay (lockdown)"
        case .classic: return "com.apple.syslog_relay (lockdown)"
        case .simulator: return "xcrun simctl spawn <udid> log stream --style ndjson"
        }
    }

    public static func available(for kind: DeviceKind) -> [LogStreamKind] {
        switch kind {
        case .physical: return [.unified, .classic]
        case .simulator: return [.simulator]
        case .demo: return []
        }
    }
}

/// Opens a live log stream for a target.
public enum LiveLogSource {
    public static func open(_ kind: LogStreamKind, target: DeviceTarget, runner: CommandRunning = ProcessCommandRunner(), usbmux: USBMuxClient = USBMuxClient()) async throws -> AsyncThrowingStream<LogChunk, Error> {
        switch kind {
        case .unified, .classic:
            guard target.kind == .physical else { throw ToolkitError(.unsupported, message: "\(kind.title) are only available for physical devices.") }
            let session = try await DeviceSession.open(target: target, usbmux: usbmux)
            let upstream: AsyncThrowingStream<LogChunk, Error>
            do {
                upstream = kind == .unified ? try await OSTraceRelay.stream(session) : try await SyslogRelay.stream(session)
            } catch {
                await session.close()
                throw error
            }
            return relay(upstream) { await session.close() }
        case .simulator:
            let request = try SimulatorClient(runner: runner).logStreamRequest(target)
            let events = runner.stream(request)
            return AsyncThrowingStream { continuation in
                let task = Task {
                    var splitter = LineSplitter()
                    do {
                        for try await event in events {
                            switch event {
                            case .standardOutput(let data):
                                let lines = splitter.consume(data).compactMap(SimulatorLogParser.parse(line:))
                                continuation.yield(LogChunk(spoolBytes: data, lines: lines))
                            case .standardError(let data):
                                let text = String(decoding: data, as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines)
                                if !text.isEmpty { continuation.yield(LogChunk(spoolBytes: Data(), lines: [LogLine(level: "stderr", message: text)])) }
                            case .finished(let result):
                                if !result.succeeded, !Task.isCancelled {
                                    throw ToolkitError(.commandFailed, message: "The simulator log stream stopped.", recovery: "Make sure the simulator is running, then start the stream again.", technicalDetail: result.technicalSummary)
                                }
                            }
                        }
                        let remainder = splitter.flush().compactMap(SimulatorLogParser.parse(line:))
                        if !remainder.isEmpty { continuation.yield(LogChunk(spoolBytes: Data(), lines: remainder)) }
                        continuation.finish()
                    } catch {
                        continuation.finish(throwing: error)
                    }
                }
                continuation.onTermination = { _ in task.cancel() }
            }
        }
    }

    /// Forwards chunks and runs `cleanup` when the consumer stops or the stream ends.
    static func relay(_ upstream: AsyncThrowingStream<LogChunk, Error>, cleanup: @escaping @Sendable () async -> Void) -> AsyncThrowingStream<LogChunk, Error> {
        AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    for try await chunk in upstream { continuation.yield(chunk) }
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
                await cleanup()
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }
}

/// Splits a byte stream into UTF-8 lines, keeping partial lines between chunks.
public struct LineSplitter: Sendable {
    private var pending = Data()
    public var maximumLineLength = 1 << 20

    public init() {}

    public mutating func consume(_ data: Data) -> [Substring] {
        pending.append(data)
        var lines: [Substring] = []
        while let newline = pending.firstIndex(of: 0x0A) {
            let line = String(decoding: pending[pending.startIndex..<newline], as: UTF8.self)
            lines.append(Substring(line))
            pending.removeSubrange(pending.startIndex...newline)
        }
        if pending.count > maximumLineLength {
            lines.append(Substring(String(decoding: pending, as: UTF8.self)))
            pending.removeAll()
        }
        return lines
    }

    public mutating func flush() -> [Substring] {
        defer { pending.removeAll() }
        return pending.isEmpty ? [] : [Substring(String(decoding: pending, as: UTF8.self))]
    }
}
