import DeviceKit
import SwiftUI
import ToolkitCore
import ToolkitFeatures

// MARK: - Overview

struct OverviewView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        WorkspacePage(workspace: .overview) {
            TargetHeader()
            if let device = model.selectedDevice {
                NextStepCard(device: device)
            }
            Card(title: "Get started", systemImage: "list.number") {
                VStack(alignment: .leading, spacing: 8) {
                    step(1, "Connect and trust", "Connect the iPhone or iPad with a USB cable, unlock it, and tap Trust. Simulators appear automatically when Xcode is installed.")
                    step(2, "Check readiness", "Run the Readiness Check to see exactly which features are available and what to fix.")
                    step(3, "Do the work", "Use Actions, Location Lab, Live Logs, Apps, or Backup. Every change asks for confirmation and names the device it affects.")
                    step(4, "Clean up", "Stop streams, clear simulated locations, and store saved output somewhere protected.")
                }
            }
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 240), spacing: 12)], spacing: 12) {
                ForEach(Workspace.allCases.filter { $0 != .overview }) { workspace in
                    Button {
                        model.workspace = workspace
                    } label: {
                        VStack(alignment: .leading, spacing: 6) {
                            Label(workspace.title, systemImage: workspace.symbolName).font(.headline)
                            Text(workspace.subtitle).font(.callout).foregroundStyle(.secondary).multilineTextAlignment(.leading)
                        }
                        .frame(maxWidth: .infinity, minHeight: 64, alignment: .topLeading)
                        .padding(12)
                        .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 10))
                        .overlay(RoundedRectangle(cornerRadius: 10).strokeBorder(.separator.opacity(0.6)))
                    }
                    .buttonStyle(.plain)
                    .accessibilityIdentifier("overview-\(workspace.rawValue)")
                }
            }
        }
    }

    private func step(_ number: Int, _ title: String, _ text: String) -> some View {
        HStack(alignment: .top, spacing: 10) {
            Text("\(number)")
                .font(.callout.bold())
                .frame(width: 22, height: 22)
                .background(Color.accentColor.opacity(0.15), in: Circle())
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.callout.weight(.semibold))
                Text(text).font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}

/// Suggests the single most useful next step for the selected device.
struct NextStepCard: View {
    @Environment(AppModel.self) private var model
    let device: Device

    var body: some View {
        let (icon, title, text, action) = suggestion
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: icon).font(.title2).foregroundStyle(Color.accentColor)
            VStack(alignment: .leading, spacing: 4) {
                Text(title).font(.headline)
                Text(text).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                if let action {
                    Button(action.0, action: action.1).padding(.top, 2)
                }
            }
            Spacer()
        }
        .padding(14)
        .background(Color.accentColor.opacity(0.08), in: RoundedRectangle(cornerRadius: 10))
    }

    private var suggestion: (String, String, String, (String, () -> Void)?) {
        switch device.kind {
        case .demo:
            return ("theatermasks", "You are in Demo Mode", "Explore every workspace safely. Turn Demo Mode off in the Device menu to work with real devices.", ("Turn Off Demo Mode", { model.demoMode = false }))
        case .simulator:
            if device.simulatorState != .booted {
                return ("play.circle", "Start the simulator", "Most simulator actions need it to be running.", ("Start Simulator", { Task { _ = try? await model.executor.simulators.boot(device.target) } }))
            }
            return ("checkmark.seal", "The simulator is running", "Try Location Lab, Live Logs, or Apps.", ("Open Location Lab", { model.workspace = .location }))
        case .physical:
            if device.pairingState == .unpaired || device.pairingState == .pairingInProgress {
                return ("hand.tap", "Trust this Mac on the device", "Unlock the device and tap Trust when asked. Nothing else works until it trusts this Mac.", nil)
            }
            if device.developerMode == .disabled {
                return ("hammer", "Turn on Developer Mode", "Location simulation, launching apps, screenshots, and Instruments need Developer Mode.", ("Show Me How", { model.isDeveloperModeGuidePresented = true }))
            }
            if model.readinessResults[device.id] == nil {
                return ("checklist", "Run the Readiness Check", "See which features are ready on \(device.name) and what, if anything, needs fixing.", ("Run Readiness Check", {
                    model.workspace = .readiness
                    Task { await model.runReadiness(for: device) }
                }))
            }
            return ("checkmark.seal", "\(device.name) is ready", "Choose a workspace below.", nil)
        }
    }
}

// MARK: - Device

struct DeviceDetailView: View {
    @Environment(AppModel.self) private var model
    @State private var rawDetails: String?
    @State private var confirmation: PendingConfirmation?

    var body: some View {
        WorkspacePage(workspace: .device) {
            TargetHeader()
            if let device = model.selectedDevice {
                identity(device)
                status(device)
                if device.kind == .physical {
                    developerServices(device)
                    handoffs(device)
                }
                if device.kind == .simulator {
                    simulatorControls(device)
                }
                technical(device)
            }
            connectionDiagnostics
        }
        .sheet(item: $confirmation) { pending in
            ConfirmationSheet(title: pending.title, detail: pending.detail, requirement: pending.requirement, target: pending.target, commandPreview: nil, onConfirm: pending.action)
        }
    }

