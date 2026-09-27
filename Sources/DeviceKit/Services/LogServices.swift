import Foundation
import ToolkitCore

/// One decoded log line for display. `fields` carries structured values when the source has
/// them (Unified Logging); classic syslog lines only have `text`.
public struct LogLine: Sendable, Hashable {
    public var timestamp: Date?
    public var process: String?
    public var pid: Int?
    public var level: String?
    public var subsystem: String?
    public var category: String?
    public var message: String

    public init(timestamp: Date? = nil, process: String? = nil, pid: Int? = nil, level: String? = nil, subsystem: String? = nil, category: String? = nil, message: String) {
        self.timestamp = timestamp
        self.process = process
        self.pid = pid
        self.level = level
        self.subsystem = subsystem
        self.category = category
        self.message = message
    }

    /// A single-line rendering used by the working view and filtered exports.
    public var rendered: String {
        var parts: [String] = []
        if let timestamp { parts.append(LogLine.timeFormatter.string(from: timestamp)) }
        if let process {
            parts.append(pid.map { "\(process)[\($0)]" } ?? process)
        }
        if let level { parts.append("<\(level)>") }
        if let subsystem, !subsystem.isEmpty {
            parts.append(category.map { "[\(subsystem):\($0)]" } ?? "[\(subsystem)]")
        }
        parts.append(message)
        return parts.joined(separator: " ")
    }

    /// A JSON object for the structured spool (one per line).
    public var jsonLine: Data {
        var object: [String: Any] = ["message": message]
        if let timestamp { object["timestamp"] = ISO8601.string(timestamp) }
        if let process { object["process"] = process }
        if let pid { object["pid"] = pid }
        if let level { object["level"] = level }
        if let subsystem { object["subsystem"] = subsystem }
        if let category { object["category"] = category }
        var data = (try? JSONSerialization.data(withJSONObject: object, options: [.sortedKeys, .withoutEscapingSlashes])) ?? Data()
        data.append(0x0A)
        return data
    }

    static let timeFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "yyyy-MM-dd HH:mm:ss.SSSSSS"
        return formatter
    }()
}

/// A chunk from a live log source: the bytes to spool verbatim plus decoded lines for display.
public struct LogChunk: Sendable {
    public var spoolBytes: Data
    public var lines: [LogLine]

    public init(spoolBytes: Data, lines: [LogLine]) {
        self.spoolBytes = spoolBytes
        self.lines = lines
    }
}

// MARK: - Classic syslog (com.apple.syslog_relay)

/// Streams the classic syslog relay: NUL-terminated text records. The spool keeps the exact
/// bytes the device sent.
public enum SyslogRelay {
    public static let serviceName = "com.apple.syslog_relay"

