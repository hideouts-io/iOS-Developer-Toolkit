import Foundation
import OSLog

/// A fully specified external command. Arguments are always passed as a vector; nothing is
/// ever interpreted by a shell.
public struct CommandRequest: Sendable, Hashable {
    public var executable: URL
    public var arguments: [String]
    public var environment: [String: String]
    public var workingDirectory: URL?
    public var standardInput: Data?
    /// `nil` means no time limit (used for explicitly stoppable streams).
    public var timeout: TimeInterval?
    /// Maximum bytes retained per output channel. Streams deliver every byte to the consumer
    /// regardless of this limit; the limit only bounds what is buffered in memory.
    public var outputLimit: Int
    /// Human-readable name for logs and errors, e.g. "devicectl list devices".
    public var displayName: String
    /// Time between SIGTERM and SIGKILL when stopping.
    public var terminationGracePeriod: TimeInterval

    public init(
        executable: URL,
        arguments: [String],
        environment: [String: String] = CommandEnvironment.minimal(),
        workingDirectory: URL? = nil,
        standardInput: Data? = nil,
        timeout: TimeInterval? = 60,
        outputLimit: Int = 32 * 1024 * 1024,
        displayName: String? = nil,
        terminationGracePeriod: TimeInterval = 3
    ) {
        self.executable = executable
        self.arguments = arguments
        self.environment = environment
        self.workingDirectory = workingDirectory
        self.standardInput = standardInput
        self.timeout = timeout
        self.outputLimit = outputLimit
        self.displayName = displayName ?? ([executable.lastPathComponent] + arguments.prefix(3)).joined(separator: " ")
        self.terminationGracePeriod = terminationGracePeriod
    }

    /// A copy-pasteable rendering of the argument vector (quoted for display only).
    public var commandLine: String {
        ([executable.path] + arguments).map(ShellQuoting.quote).joined(separator: " ")
    }
}

public enum ShellQuoting {
    /// Quotes a single argument for *display*. The runner never executes this string.
    public static func quote(_ argument: String) -> String {
        if argument.isEmpty { return "''" }
        let safe = CharacterSet(charactersIn: "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789@%_+=:,./-")
        if argument.unicodeScalars.allSatisfy({ safe.contains($0) }) { return argument }
        return "'" + argument.replacingOccurrences(of: "'", with: "'\"'\"'") + "'"
    }
}

public enum CommandEnvironment {
    /// A minimal, predictable environment. Inherited variables such as `DYLD_*`,
    /// credential tokens, or tool-specific target selectors are not passed to children.
    public static func minimal(adding extra: [String: String] = [:]) -> [String: String] {
        let parent = ProcessInfo.processInfo.environment
        var environment: [String: String] = [
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "LANG": "en_US.UTF-8",
            "NSUnbufferedIO": "YES",
            "NO_COLOR": "1",
        ]
        for key in ["HOME", "USER", "LOGNAME", "TMPDIR", "DEVELOPER_DIR"] {
            if let value = parent[key], !value.isEmpty { environment[key] = value }
        }
        for (key, value) in extra { environment[key] = value }
        return environment
    }
}

public struct CommandResult: Sendable, Hashable {
    public enum Termination: Sendable, Hashable {
        case exited(Int32)
        case signaled(Int32)
    }

    public let request: CommandRequest
    public let termination: Termination
    public let standardOutput: Data
    public let standardError: Data
    public let standardOutputTruncated: Bool
    public let standardErrorTruncated: Bool
    public let startedAt: Date
    public let finishedAt: Date

    public init(
        request: CommandRequest,
        termination: Termination,
        standardOutput: Data,
        standardError: Data,
        standardOutputTruncated: Bool = false,
        standardErrorTruncated: Bool = false,
        startedAt: Date,
        finishedAt: Date
    ) {
        self.request = request
        self.termination = termination
        self.standardOutput = standardOutput
        self.standardError = standardError
        self.standardOutputTruncated = standardOutputTruncated
        self.standardErrorTruncated = standardErrorTruncated
        self.startedAt = startedAt
        self.finishedAt = finishedAt
    }