    private func identity(_ device: Device) -> some View {
        Card(title: "Identity", systemImage: "person.text.rectangle") {
            InfoRow(.name, value: device.name)
            InfoRow(.model, value: device.marketingName)
            InfoRow(.hardwareIdentifier, value: device.productType)
            InfoRow(.hardwareModel, value: device.hardwareModel)
            InfoRow(.osVersion, value: device.osVersion.map { "\(device.osName ?? "iOS") \($0)" })
            InfoRow(.buildNumber, value: device.buildVersion)
            InfoRow(.architecture, value: device.architecture)
            if device.kind == .simulator {
                InfoRow(.simulatorRuntime, value: device.simulatorRuntime)
            }
            InfoRow(.udid, value: device.udid)
            if device.kind == .physical {
                InfoRow(.serialNumber, value: device.serialNumber)
                InfoRow(.ecid, value: device.ecid)
            }
        }
    }

    private func status(_ device: Device) -> some View {
        Card(title: "Status", systemImage: "antenna.radiowaves.left.and.right") {
            InfoRow(.connection, value: device.transports.isEmpty ? "Not connected" : device.transports.map(\.label).sorted().joined(separator: " and "))
            if device.kind == .simulator {
                InfoRow(.simulatorState, value: device.simulatorState?.label)
            } else {
                InfoRow(.pairing, value: device.pairingState.label)
                HStack {
                    InfoRow(.developerMode, value: device.developerMode.label)
                    if device.developerMode != .enabled && device.kind == .physical {
                        Button("How to Turn On…") { model.isDeveloperModeGuidePresented = true }
                            .controlSize(.small)
                    }
                }
                InfoRow(.developerServices, value: device.ddiServicesAvailable.map { $0 ? "Available" : "Not prepared" } ?? (device.supportsCoreDevice ? "Unknown" : "Needs Xcode"))
                InfoRow("Reported by", device.sources.map { source -> String in
                    switch source {
                    case .usbmux: return "USB services (usbmuxd)"
                    case .coreDevice: return "Xcode device service"
                    case .simctl: return "simctl"
                    case .demo: return "Demo Mode"
                    }
                }.sorted().joined(separator: ", "), explanation: "Which macOS services currently see this device. USB services work without Xcode; developer features need Xcode's device service.")
            }
        }
    }

    private func developerServices(_ device: Device) -> some View {
        Card(title: "Developer services", systemImage: "hammer", subtitle: "Screenshots, location simulation, launching apps, and Instruments need Apple's Developer Disk Image on the device. Xcode's device service installs the correct, personalized image for you.") {
            HStack {
                Button("Check Status") { runAction("ddi-status", device) }
                Button("Prepare Developer Services") {
                    confirmation = PendingConfirmation(title: "Prepare developer services", detail: "Xcode's device service will install the Developer Disk Image that matches \(device.name). Keep the device unlocked and this Mac online.", requirement: .make(for: .deviceChange, target: device.target), target: device.target) {
                        runAction("ddi-prepare", device)
                    }
                }
                .disabled(!device.supportsCoreDevice)
                Button("Unmount Image") {
                    confirmation = PendingConfirmation(title: "Unmount the developer image", detail: "Developer services stop until they are prepared again. Restarting the device has the same effect.", requirement: .make(for: .deviceChange, target: device.target), target: device.target) {
                        runAction("ddi-unmount", device)
                    }
                }
                .disabled(!device.supportsLockdownServices)
            }
            if !device.supportsCoreDevice {
                Label("Needs Xcode. Install Xcode and open Window › Devices and Simulators once with the device connected.", systemImage: "info.circle")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }
        }
    }

    private func handoffs(_ device: Device) -> some View {
        Card(title: "Apple developer tools", systemImage: "arrow.up.forward.app") {
            HStack {
                Button("Open Xcode Project…") {
                    guard let url = FilePanels.chooseFile(title: "Choose a project", allowedExtensions: ["xcodeproj", "xcworkspace", "swift"]) else { return }
                    let runner = model.runner
                    Task { _ = await model.run("Open project in Xcode", workspace: .device, target: nil, transport: "xed") { _ in try await runner.run(try XcodeHandoff.openProject(url)) } }
                }
                Button("Open Result or Trace…") {
                    guard let url = FilePanels.chooseFile(title: "Choose an .xcresult or .trace", allowedExtensions: ["xcresult", "trace"]) else { return }
                    let runner = model.runner
                    Task { _ = await model.run("Open result bundle", workspace: .device, target: nil, transport: "open") { _ in try await runner.run(try XcodeHandoff.openResult(url)) } }
                }
                Button("Remote Virtual Interfaces") { runAction("rvi", device) }
            }
        }
    }

