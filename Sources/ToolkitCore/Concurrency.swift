import Foundation

/// Runs `operation`, failing with a `.timedOut` `ToolkitError` if it does not finish in time.
/// The operation is cancelled when the deadline passes.
public func withTimeout<T: Sendable>(
    _ seconds: TimeInterval,
    operation name: String,
    _ body: @escaping @Sendable () async throws -> T
) async throws -> T {
    try await withThrowingTaskGroup(of: T.self) { group in
        group.addTask { try await body() }
        group.addTask {
            try await Task.sleep(nanoseconds: UInt64(max(0, seconds) * 1_000_000_000))
            throw ToolkitError.timedOut(name, after: seconds)
        }
        defer { group.cancelAll() }
        guard let first = try await group.next() else {
            throw ToolkitError(.internalInconsistency, message: "\(name) produced no result.")
        }
        return first
    }
}

/// A small lock-protected box for state shared with callbacks that are not async-aware.
public final class LockedValue<Value>: @unchecked Sendable {
    private let lock = NSLock()
    private var value: Value

    public init(_ value: Value) {
        self.value = value
    }

    public func withLock<Result>(_ body: (inout Value) throws -> Result) rethrows -> Result {
        lock.lock()
        defer { lock.unlock() }
        return try body(&value)
    }

    public var current: Value {
        withLock { $0 }
    }
}
