import Foundation
import OSLog
import ToolkitCore

/// What lockdown told us about a USB/Wi-Fi device (used when CoreDevice is unavailable, and to
/// fill in pairing and Developer Mode state).
public struct LockdownEnrichment: Sendable, Hashable {
    public var name: String?
    public var productType: String?
    public var productVersion: String?
    public var buildVersion: String?
    public var architecture: String?
    public var hardwareModel: String?
    public var serialNumber: String?
    public var pairingState: PairingState
    public var developerMode: DeveloperModeState
    public var problem: String?

    public init(name: String? = nil, productType: String? = nil, productVersion: String? = nil, buildVersion: String? = nil, architecture: String? = nil, hardwareModel: String? = nil, serialNumber: String? = nil, pairingState: PairingState = .unknown, developerMode: DeveloperModeState = .unknown, problem: String? = nil) {
        self.name = name
        self.productType = productType
        self.productVersion = productVersion
        self.buildVersion = buildVersion
        self.architecture = architecture
        self.hardwareModel = hardwareModel
        self.serialNumber = serialNumber
        self.pairingState = pairingState
        self.developerMode = developerMode
        self.problem = problem
    }
}

/// The health of one discovery source, shown in the connection diagnostics panel.
public enum SourceStatus: Sendable, Hashable {
    case notChecked
    case available(count: Int)
    case unavailable(reason: String)

    public var summary: String {
        switch self {
        case .notChecked: return "Not checked yet"
        case .available(let count): return count == 1 ? "1 device" : "\(count) devices"
        case .unavailable(let reason): return reason
        }
    }

    public var isAvailable: Bool {
        if case .available = self { return true }
        return false
    }
}

public struct DiscoverySnapshot: Sendable, Hashable {
    public var devices: [Device]
    public var usbmux: SourceStatus
    public var coreDevice: SourceStatus
    public var simulators: SourceStatus
    public var updatedAt: Date

    public init(devices: [Device] = [], usbmux: SourceStatus = .notChecked, coreDevice: SourceStatus = .notChecked, simulators: SourceStatus = .notChecked, updatedAt: Date = Date()) {
        self.devices = devices
        self.usbmux = usbmux
        self.coreDevice = coreDevice
        self.simulators = simulators
        self.updatedAt = updatedAt
    }

    public var physicalDevices: [Device] { devices.filter { $0.kind == .physical } }
    public var simulatorDevices: [Device] { devices.filter { $0.kind == .simulator } }
}

/// Pure merge of all discovery sources into display records.
public enum DeviceMerger {
    public static func merge(
        usbmux: [USBMuxDevice],
        enrichment: [String: LockdownEnrichment],
        coreDevice: [CoreDeviceRecord],
        simulators: [SimulatorRecord],
        now: Date = Date()
    ) -> [Device] {
        var physical: [String: Device] = [:]

        // CoreDevice records that are currently reachable (wired or network transport).
        for record in coreDevice where record.isPhysical && (record.transport != nil || usbmux.contains { $0.udid == record.udid }) {
            var device = record.device(lastSeen: now)
            device.udid = record.udid ?? record.identifier
            physical[key(device.udid)] = device
        }

        for mux in usbmux {
            let identifier = key(mux.udid)
            var device = physical[identifier] ?? Device(kind: .physical, udid: mux.udid, name: "iOS device", lastSeen: now)
            if device.usbmuxDeviceID == nil || mux.transport == .usb {
                device.usbmuxDeviceID = mux.deviceID
            }
            device.transports.insert(mux.transport)
            device.sources.insert(.usbmux)
            if let info = enrichment[identifier] {
                if !device.sources.contains(.coreDevice) {
                    device.name = info.name ?? device.name
                    device.productType = info.productType ?? device.productType
                    device.marketingName = device.marketingName ?? info.productType.flatMap(ProductCatalog.marketingName(for:))
                    device.family = DeviceFamily.from(productType: device.productType)
                    device.osVersion = info.productVersion ?? device.osVersion
                    device.buildVersion = info.buildVersion ?? device.buildVersion
                    device.architecture = info.architecture ?? device.architecture
                    device.hardwareModel = info.hardwareModel ?? device.hardwareModel
                    device.serialNumber = info.serialNumber ?? device.serialNumber
                    device.osName = device.family == .iPad ? "iPadOS" : "iOS"
                }
                if device.pairingState == .unknown || info.pairingState == .unpaired {
                    device.pairingState = info.pairingState
                }
                if device.developerMode == .unknown {
                    device.developerMode = info.developerMode
                }
            }
            physical[identifier] = device
        }

        let simulatorDevices = simulators.filter(\.isAvailable).map(\.device)
        let sortedPhysical = physical.values.sorted {
            if $0.transports.contains(.usb) != $1.transports.contains(.usb) { return $0.transports.contains(.usb) }
            return $0.name.localizedStandardCompare($1.name) == .orderedAscending
        }
        return sortedPhysical + simulatorDevices
    }

