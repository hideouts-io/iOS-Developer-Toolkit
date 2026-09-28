import Foundation
@testable import DeviceKit
import ToolkitCore

/// A `CommandRunning` double that records requests and replays scripted results. When a
/// request carries `--json-output <path>`, the scripted JSON is written there, the way
/// devicectl does.
final class ScriptedCommandRunner: CommandRunning, @unchecked Sendable {
    struct Reply: Sendable {
        var exitCode: Int32 = 0
        var standardOutput = Data()
        var standardError = Data()
        var jsonFile: Data?
        var error: ToolkitError?
    }

    private let lock = NSLock()
    private var handler: @Sendable (CommandRequest) -> Reply
    private(set) var requests: [CommandRequest] = []

    init(_ handler: @escaping @Sendable (CommandRequest) -> Reply) {
        self.handler = handler
    }

    var recorded: [CommandRequest] { lock.withLock { requests } }

    func run(_ request: CommandRequest) async throws -> CommandResult {
        lock.withLock { requests.append(request) }
        let reply = handler(request)
        if let error = reply.error { throw error }
        if let json = reply.jsonFile, let index = request.arguments.firstIndex(of: "--json-output"), index + 1 < request.arguments.count {
            try json.write(to: URL(fileURLWithPath: request.arguments[index + 1]))
        }
        return CommandResult(request: request, termination: .exited(reply.exitCode), standardOutput: reply.standardOutput, standardError: reply.standardError, startedAt: Date(), finishedAt: Date())
    }

    func stream(_ request: CommandRequest) -> AsyncThrowingStream<CommandStreamEvent, Error> {
        AsyncThrowingStream { continuation in
            Task {
                do {
                    let result = try await self.run(request)
                    if !result.standardOutput.isEmpty { continuation.yield(.standardOutput(result.standardOutput)) }
                    continuation.yield(.finished(result))
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
        }
    }
}

enum Fixture {
    static func data(_ path: String) -> Data {
        let url = Bundle.module.url(forResource: "Fixtures/\(path)", withExtension: nil)!
        return try! Data(contentsOf: url)
    }

    static func json(_ path: String) -> JSONValue {
        try! JSONValue.parse(data(path))
    }
}