    public static func stream(_ session: DeviceSession) async throws -> AsyncThrowingStream<LogChunk, Error> {
        let service = try await session.openService(serviceName)
        return AsyncThrowingStream { continuation in
            let task = Task {
                var parser = SyslogRecordParser()
                do {
                    while !Task.isCancelled, let data = try await service.channel.readSome() {
                        continuation.yield(LogChunk(spoolBytes: data, lines: parser.consume(data)))
                    }
                    let remainder = parser.flush()
                    if !remainder.isEmpty { continuation.yield(LogChunk(spoolBytes: Data(), lines: remainder)) }
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
                await service.close()
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }
}

/// Splits the syslog relay byte stream into records and decodes the escaping it uses.
public struct SyslogRecordParser: Sendable {
    private var pending = Data()
    /// Upper bound for one record, so a missing terminator cannot grow memory without limit.
    public var maximumRecordLength = 64 * 1024

    public init() {}

    public mutating func consume(_ data: Data) -> [LogLine] {
        pending.append(data)
        var lines: [LogLine] = []
        while let terminator = pending.firstIndex(of: 0) {
            let record = pending[pending.startIndex..<terminator]
            pending.removeSubrange(pending.startIndex...terminator)
            lines.append(contentsOf: Self.decode(record))
        }
        if pending.count > maximumRecordLength {
            lines.append(contentsOf: Self.decode(pending))
            pending.removeAll()
        }
        return lines
    }

    public mutating func flush() -> [LogLine] {
        defer { pending.removeAll() }
        return pending.isEmpty ? [] : Self.decode(pending)
    }

    static func decode(_ record: Data) -> [LogLine] {
        let text = unescape(String(decoding: record, as: UTF8.self))
        return text.split(separator: "\n", omittingEmptySubsequences: true).map { LogLine(message: String($0)) }
    }

    /// The relay escapes non-printable bytes as `\\` sequences (e.g. `\M-^@`, `\^[`, `\134`).
    /// Common sequences are restored; unknown ones are left visible rather than guessed.
    static func unescape(_ text: String) -> String {
        guard text.contains("\\") else { return text }
        var result = text
        result = result.replacingOccurrences(of: "\\134", with: "\\")
        result = result.replacingOccurrences(of: "\\^[", with: "")
        result = result.replacingOccurrences(of: "\\M-^@", with: "")
        return result
    }
}

// MARK: - Unified Logging (com.apple.os_trace_relay)

/// Streams structured Unified Logging records. The device sends a length-prefixed plist reply
/// followed by binary records (`0x02`, 32-bit little-endian length, payload). Each payload is
/// decoded into process, level, subsystem, category, and message; the spool stores the decoded
/// records as JSON lines.
public enum OSTraceRelay {
    public static let serviceName = "com.apple.os_trace_relay"

    public static func stream(_ session: DeviceSession, pid: Int = -1) async throws -> AsyncThrowingStream<LogChunk, Error> {
        let service = try await session.openService(serviceName)
        let request: PlistValue = ["Request": "StartActivity", "MessageFilter": 65_535, "Pid": .integer(Int64(pid)), "StreamFlags": 60]
        try await service.messages.send(request, format: .binary)
        let status = try await readStartReply(service.channel)
        guard status["Status"]?.stringValue == "RequestSuccessful" else {
            await service.close()
            throw ToolkitError(.serviceUnavailable, message: "The device declined to start the Unified Logging stream.", technicalDetail: status.prettyJSONString())
        }
        return AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    while !Task.isCancelled, try await service.channel.hasMoreData() {
                        let marker = try await service.channel.read(exactly: 1, timeout: nil)
                        guard marker.first == 0x02 else {
                            throw ToolkitError(.protocolViolation, message: "The Unified Logging stream lost synchronization.", technicalDetail: "marker=\(marker.first ?? 0)")
                        }
                        let length = Int(try await service.channel.read(exactly: 4, timeout: nil).readLittleEndianUInt32(at: 0))
                        guard length > 0, length <= 1_048_576 else {
                            throw ToolkitError(.protocolViolation, message: "The device sent an invalid log record.", technicalDetail: "length=\(length)")
                        }
                        let payload = try await service.channel.read(exactly: length, timeout: 60)
                        let line = OSTraceRecordParser.parse(payload)
                        continuation.yield(LogChunk(spoolBytes: line.jsonLine, lines: [line]))
                    }
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
                await service.close()
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }

    /// The device's running processes (`PidList`). Works over lockdown on any trusted device,
    /// without Xcode or a developer image. The reply is one leading byte, then a 4-byte big-endian
    /// length and a plist whose `Payload` maps each process ID to details such as `ProcessName`.
    public static func processList(_ session: DeviceSession) async throws -> [DeviceProcessInfo] {
        let service = try await session.openService(serviceName)
        defer { Task { await service.close() } }
        try await service.messages.send(["Request": "PidList"])
        _ = try await service.channel.read(exactly: 1, timeout: 30)
        let length = Int(try await service.channel.read(exactly: 4, timeout: 30).readBigEndianUInt32(at: 0))
        guard length > 0, length <= 16 << 20 else {
            throw ToolkitError(.protocolViolation, message: "The device sent an invalid process list.", technicalDetail: "length=\(length)")
        }
        return try parseProcessList(PlistValue.decode(try await service.channel.read(exactly: length, timeout: 60)))
    }

    static func parseProcessList(_ reply: PlistValue) throws -> [DeviceProcessInfo] {
        guard let payload = reply["Payload"]?.dictionaryValue else {
            throw ToolkitError(.serviceUnavailable, message: "The device did not return its process list.", recovery: "Unlock the device and try again.", technicalDetail: reply.prettyJSONString())
        }
        return payload.compactMap { key, value -> DeviceProcessInfo? in
            guard let pid = Int(key) else { return nil }
            return DeviceProcessInfo(pid: pid, name: value["ProcessName"]?.stringValue ?? "PID \(pid)")
        }.sorted { $0.pid < $1.pid }
    }

    /// The start reply is a plist preceded by a 4-byte little-endian size-of-length field and
    /// a little-endian length of that size.
    static func readStartReply(_ channel: DeviceChannel) async throws -> PlistValue {
        let lengthSize = Int(try await channel.read(exactly: 4).readLittleEndianUInt32(at: 0))
        guard (1...8).contains(lengthSize) else {
            throw ToolkitError(.protocolViolation, message: "The Unified Logging service replied unexpectedly.", technicalDetail: "length size \(lengthSize)")
        }
        let lengthBytes = try await channel.read(exactly: lengthSize)
        var length = 0
        for (index, byte) in lengthBytes.enumerated() { length |= Int(byte) << (8 * index) }
        guard length > 0, length <= 1_048_576 else {
            throw ToolkitError(.protocolViolation, message: "The Unified Logging service replied with an invalid length.")
        }
        return try PlistValue.decode(try await channel.read(exactly: length))
    }
}

/// Decodes one os_trace_relay record. All offsets are bounds-checked; if a record does not have
/// the expected layout, the printable text inside it is returned so nothing is silently lost.
public enum OSTraceRecordParser {
    static let pidOffset = 9
    static let secondsOffset = 55
    static let microsecondsOffset = 63
    static let levelOffset = 68
    static let imageNameSizeOffset = 107
    static let messageSizeOffset = 109
    static let subsystemSizeOffset = 117
    static let categorySizeOffset = 121
    static let stringsOffset = 129

    public static func parse(_ record: Data) -> LogLine {
        let bytes = [UInt8](record)
        guard bytes.count > stringsOffset else { return fallback(bytes) }
        let pid = Int(readUInt32(bytes, pidOffset))
        let seconds = TimeInterval(readUInt32(bytes, secondsOffset))
        let microseconds = TimeInterval(readUInt32(bytes, microsecondsOffset))
        let level = levelName(bytes[levelOffset])
        let imageNameSize = Int(readUInt16(bytes, imageNameSizeOffset))
        let messageSize = Int(readUInt16(bytes, messageSizeOffset))
        let subsystemSize = Int(readUInt32(bytes, subsystemSizeOffset))
        let categorySize = Int(readUInt32(bytes, categorySizeOffset))

        var cursor = stringsOffset
        guard let filename = readCString(bytes, &cursor),
              let imageName = readSized(bytes, &cursor, imageNameSize),
              let message = readSized(bytes, &cursor, messageSize)
        else { return fallback(bytes) }
        var subsystem: String?
        var category: String?
        if subsystemSize > 0 {
            subsystem = readSized(bytes, &cursor, subsystemSize)
            category = readSized(bytes, &cursor, categorySize)
        }
        let process = URL(fileURLWithPath: filename.isEmpty ? imageName : filename).lastPathComponent
        let timestamp = seconds > 0 ? Date(timeIntervalSince1970: seconds + microseconds / 1_000_000) : nil
        return LogLine(timestamp: timestamp, process: process.isEmpty ? nil : process, pid: pid, level: level, subsystem: subsystem, category: category, message: message)
    }

    static func levelName(_ value: UInt8) -> String {
        switch value {
        case 0x00: return "Notice"
        case 0x01: return "Info"
        case 0x02: return "Debug"
        case 0x03: return "User Action"
        case 0x10: return "Error"
        case 0x11: return "Fault"
        default: return "Default"
        }
    }

    static func readUInt32(_ bytes: [UInt8], _ offset: Int) -> UInt32 {
        guard offset + 4 <= bytes.count else { return 0 }
        return UInt32(bytes[offset]) | UInt32(bytes[offset + 1]) << 8 | UInt32(bytes[offset + 2]) << 16 | UInt32(bytes[offset + 3]) << 24
    }

    static func readUInt16(_ bytes: [UInt8], _ offset: Int) -> UInt16 {
        guard offset + 2 <= bytes.count else { return 0 }
        return UInt16(bytes[offset]) | UInt16(bytes[offset + 1]) << 8
    }

    static func readCString(_ bytes: [UInt8], _ cursor: inout Int) -> String? {
        guard cursor < bytes.count, let end = bytes[cursor...].firstIndex(of: 0) else { return nil }
        let value = String(decoding: bytes[cursor..<end], as: UTF8.self)
        cursor = end + 1
        return value
    }

    /// Reads a size-prefixed string that may include a trailing NUL.
    static func readSized(_ bytes: [UInt8], _ cursor: inout Int, _ size: Int) -> String? {
        guard size >= 0, cursor + size <= bytes.count else { return nil }
        var slice = bytes[cursor..<(cursor + size)]
        cursor += size
        while let last = slice.last, last == 0 { slice = slice.dropLast() }
        return String(decoding: slice, as: UTF8.self)
    }

    static func fallback(_ bytes: [UInt8]) -> LogLine {
        var runs: [String] = []
        var current: [UInt8] = []
        for byte in bytes {
            if byte >= 0x20 && byte < 0x7F || byte >= 0x80 {
                current.append(byte)
            } else {
                if current.count >= 4 { runs.append(String(decoding: current, as: UTF8.self)) }
                current.removeAll()
            }
        }
        if current.count >= 4 { runs.append(String(decoding: current, as: UTF8.self)) }
        return LogLine(level: "Undecoded", message: runs.joined(separator: " "))
    }
}

// MARK: - Simulator unified log (simctl spawn log stream --style ndjson)

public enum SimulatorLogParser {
    /// Parses NDJSON lines from `log stream --style ndjson`. Non-JSON lines (such as the
    /// "Filtering the log data" banner) are kept as plain messages.
    public static func parse(line: Substring) -> LogLine? {
        let trimmed = line.trimmingCharacters(in: .whitespaces)
        guard !trimmed.isEmpty else { return nil }
        guard trimmed.first == "{", let json = try? JSONValue.parse(Data(trimmed.utf8)) else {
            return LogLine(message: trimmed)
        }
        let process = json["processImagePath"]?.string.map { URL(fileURLWithPath: $0).lastPathComponent }
        let timestamp = json["timestamp"]?.string.flatMap(parseTimestamp)
        return LogLine(
            timestamp: timestamp,
            process: process,
            pid: json["processID"]?.int,
            level: json["messageType"]?.string,
            subsystem: json["subsystem"]?.nonEmptyString,
            category: json["category"]?.nonEmptyString,
            message: json["eventMessage"]?.string ?? ""
        )
    }

    static func parseTimestamp(_ value: String) -> Date? {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "yyyy-MM-dd HH:mm:ss.SSSSSSZ"
        return formatter.date(from: value)
    }
}

/// A process reported by the device's `os_trace_relay` process list.
public struct DeviceProcessInfo: Sendable, Hashable, Codable, Identifiable {
    public var id: Int { pid }
    public var pid: Int
    public var name: String

    public init(pid: Int, name: String) {
        self.pid = pid
        self.name = name
    }
}
