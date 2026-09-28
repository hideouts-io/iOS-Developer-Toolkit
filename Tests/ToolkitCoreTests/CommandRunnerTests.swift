import Foundation
import Testing
@testable import ToolkitCore

@Suite("CommandRunner")
struct CommandRunnerTests {
    let runner = ProcessCommandRunner()

    func shell(_ script: String, timeout: TimeInterval? = 10, input: Data? = nil, limit: Int = 1 << 20) -> CommandRequest {
        CommandRequest(
            executable: URL(fileURLWithPath: "/bin/sh"),
            arguments: ["-c", script],
            standardInput: input,
            timeout: timeout,
            outputLimit: limit,
            displayName: "test shell",
            terminationGracePeriod: 1
        )
    }

    /// Display names are logged publicly, so identifiers, paths, and values must never appear.
    @Test func defaultDisplayNamesContainNoIdentifiers() throws {
        #expect(CommandRequest.defaultDisplayName(tool: "simctl", arguments: ["boot", "780C6431-EBAF-4AAE-AA6C-E8886DD4D415"]) == "simctl boot")
        #expect(CommandRequest.defaultDisplayName(tool: "devicectl", arguments: ["device", "info", "details", "--device", "00008110-001234560ABC801E"]) == "devicectl device info details")
        #expect(CommandRequest.defaultDisplayName(tool: "simctl", arguments: ["openurl", "booted", "https://example.com"]) == "simctl openurl booted")
        #expect(CommandRequest.defaultDisplayName(tool: "open", arguments: ["/Users/someone/Case.trace"]) == "open")
        #expect(CommandRequest.defaultDisplayName(tool: "devicectl", arguments: ["list", "devices", "--json-output", "/tmp/x.json"]) == "devicectl list devices")
        let request = try XcodeTool.simctl.request(["shutdown", "780C6431-EBAF-4AAE-AA6C-E8886DD4D415"])
        #expect(request.displayName == "simctl shutdown")
    }

    @Test func capturesStandardOutputAndError() async throws {
        let result = try await runner.run(shell("printf out; printf err 1>&2"))
        #expect(result.succeeded)
        #expect(result.standardOutputText == "out")
        #expect(result.standardErrorText == "err")
        #expect(result.exitCode == 0)
    }

    @Test func returnsNonZeroExitWithoutThrowing() async throws {
        let result = try await runner.run(shell("echo nope 1>&2; exit 7"))
        #expect(!result.succeeded)
        #expect(result.exitCode == 7)
        #expect(result.technicalSummary.contains("Exit status: 7"))
        #expect(result.technicalSummary.contains("nope"))
    }

    @Test func argumentsAreNotInterpretedByAShell() async throws {
        let request = CommandRequest(
            executable: URL(fileURLWithPath: "/bin/echo"),
            arguments: ["$(whoami)", "; rm -rf /", "`id`"],
            timeout: 10
        )
        let result = try await runner.run(request)
        #expect(result.standardOutputText == "$(whoami) ; rm -rf / `id`\n")
    }

    @Test func passesStandardInput() async throws {
        let result = try await runner.run(shell("cat", input: Data("secret-password\n".utf8)))
        #expect(result.standardOutputText == "secret-password\n")
    }

    @Test func timeoutThrowsTimedOut() async throws {
        let start = Date()
        await #expect(throws: ToolkitError.self) {
            _ = try await runner.run(shell("sleep 30", timeout: 0.5))
        }
        #expect(Date().timeIntervalSince(start) < 10)
        do {
            _ = try await runner.run(shell("sleep 30", timeout: 0.3))
            Issue.record("expected timeout")
        } catch let error as ToolkitError {
            #expect(error.kind == .timedOut)
        }
    }

    @Test func cancellationStopsTheProcess() async throws {
        let task = Task {
            try await runner.run(shell("sleep 30", timeout: nil))
        }
        try await Task.sleep(for: .milliseconds(300))
        task.cancel()
        let start = Date()
        do {
            _ = try await task.value
            Issue.record("expected cancellation")
        } catch let error as ToolkitError {
            #expect(error.kind == .cancelled)
        }
        #expect(Date().timeIntervalSince(start) < 8)
    }

    @Test func missingExecutableIsActionable() async throws {
        let request = CommandRequest(executable: URL(fileURLWithPath: "/nonexistent/tool"), arguments: [])
        do {
            _ = try await runner.run(request)
            Issue.record("expected failure")
        } catch let error as ToolkitError {
            #expect(error.kind == .toolMissing)
            #expect(error.recovery != nil)
        }
    }

    @Test func relativeExecutableIsRejected() async throws {
        do {
            _ = try await runner.run(CommandRequest(executable: URL(fileURLWithPath: "tool", relativeTo: nil), arguments: []))
        } catch let error as ToolkitError {
            #expect(error.kind == .toolMissing || error.kind == .permissionDenied)
        }
    }

    @Test func outputLimitTruncatesBufferButStreamsEverything() async throws {
        var streamed = 0
        var final: CommandResult?
        for try await event in runner.stream(shell("head -c 200000 /dev/zero", limit: 1000)) {
            switch event {
            case .standardOutput(let data): streamed += data.count
            case .standardError: break
            case .finished(let result): final = result
            }
        }
        #expect(streamed == 200_000)
        #expect(final?.standardOutput.count == 1000)
        #expect(final?.standardOutputTruncated == true)
    }

    @Test func minimalEnvironmentDropsInheritedSecrets() async throws {
        setenv("TOOLKIT_TEST_SECRET", "leak", 1)
        defer { unsetenv("TOOLKIT_TEST_SECRET") }
        let result = try await runner.run(shell("echo \"[$TOOLKIT_TEST_SECRET]\"; echo $PATH"))
        #expect(result.standardOutputText.hasPrefix("[]\n"))
        #expect(result.standardOutputText.contains("/usr/bin:/bin:/usr/sbin:/sbin"))
    }

    @Test func worldWritableExecutableIsRefused() async throws {
        let directory = try SecureFileIO.makeTemporaryDirectory(prefix: "runner-test")
        defer { try? FileManager.default.removeItem(at: directory) }
        let tool = directory.appendingPathComponent("tool")
        try Data("#!/bin/sh\necho hi\n".utf8).write(to: tool)
        chmod(tool.path, 0o777)
        do {
            _ = try await runner.run(CommandRequest(executable: tool, arguments: []))
            Issue.record("expected refusal")
        } catch let error as ToolkitError {
            #expect(error.kind == .permissionDenied)
        }
    }

    @Test func displayQuotingIsSafe() {
        #expect(ShellQuoting.quote("simple") == "simple")
        #expect(ShellQuoting.quote("has space") == "'has space'")
        #expect(ShellQuoting.quote("it's") == "'it'\"'\"'s'")
        #expect(ShellQuoting.quote("") == "''")
    }
}
