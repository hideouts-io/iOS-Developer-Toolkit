import DeviceKit
import Foundation
import ToolkitCore

/// The result of running an action.
public struct ActionResult: Sendable, Hashable {
    public var actionID: String
    public var title: String
    public var target: DeviceTarget?
    /// Plain-language headline for non-technical users.
    public var summary: String
    /// Readable detail lines ("key: value").
    public var details: [(String, String)]
    /// Complete raw output (JSON/plist/text) for advanced users.
    public var raw: String
    public var outputFiles: [URL]
    public var mechanism: String
    public var argv: [String]
    public var startedAt: Date
    public var finishedAt: Date

    public static func == (lhs: ActionResult, rhs: ActionResult) -> Bool {
        lhs.actionID == rhs.actionID && lhs.startedAt == rhs.startedAt && lhs.raw == rhs.raw
    }

    public func hash(into hasher: inout Hasher) {
        hasher.combine(actionID)
        hasher.combine(startedAt)
    }
}

/// Runs catalog actions. The target is captured when the action starts and passed explicitly to
/// every underlying call.
public struct ActionExecutor: Sendable {
    public let runner: CommandRunning
    public let coreDevice: CoreDeviceClient
    public let simulators: SimulatorClient
    public let usbmux: USBMuxClient
    public let location: LocationController
    public let developerImages: DeveloperImageManager
    /// Folders the user chose that contain developer images (in addition to Xcode's).
    public let developerImageFolders: [URL]

    public init(runner: CommandRunning = ProcessCommandRunner(), usbmux: USBMuxClient = USBMuxClient(), developerImageFolders: [URL] = [], personalization: PersonalizationTransport = AppleTSSTransport()) {
        self.runner = runner
        coreDevice = CoreDeviceClient(runner: runner)
        simulators = SimulatorClient(runner: runner)
        self.usbmux = usbmux
        location = LocationController(coreDevice: coreDevice, simulators: simulators, usbmux: usbmux)
        developerImages = DeveloperImageManager(usbmux: usbmux, coreDevice: coreDevice, transport: personalization)
        self.developerImageFolders = developerImageFolders
    }

    public func execute(_ action: ActionDescriptor, target: DeviceTarget?, values: [String: String]) async throws -> ActionResult {
        let parameters = try ActionCatalog.validate(action, values: values)
        if let target {
            guard action.supports(target.kind) else {
                throw ToolkitError(.unsupported, message: "“\(action.title)” is not available for \(target.kind.label.lowercased())s.")
            }
            guard target.kind != .demo else {
                throw ToolkitError(.unsupported, message: "Demo Mode shows a simulated device; actions are disabled.")
            }
        } else if action.requirements.contains(where: { $0 != .xcode }) {
            throw ToolkitError.invalidInput("Select a device first.")
        }
        let started = Date()
        var result = try await perform(action, target: target, parameters: parameters)
        result.startedAt = started
        result.finishedAt = Date()
        return result
    }

    private func requireTarget(_ target: DeviceTarget?) throws -> DeviceTarget {
        guard let target else { throw ToolkitError.invalidInput("Select a device first.") }
        return target
    }

    private func make(_ action: ActionDescriptor, _ target: DeviceTarget?, summary: String, details: [(String, String)] = [], raw: String = "", files: [URL] = [], argv: [String] = []) -> ActionResult {
        ActionResult(actionID: action.id, title: action.title, target: target, summary: summary, details: details, raw: raw, outputFiles: files, mechanism: action.mechanism, argv: argv, startedAt: Date(), finishedAt: Date())
    }

    private func session<T: Sendable>(_ target: DeviceTarget, _ body: @Sendable (DeviceSession) async throws -> T) async throws -> T {
        try await DeviceSession.with(target, usbmux: usbmux, body)
    }

