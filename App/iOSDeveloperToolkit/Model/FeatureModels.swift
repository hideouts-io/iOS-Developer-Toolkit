import DeviceKit
import Foundation
import Observation
import ToolkitCore
import ToolkitFeatures

extension URL {
    static var documents: URL { FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Documents") }
}

// MARK: - Apps

/// One installed app from any source (lockdown, CoreDevice, or simctl).
struct AppRow: Identifiable, Hashable, Sendable {
    var id: String { bundleIdentifier }
    var name: String
    var bundleIdentifier: String
    var version: String
    var build: String
    var kind: String
    var sizeBytes: Int64?
    var isRemovable: Bool

    var sizeText: String { ByteFormatting.string(sizeBytes) }
}

@Observable
@MainActor
final class AppsModel {
    var rows: [AppRow] = []
    var loadedFor: DeviceTarget?
    var source = ""
    var search = ""
    var includeSystemApps = false
    var calculateSizes = true
    var sortOrder = [KeyPathComparator(\AppRow.name, comparator: .localizedStandard)]
    var selection: Set<AppRow.ID> = []
    var isLoading = false

    var visibleRows: [AppRow] {
        let filtered = rows.filter { row in
            (includeSystemApps || row.kind != "System" && row.kind != "Built-in" && row.kind != "Hidden system app")
                && (search.isEmpty || row.name.localizedCaseInsensitiveContains(search) || row.bundleIdentifier.localizedCaseInsensitiveContains(search))
        }
        return filtered.sorted(using: sortOrder)
    }

    func refresh(app: AppModel, device: Device) async {
        let target = device.target
        let runner = app.runner
        let sizes = calculateSizes
        isLoading = true
        defer { isLoading = false }
        let result: (rows: [AppRow], source: String)? = await app.run("Load installed apps", workspace: .apps, target: target, transport: device.kind == .simulator ? "simctl listapps" : (device.supportsLockdownServices ? "installation_proxy" : "devicectl device info apps")) { _ in
            switch device.kind {
            case .simulator:
                let apps = try await SimulatorClient(runner: runner).apps(target)
                return (apps.map { AppRow(name: $0.name, bundleIdentifier: $0.bundleIdentifier, version: $0.version ?? "", build: $0.build ?? "", kind: $0.applicationType == "User" ? "Installed by user" : "Built-in", sizeBytes: nil, isRemovable: $0.applicationType == "User") }, "Simulator (simctl)")
            case .physical where device.supportsLockdownServices:
                let apps = try await DeviceSession.with(target) { session in
                    let proxy = try await InstallationProxy.open(session)
                    defer { Task { await proxy.close() } }
                    return try await proxy.browse(includeSizes: sizes)
                }
                return (apps.map { AppRow(name: $0.name, bundleIdentifier: $0.bundleIdentifier, version: $0.version ?? "", build: $0.build ?? "", kind: $0.typeLabel, sizeBytes: $0.totalBytes, isRemovable: $0.applicationType == "User") }, "Installation service (USB)")
            case .physical:
                let apps = try await CoreDeviceClient(runner: runner).apps(target)
                return (apps.map { AppRow(name: $0.name, bundleIdentifier: $0.bundleIdentifier, version: $0.version ?? "", build: $0.bundleVersion ?? "", kind: $0.isDefaultApp == true ? "Built-in" : ($0.isBuiltByDeveloper == true ? "Developer build" : "Installed by user"), sizeBytes: nil, isRemovable: $0.isRemovable ?? false) }, "Xcode device service")
            case .demo:
                return ([
                    AppRow(name: "Safari", bundleIdentifier: "com.apple.mobilesafari", version: "26.0", build: "8621", kind: "Built-in", sizeBytes: nil, isRemovable: false),
                    AppRow(name: "Sample App", bundleIdentifier: "com.example.sample", version: "1.4", build: "28", kind: "Installed by user", sizeBytes: 48_200_000, isRemovable: true),
                ], "Demo data")
            }
        }
        if let result {
            rows = result.rows
            source = result.source
            loadedFor = target
        }
    }

