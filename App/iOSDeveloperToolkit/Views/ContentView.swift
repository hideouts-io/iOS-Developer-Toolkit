import DeviceKit
import SwiftUI
import ToolkitCore
import ToolkitFeatures

struct ContentView: View {
    @Environment(AppModel.self) private var model
    @State private var columnVisibility = NavigationSplitViewVisibility.all

    var body: some View {
        @Bindable var model = model
        NavigationSplitView(columnVisibility: $columnVisibility) {
            SidebarView()
                .navigationSplitViewColumnWidth(min: 200, ideal: 220, max: 280)
        } detail: {
            VStack(spacing: 0) {
                if model.selectedDevice?.kind == .demo {
                    DemoBanner()
                }
                WorkspaceView(workspace: model.workspace)
                    // Zero ideal size: pages fill the window but can never make it grow.
                    .frame(minWidth: 0, idealWidth: 0, maxWidth: .infinity, minHeight: 0, idealHeight: 0, maxHeight: .infinity)
            }
            .navigationTitle(model.workspace.title)
            .navigationSubtitle(model.selectedDevice.map { "\($0.name) · \($0.kind.label)" } ?? "No device selected")
            .toolbar { MainToolbar() }
        }
        .sheet(item: $model.presentedError) { error in
            ErrorSheet(error: error)
        }
        .sheet(isPresented: $model.isCommandPalettePresented) {
            CommandPaletteView()
        }
        .sheet(isPresented: $model.isDeveloperModeGuidePresented) {
            DeveloperModeGuideView()
        }
        .sheet(isPresented: $model.isReconnectGuidePresented) {
            ReconnectGuideView()
        }
        .overlay(alignment: .bottom) {
            if let message = model.statusMessage {
                StatusToast(message: message) { model.statusMessage = nil }
                    .padding(.bottom, 16)
                    .transition(.move(edge: .bottom).combined(with: .opacity))
            }
        }
        .animation(.easeInOut(duration: 0.2), value: model.statusMessage)
    }
}

struct WorkspaceView: View {
    let workspace: Workspace

    var body: some View {
        switch workspace {
        case .overview: OverviewView()
        case .device: DeviceDetailView()
        case .readiness: ReadinessView()
        case .apps: AppsView()
        case .installApp: InstallAppView()
        case .location: LocationLabView()
        case .liveLogs: LiveLogsView()
        case .actions: ActionsView()
        case .backup: BackupView()
        case .evidence: EvidenceView()
        case .externalTools: ExternalToolsView()
        case .activity: ActivityView()
        case .help: ToolReferenceView()
        case .safety: SafetyView()
        }
    }
}

struct SidebarView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        @Bindable var model = model
        VStack(spacing: 0) {
        List(selection: Binding(get: { model.workspace }, set: { if let value = $0 { model.workspace = value } })) {
            ForEach(Workspace.Group.allCases, id: \.self) { group in
                Section(group.rawValue) {
                    ForEach(Workspace.allCases.filter { $0.group == group }) { workspace in
                        Label(workspace.title, systemImage: workspace.symbolName)
                            .tag(workspace)
                            .accessibilityIdentifier("sidebar-\(workspace.rawValue)")
                            .help(workspace.subtitle)
                    }
                }
            }
        }
        .modifier(SidebarListStyle())
        // The list scrolls within whatever height remains; it must never set the window height.
        .frame(minHeight: 0, idealHeight: 0, maxHeight: .infinity)
        ConnectionSummaryView()
            .padding(10)
        }
    }
}

/// Compact discovery health at the bottom of the sidebar.
struct ConnectionSummaryView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            row("USB & Wi-Fi", model.snapshot.usbmux)
            row("Xcode devices", model.snapshot.coreDevice)
            row("Simulators", model.snapshot.simulators)
        }
        .font(.caption)
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(8)
        .background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: 8))
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("connection-summary")
    }

    private func row(_ title: String, _ status: SourceStatus) -> some View {
        HStack(spacing: 6) {
            Circle()
                .fill(status.isAvailable ? Color.green : (status == .notChecked ? Color.gray : Color.orange))
                .frame(width: 7, height: 7)
            Text(title).foregroundStyle(.secondary)
            Spacer(minLength: 4)
            // The full reason for an unavailable source is a sentence; the sidebar shows a short
            // state and keeps the reason in the tooltip, the accessibility label, and Device ›
            // Connection diagnostics.
            Text(status.isAvailable || status == .notChecked ? status.summary : "Unavailable")
                .lineLimit(1)
                .truncationMode(.tail)
        }
        .help(status.summary)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(title): \(status.summary)")
    }
}

struct MainToolbar: ToolbarContent {
    @Environment(AppModel.self) private var model

    var body: some ToolbarContent {
        ToolbarItem(placement: .navigation) {
            TargetPicker()
        }
        ToolbarItemGroup(placement: .primaryAction) {
            if !model.operations.isEmpty {
                OperationsButton()
            }
            Button {
                Task { await model.refreshDevices() }
            } label: {
                Label("Refresh Devices", systemImage: "arrow.clockwise")
            }
            .help("Look for devices again (⌘R)")
            .disabled(model.isRefreshing)
            Button {
                model.isCommandPalettePresented = true
            } label: {
                Label("Command Palette", systemImage: "command")
            }
            .help("Search every workspace and action (⌘K)")
        }
    }
}