    public var succeeded: Bool { termination == .exited(0) }
    public var exitCode: Int32? {
        if case .exited(let code) = termination { return code }
        return nil
    }
    public var duration: TimeInterval { finishedAt.timeIntervalSince(startedAt) }
    public var standardOutputText: String { String(decoding: standardOutput, as: UTF8.self) }
    public var standardErrorText: String { String(decoding: standardError, as: UTF8.self) }

    /// A compact technical summary (never shown as the primary error message).
    public var technicalSummary: String {
        var lines = ["Command: \(request.commandLine)"]
        switch termination {
        case .exited(let code): lines.append("Exit status: \(code)")
        case .signaled(let signal): lines.append("Terminated by signal \(signal)")
        }
        let errorText = standardErrorText.trimmingCharacters(in: .whitespacesAndNewlines)
        if !errorText.isEmpty { lines.append("stderr: \(String(errorText.suffix(4_000)))") }
        return lines.joined(separator: "\n")
    }
}

public enum CommandStreamEvent: Sendable {
    case standardOutput(Data)
    case standardError(Data)
    case finished(CommandResult)
}

/// The abstraction every service depends on, so tests can substitute scripted results.
public protocol CommandRunning: Sendable {
    /// Runs to completion. Non-zero exit codes are returned, not thrown; launch failure,
    /// timeout, and cancellation throw `ToolkitError`.
    func run(_ request: CommandRequest) async throws -> CommandResult
    /// Streams output as it arrives and finishes with `.finished`. Cancelling the consuming
    /// task stops the child process.
    func stream(_ request: CommandRequest) -> AsyncThrowingStream<CommandStreamEvent, Error>
}

/// The production runner. It is the only type in the code base that creates `Process`.
public struct ProcessCommandRunner: CommandRunning {
    public init() {}

    public func run(_ request: CommandRequest) async throws -> CommandResult {
        var final: CommandResult?
        for try await event in stream(request) {
            if case .finished(let result) = event { final = result }
        }
        if Task.isCancelled {
            throw ToolkitError.cancelled(request.displayName)
        }
        guard let final else {
            throw ToolkitError(.internalInconsistency, message: "\(request.displayName) ended without a result.")
        }
        return final
    }

    public func stream(_ request: CommandRequest) -> AsyncThrowingStream<CommandStreamEvent, Error> {
        AsyncThrowingStream { continuation in
            let execution = ProcessExecution(request: request, continuation: continuation)
            continuation.onTermination = { termination in
                if case .cancelled = termination { execution.cancel() }
            }
            execution.start()
        }
    }
}

extension ProcessCommandRunner {
    /// Starts an independent application (for example a separately installed forensic GUI)
    /// that should keep running after the toolkit's operation ends. Output is discarded and the
    /// process is not terminated when the toolkit quits. Returns the process identifier.
    public func launchDetached(_ request: CommandRequest) throws -> Int32 {
        try ExecutableValidator.validate(request.executable)
        let process = Process()
        process.executableURL = request.executable
        process.arguments = request.arguments
        process.environment = request.environment
        if let directory = request.workingDirectory { process.currentDirectoryURL = directory }
        process.standardInput = FileHandle.nullDevice
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        do {
            try process.run()
        } catch {
            throw ToolkitError(.toolMissing, message: "\(request.displayName) could not be started.", technicalDetail: "\(request.commandLine)\n\(error.localizedDescription)")
        }
        ToolkitLog.commands.info("Launched detached \(request.displayName, privacy: .public) pid=\(process.processIdentifier, privacy: .public)")
        return process.processIdentifier
    }
}