    func uninstall(_ row: AppRow, app: AppModel, device: Device) async {
        let target = device.target
        let runner = app.runner
        let succeeded = await app.run("Remove \(row.name)", workspace: .apps, target: target, transport: device.kind == .simulator ? "simctl uninstall" : "installation_proxy / devicectl", argv: [row.bundleIdentifier]) { operation in
            switch device.kind {
            case .simulator:
                try await SimulatorClient(runner: runner).uninstall(bundleIdentifier: row.bundleIdentifier, on: target)
            case .physical where device.supportsLockdownServices:
                try await DeviceSession.with(target) { session in
                    let proxy = try await InstallationProxy.open(session)
                    defer { Task { await proxy.close() } }
                    try await proxy.uninstall(bundleIdentifier: row.bundleIdentifier) { percent in operation.report("\(percent)%", progress: Double(percent) / 100) }
                }
            case .physical:
                _ = try await CoreDeviceClient(runner: runner).uninstall(bundleIdentifier: row.bundleIdentifier, on: target)
            case .demo:
                throw ToolkitError(.unsupported, message: "Demo Mode cannot change apps.")
            }
            return true
        }
        if succeeded == true {
            rows.removeAll { $0.id == row.id }
            app.statusMessage = "Removed \(row.name) from \(target.name)."
        }
    }
}

// MARK: - Install

@Observable
@MainActor
final class InstallModel {
    var packageURL: URL?
    var inspection: IPAInspection?
    var appBundleName: String?
    var installAsDeveloperPackage = false

    var isAppBundle: Bool { packageURL?.pathExtension == "app" }

    func choose(_ url: URL, app: AppModel) async {
        packageURL = url
        inspection = nil
        appBundleName = nil
        if url.pathExtension == "app" {
            appBundleName = url.deletingPathExtension().lastPathComponent
            return
        }
        let result = await app.run("Inspect \(url.lastPathComponent)", workspace: .installApp, target: nil, transport: "Local inspection (Security.framework)", outputPaths: [url.path]) { _ in
            try IPAInspector.inspect(url)
        }
        inspection = result
    }

    func install(app: AppModel, device: Device) async {
        guard let url = packageURL else { return }
        let target = device.target
        let runner = app.runner
        let developer = installAsDeveloperPackage
        let succeeded = await app.run("Install \(url.lastPathComponent)", workspace: .installApp, target: target, transport: device.kind == .simulator ? "simctl install" : (device.supportsCoreDevice ? "devicectl device install app" : "AFC + installation_proxy"), argv: [url.path]) { operation in
            switch device.kind {
            case .simulator:
                try await SimulatorClient(runner: runner).install(appAt: url, on: target)
            case .physical where device.supportsCoreDevice:
                _ = try await CoreDeviceClient(runner: runner).install(appAt: url, on: target)
            case .physical:
                guard url.pathExtension == "ipa" else { throw ToolkitError(.unsupported, message: "Without Xcode, only .ipa packages can be installed.") }
                try await DeviceSession.with(target) { session in
                    let afc = try await AFCClient.openMedia(session)
                    let stagedName = "idt-\(UUID().uuidString.prefix(8)).ipa"
                    try? await afc.makeDirectory("/PublicStaging")
                    try await afc.upload(url, to: "/PublicStaging/\(stagedName)") { sent, total in
                        operation.report("Uploading \(ByteFormatting.string(sent)) of \(ByteFormatting.string(total))", progress: total > 0 ? Double(sent) / Double(total) * 0.5 : nil)
                    }
                    await afc.close()
                    let proxy = try await InstallationProxy.open(session)
                    defer { Task { await proxy.close() } }
                    try await proxy.install(stagedPackagePath: "PublicStaging/\(stagedName)", developerPackage: developer) { percent in
                        operation.report("Installing \(percent)%", progress: 0.5 + Double(percent) / 200)
                    }
                }
            case .demo:
                throw ToolkitError(.unsupported, message: "Demo Mode cannot install apps.")
            }
            return true
        }
        if succeeded == true {
            app.statusMessage = "Installed \(inspection?.appName ?? url.lastPathComponent) on \(target.name)."
            await app.apps.refresh(app: app, device: device)
        }
    }
}

// MARK: - Backup

@Observable
@MainActor
final class BackupModel {
    var destination = URL.documents.appendingPathComponent("iOS Developer Toolkit Backups")
    var forceFullBackup = false
    var requireEncryption = true
    var encryptionEnabled: Bool?
    var encryptionCheckedFor: DeviceTarget?
    var newPassword = ""
    var confirmPassword = ""
    var progress: Double?
    var log: [String] = []
    var lastBackup: URL?
    var isRunning = false
    var activeOperation: RunningOperation?

    var passwordsValid: Bool {
        newPassword.count >= 8 && newPassword == confirmPassword
    }

    func checkEncryption(app: AppModel, target: DeviceTarget) async {
        let enabled = await app.run("Check backup encryption", workspace: .backup, target: target, transport: "lockdownd com.apple.mobile.backup") { _ in
            try await DeviceSession.with(target) { try await MobileBackup2.isEncryptionEnabled($0) }
        }
        encryptionEnabled = enabled
        encryptionCheckedFor = enabled == nil ? nil : target
    }