/// The global target picker. Physical devices and simulators are listed in separate sections
/// and labelled, so it is always clear which kind of device an action will affect.
struct TargetPicker: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        Menu {
            Section("Physical Devices") {
                if model.physicalDevices.isEmpty {
                    Text("None connected")
                }
                ForEach(model.physicalDevices) { device in
                    Button {
                        model.selectedDeviceID = device.id
                    } label: {
                        Label("\(device.name) — \(device.displayModel), \(device.displayVersion)", systemImage: device.family.symbolName)
                    }
                }
            }
            Section("Simulators") {
                if model.simulatorDevices.isEmpty {
                    Text(model.snapshot.simulators.isAvailable ? "No simulators" : "Unavailable (needs Xcode)")
                }
                ForEach(model.simulatorDevices) { device in
                    Button {
                        model.selectedDeviceID = device.id
                    } label: {
                        Label("\(device.name) — \(device.displayVersion)\(device.simulatorState == .booted ? " (running)" : "")", systemImage: "\(device.family.symbolName)")
                    }
                }
            }
            if model.selectedDeviceID != nil {
                Divider()
                Button("Clear Selection") { model.selectedDeviceID = nil }
            }
        } label: {
            HStack(spacing: 6) {
                Image(systemName: model.selectedDevice?.family.symbolName ?? "iphone.slash")
                VStack(alignment: .leading, spacing: 0) {
                    Text(model.selectedDevice?.name ?? "Choose a Device")
                        .font(.headline)
                        .lineLimit(1)
                    if let device = model.selectedDevice {
                        Text(device.kind.label)
                            .font(.caption2)
                            .foregroundStyle(device.kind == .simulator ? Color.purple : (device.kind == .demo ? Color.orange : Color.blue))
                    }
                }
            }
        }
        .menuIndicator(.visible)
        .fixedSize()
        .help("Choose the device or simulator that actions affect")
        .accessibilityIdentifier("target-picker")
    }
}

struct OperationsButton: View {
    @Environment(AppModel.self) private var model
    @State private var isPresented = false

    var body: some View {
        Button {
            isPresented.toggle()
        } label: {
            HStack(spacing: 4) {
                ProgressView().controlSize(.small)
                Text("\(model.operations.count)")
                    .monospacedDigit()
            }
        }
        .help("Running operations")
        .accessibilityLabel("\(model.operations.count) running operations")
        .popover(isPresented: $isPresented, arrowEdge: .bottom) {
            VStack(alignment: .leading, spacing: 12) {
                Text("Running").font(.headline)
                ForEach(model.operations) { operation in
                    OperationRow(operation: operation)
                }
            }
            .padding()
            .frame(width: 360)
        }
    }
}

struct OperationRow: View {
    let operation: RunningOperation

    var body: some View {
        HStack(alignment: .top) {
            VStack(alignment: .leading, spacing: 4) {
                Text(operation.title).font(.callout.weight(.medium))
                if let target = operation.targetLabel {
                    Text(target).font(.caption).foregroundStyle(.secondary)
                }
                if let progress = operation.progress {
                    ProgressView(value: progress)
                } else {
                    ProgressView().progressViewStyle(.linear)
                }
                if !operation.status.isEmpty {
                    Text(operation.status).font(.caption).foregroundStyle(.secondary)
                }
                Text(operation.startedAt, style: .timer)
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(.tertiary)
            }
            Spacer()
            Button("Stop") { operation.cancel() }
                .controlSize(.small)
        }
    }
}

struct DemoBanner: View {
    var body: some View {
        HStack {
            Image(systemName: "theatermasks")
            Text(DemoMode.banner)
                .font(.callout)
            Spacer()
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 8)
        .background(Color.orange.opacity(0.18))
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("demo-banner")
    }
}

struct StatusToast: View {
    let message: String
    let dismiss: () -> Void

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: "info.circle")
            Text(message).lineLimit(2)
            Button(action: dismiss) { Image(systemName: "xmark") }
                .buttonStyle(.plain)
                .accessibilityLabel("Dismiss")
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
        .background(.regularMaterial, in: Capsule())
        .shadow(radius: 4, y: 2)
        .task(id: message) {
            try? await Task.sleep(for: .seconds(6))
            dismiss()
        }
    }
}

/// The standard vibrant sidebar, or a plain list while the screenshot harness is capturing
/// (vibrancy cannot be rendered into an offscreen image).
struct SidebarListStyle: ViewModifier {
    func body(content: Content) -> some View {
        if ScreenshotHarness.isCapturing {
            content
                .listStyle(.inset)
                .scrollContentBackground(.hidden)
                .background(Color(nsColor: .windowBackgroundColor))
        } else {
            content.listStyle(.sidebar)
        }
    }
}