    // swiftlint:disable:next cyclomatic_complexity function_body_length
    private func perform(_ action: ActionDescriptor, target: DeviceTarget?, parameters: [String: String]) async throws -> ActionResult {
        switch action.id {
        case "device-details":
            let target = try requireTarget(target)
            let (record, response) = try await coreDevice.details(target)
            var details: [(String, String)] = []
            if let record {
                details = [("Name", record.name), ("Model", record.marketingName ?? "—"), ("Hardware identifier", record.productType ?? "—"), ("System", "\(record.platform ?? "iOS") \(record.osVersion ?? "") (\(record.buildVersion ?? ""))"), ("Developer Mode", record.developerMode.label), ("Developer services", record.ddiServicesAvailable.map { $0 ? "Available" : "Not mounted" } ?? "Unknown"), ("Connection", record.transportType ?? "—"), ("Trust", record.pairingState.label)]
            }
            return make(action, target, summary: record.map { "\($0.name) — \($0.marketingName ?? $0.productType ?? "device")" } ?? "Device details received.", details: details, raw: response.json.prettyString(), argv: response.command.request.arguments)

        case "lockdown-values":
            let target = try requireTarget(target)
            let values = try await session(target) { try await $0.getValue() } ?? .dictionary([:])
            let keys = ["DeviceName", "ProductType", "ProductVersion", "BuildVersion", "DeviceClass", "HardwareModel", "CPUArchitecture", "ActivationState", "TimeZone"]
            return make(action, target, summary: "\(values.dictionaryValue?.count ?? 0) values reported.", details: keys.compactMap { key in values[key].map { (key, $0.stringValue ?? $0.prettyJSONString()) } }, raw: values.prettyJSONString())

        case "activation-state":
            let target = try requireTarget(target)
            let state = try await session(target) { try await $0.getValue(key: "ActivationState") }?.stringValue ?? "Unknown"
            let meaning = state == "Activated" ? "The device is activated." : "The device reports “\(state)”."
            return make(action, target, summary: meaning, details: [("ActivationState", state)], raw: state)

        case "developer-mode-status":
            let target = try requireTarget(target)
            let enabled = try await session(target) { session -> Bool? in
                if let value = try await session.developerModeEnabled() { return value }
                let mounter = try await ImageMounter.open(session)
                defer { Task { await mounter.close() } }
                return try await mounter.developerModeStatus()
            }
            let summary: String
            switch enabled {
            case true?: summary = "Developer Mode is on."
            case false?: summary = "Developer Mode is off. Turn it on in Settings › Privacy & Security › Developer Mode."
            case nil: summary = "The device did not report Developer Mode (it may run iOS 15 or earlier, where it does not exist)."
            }
            return make(action, target, summary: summary, details: [("Developer Mode", enabled.map { $0 ? "On" : "Off" } ?? "Not reported")], raw: String(describing: enabled))

        case "diagnostics", "battery", "ioregistry", "mobilegestalt":
            let target = try requireTarget(target)
            let id = action.id
            let value = try await session(target) { session -> PlistValue in
                let relay = try await DiagnosticsRelay.open(session)
                defer { Task { await relay.close() } }
                switch id {
                case "battery": return try await relay.battery()
                case "ioregistry": return try await relay.ioRegistry(plane: "IODeviceTree")
                case "mobilegestalt": return try await relay.mobileGestalt(keys: DiagnosticsRelay.defaultGestaltKeys)
                default: return try await relay.all()
                }
            }
            if id == "battery" {
                let battery = BatterySummary(registry: value)
                let details: [(String, String)] = [
                    ("Charge", battery.percentage.map { "\($0)%" } ?? "—"),
                    ("Charging", battery.isCharging.map { $0 ? "Yes" : "No" } ?? "—"),
                    ("Power connected", battery.externalConnected.map { $0 ? "Yes" : "No" } ?? "—"),
                    ("Cycle count", battery.cycleCount.map(String.init) ?? "—"),
                    ("Temperature", battery.temperatureCelsius.map { String(format: "%.1f °C", $0) } ?? "—"),
                    ("Estimated health", battery.healthPercentage.map { "\($0)% of design capacity" } ?? "—"),
                ]
                return make(action, target, summary: battery.percentage.map { "Battery at \($0)%\(battery.isCharging == true ? ", charging" : "")." } ?? "Battery information received.", details: details, raw: value.prettyJSONString())
            }
            return make(action, target, summary: "\(action.title) received.", raw: value.prettyJSONString())

        case "processes":
            let target = try requireTarget(target)
            if target.usbmuxDeviceID != nil {
                let processes = try await session(target) { try await OSTraceRelay.processList($0) }
                return make(action, target, summary: processes.count == 1 ? "1 process running." : "\(processes.count) processes running.", details: processes.prefix(500).map { ("\($0.pid)", $0.name) }, raw: processes.map { "\($0.pid)\t\($0.name)" }.joined(separator: "\n"))
            }
            let processes = try await coreDevice.processes(target)
            return make(action, target, summary: "\(processes.count) processes running.", details: processes.prefix(500).map { ("\($0.pid)", $0.name) }, raw: processes.map { "\($0.pid)\t\($0.executablePath ?? "")" }.joined(separator: "\n"))

        case "lock-state":
            let target = try requireTarget(target)
            let state = try await coreDevice.lockState(target)
            return make(action, target, summary: state.summary, details: [("Passcode required now", state.passcodeRequired.map { $0 ? "Yes" : "No" } ?? "—"), ("Unlocked since restart", state.unlockedSinceBoot.map { $0 ? "Yes" : "No" } ?? "—")], raw: state.summary)

        case "displays":
            let response = try await coreDevice.displays(try requireTarget(target))
            return make(action, target, summary: "Display information received.", raw: response.json.prettyString())

        case "configuration-profiles":
            let target = try requireTarget(target)
            if target.usbmuxDeviceID != nil {
                let profiles = try await session(target) { session -> [InstalledConfigurationProfile] in
                    let service = try await ConfigurationProfileService.open(session)
                    defer { Task { await service.close() } }
                    return try await service.profiles()
                }
                return make(action, target, summary: profiles.isEmpty ? "No configuration profiles installed." : (profiles.count == 1 ? "1 configuration profile installed." : "\(profiles.count) configuration profiles installed."), details: profiles.map { ($0.displayName ?? $0.identifier, [$0.organization, $0.isActive == false ? "inactive" : nil, $0.removalDisallowed == true ? "cannot be removed by the user" : nil].compactMap { $0 }.joined(separator: " · ")) }, raw: String(decoding: (try? JSONOutput.encode(profiles)) ?? Data(), as: UTF8.self))
            }
            let response = try await coreDevice.profiles(target, type: "configuration")
            let profiles = response.result?["profiles"]?.array ?? []
            return make(action, target, summary: profiles.isEmpty ? "No configuration profiles reported." : "\(profiles.count) configuration profiles installed.", details: profiles.map { ($0["displayName"]?.string ?? $0["name"]?.string ?? "Profile", $0["identifier"]?.string ?? "") }, raw: response.json.prettyString())

        case "provisioning-profiles":
            let target = try requireTarget(target)
            let payloads = try await session(target) { session -> [Data] in
                let service = try await ProvisioningProfileService.open(session)
                defer { Task { await service.close() } }
                return try await service.copyAll()
            }
            let profiles = payloads.map(ProvisioningProfileDecoder.decode)
            let formatter = ISO8601DateFormatter()
            return make(action, target, summary: profiles.isEmpty ? "No provisioning profiles installed." : "\(profiles.count) provisioning profiles installed.", details: profiles.map { ($0.name ?? "Unnamed", "\($0.profileKind), expires \($0.expirationDate.map(formatter.string(from:)) ?? "—")\($0.isExpired ? " (expired)" : "")") }, raw: String(decoding: (try? JSONOutput.encode(profiles)) ?? Data(), as: UTF8.self))

        case "orientation", "icon-metrics":
            let target = try requireTarget(target)
            let id = action.id
            let text = try await session(target) { session -> (String, String) in
                let springboard = try await SpringBoardServices.open(session)
                defer { Task { await springboard.close() } }
                if id == "orientation" {
                    let orientation = try await springboard.interfaceOrientation()
                    return (orientation.label, orientation.label)
                }
                return ("Icon metrics received.", try await springboard.homeScreenIconMetrics().prettyJSONString())
            }
            return make(action, target, summary: text.0, raw: text.1)

        case "app-query":
            let target = try requireTarget(target)
            let bundle = parameters["bundle"] ?? ""
            let apps = try await session(target) { session -> [InstalledApplication] in
                let proxy = try await InstallationProxy.open(session)
                defer { Task { await proxy.close() } }
                return try await proxy.browse(includeSizes: true)
            }
            guard let app = apps.first(where: { $0.bundleIdentifier == bundle }) else {
                throw ToolkitError(.commandFailed, message: "\(bundle) is not installed (or not visible to the installation service).")
            }
            return make(action, target, summary: "\(app.name) \(app.version ?? "") is installed.", details: [("Name", app.name), ("Bundle identifier", app.bundleIdentifier), ("Version", "\(app.version ?? "—") (\(app.build ?? "—"))"), ("Type", app.typeLabel), ("Size", ByteFormatting.string(app.totalBytes))], raw: "\(app)")

        case "media-list":
            let target = try requireTarget(target)
            let path = parameters["path"] ?? "/"
            let entries = try await session(target) { session -> [String] in
                let afc = try await AFCClient.openMedia(session)
                defer { Task { await afc.close() } }
                return try await afc.listDirectory(path)
            }
            return make(action, target, summary: "\(entries.count) items in \(path).", details: entries.map { ($0, "") }, raw: entries.joined(separator: "\n"))

        case "crash-list":
            let target = try requireTarget(target)
            let files = try await session(target) { session -> [String] in
                let afc = try await AFCClient.openCrashReports(session)
                defer { Task { await afc.close() } }
                return try await afc.walk("/")
            }
            return make(action, target, summary: files.isEmpty ? "No crash reports are available." : "\(files.count) reports available.", details: files.prefix(1000).map { ($0, "") }, raw: files.joined(separator: "\n"))

        case "crash-pull":
            let target = try requireTarget(target)
            let parent = URL(fileURLWithPath: parameters["folder"] ?? "")
            let folder = parent.appendingPathComponent("Crash Reports \(ISO8601.compactUTC(Date())) \(target.confirmationSuffix)")
            try SecureFileIO.createNewPrivateDirectory(at: folder)
            let copied = try await session(target) { session -> Int in
                let afc = try await AFCClient.openCrashReports(session)
                defer { Task { await afc.close() } }
                var count = 0
                for path in try await afc.walk("/") {
                    let destination = try SecureFileIO.safeChild(of: folder, relativePath: String(path.drop { $0 == "/" }))
                    try SecureFileIO.createPrivateDirectory(at: destination.deletingLastPathComponent())
                    _ = try await afc.download(path, to: destination)
                    count += 1
                }
                return count
            }
            try HashManifest.write(for: folder)
            return make(action, target, summary: "Copied \(copied) reports.", details: [("Folder", folder.path)], raw: folder.path, files: [folder])

        case "ddi-status":
            let target = try requireTarget(target)
            let status = await developerImages.status(for: target, userFolders: developerImageFolders)
            return make(action, target, summary: status.headline, details: status.detailRows, raw: status.explanation + (status.technicalDetail.map { "\n\n\($0)" } ?? ""))

        case "ddi-prepare":
            let target = try requireTarget(target)
            let mechanism = DeveloperImageMechanism.allCases.first { $0.label == parameters["mechanism"] } ?? .automatic
            let status = try await developerImages.mount(target, mechanism: mechanism, userFolders: developerImageFolders)
            return make(action, target, summary: status.headline, details: status.detailRows, raw: status.explanation)

        case "ddi-unmount":
            let target = try requireTarget(target)
            let status = try await developerImages.unmount(target, userFolders: developerImageFolders)
            return make(action, target, summary: "The developer image is no longer mounted.", details: status.detailRows, raw: status.explanation)

        case "mounted-images", "personalization":
            let target = try requireTarget(target)
            let id = action.id
            let output = try await session(target) { session -> (String, String) in
                let mounter = try await ImageMounter.open(session)
                defer { Task { await mounter.close() } }
                if id == "mounted-images" {
                    let images = try await mounter.mountedImages()
                    return (images.isEmpty ? "No images are mounted." : "\(images.count) images mounted.", images.map { $0.raw.prettyJSONString() }.joined(separator: "\n"))
                }
                return ("Personalization identifiers received.", try await mounter.personalizationIdentifiers().prettyJSONString())
            }
            return make(action, target, summary: output.0, raw: output.1)

        case "host-ddis-update":
            let response = try await coreDevice.updateHostDDIs()
            return make(action, target, summary: "This Mac's developer images are up to date.", raw: response.json.prettyString())

        case "preferred-ddi":
            let response = try await coreDevice.preferredDDI()
            return make(action, target, summary: "Preferred developer image received.", raw: response.json.prettyString())

        case "screenshot":
            let target = try requireTarget(target)
            let output = URL(fileURLWithPath: parameters["output"] ?? "")
            if target.kind == .simulator {
                try await simulators.screenshot(target, to: output)
            } else {
                _ = try await coreDevice.screenshot(target, to: output)
            }
            return make(action, target, summary: "Saved the screenshot.", details: [("File", output.path), ("SHA-256", (try? SecureFileIO.sha256(of: output)) ?? "—")], raw: output.path, files: [output])

        case "sysdiagnose":
            let target = try requireTarget(target)
            let folder = URL(fileURLWithPath: parameters["folder"] ?? "")
            let response = try await coreDevice.sysdiagnose(target, destination: folder, fullLogs: false)
            return make(action, target, summary: "Sysdiagnose saved.", details: [("Folder", folder.path)], raw: response.json.prettyString(), files: [folder])

        case "instruments":
            let target = try requireTarget(target)
            let output = URL(fileURLWithPath: parameters["output"] ?? "")
            let request = try InstrumentsRecorder.request(template: parameters["template"] ?? "", target: target, durationSeconds: Int(parameters["duration"] ?? "") ?? 15, output: output)
            let result = try await runner.run(request)
            guard result.succeeded else {
                throw ToolkitError(.commandFailed, message: "Instruments could not record from the device.", recovery: "Make sure Developer Mode is on, the device is unlocked, and developer services are prepared.", technicalDetail: result.technicalSummary)
            }
            return make(action, target, summary: "Recording saved. Open it in Instruments.", details: [("File", output.path)], raw: result.standardOutputText, files: [output], argv: request.arguments)

        case "bluetooth-capture":
            let target = try requireTarget(target)
            let output = URL(fileURLWithPath: parameters["output"] ?? "")
            let seconds = Int(parameters["duration"] ?? "") ?? 30
            let writer = try PacketLoggerFileWriter(creatingNewFileAt: output)
            let ending: String
            do {
                ending = try await session(target) { session -> String in
                    let records = try await BluetoothPacketLogger.records(session)
                    return try await withThrowingTaskGroup(of: String.self) { group in
                        group.addTask {
                            for try await record in records { try writer.write(record) }
                            return "The device ended the capture early."
                        }
                        group.addTask {
                            try await Task.sleep(for: .seconds(seconds))
                            return "Captured for \(seconds) seconds."
                        }
                        let first = try await group.next() ?? ""
                        group.cancelAll()
                        return first
                    }
                }
            } catch {
                _ = try? writer.finish()
                throw error
            }
            let digest = try writer.finish()
            let count = writer.recordCount
            let summary = count == 0
                ? "\(ending) No Bluetooth packets arrived — check that the Bluetooth logging profile is installed and Bluetooth is in use."
                : "\(ending) \(count == 1 ? "1 packet" : "\(count) packets") saved."
            let byType = writer.countsByType.sorted { $0.key < $1.key }.map { (PacketLoggerRecord.label(for: $0.key), "\($0.value)") }
            return make(action, target, summary: summary, details: [("File", output.path), ("Packets", "\(count)"), ("SHA-256", digest)] + byType, raw: output.path, files: [output])

        case "packet-capture":
            let target = try requireTarget(target)
            let output = URL(fileURLWithPath: parameters["output"] ?? "")
            let seconds = Int(parameters["duration"] ?? "") ?? 30
            let writer = try PcapFileWriter(creatingNewFileAt: output)
            let ending: String
            do {
                ending = try await session(target) { session -> String in
                    try await withThrowingTaskGroup(of: String.self) { group in
                        group.addTask {
                            for try await packet in try await PacketCaptureService.stream(session) {
                                try writer.write(packet)
                            }
                            return "The device ended the capture early."
                        }
                        group.addTask {
                            try await Task.sleep(for: .seconds(seconds))
                            return "Captured for \(seconds) seconds."
                        }
                        let first = try await group.next() ?? ""
                        group.cancelAll()
                        return first
                    }
                }
            } catch {
                // Keep whatever was captured as a valid file, then report the failure.
                _ = try? writer.finish()
                throw error
            }
            let digest = try writer.finish()
            return make(action, target, summary: "\(ending) \(writer.packetCount == 1 ? "1 packet" : "\(writer.packetCount) packets") saved.", details: [("File", output.path), ("Packets", "\(writer.packetCount)"), ("SHA-256", digest)], raw: output.path, files: [output])

        case "web-tabs":
            let target = try requireTarget(target)
            let applications = try await WebInspector.openPages(on: target, usbmux: usbmux)
            let pages = applications.flatMap { app in app.pages.map { (app, $0) } }
            let summary = pages.isEmpty
                ? (applications.isEmpty ? "No app currently allows inspection." : "\(applications.count) inspectable apps, no open pages.")
                : (pages.count == 1 ? "1 inspectable page." : "\(pages.count) inspectable pages.")
            return make(action, target, summary: summary, details: pages.prefix(500).map { app, page in (page.title.flatMap { $0.isEmpty ? nil : $0 } ?? "Untitled", [page.url, app.name ?? app.bundleIdentifier, page.kindLabel].compactMap { $0 }.joined(separator: " · ")) }, raw: String(decoding: (try? JSONOutput.encode(applications)) ?? Data(), as: UTF8.self))

        case "bonjour":
            let services = await NetworkServiceBrowser.browse()
            return make(action, target, summary: services.isEmpty ? "No devices are advertising on this network." : "\(services.count) services found.", details: services.map { ($0.name, $0.meaning) }, raw: services.map { "\($0.type)\t\($0.name)\t\($0.interface ?? "")" }.joined(separator: "\n"))

        case "rvi":
            let request = try XcodeHandoff.remoteVirtualInterfaces()
            let result = try await runner.run(request)
            return make(action, target, summary: result.succeeded ? "Remote Virtual Interfaces listed." : "rvictl reported a problem.", raw: result.standardOutputText + result.standardErrorText, argv: request.arguments)

        case "launch-app":
            let target = try requireTarget(target)
            let bundle = parameters["bundle"] ?? ""
            if target.kind == .simulator {
                let output = try await simulators.launch(bundleIdentifier: bundle, on: target, terminateExisting: true)
                return make(action, target, summary: "Launched \(bundle).", raw: output)
            }
            let response = try await coreDevice.launch(bundleIdentifier: bundle, on: target, terminateExisting: true)
            return make(action, target, summary: "Launched \(bundle).", raw: response.json.prettyString())

        case "terminate":
            let target = try requireTarget(target)
            let response = try await coreDevice.terminate(pid: Int(parameters["pid"] ?? "") ?? 0, on: target, force: false)
            return make(action, target, summary: "Asked process \(parameters["pid"] ?? "") to stop.", raw: response.json.prettyString())

        case "open-url":
            let target = try requireTarget(target)
            guard let url = URL(string: parameters["url"] ?? "") else { throw ToolkitError.invalidInput("Enter a valid URL.") }
            if target.kind == .simulator {
                try await simulators.openURL(url, on: target)
            } else {
                _ = try await coreDevice.openURL(url, on: target)
            }
            return make(action, target, summary: "Opened \(url.absoluteString).")

        case "set-location":
            let target = try requireTarget(target)
            let latitude = Double(parameters["latitude"] ?? "") ?? 0
            let longitude = Double(parameters["longitude"] ?? "") ?? 0
            try await location.set(latitude: latitude, longitude: longitude, on: target)
            return make(action, target, summary: "Simulated location set to \(Coordinates(latitude: latitude, longitude: longitude).formatted).", details: [("Mechanism", location.mechanism(for: target).rawValue)])

        case "clear-location":
            let target = try requireTarget(target)
            try await location.clear(on: target)
            return make(action, target, summary: "Simulated location cleared.")

        case "reboot":
            let target = try requireTarget(target)
            _ = try await coreDevice.reboot(target)
            return make(action, target, summary: "The device is restarting.")

        case "sim-boot":
            let target = try requireTarget(target)
            try await simulators.boot(target)
            return make(action, target, summary: "\(target.name) is starting.")
        case "sim-open":
            let target = try requireTarget(target)
            try await simulators.showInSimulatorApp(target)
            return make(action, target, summary: "Opened Simulator.")
        case "sim-shutdown":
            let target = try requireTarget(target)
            try await simulators.shutdown(target)
            return make(action, target, summary: "\(target.name) is shut down.")
        case "sim-dark", "sim-light":
            let target = try requireTarget(target)
            try await simulators.setAppearance(dark: action.id == "sim-dark", on: target)
            return make(action, target, summary: "Appearance changed.")
        case "sim-erase":
            let target = try requireTarget(target)
            try await simulators.erase(target)
            return make(action, target, summary: "\(target.name) was erased.")

        default:
            throw ToolkitError(.internalInconsistency, message: "The action “\(action.title)” is not implemented.")
        }
    }

    /// Splits, binds, and classifies an Advanced Mode devicectl command without running it, so
    /// the UI can show the exact argument vector and ask for the matching confirmation.
    public static func prepareAdvanced(_ text: String, target: DeviceTarget?) throws -> (arguments: [String], risk: ActionRisk) {
        let arguments = try AdvancedCommandPolicy.bind(try ArgumentSplitter.split(text), to: target)
        return (arguments, AdvancedCommandPolicy.risk(for: arguments))
    }

    /// Runs prepared Advanced Mode arguments (no time limit; stop with task cancellation).
    public func runAdvanced(arguments: [String]) async throws -> CommandResult {
        try await runner.run(try XcodeTool.devicectl.request(arguments, timeout: nil, displayName: "devicectl (Advanced Mode)"))
    }
}