/// Validates executables before they are launched.
public enum ExecutableValidator {
    public static func validate(_ url: URL, requireSystemOwned: Bool = false) throws {
        guard url.isFileURL, url.path.hasPrefix("/") else {
            throw ToolkitError(.toolMissing, message: "The tool path must be absolute.", technicalDetail: url.absoluteString)
        }
        let path = url.path
        var info = stat()
        guard stat(path, &info) == 0 else {
            throw ToolkitError(
                .toolMissing,
                message: "\(url.lastPathComponent) is not installed at the expected location.",
                recovery: "Install Xcode or the Command Line Tools, then select them with xcode-select.",
                technicalDetail: "Missing executable: \(path)"
            )
        }
        guard (info.st_mode & S_IFMT) == S_IFREG, access(path, X_OK) == 0 else {
            throw ToolkitError(.toolMissing, message: "\(url.lastPathComponent) is not an executable file.", technicalDetail: path)
        }
        if (info.st_mode & S_IWOTH) != 0 {
            throw ToolkitError(
                .permissionDenied,
                message: "\(url.lastPathComponent) is writable by every user, so it will not be run.",
                recovery: "Restrict the file's permissions (for example chmod o-w) and try again.",
                technicalDetail: path
            )
        }
        if requireSystemOwned && info.st_uid != 0 {
            throw ToolkitError(.permissionDenied, message: "\(url.lastPathComponent) is not owned by the system.", technicalDetail: path)
        }
    }
}

// MARK: - Process lifecycle

private final class ProcessExecution: @unchecked Sendable {
    private let request: CommandRequest
    private let continuation: AsyncThrowingStream<CommandStreamEvent, Error>.Continuation
    private let lock = NSLock()
    private let process = Process()
    private let outputPipe = Pipe()
    private let errorPipe = Pipe()
    private let inputPipe = Pipe()
    private var stdoutBuffer = Data()
    private var stderrBuffer = Data()
    private var stdoutTruncated = false
    private var stderrTruncated = false
    private var stdoutClosed = false
    private var stderrClosed = false
    private var terminationStatus: CommandResult.Termination?
    private var finished = false
    private var cancelled = false
    private var timedOut = false
    private var startedAt = Date()
    private static let logger = ToolkitLog.commands

    init(request: CommandRequest, continuation: AsyncThrowingStream<CommandStreamEvent, Error>.Continuation) {
        self.request = request
        self.continuation = continuation
    }

