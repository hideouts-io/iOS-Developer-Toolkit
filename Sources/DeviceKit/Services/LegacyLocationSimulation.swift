import Foundation
import ToolkitCore

/// `com.apple.dt.simulatelocation`, the location simulation service used by iOS 16 and earlier.
/// It is only available after a developer disk image has been mounted (for example by Xcode).
public enum LegacyLocationSimulation {
    public static let serviceName = "com.apple.dt.simulatelocation"

    static func encodeSet(latitude: Double, longitude: Double) -> Data {
        var data = Data()
        data.appendBigEndian(0)
        for value in [String(latitude), String(longitude)] {
            data.appendBigEndian(UInt32(value.utf8.count))
            data.append(Data(value.utf8))
        }
        return data
    }

    static func encodeClear() -> Data {
        var data = Data()
        data.appendBigEndian(1)
        return data
    }

    public static func set(latitude: Double, longitude: Double, session: DeviceSession) async throws {
        try Coordinate.validate(latitude: latitude, longitude: longitude)
        let service = try await openService(session)
        defer { Task { await service.close() } }
        try await service.channel.write(encodeSet(latitude: latitude, longitude: longitude))
    }

    public static func clear(session: DeviceSession) async throws {
        let service = try await openService(session)
        defer { Task { await service.close() } }
        try await service.channel.write(encodeClear())
    }

    static func openService(_ session: DeviceSession) async throws -> ServiceConnection {
        do {
            return try await session.openService(serviceName)
        } catch let error as ToolkitError where error.kind == .serviceUnavailable {
            throw ToolkitError(
                .developerDiskImageUnavailable,
                message: "Location simulation on this iOS version needs Xcode's developer disk image.",
                recovery: "Connect the device to Xcode once (Window › Devices and Simulators) so it mounts the developer image, then try again.",
                technicalDetail: error.technicalDetail
            )
        }
    }
}

/// Routes location requests to the right Apple mechanism for the target:
/// simulators → simctl; iOS 17+ → CoreDevice; earlier iOS → the legacy lockdown service.
public struct LocationController: Sendable {
    public let coreDevice: CoreDeviceClient
    public let simulators: SimulatorClient
    public let usbmux: USBMuxClient

    public init(coreDevice: CoreDeviceClient = CoreDeviceClient(), simulators: SimulatorClient = SimulatorClient(), usbmux: USBMuxClient = USBMuxClient()) {
        self.coreDevice = coreDevice
        self.simulators = simulators
        self.usbmux = usbmux
    }

    public enum Mechanism: String, Sendable {
        case simulator = "simctl location"
        case coreDevice = "CoreDevice (devicectl) location simulation"
        case legacyService = "com.apple.dt.simulatelocation (iOS 16 and earlier)"
        case unavailable = "Unavailable"
    }

    public func mechanism(for target: DeviceTarget) -> Mechanism {
        switch target.kind {
        case .simulator: return .simulator
        case .demo: return .unavailable
        case .physical:
            if let major = target.osMajorVersion, major < 17 { return .legacyService }
            return .coreDevice
        }
    }

    public func set(latitude: Double, longitude: Double, on target: DeviceTarget) async throws {
        switch mechanism(for: target) {
        case .simulator:
            try await simulators.setLocation(latitude: latitude, longitude: longitude, on: target)
        case .coreDevice:
            _ = try await coreDevice.setLocation(latitude: latitude, longitude: longitude, on: target)
        case .legacyService:
            try await DeviceSession.with(target, usbmux: usbmux) { session in
                try await LegacyLocationSimulation.set(latitude: latitude, longitude: longitude, session: session)
            }
        case .unavailable:
            throw ToolkitError(.unsupported, message: "Location simulation is not available for the demo device.")
        }
    }

    public func clear(on target: DeviceTarget) async throws {
        switch mechanism(for: target) {
        case .simulator:
            try await simulators.clearLocation(on: target)
        case .coreDevice:
            _ = try await coreDevice.clearLocation(on: target)
        case .legacyService:
            try await DeviceSession.with(target, usbmux: usbmux) { session in
                try await LegacyLocationSimulation.clear(session: session)
            }
        case .unavailable:
            throw ToolkitError(.unsupported, message: "Location simulation is not available for the demo device.")
        }
    }

    /// Starts constant-speed movement handled by the device service itself.
    public func startRoute(_ waypoints: [(latitude: Double, longitude: Double)], speedMetresPerSecond: Double, intervalSeconds: Double, on target: DeviceTarget) async throws {
        switch mechanism(for: target) {
        case .simulator:
            try await simulators.simulateRoute(waypoints, speedMetresPerSecond: speedMetresPerSecond, updateIntervalSeconds: intervalSeconds, on: target)
        case .coreDevice:
            _ = try await coreDevice.simulateRoute(waypoints, speedMetresPerSecond: speedMetresPerSecond, updateIntervalSeconds: intervalSeconds, on: target)
        case .legacyService, .unavailable:
            throw ToolkitError(.unsupported, message: "Native route simulation needs iOS 17 or later.", recovery: "Use GPX playback instead; the toolkit will send each point in turn.")
        }
    }
}