    private func simulatorControls(_ device: Device) -> some View {
        Card(title: "Simulator", systemImage: "macwindow") {
            HStack {
                Button("Start") { runAction("sim-boot", device) }.disabled(device.simulatorState == .booted)
                Button("Show in Simulator") { runAction("sim-open", device) }
                Button("Shut Down") {
                    confirmation = PendingConfirmation(title: "Shut down \(device.name)", detail: "Running apps in the simulator stop.", requirement: .make(for: .deviceChange, target: device.target), target: device.target) {
                        runAction("sim-shutdown", device)
                    }
                }
                .disabled(device.simulatorState != .booted)
            }
        }
    }

    private func technical(_ device: Device) -> some View {
        Card(title: "Technical details", systemImage: "curlybraces") {
            DisclosureGroup("Full device record") {
                RawOutputView(text: (try? String(decoding: JSONOutput.encode(device), as: UTF8.self)) ?? "", maxHeight: 240)
            }
            if device.supportsCoreDevice {
                Button("Load CoreDevice Details") {
                    let client = model.executor.coreDevice
                    Task {
                        rawDetails = await model.run("CoreDevice details", workspace: .device, target: device.target, transport: "devicectl device info details") { _ in
                            try await client.details(device.target).response.json.prettyString()
                        }
                    }
                }
                if let rawDetails {
                    RawOutputView(text: rawDetails, maxHeight: 280)
                }
            }
        }
    }

    private var connectionDiagnostics: some View {
        Card(title: "Connection diagnostics", systemImage: "stethoscope", subtitle: "Which macOS services are answering. Useful when a device does not appear.") {
            InfoRow("USB & Wi-Fi (usbmuxd)", model.snapshot.usbmux.summary, explanation: "macOS's own device service. It sees every trusted device connected by USB or Wi-Fi sync, even without Xcode.")
            InfoRow("Xcode devices (CoreDevice)", model.snapshot.coreDevice.summary, explanation: "Xcode's device service, needed for developer features on iOS 17 and later.")
            InfoRow("Simulators (simctl)", model.snapshot.simulators.summary, explanation: "Simulators installed with Xcode.")
            InfoRow("Last updated", model.snapshot.updatedAt.formatted(date: .omitted, time: .standard))
            if let tools = model.developerTools {
                InfoRow("Developer directory", tools.developerDirectory ?? "Not set", monospaced: true)
                InfoRow("Xcode", tools.xcodeVersion ?? (tools.isCommandLineToolsOnly ? "Command Line Tools only" : "Not installed"))
            }
            DisclosureGroup("Device not showing up?") {
                VStack(alignment: .leading, spacing: 6) {
                    Text("1. Use a cable that carries data (some charging cables do not).")
                    Text("2. Unlock the device and keep the screen on.")
                    Text("3. Tap Trust when asked, and enter the device passcode on the device — never in this app.")
                    Text("4. If no prompt appears, disconnect and reconnect the cable.")
                    Text("5. Try another USB port or remove hubs.")
                    Text("6. If none of this works, restart the Mac. The toolkit never restarts system services itself.")
                }
                .font(.callout)
                .padding(.top, 4)
            }
        }
    }

    private func runAction(_ id: String, _ device: Device) {
        guard let action = ActionCatalog.descriptor(id) else { return }
        let executor = model.executor
        let target = device.target
        Task {
            if let result = await model.run(action.title, workspace: .device, target: target, transport: action.mechanism, { _ in try await executor.execute(action, target: target, values: [:]) }) {
                model.statusMessage = result.summary
                if !result.raw.isEmpty && id != "sim-open" { rawDetails = result.raw }
            }
        }
    }
}

struct PendingConfirmation: Identifiable {
    let id = UUID()
    let title: String
    let detail: String
    let requirement: ConfirmationRequirement
    let target: DeviceTarget?
    var commandPreview: String?
    let action: () -> Void

    init(title: String, detail: String, requirement: ConfirmationRequirement, target: DeviceTarget?, commandPreview: String? = nil, action: @escaping () -> Void) {
        self.title = title
        self.detail = detail
        self.requirement = requirement
        self.target = target
        self.commandPreview = commandPreview
        self.action = action
    }
}

// MARK: - Developer Mode guide

struct DeveloperModeGuideView: View {
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Label("Turn on Developer Mode", systemImage: "hammer").font(.title2.bold())
            Text("Developer Mode (iOS 16 and later) lets the device run development features such as location simulation, screenshots through developer services, launching apps, and Instruments.")
                .fixedSize(horizontal: false, vertical: true)
            VStack(alignment: .leading, spacing: 8) {
                Text("1. On the device, open **Settings › Privacy & Security › Developer Mode**.")
                Text("2. Turn it on and tap **Restart**.")
                Text("3. After the restart, unlock the device and tap **Turn On** when asked, then enter the passcode.")
                Text("4. Reconnect the device and tap **Trust** if asked.")
            }
            GroupBox {
                Text("If Developer Mode is missing from Settings, connect the device, open Xcode, and choose **Window › Devices and Simulators** once. Xcode makes the setting appear.")
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            Text("Developer Mode widens what a connected computer can do. Turn it off again (and restart) when you no longer need it.")
                .font(.callout)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            HStack {
                Spacer()
                Button("Done") { dismiss() }.keyboardShortcut(.defaultAction)
            }
        }
        .padding(22)
        .frame(width: 540)
    }
}