    static func key(_ udid: String) -> String {
        USBMuxDevice.normalizedUDID(udid).uppercased()
    }
}

/// Coordinates device discovery without busy polling:
/// - usbmuxd `Listen` pushes USB and Wi-Fi attach/detach events;
/// - CoreDevice (`devicectl`) is refreshed after those events and every 30 s for network-only
///   devices usbmuxd cannot see;
/// - simulators are refreshed when FSEvents reports a change under CoreSimulator/Devices.
public actor DeviceDiscovery {
    public struct Configuration: Sendable {
        public var usbmux: USBMuxClient
        public var coreDevice: CoreDeviceClient?
        public var simulators: SimulatorClient?
        public var enrichWithLockdown: Bool
        public var coreDeviceInterval: TimeInterval
        public var fallbackSimulatorInterval: TimeInterval

        public init(usbmux: USBMuxClient = USBMuxClient(), coreDevice: CoreDeviceClient? = CoreDeviceClient(), simulators: SimulatorClient? = SimulatorClient(), enrichWithLockdown: Bool = true, coreDeviceInterval: TimeInterval = 30, fallbackSimulatorInterval: TimeInterval = 120) {
            self.usbmux = usbmux
            self.coreDevice = coreDevice
            self.simulators = simulators
            self.enrichWithLockdown = enrichWithLockdown
            self.coreDeviceInterval = coreDeviceInterval
            self.fallbackSimulatorInterval = fallbackSimulatorInterval
        }
    }

    private let configuration: Configuration
    private let logger = ToolkitLog.deviceDiscovery
    private var usbmuxDevices: [Int: USBMuxDevice] = [:]
    private var enrichment: [String: LockdownEnrichment] = [:]
    private var enrichmentTasks: [String: Task<Void, Never>] = [:]
    private var coreDeviceRecords: [CoreDeviceRecord] = []
    private var simulatorRecords: [SimulatorRecord] = []
    private var snapshot = DiscoverySnapshot()
    private var observers: [UUID: AsyncStream<DiscoverySnapshot>.Continuation] = [:]
    private var tasks: [Task<Void, Never>] = []
    private var watcher: DirectoryWatcher?
    private var coreDeviceRefreshTask: Task<Void, Never>?
    private var simulatorRefreshTask: Task<Void, Never>?
    private var started = false

    public init(configuration: Configuration = Configuration()) {
        self.configuration = configuration
    }

    public var current: DiscoverySnapshot { snapshot }

    public func updates() -> AsyncStream<DiscoverySnapshot> {
        let identifier = UUID()
        let (stream, continuation) = AsyncStream<DiscoverySnapshot>.makeStream(bufferingPolicy: .bufferingNewest(1))
        continuation.yield(snapshot)
        observers[identifier] = continuation
        continuation.onTermination = { [weak self] _ in
            Task { await self?.removeObserver(identifier) }
        }
        return stream
    }

    private func removeObserver(_ identifier: UUID) {
        observers[identifier] = nil
    }

    public func start() {
        guard !started else { return }
        started = true
        tasks.append(Task { [weak self] in await self?.runUSBMuxListener() })
        if configuration.coreDevice != nil {
            tasks.append(Task { [weak self] in
                while !Task.isCancelled {
                    await self?.refreshCoreDevice()
                    try? await Task.sleep(nanoseconds: UInt64((self?.configuration.coreDeviceInterval ?? 30) * 1_000_000_000))
                }
            })
        }
        if configuration.simulators != nil {
            let path = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Developer/CoreSimulator/Devices").path
            let watcher = DirectoryWatcher(path: path, latency: 1.0) { [weak self] in
                Task { await self?.scheduleSimulatorRefresh() }
            }
            let watching = watcher.start()
            self.watcher = watcher
            tasks.append(Task { [weak self] in
                while !Task.isCancelled {
                    await self?.refreshSimulators()
                    let interval = watching ? (self?.configuration.fallbackSimulatorInterval ?? 120) : 15
                    try? await Task.sleep(nanoseconds: UInt64(interval * 1_000_000_000))
                }
            })
        }
        logger.info("Device discovery started")
    }

    public func stop() {
        tasks.forEach { $0.cancel() }
        tasks.removeAll()
        enrichmentTasks.values.forEach { $0.cancel() }
        enrichmentTasks.removeAll()
        coreDeviceRefreshTask?.cancel()
        simulatorRefreshTask?.cancel()
        watcher?.stop()
        watcher = nil
        started = false
        for observer in observers.values { observer.finish() }
        observers.removeAll()
    }

    /// Explicit refresh ("Refresh" button, ⌘R).
    public func refreshAll() async {
        await refreshUSBMuxList()
        async let core: Void = refreshCoreDevice()
        async let sims: Void = refreshSimulators()
        _ = await (core, sims)
        for device in usbmuxDevices.values { scheduleEnrichment(device, force: true) }
    }

    // MARK: usbmuxd

    private func runUSBMuxListener() async {
        var backoff: UInt64 = 1
        while !Task.isCancelled {
            guard configuration.usbmux.isSocketPresent else {
                snapshot.usbmux = .unavailable(reason: "The macOS device service (usbmuxd) is not running.")
                publish()
                try? await Task.sleep(nanoseconds: 10_000_000_000)
                continue
            }
            await refreshUSBMuxList()
            do {
                for try await event in configuration.usbmux.listen() {
                    backoff = 1
                    await handle(event)
                }
            } catch {
                logger.error("usbmuxd listener ended: \((error as? ToolkitError)?.kind.rawValue ?? "error", privacy: .public) \(String(describing: error), privacy: .private)")
            }
            try? await Task.sleep(nanoseconds: backoff * 1_000_000_000)
            backoff = min(backoff * 2, 30)
        }
    }

    private func refreshUSBMuxList() async {
        do {
            let devices = try await configuration.usbmux.listDevices()
            usbmuxDevices = Dictionary(devices.map { ($0.deviceID, $0) }, uniquingKeysWith: { $1 })
            snapshot.usbmux = .available(count: Set(devices.map(\.udid)).count)
            for device in devices { scheduleEnrichment(device, force: false) }
        } catch {
            usbmuxDevices = [:]
            snapshot.usbmux = .unavailable(reason: (error as? ToolkitError)?.message ?? error.localizedDescription)
        }
        publish()
    }

    private func handle(_ event: USBMuxEvent) async {
        switch event {
        case .attached(let device):
            logger.info("Device attached via \(device.connectionType, privacy: .public)")
            usbmuxDevices[device.deviceID] = device
            scheduleEnrichment(device, force: false)
            scheduleCoreDeviceRefresh()
        case .detached(let deviceID):
            logger.info("Device detached")
            if let device = usbmuxDevices.removeValue(forKey: deviceID),
               !usbmuxDevices.values.contains(where: { $0.udid == device.udid }) {
                enrichmentTasks[DeviceMerger.key(device.udid)]?.cancel()
                enrichmentTasks[DeviceMerger.key(device.udid)] = nil
                enrichment[DeviceMerger.key(device.udid)] = nil
            }
            scheduleCoreDeviceRefresh()
        case .paired(let deviceID):
            if let device = usbmuxDevices[deviceID] { scheduleEnrichment(device, force: true) }
            scheduleCoreDeviceRefresh()
        }
        snapshot.usbmux = .available(count: Set(usbmuxDevices.values.map(\.udid)).count)
        publish()
    }

    // MARK: Lockdown enrichment

    private func scheduleEnrichment(_ device: USBMuxDevice, force: Bool) {
        guard configuration.enrichWithLockdown else { return }
        let identifier = DeviceMerger.key(device.udid)
        if !force, enrichment[identifier] != nil || enrichmentTasks[identifier] != nil { return }
        enrichmentTasks[identifier]?.cancel()
        let usbmux = configuration.usbmux
        enrichmentTasks[identifier] = Task { [weak self] in
            let result = await Self.enrich(device, usbmux: usbmux)
            await self?.store(result, for: identifier)
        }
    }

    private func store(_ result: LockdownEnrichment, for identifier: String) {
        enrichmentTasks[identifier] = nil
        guard usbmuxDevices.values.contains(where: { DeviceMerger.key($0.udid) == identifier }) else { return }
        enrichment[identifier] = result
        publish()
    }

    static func enrich(_ device: USBMuxDevice, usbmux: USBMuxClient) async -> LockdownEnrichment {
        let target = DeviceTarget(kind: .physical, udid: device.udid, name: "iOS device", osVersion: nil, usbmuxDeviceID: device.deviceID, coreDeviceIdentifier: nil, transport: device.transport)
        do {
            return try await DeviceSession.with(target, usbmux: usbmux) { session in
                let values = try await session.getValue()
                let developerMode = try? await session.developerModeEnabled()
                return LockdownEnrichment(
                    name: values?["DeviceName"]?.stringValue,
                    productType: values?["ProductType"]?.stringValue,
                    productVersion: values?["ProductVersion"]?.stringValue,
                    buildVersion: values?["BuildVersion"]?.stringValue,
                    architecture: values?["CPUArchitecture"]?.stringValue,
                    hardwareModel: values?["HardwareModel"]?.stringValue,
                    serialNumber: values?["SerialNumber"]?.stringValue,
                    pairingState: .paired,
                    developerMode: developerMode.map { $0 ? .enabled : .disabled } ?? .unknown
                )
            }
        } catch let error as ToolkitError {
            let basic = try? await DeviceSession.basicInfo(for: device, usbmux: usbmux)
            let pairing: PairingState
            switch error.kind {
            case .notPaired: pairing = .unpaired
            case .pairingPending: pairing = .pairingInProgress
            case .deviceLocked: pairing = .paired
            default: pairing = .unknown
            }
            return LockdownEnrichment(
                name: basic?.deviceName,
                productType: basic?.productType,
                productVersion: basic?.productVersion,
                buildVersion: basic?.buildVersion,
                pairingState: pairing,
                problem: error.message
            )
        } catch {
            return LockdownEnrichment(problem: error.localizedDescription)
        }
    }

    // MARK: CoreDevice

    private func scheduleCoreDeviceRefresh() {
        guard configuration.coreDevice != nil else { return }
        coreDeviceRefreshTask?.cancel()
        coreDeviceRefreshTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: 1_500_000_000)
            guard !Task.isCancelled else { return }
            await self?.refreshCoreDevice()
        }
    }

    private func refreshCoreDevice() async {
        guard let client = configuration.coreDevice else {
            snapshot.coreDevice = .unavailable(reason: "Not configured")
            return
        }
        do {
            coreDeviceRecords = try await client.listDevices()
            snapshot.coreDevice = .available(count: coreDeviceRecords.filter { $0.transport != nil }.count)
        } catch {
            coreDeviceRecords = []
            let reason = (error as? ToolkitError).map { $0.kind == .toolMissing ? "Xcode is not installed, so developer services are unavailable." : $0.message } ?? error.localizedDescription
            snapshot.coreDevice = .unavailable(reason: reason)
        }
        publish()
    }

    // MARK: Simulators

    private func scheduleSimulatorRefresh() {
        simulatorRefreshTask?.cancel()
        simulatorRefreshTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: 300_000_000)
            guard !Task.isCancelled else { return }
            await self?.refreshSimulators()
        }
    }

    private func refreshSimulators() async {
        guard let client = configuration.simulators else { return }
        do {
            simulatorRecords = try await client.list()
            snapshot.simulators = .available(count: simulatorRecords.filter(\.isAvailable).count)
        } catch {
            simulatorRecords = []
            let reason = (error as? ToolkitError).map { $0.kind == .toolMissing ? "Xcode is not installed, so simulators are unavailable." : $0.message } ?? error.localizedDescription
            snapshot.simulators = .unavailable(reason: reason)
        }
        publish()
    }

    // MARK: Publishing

    private func publish() {
        snapshot.devices = DeviceMerger.merge(usbmux: Array(usbmuxDevices.values), enrichment: enrichment, coreDevice: coreDeviceRecords, simulators: simulatorRecords)
        snapshot.updatedAt = Date()
        for observer in observers.values { observer.yield(snapshot) }
    }
}