    func enableEncryption(app: AppModel, target: DeviceTarget) async {
        guard passwordsValid else { return }
        let password = newPassword
        newPassword = ""
        confirmPassword = ""
        isRunning = true
        defer { isRunning = false }
        let succeeded = await app.run("Turn on backup encryption", workspace: .backup, target: target, transport: "com.apple.mobilebackup2 ChangePassword", onStart: { [weak self] in self?.activeOperation = $0 }) { [weak self] _ in
            try await DeviceSession.with(target) { session in
                try await MobileBackup2.enableEncryption(session, newPassword: password) { event in
                    Task { @MainActor in self?.handle(event) }
                }
            }
            return true
        }
        activeOperation = nil
        if succeeded == true { encryptionEnabled = true }
    }

    func startBackup(app: AppModel, target: DeviceTarget) async {
        let options = BackupOptions(destinationRoot: destination, forceFullBackup: forceFullBackup)
        let mustEncrypt = requireEncryption
        log = []
        progress = 0
        isRunning = true
        defer { isRunning = false }
        let result = await app.run("Back up \(target.name)", workspace: .backup, target: target, transport: "com.apple.mobilebackup2", outputPaths: [destination.path], onStart: { [weak self] in self?.activeOperation = $0 }) { [weak self] operation in
            try await DeviceSession.with(target) { session in
                if mustEncrypt, try await !MobileBackup2.isEncryptionEnabled(session) {
                    throw ToolkitError(.invalidInput, message: "Backup encryption is off on this device.", recovery: "Turn on encryption with a new backup password first, or clear “Require encrypted backup”.")
                }
                return try await MobileBackup2.backup(session, options: options) { event in
                    if case .progress(let value) = event { operation.report("\(Int(value))%", progress: value / 100) }
                    Task { @MainActor in self?.handle(event) }
                }
            }
        }
        progress = nil
        activeOperation = nil
        if let result {
            lastBackup = result
            app.statusMessage = "Backup of \(target.name) finished."
        }
    }

    private func handle(_ event: BackupEvent) {
        switch event {
        case .status(let message): log.append(message)
        case .progress(let value): progress = value / 100
        case .encryption(let enabled):
            encryptionEnabled = enabled
            log.append(enabled ? "Backups from this device are encrypted." : "Backup encryption is off on this device.")
        case .bytesReceived(let bytes): if log.last?.hasPrefix("Received") == true { log.removeLast() }; log.append("Received \(ByteFormatting.string(bytes))")
        case .finished(let url): log.append("Saved to \(url.path)")
        }
    }
}

// MARK: - Evidence

@Observable
@MainActor
final class EvidenceModel {
    var outputRoot = URL.documents.appendingPathComponent("iOS Developer Toolkit Cases")
    var options = CollectionOptions()
    var caseTitle = ""
    var casePurpose = ""
    var authorized = false
    var activeCase: URL?
    var activeIntake: CaseIntake?
    var steps: [CollectionStep] = []
    var currentStep: String?
    var secondsRemaining: Int?
    var manifest: CollectionManifest?
    var isCollecting = false
    private var collector: EvidenceCollector?

    func createCase(app: AppModel, target: DeviceTarget) {
        do {
            let (folder, intake) = try CaseWorkflow.createGuidedCase(in: outputRoot, target: target, title: caseTitle, purpose: casePurpose, authorized: authorized)
            activeCase = folder
            activeIntake = intake
            manifest = nil
            steps = []
            app.statusMessage = "Created case “\(intake.title)”."
        } catch {
            app.present(error)
        }
    }

    func collect(app: AppModel, device: Device) async {
        let target = device.target
        let folder: URL
        do {
            if let activeCase {
                try CaseWorkflow.validateForCollection(activeCase, target: target)
                folder = activeCase
            } else {
                folder = try CaseWorkflow.createCaseFolder(in: outputRoot, target: target)
            }
            collector = try EvidenceCollector(device: device, caseFolder: folder, options: options, runner: app.runner)
        } catch {
            app.present(error)
            return
        }
        guard let collector else { return }
        steps = []
        manifest = nil
        isCollecting = true
        defer {
            isCollecting = false
            currentStep = nil
            secondsRemaining = nil
            self.collector = nil
        }
        let result = await app.run("Evidence collection", workspace: .evidence, target: target, transport: "Native services + CoreDevice", outputPaths: [folder.path]) { [weak self] _ in
            await collector.run { event in
                Task { @MainActor in
                    switch event {
                    case .stepStarted(let title): self?.currentStep = title
                    case .stepFinished(let step): self?.steps.append(step)
                    case .streaming(let remaining): self?.secondsRemaining = remaining
                    case .finalizing: self?.currentStep = "Writing manifest and hashes"
                    }
                }
            }
        }
        manifest = result
        activeCase = nil
        activeIntake = nil
        if let result {
            app.statusMessage = "Evidence case \(result.outcome == .complete ? "completed" : result.outcome == .partial ? "finished with coverage gaps" : "could not identify the device")."
        }
        lastCaseFolder = folder
    }

