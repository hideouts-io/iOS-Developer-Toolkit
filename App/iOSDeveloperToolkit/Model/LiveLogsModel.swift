import DeviceKit
import Foundation
import Observation
import ToolkitCore
import ToolkitFeatures

/// One live log stream: spooled completely to disk, with a bounded working view in memory.
@Observable
@MainActor
final class LogSession: Identifiable {
    enum State: Equatable {
        case starting, running, stopped(String), failed(String)

        var isActive: Bool { self == .starting || self == .running }

        var label: String {
            switch self {
            case .starting: return "Starting…"
            case .running: return "Capturing"
            case .stopped(let reason): return "Stopped — \(reason)"
            case .failed(let message): return "Failed — \(message)"
            }
        }
    }

    static let maximumLines = 50_000

    let id = UUID()
    let kind: LogStreamKind
    let target: DeviceTarget
    let capture: LogCapture
    var state: State = .starting
    var lines: [LogLine] = []
    var isPaused = false
    var followTail = true
    var filter = LogFilter()
    var filterError: String?
    var rawBytes: Int64 = 0
    var totalLines = 0
    var findings: [LiveLogFinding] = []
    var investigationReference = ""
    var hasUnsavedData = true
    fileprivate var task: Task<Void, Never>?
    private var pending: [LogLine] = []
    private var flushScheduled = false

    init(kind: LogStreamKind, target: DeviceTarget, capture: LogCapture) {
        self.kind = kind
        self.target = target
        self.capture = capture
    }

    var title: String { "\(kind.title) — \(target.name)" }

    /// Lines shown in the working view (filtered). Paused views keep their snapshot.
    var visibleLines: [LogLine] {
        guard !filter.isEmpty, let matcher = try? filter.matcher() else { return lines }
        return lines.filter { matcher($0.rendered) }
    }

    func receive(_ chunk: LogChunk) {
        rawBytes += Int64(chunk.spoolBytes.count)
        totalLines += chunk.lines.count
        guard !isPaused else { return }
        pending.append(contentsOf: chunk.lines)
        // Batch UI updates so a busy stream does not redraw for every line.
        if !flushScheduled {
            flushScheduled = true
            Task { @MainActor [weak self] in
                try? await Task.sleep(for: .milliseconds(150))
                self?.flush()
            }
        }
    }

    private func flush() {
        flushScheduled = false
        lines.append(contentsOf: pending)
        pending.removeAll(keepingCapacity: true)
        if lines.count > Self.maximumLines {
            lines.removeFirst(lines.count - Self.maximumLines)
        }
    }

    func validateFilter() {
        do {
            _ = try filter.matcher()
            filterError = nil
        } catch {
            filterError = (error as? ToolkitError)?.message ?? error.localizedDescription
        }
    }

    func stop(reason: String = "stopped by you") {
        task?.cancel()
        task = nil
        if state.isActive { state = .stopped(reason) }
        let capture = self.capture
        Task { try? await capture.finish(reason: reason) }
    }
}

@Observable
@MainActor
final class LiveLogsModel {
    var sessions: [LogSession] = []
    var selectedSessionID: UUID?

    var selectedSession: LogSession? {
        sessions.first { $0.id == selectedSessionID } ?? sessions.last
    }

    func start(_ kind: LogStreamKind, target: DeviceTarget, app: AppModel) {
        let capture: LogCapture
        do {
            capture = try LogCapture(kind: kind, target: target)
        } catch {
            app.present(error)
            return
        }
        let session = LogSession(kind: kind, target: target, capture: capture)
        sessions.append(session)
        selectedSessionID = session.id
        let runner = app.runner
        let started = Date()
        session.task = Task { [weak session] in
            do {
                let stream = try await LiveLogSource.open(kind, target: target, runner: runner)
                session?.state = .running
                for try await chunk in stream {
                    try await capture.append(chunk)
                    session?.receive(chunk)
                }
                session?.stop(reason: "the device ended the stream")
                app.record(title: kind.title, workspace: .liveLogs, target: target, transport: kind.serviceDescription, argv: [], started: started, finished: Date(), outcome: .succeeded, error: nil, outputPaths: [capture.spoolURL.path])
            } catch is CancellationError {
                app.record(title: kind.title, workspace: .liveLogs, target: target, transport: kind.serviceDescription, argv: [], started: started, finished: Date(), outcome: .cancelled, error: nil, outputPaths: [capture.spoolURL.path])
            } catch {
                let message = (error as? ToolkitError)?.message ?? error.localizedDescription
                if Task.isCancelled {
                    session?.stop()
                } else {
                    session?.state = .failed(message)
                    try? await capture.finish(reason: "failed: \(message)")
                    app.present(error)
                }
                app.record(title: kind.title, workspace: .liveLogs, target: target, transport: kind.serviceDescription, argv: [], started: started, finished: Date(), outcome: OperationOutcome.from(error), error: message, outputPaths: [capture.spoolURL.path])
            }
        }
    }

    func close(_ session: LogSession) {
        session.stop()
        sessions.removeAll { $0.id == session.id }
        if selectedSessionID == session.id { selectedSessionID = sessions.last?.id }
    }

    func stopAll() {
        for session in sessions where session.state.isActive { session.stop(reason: "the app quit") }
    }
}