    func start() {
        do {
            try ExecutableValidator.validate(request.executable)
        } catch {
            Self.logger.error("Refused to launch \(self.request.displayName, privacy: .public): \(String(describing: error), privacy: .public)")
            continuation.finish(throwing: error)
            return
        }

        process.executableURL = request.executable
        process.arguments = request.arguments
        process.environment = request.environment
        if let directory = request.workingDirectory { process.currentDirectoryURL = directory }
        process.standardOutput = outputPipe
        process.standardError = errorPipe
        process.standardInput = request.standardInput == nil ? FileHandle.nullDevice : inputPipe

        outputPipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            self?.consume(handle.availableData, isError: false)
        }
        errorPipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            self?.consume(handle.availableData, isError: true)
        }
        process.terminationHandler = { [weak self] process in
            let termination: CommandResult.Termination = process.terminationReason == .uncaughtSignal
                ? .signaled(process.terminationStatus)
                : .exited(process.terminationStatus)
            self?.processTerminated(termination)
        }

        startedAt = Date()
        do {
            try process.run()
        } catch {
            outputPipe.fileHandleForReading.readabilityHandler = nil
            errorPipe.fileHandleForReading.readabilityHandler = nil
            Self.logger.error("Launch failed for \(self.request.displayName, privacy: .public): \(error.localizedDescription, privacy: .public)")
            continuation.finish(throwing: ToolkitError(
                .toolMissing,
                message: "\(request.executable.lastPathComponent) could not be started.",
                recovery: "Confirm that Xcode or the Command Line Tools are installed and selected.",
                technicalDetail: "\(request.commandLine)\n\(error.localizedDescription)"
            ))
            return
        }
        Self.logger.info("Started \(self.request.displayName, privacy: .public) pid=\(self.process.processIdentifier, privacy: .public) args=\(self.request.arguments.joined(separator: " "), privacy: .private)")

        if let input = request.standardInput {
            let handle = inputPipe.fileHandleForWriting
            DispatchQueue.global(qos: .userInitiated).async {
                do {
                    try handle.write(contentsOf: input)
                } catch {
                    Self.logger.error("Could not write standard input: \(error.localizedDescription, privacy: .public)")
                }
                try? handle.close()
            }
        }

        if let timeout = request.timeout {
            DispatchQueue.global().asyncAfter(deadline: .now() + timeout) { [weak self] in
                self?.timeoutFired()
            }
        }
    }

    func cancel() {
        lock.lock()
        let alreadyDone = finished || terminationStatus != nil
        cancelled = true
        lock.unlock()
        guard !alreadyDone else { return }
        stopProcess()
    }

    private func timeoutFired() {
        lock.lock()
        let alreadyDone = finished || terminationStatus != nil
        if !alreadyDone { timedOut = true }
        lock.unlock()
        guard !alreadyDone else { return }
        Self.logger.error("\(self.request.displayName, privacy: .public) exceeded its time limit")
        stopProcess()
    }

    private func stopProcess() {
        guard process.isRunning else { return }
        let pid = process.processIdentifier
        process.terminate()
        let grace = request.terminationGracePeriod
        DispatchQueue.global().asyncAfter(deadline: .now() + grace) { [weak self] in
            guard let self, self.process.isRunning else { return }
            Self.logger.error("Force-stopping pid \(pid, privacy: .public) after \(grace, privacy: .public)s grace period")
            kill(pid, SIGKILL)
        }
    }

    private func consume(_ data: Data, isError: Bool) {
        lock.lock()
        if finished {
            lock.unlock()
            return
        }
        if data.isEmpty {
            if isError {
                stderrClosed = true
                errorPipe.fileHandleForReading.readabilityHandler = nil
            } else {
                stdoutClosed = true
                outputPipe.fileHandleForReading.readabilityHandler = nil
            }
            let ready = terminationStatus != nil && stdoutClosed && stderrClosed
            lock.unlock()
            if ready { finish() }
            return
        }
        let limit = request.outputLimit
        if isError {
            let room = max(0, limit - stderrBuffer.count)
            if room > 0 { stderrBuffer.append(data.prefix(room)) }
            if data.count > room { stderrTruncated = true }
        } else {
            let room = max(0, limit - stdoutBuffer.count)
            if room > 0 { stdoutBuffer.append(data.prefix(room)) }
            if data.count > room { stdoutTruncated = true }
        }
        lock.unlock()
        continuation.yield(isError ? .standardError(data) : .standardOutput(data))
    }

    private func processTerminated(_ termination: CommandResult.Termination) {
        lock.lock()
        terminationStatus = termination
        let ready = stdoutClosed && stderrClosed
        lock.unlock()
        if ready {
            finish()
        } else {
            // A grandchild can keep a pipe open after the child exits. Give the pipes a short
            // window to reach EOF, then finish with what was captured.
            DispatchQueue.global().asyncAfter(deadline: .now() + 2) { [weak self] in
                self?.finish()
            }
        }
    }

    private func finish() {
        lock.lock()
        guard !finished, let termination = terminationStatus else {
            lock.unlock()
            return
        }
        finished = true
        let result = CommandResult(
            request: request,
            termination: termination,
            standardOutput: stdoutBuffer,
            standardError: stderrBuffer,
            standardOutputTruncated: stdoutTruncated,
            standardErrorTruncated: stderrTruncated,
            startedAt: startedAt,
            finishedAt: Date()
        )
        let wasCancelled = cancelled
        let wasTimedOut = timedOut
        lock.unlock()

        outputPipe.fileHandleForReading.readabilityHandler = nil
        errorPipe.fileHandleForReading.readabilityHandler = nil

        Self.logger.info("Finished \(self.request.displayName, privacy: .public) status=\(String(describing: termination), privacy: .public) duration=\(result.duration, format: .fixed(precision: 2), privacy: .public)s")

        if wasTimedOut, let timeout = request.timeout {
            continuation.finish(throwing: ToolkitError.timedOut(request.displayName, after: timeout).appendingDetail(result.technicalSummary))
        } else if wasCancelled {
            continuation.finish(throwing: ToolkitError.cancelled(request.displayName))
        } else {
            continuation.yield(.finished(result))
            continuation.finish()
        }
    }
}
