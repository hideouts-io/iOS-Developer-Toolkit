import Foundation
import OSLog
import ToolkitCore

/// A connection to one lockdown service on the device.
public struct ServiceConnection: Sendable {
    public let name: String
    public let channel: DeviceChannel

    public var messages: PlistMessageConnection { PlistMessageConnection(channel: channel) }

    public func close() async {
        await channel.close()
    }
}

/// Basic identity readable before trust (no session), used to label untrusted devices.
public struct LockdownBasicInfo: Sendable, Hashable {
    public var deviceName: String?
    public var productType: String?
    public var productVersion: String?
    public var buildVersion: String?
    public var deviceClass: String?
}

/// An authenticated lockdown session bound to exactly one device.
///
/// `open(target:)` resolves the device by UDID at the moment the operation starts, establishes
/// a TLS session using this Mac's pairing record, and then asks the device for its UDID. If the
/// device that answered is not the intended one, the session is closed and the operation fails,
/// so an action can never reach a different device than the one the user confirmed.
public actor DeviceSession {
    public nonisolated let target: DeviceTarget
    public nonisolated let usbmuxDevice: USBMuxDevice
    private let usbmux: USBMuxClient
    private let pairRecord: PairRecord
    private let lockdown: LockdownClient
    private var openServices: [ServiceConnection] = []
    private let logger = ToolkitLog.deviceCommunication

    private init(target: DeviceTarget, usbmuxDevice: USBMuxDevice, usbmux: USBMuxClient, pairRecord: PairRecord, lockdown: LockdownClient) {
        self.target = target
        self.usbmuxDevice = usbmuxDevice
        self.usbmux = usbmux
        self.pairRecord = pairRecord
        self.lockdown = lockdown
    }

    public static func open(target: DeviceTarget, usbmux: USBMuxClient = USBMuxClient(), timeout: TimeInterval = 30) async throws -> DeviceSession {
        guard target.kind == .physical else {
            throw ToolkitError(.internalInconsistency, message: "Lockdown services are only available for physical devices.")
        }
        return try await withTimeout(timeout, operation: "Connecting to \(target.name)") {
            let device = try await resolve(target: target, usbmux: usbmux)
            let pairRecord = try await usbmux.readPairRecord(for: device)
            let lockdown = try await LockdownClient.connect(usbmux: usbmux, device: device)
            do {
                try await lockdown.startSession(pairRecord: pairRecord)
                let reported = try await lockdown.getValue(key: "UniqueDeviceID")?.stringValue
                guard let reported, USBMuxDevice.normalizedUDID(reported).caseInsensitiveCompare(target.udid) == .orderedSame else {
                    throw ToolkitError(
                        .internalInconsistency,
                        message: "The device that answered is not the one you selected, so nothing was sent to it.",
                        recovery: "Disconnect other devices, refresh the device list, and select the intended device again.",
                        technicalDetail: "Expected …\(target.confirmationSuffix)"
                    )
                }
                return DeviceSession(target: target, usbmuxDevice: device, usbmux: usbmux, pairRecord: pairRecord, lockdown: lockdown)
            } catch {
                await lockdown.close()
                throw error
            }
        }
    }

    /// Finds the usbmuxd record for the target UDID right now (usbmuxd device IDs change on
    /// every reconnect). USB is preferred over network when both are present.
    static func resolve(target: DeviceTarget, usbmux: USBMuxClient) async throws -> USBMuxDevice {
        let devices = try await usbmux.listDevices()
        let matches = devices.filter { $0.udid.caseInsensitiveCompare(target.udid) == .orderedSame }
        if let usb = matches.first(where: { $0.transport == .usb }) { return usb }
        if let any = matches.first { return any }
        throw ToolkitError(
            .deviceNotFound,
            message: "\(target.name) is not connected to this Mac.",
            recovery: "Connect the device with a USB cable, unlock it, and wait for it to appear in the device list."
        )
    }

    /// Reads identity values that lockdown exposes before a device trusts the Mac.
    public static func basicInfo(for device: USBMuxDevice, usbmux: USBMuxClient = USBMuxClient()) async throws -> LockdownBasicInfo {
        let lockdown = try await LockdownClient.connect(usbmux: usbmux, device: device)
        defer { Task { await lockdown.close() } }
        let all = try await lockdown.getValue()
        return LockdownBasicInfo(
            deviceName: all?["DeviceName"]?.stringValue,
            productType: all?["ProductType"]?.stringValue,
            productVersion: all?["ProductVersion"]?.stringValue,
            buildVersion: all?["BuildVersion"]?.stringValue,
            deviceClass: all?["DeviceClass"]?.stringValue
        )
    }

    // MARK: Values

    public func getValue(domain: String? = nil, key: String? = nil) async throws -> PlistValue? {
        try await lockdown.getValue(domain: domain, key: key)
    }

    /// Developer Mode status from AMFI (iOS 16+). `nil` when the device does not report it.
    public func developerModeEnabled() async throws -> Bool? {
        try await lockdown.getValue(domain: "com.apple.security.mac.amfi", key: "DeveloperModeStatus")?.boolValue
    }

    // MARK: Services

    public func openService(_ name: String, useEscrowBag: Bool = false) async throws -> ServiceConnection {
        let descriptor = try await lockdown.startService(name, escrowBag: useEscrowBag ? pairRecord.escrowBag : nil)
        let channel = try await usbmux.connect(to: usbmuxDevice, port: descriptor.port)
        do {
            if descriptor.usesTLS {
                try await channel.startTLS(pairRecord.tlsCredentials)
            }
        } catch {
            await channel.close()
            throw error
        }
        let connection = ServiceConnection(name: name, channel: channel)
        openServices.append(connection)
        return connection
    }

    public func close() async {
        for service in openServices { await service.close() }
        openServices.removeAll()
        await lockdown.stopSession()
        await lockdown.close()
        logger.info("Device session closed")
    }

    /// Opens a session, runs `body`, and always closes the session.
    public static func with<T: Sendable>(
        _ target: DeviceTarget,
        usbmux: USBMuxClient = USBMuxClient(),
        _ body: @Sendable (DeviceSession) async throws -> T
    ) async throws -> T {
        let session = try await open(target: target, usbmux: usbmux)
        do {
            let value = try await body(session)
            await session.close()
            return value
        } catch {
            await session.close()
            throw error
        }
    }
}
