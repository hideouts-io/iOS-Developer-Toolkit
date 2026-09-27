import DeviceKit
import Foundation
import Observation
import OSLog
import SwiftUI
import ToolkitCore
import ToolkitFeatures

/// An error ready for an alert: plain message first, technical details on request.
struct PresentedError: Identifiable, Equatable {
    let id = UUID()
    let title: String
    let message: String
    let recovery: String?
    let details: String?

    init(_ error: Error, context: String? = nil) {
        if let toolkitError = error as? ToolkitError {
            title = toolkitError.title
            message = toolkitError.message
            recovery = toolkitError.recovery
            details = toolkitError.technicalDetail
        } else {
            title = context ?? "Something Went Wrong"
            message = error.localizedDescription
            recovery = nil
            details = String(describing: error)
        }
    }
}

/// A long-running operation shown in the toolbar activity list, with a Stop control.
@Observable
@MainActor
final class RunningOperation: Identifiable {
    let id = UUID()
    let title: String
    let targetLabel: String?
    let startedAt = Date()
    var progress: Double?
    var status: String = ""
    fileprivate var cancelAction: (() -> Void)?

    init(title: String, targetLabel: String?) {
        self.title = title
        self.targetLabel = targetLabel
    }

    func cancel() {
        cancelAction?()
        status = "Stopping…"
    }

    /// Updates progress from any context.
    nonisolated func report(_ status: String, progress: Double? = nil) {
        Task { @MainActor in
            self.status = status
            if let progress { self.progress = progress }
        }
    }
}

@Observable
@MainActor
final class AppModel {
    // MARK: Services

    let runner: CommandRunning = ProcessCommandRunner()
    let journal = OperationJournal()
    let discovery: DeviceDiscovery
    let executor: ActionExecutor
    let logger = ToolkitLog.application

    // MARK: State

    var snapshot = DiscoverySnapshot()
    var selectedDeviceID: String? {
        didSet { UserDefaults.standard.set(selectedDeviceID, forKey: "selectedDeviceID") }
    }
    var workspace: Workspace = .overview
    var demoMode: Bool {
        didSet {
            UserDefaults.standard.set(demoMode, forKey: "demoMode")
            if demoMode { selectedDeviceID = DemoMode.device.id }
        }
    }
    var journalRecords: [OperationRecord] = []
    var operations: [RunningOperation] = []
    var presentedError: PresentedError?
    var developerTools: DeveloperToolsStatus?
    var readinessResults: [String: [CapabilityResult]] = [:]
    var toolchainReport = ""
    var isCommandPalettePresented = false
    var isDeveloperModeGuidePresented = false
    var isRefreshing = false
    var statusMessage: String?

    // Feature models
    let location: LocationModel
    let logs: LiveLogsModel
    let apps: AppsModel
    let backup: BackupModel
    let evidence: EvidenceModel
    let install: InstallModel
    let externalTools: ExternalToolsModel

    private var discoveryTask: Task<Void, Never>?
    private var journalTask: Task<Void, Never>?

    init(arguments: [String] = ProcessInfo.processInfo.arguments) {
        // Launch flags are `-flag YES|NO` pairs (AppKit parses arguments as key/value pairs).
        func flag(_ name: String) -> Bool? {
            guard let index = arguments.firstIndex(of: name) else { return nil }
            let value = arguments.indices.contains(index + 1) ? arguments[index + 1].uppercased() : "YES"
            return !(value == "NO" || value == "0" || value == "FALSE")
        }
        let uiTesting = flag("-ui-testing") ?? false
        let forceDemo = flag("-demo-mode") ?? false
        demoMode = forceDemo || (!uiTesting && UserDefaults.standard.bool(forKey: "demoMode"))
        selectedDeviceID = forceDemo ? DemoMode.device.id : UserDefaults.standard.string(forKey: "selectedDeviceID")
        // UI tests run without touching real discovery sources.
        let configuration: DeviceDiscovery.Configuration = uiTesting
            ? .init(usbmux: USBMuxClient(socketPath: "/nonexistent/ui-testing-usbmuxd"), coreDevice: nil, simulators: nil, enrichWithLockdown: false)
            : .init()
        discovery = DeviceDiscovery(configuration: configuration)
        executor = ActionExecutor(runner: runner)
        location = LocationModel()
        logs = LiveLogsModel()
        apps = AppsModel()
        backup = BackupModel()
        evidence = EvidenceModel()
        install = InstallModel()
        externalTools = ExternalToolsModel()
        if let destination = arguments.firstIndex(of: "-workspace").flatMap({ arguments.indices.contains($0 + 1) ? arguments[$0 + 1] : nil }).flatMap(Workspace.init(rawValue:)) {
            workspace = destination
        }
    }

    // MARK: Lifecycle

    func start() {
        guard discoveryTask == nil else { return }
        logger.info("Application started")
        let discovery = self.discovery
        discoveryTask = Task { [weak self] in
            await discovery.start()
            for await snapshot in await discovery.updates() {
                self?.apply(snapshot)
            }
        }
        let journal = self.journal
        journalTask = Task { [weak self] in
            for await records in await journal.updates() {
                self?.journalRecords = records
            }
        }
        Task { [weak self] in
            guard let self else { return }
            self.developerTools = await DeveloperToolsStatus.probe(runner: self.runner)
        }
    }

