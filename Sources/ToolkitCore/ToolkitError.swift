import Foundation

/// The single error type surfaced to the UI.
///
/// `message` is written for someone who does not use Terminal; `recovery` says what to do next;
/// `technicalDetail` preserves the underlying cause (exit status, stderr, protocol error) for
/// advanced users and bug reports.
public struct ToolkitError: Error, LocalizedError, Sendable, Equatable {
    public enum Kind: String, Sendable, Codable, CaseIterable {
        case deviceNotFound
        case deviceDisconnected
        case deviceLocked
        case notPaired
        case pairingPending
        case developerModeDisabled
        case developerDiskImageUnavailable
        case serviceUnavailable
        case protocolViolation
        case timedOut
        case cancelled
        case toolMissing
        case commandFailed
        case invalidInput
        case fileSystem
        case unsupported
        case permissionDenied
        case internalInconsistency
    }

    public let kind: Kind
    public let message: String
    public let recovery: String?
    public let technicalDetail: String?

    public init(_ kind: Kind, message: String, recovery: String? = nil, technicalDetail: String? = nil) {
        self.kind = kind
        self.message = message
        self.recovery = recovery
        self.technicalDetail = technicalDetail
    }

    public var errorDescription: String? { message }
    public var recoverySuggestion: String? { recovery }
    public var failureReason: String? { technicalDetail }

    /// A compact title for alerts.
    public var title: String {
        switch kind {
        case .deviceNotFound: return "Device Not Found"
        case .deviceDisconnected: return "Device Disconnected"
        case .deviceLocked: return "Device Is Locked"
        case .notPaired: return "Device Has Not Trusted This Mac"
        case .pairingPending: return "Waiting for Trust"
        case .developerModeDisabled: return "Developer Mode Is Off"
        case .developerDiskImageUnavailable: return "Developer Services Unavailable"
        case .serviceUnavailable: return "Service Unavailable"
        case .protocolViolation: return "Unexpected Device Response"
        case .timedOut: return "Operation Timed Out"
        case .cancelled: return "Operation Cancelled"
        case .toolMissing: return "Developer Tool Missing"
        case .commandFailed: return "Operation Failed"
        case .invalidInput: return "Check the Entered Value"
        case .fileSystem: return "File Error"
        case .unsupported: return "Not Supported"
        case .permissionDenied: return "Permission Denied"
        case .internalInconsistency: return "Unexpected Error"
        }
    }

    /// Returns a copy with more technical context appended.
    public func appendingDetail(_ detail: String) -> ToolkitError {
        let combined = [technicalDetail, detail].compactMap { $0 }.filter { !$0.isEmpty }.joined(separator: "\n")
        return ToolkitError(kind, message: message, recovery: recovery, technicalDetail: combined.isEmpty ? nil : combined)
    }

    // MARK: Common constructors

    public static func cancelled(_ what: String = "The operation") -> ToolkitError {
        ToolkitError(.cancelled, message: "\(what) was cancelled.", recovery: "Review any partial output before relying on it.")
    }

    public static func timedOut(_ what: String, after seconds: TimeInterval) -> ToolkitError {
        ToolkitError(
            .timedOut,
            message: "\(what) did not finish within \(Int(seconds.rounded())) seconds.",
            recovery: "Make sure the device is unlocked and still connected, then try again."
        )
    }

    public static func invalidInput(_ message: String) -> ToolkitError {
        ToolkitError(.invalidInput, message: message)
    }

    public static func fileSystem(_ message: String, path: String? = nil, underlying: Error? = nil) -> ToolkitError {
        let detail = [path.map { "Path: \($0)" }, underlying.map { String(describing: $0) }].compactMap { $0 }.joined(separator: "\n")
        return ToolkitError(.fileSystem, message: message, technicalDetail: detail.isEmpty ? nil : detail)
    }

    public static func deviceCommunication(technicalDetail: String? = nil) -> ToolkitError {
        ToolkitError(
            .serviceUnavailable,
            message: "Unable to communicate with the selected device.",
            recovery: "Make sure the device is unlocked, connected, and has trusted this Mac.",
            technicalDetail: technicalDetail
        )
    }

    /// Wraps any error, preserving a `ToolkitError` untouched.
    public static func wrap(_ error: Error, message: String, recovery: String? = nil) -> ToolkitError {
        if let toolkitError = error as? ToolkitError { return toolkitError }
        if error is CancellationError { return .cancelled() }
        return ToolkitError(.internalInconsistency, message: message, recovery: recovery, technicalDetail: String(describing: error))
    }
}
