import Foundation
import OSLog
import ToolkitCore

public struct LockdownServiceDescriptor: Sendable, Hashable {
    public let name: String
    public let port: UInt16
    public let usesTLS: Bool
}

/// The lockdownd protocol (TCP port 62078 on the device, reached through usbmuxd).
public actor LockdownClient {
    public static let port: UInt16 = 62_078
    public static let label = "iOSDeveloperToolkit"

    private let messages: PlistMessageConnection
    public private(set) var sessionID: String?
    private let logger = ToolkitLog.deviceCommunication

    init(messages: PlistMessageConnection) {
        self.messages = messages
    }

    public static func connect(usbmux: USBMuxClient, device: USBMuxDevice) async throws -> LockdownClient {
        let channel = try await usbmux.connect(to: device, port: port)
        let client = LockdownClient(messages: PlistMessageConnection(channel: channel))
        do {
            let reply = try await client.request("QueryType")
            guard reply["Type"]?.stringValue == "com.apple.mobile.lockdown" else {
                throw ToolkitError(.protocolViolation, message: "The device's lockdown service returned an unexpected type.", technicalDetail: reply.prettyJSONString())
            }
        } catch {
            await client.close()
            throw error
        }
        return client
    }

    /// Sends a lockdown request and returns the reply, converting lockdown `Error` strings into
    /// actionable errors.
    public func request(_ name: String, _ extra: [String: PlistValue] = [:], timeout: TimeInterval = 30) async throws -> PlistValue {
        var body: [String: PlistValue] = ["Label": .string(Self.label), "Request": .string(name)]
        for (key, value) in extra { body[key] = value }
        let reply = try await messages.request(.dictionary(body), timeout: timeout)
        if let error = reply["Error"]?.stringValue {
            throw LockdownErrorInterpreter.interpret(error, request: name, detail: reply["ErrorDescription"]?.stringValue)
        }
        return reply
    }

    public func getValue(domain: String? = nil, key: String? = nil) async throws -> PlistValue? {
        var extra: [String: PlistValue] = [:]
        if let domain { extra["Domain"] = .string(domain) }
        if let key { extra["Key"] = .string(key) }
        do {
            return try await request("GetValue", extra)["Value"]
        } catch let error as ToolkitError where error.technicalDetail?.contains("MissingValue") == true {
            return nil
        }
    }

    /// Starts an authenticated session and upgrades the connection to TLS when requested.
    public func startSession(pairRecord: PairRecord) async throws {
        let reply = try await request("StartSession", [
            "HostID": .string(pairRecord.hostID),
            "SystemBUID": .string(pairRecord.systemBUID),
        ])
        guard let sessionID = reply["SessionID"]?.stringValue else {
            throw ToolkitError(.protocolViolation, message: "The device did not start a lockdown session.")
        }
        self.sessionID = sessionID
        if reply["EnableSessionSSL"]?.boolValue == true {
            try await messages.channel.startTLS(pairRecord.tlsCredentials)
        }
        logger.info("Lockdown session started")
    }

    public func startService(_ name: String, escrowBag: Data? = nil) async throws -> LockdownServiceDescriptor {
        guard sessionID != nil else {
            throw ToolkitError(.internalInconsistency, message: "A device service was requested before a session was established.")
        }
        var extra: [String: PlistValue] = ["Service": .string(name)]
        if let escrowBag { extra["EscrowBag"] = .data(escrowBag) }
        let reply = try await request("StartService", extra)
        guard let port = reply["Port"]?.intValue, let validPort = UInt16(exactly: port), validPort > 0 else {
            throw ToolkitError(.protocolViolation, message: "The device did not provide a port for \(name).")
        }
        logger.info("Started service \(name, privacy: .public)")
        return LockdownServiceDescriptor(name: name, port: validPort, usesTLS: reply["EnableServiceSSL"]?.boolValue ?? false)
    }

    public func stopSession() async {
        guard let sessionID else { return }
        _ = try? await request("StopSession", ["SessionID": .string(sessionID)], timeout: 5)
        self.sessionID = nil
    }

    public func close() async {
        await messages.close()
    }
}

/// Maps lockdownd error strings to plain-language errors.
public enum LockdownErrorInterpreter {
    public static func interpret(_ code: String, request: String, detail: String? = nil) -> ToolkitError {
        let technical = "lockdown \(request) error: \(code)" + (detail.map { " — \($0)" } ?? "")
        switch code {
        case "PasswordProtected":
            return ToolkitError(.deviceLocked, message: "The device is locked.", recovery: "Unlock the device with its passcode, then try again.", technicalDetail: technical)
        case "PairingDialogResponsePending":
            return ToolkitError(.pairingPending, message: "The device is waiting for you to tap Trust.", recovery: "Unlock the device and answer the “Trust This Computer?” prompt.", technicalDetail: technical)
        case "UserDeniedPairing":
            return ToolkitError(.notPaired, message: "Trust was declined on the device.", recovery: "Disconnect and reconnect the device, then tap Trust. If no prompt appears, reset Location & Privacy on the device.", technicalDetail: technical)
        case "InvalidHostID", "InvalidPairRecord", "NoHostCertificate", "InvalidHostCertificate":
            return ToolkitError(.notPaired, message: "The device no longer trusts this Mac.", recovery: "Disconnect and reconnect the device, unlock it, and tap Trust again.", technicalDetail: technical)
        case "InvalidService", "ServiceProhibited":
            return ToolkitError(.serviceUnavailable, message: "This service is not available on the selected device.", recovery: "The iOS version on the device may not offer it, or it may be restricted by device management.", technicalDetail: technical)
        case "EscrowLocked", "DeviceLocked":
            return ToolkitError(.deviceLocked, message: "The device must be unlocked at least once after restarting.", recovery: "Unlock the device, then try again.", technicalDetail: technical)
        case "SessionInactive", "InvalidSessionID":
            return ToolkitError(.deviceDisconnected, message: "The connection to the device was interrupted.", recovery: "Reconnect the device and try again.", technicalDetail: technical)
        case "MissingValue":
            return ToolkitError(.serviceUnavailable, message: "The device did not provide the requested value.", technicalDetail: technical)
        default:
            return ToolkitError(.commandFailed, message: "The device rejected the request.", recovery: "Make sure the device is unlocked, connected, and has trusted this Mac.", technicalDetail: technical)
        }
    }
}