    var lastCaseFolder: URL?

    func stop() {
        guard let collector else { return }
        Task { await collector.requestStop() }
    }
}

// MARK: - External tools

@Observable
@MainActor
final class ExternalToolsModel {
    // MVT
    var mvtPath = MVTConnector.discover().first ?? ""
    var mvt: ValidatedExecutable?
    var mvtBackup: URL?
    var mvtOutputParent = URL.documents
    var mvtOutputName = "MVT Results \(ISO8601.compactUTC(Date()))"
    var mvtIndicators: [URL] = []
    var mvtFast = false
    var mvtHashes = false
    var mvtAllowNetwork = false
    var mvtConsent = false
    var mvtNoVerdict = false
    var mvtOutput = ""
    var mvtRunning = false

    // UFADE
    var ufadeCheckout: URL?
    var ufadePython = ""
    var ufadeWorkingDirectory = URL.documents
    var ufade: UFADEConnector.Installation?

    // idb
    var idbPath = IDBCompanionConnector.discover().first ?? ""
    var idb: ValidatedExecutable?
    var idbOutput = ""

    func validateMVT(app: AppModel) async {
        let path = mvtPath
        let runner = app.runner
        mvt = await app.run("Validate MVT", workspace: .externalTools, target: nil, transport: "mvt-ios version", argv: [path]) { _ in
            try await MVTConnector.validate(executablePath: path, runner: runner)
        }
    }

    func runMVT(app: AppModel) async {
        guard let mvt, let backupSelection = mvtBackup else { return }
        let backup: URL
        do { backup = try MVTConnector.resolveBackup(backupSelection) } catch { app.present(error); return }
        let request = MVTConnector.AnalysisRequest(executable: mvt, backup: backup, output: mvtOutputParent.appendingPathComponent(mvtOutputName), indicatorFiles: mvtIndicators, fast: mvtFast, hashes: mvtHashes, allowNetwork: mvtAllowNetwork)
        let runner = app.runner
        mvtOutput = ""
        mvtRunning = true
        defer { mvtRunning = false }
        let result = await app.run("MVT backup analysis", workspace: .externalTools, target: nil, transport: "mvt-ios check-backup", argv: MVTConnector.arguments(for: request), outputPaths: [request.output.path]) { [weak self] _ in
            let config = try SecureFileIO.makeTemporaryDirectory(prefix: "idt-mvt-config")
            defer { try? FileManager.default.removeItem(at: config) }
            let command = try MVTConnector.analysisRequest(request, configDirectory: config)
            var final: CommandResult?
            for try await event in runner.stream(command) {
                switch event {
                case .standardOutput(let data), .standardError(let data):
                    let text = String(decoding: data, as: UTF8.self)
                    Task { @MainActor in self?.mvtOutput += text }
                case .finished(let result): final = result
                }
            }
            guard let final, final.succeeded else {
                throw ToolkitError(.commandFailed, message: "MVT stopped before finishing; the result folder is partial.", technicalDetail: final?.technicalSummary)
            }
            return final
        }
        if result != nil { app.statusMessage = "MVT finished. Review its output directly; no verdict is implied." }
    }

    func validateUFADE(app: AppModel) async {
        guard let checkout = ufadeCheckout else { return }
        let python = ufadePython.isEmpty ? checkout.appendingPathComponent(".venv/bin/python").path : ufadePython
        let runner = app.runner
        ufade = await app.run("Validate UFADE", workspace: .externalTools, target: nil, transport: "UFADE Python checks", argv: [checkout.path]) { _ in
            try await UFADEConnector.validate(checkout: checkout, python: python, runner: runner)
        }
    }

    func launchUFADE(app: AppModel) async {
        guard let ufade else { return }
        let directory = ufadeWorkingDirectory
        _ = await app.run("Launch UFADE", workspace: .externalTools, target: nil, transport: "UFADE (separate process)") { _ in
            try UFADEConnector.launch(ufade, workingDirectory: directory)
        }
    }

    func validateIDB(app: AppModel) async {
        let path = idbPath
        let runner = app.runner
        idb = await app.run("Validate idb Companion", workspace: .externalTools, target: nil, transport: "idb_companion --version", argv: [path]) { _ in
            try await IDBCompanionConnector.validate(executablePath: path, runner: runner)
        }
    }

    func probeIDB(app: AppModel) async {
        guard let idb else { return }
        let runner = app.runner
        if let result = await app.run("idb Companion inventory", workspace: .externalTools, target: nil, transport: "idb_companion --list 1", { _ in try await runner.run(try IDBCompanionConnector.probeRequest(idb)) }) {
            idbOutput = result.standardOutputText + result.standardErrorText
        }
    }
}