    func stop() {
        discoveryTask?.cancel()
        journalTask?.cancel()
        let discovery = self.discovery
        Task { await discovery.stop() }
        logs.stopAll()
        location.stopPlayback()
    }

    private func apply(_ snapshot: DiscoverySnapshot) {
        let previous = Set(self.snapshot.devices.map(\.id))
        self.snapshot = snapshot
        let current = Set(snapshot.devices.map(\.id))
        if let selectedDeviceID, !demoMode || selectedDeviceID != DemoMode.device.id, !current.contains(selectedDeviceID), previous.contains(selectedDeviceID) {
            statusMessage = "The selected device disconnected."
        }
        if selectedDeviceID == nil || (selectedDevice == nil && !previous.contains(selectedDeviceID ?? "")) {
            // Choose a sensible default: the first connected physical device, else nothing.
            if let firstPhysical = snapshot.physicalDevices.first { selectedDeviceID = firstPhysical.id }
        }
    }

    func refreshDevices() async {
        isRefreshing = true
        await discovery.refreshAll()
        isRefreshing = false
    }

    // MARK: Devices

    var allDevices: [Device] {
        demoMode ? [DemoMode.device] + snapshot.devices : snapshot.devices
    }

    var physicalDevices: [Device] { allDevices.filter { $0.kind == .physical || $0.kind == .demo } }
    var simulatorDevices: [Device] { allDevices.filter { $0.kind == .simulator } }

    var selectedDevice: Device? {
        guard let selectedDeviceID else { return nil }
        return allDevices.first { $0.id == selectedDeviceID }
    }

    var selectedTarget: DeviceTarget? { selectedDevice?.target }

    func readiness(for device: Device?) -> [CapabilityResult] {
        guard let device else { return [] }
        return readinessResults[device.id] ?? CapabilityRow.rows(for: device.kind).map(\.untested)
    }

    // MARK: Operations

    /// Runs `body` as a tracked, cancellable operation. Errors are presented to the user (never
    /// silently dropped) and a journal record is written either way.
    @discardableResult
    func run<T: Sendable>(
        _ title: String,
        workspace: Workspace,
        target: DeviceTarget?,
        transport: String,
        argv: [String] = [],
        outputPaths: [String] = [],
        presentErrors: Bool = true,
        onStart: ((RunningOperation) -> Void)? = nil,
        _ body: @escaping @Sendable (RunningOperation) async throws -> T
    ) async -> T? {
        let operation = RunningOperation(title: title, targetLabel: target?.shortLabel)
        operations.append(operation)
        onStart?(operation)
        let started = Date()
        let work = Task<T, Error> { try await body(operation) }
        operation.cancelAction = { work.cancel() }
        let result: Result<T, Error> = await withTaskCancellationHandler {
            do {
                return .success(try await work.value)
            } catch {
                return .failure(error)
            }
        } onCancel: {
            work.cancel()
        }
        operations.removeAll { $0.id == operation.id }

        let finished = Date()
        switch result {
        case .success(let value):
            record(title: title, workspace: workspace, target: target, transport: transport, argv: argv, started: started, finished: finished, outcome: .succeeded, error: nil, outputPaths: outputPaths)
            return value
        case .failure(let error):
            let outcome = OperationOutcome.from(error)
            record(title: title, workspace: workspace, target: target, transport: transport, argv: argv, started: started, finished: finished, outcome: outcome, error: (error as? ToolkitError)?.message ?? error.localizedDescription, outputPaths: outputPaths)
            logger.error("\(title, privacy: .public) failed: \(outcome.rawValue, privacy: .public)")
            if presentErrors && outcome != .cancelled {
                presentedError = PresentedError(error, context: title)
            } else if outcome == .cancelled {
                statusMessage = "\(title) was stopped."
            }
            return nil
        }
    }

    func record(title: String, workspace: Workspace, target: DeviceTarget?, transport: String, argv: [String], started: Date, finished: Date, outcome: OperationOutcome, error: String?, outputPaths: [String]) {
        let record = OperationRecord(title: title, workspace: workspace.title, target: target?.shortLabel ?? "This Mac", transport: transport, argv: argv, startedAt: started, finishedAt: finished, outcome: outcome, errorMessage: error, outputPaths: outputPaths)
        let journal = self.journal
        Task { await journal.append(record) }
    }

    func present(_ error: Error) {
        presentedError = PresentedError(error)
    }

    // MARK: Readiness

    func runReadiness(for device: Device) async {
        let probe = CapabilityProbe(runner: runner)
        readinessResults[device.id] = CapabilityRow.rows(for: device.kind).map(\.untested)
        let results = await run("Readiness Check", workspace: .readiness, target: device.target, transport: "Native services + CoreDevice", presentErrors: false) { _ in
            await probe.run(for: device)
        }
        guard let results else { return }
        readinessResults[device.id] = results
        if device.kind == .physical {
            try? CompatibilityStore().append(CompatibilityObservation(device: device, results: results))
        }
    }

    func runToolchainCheck() async {
        let runner = self.runner
        if let results = await run("Toolchain check", workspace: .help, target: nil, transport: "xcrun help", { _ in await ToolchainCheck.run(runner: runner) }) {
            toolchainReport = ToolchainCheck.render(results)
        }
    }
}
