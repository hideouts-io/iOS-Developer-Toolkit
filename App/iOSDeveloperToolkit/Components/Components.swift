import AppKit
import UniformTypeIdentifiers
import DeviceKit
import SwiftUI
import ToolkitCore
import ToolkitFeatures

/// A titled card used to group related controls.
struct Card<Content: View>: View {
    let title: String
    var systemImage: String?
    var subtitle: String?
    @ViewBuilder var content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                if let systemImage { Image(systemName: systemImage).foregroundStyle(.secondary) }
                Text(title).font(.headline)
            }
            if let subtitle {
                Text(subtitle).font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
            content
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 10))
        .overlay(RoundedRectangle(cornerRadius: 10).strokeBorder(.separator.opacity(0.6)))
    }
}

/// A scrollable workspace page with a consistent header.
struct WorkspacePage<Content: View>: View {
    let workspace: Workspace
    @ViewBuilder var content: Content

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                Text(workspace.subtitle)
                    .font(.title3)
                    .foregroundStyle(.secondary)
                content
            }
            .padding(20)
            .frame(maxWidth: 1100, alignment: .leading)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

/// A labelled value with an optional plain-language explanation popover.
struct InfoRow: View {
    let title: String
    let value: String
    var explanation: String?
    var monospaced = false
    var sensitive = false
    @State private var showsExplanation = false
    @State private var revealed = false

    init(_ field: DeviceField, value: String?) {
        title = field.title
        self.value = value ?? "—"
        explanation = field.explanation
        monospaced = [.udid, .serialNumber, .ecid, .coreDeviceIdentifier, .hardwareIdentifier, .buildNumber].contains(field)
        sensitive = field.isSensitive
    }

    init(_ title: String, _ value: String, explanation: String? = nil, monospaced: Bool = false) {
        self.title = title
        self.value = value
        self.explanation = explanation
        self.monospaced = monospaced
    }

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            HStack(spacing: 4) {
                Text(title).foregroundStyle(.secondary)
                if let explanation {
                    Button {
                        showsExplanation.toggle()
                    } label: {
                        Image(systemName: "questionmark.circle").imageScale(.small)
                    }
                    .buttonStyle(.plain)
                    .foregroundStyle(.secondary)
                    .accessibilityLabel("About \(title)")
                    .popover(isPresented: $showsExplanation, arrowEdge: .trailing) {
                        Text(explanation)
                            .font(.callout)
                            .padding()
                            .frame(width: 320, alignment: .leading)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
            .frame(width: 190, alignment: .leading)
            Group {
                if sensitive && !revealed && value != "—" {
                    Button("Show") { revealed = true }
                        .buttonStyle(.link)
                        .help("This value identifies the device. It is hidden until you choose to show it.")
                } else {
                    Text(value)
                        .font(monospaced ? .body.monospaced() : .body)
                        .textSelection(.enabled)
                }
            }
            Spacer(minLength: 0)
        }
        .accessibilityElement(children: .combine)
    }
}

struct StateBadge: View {
    let state: CapabilityState

    var color: Color {
        switch state {
        case .ready: return .green
        case .attention: return .orange
        case .unavailable: return .red
        case .blocked: return .gray
        case .notTested, .notApplicable: return .secondary
        }
    }

    var body: some View {
        Label(state.label, systemImage: state.symbolName)
            .foregroundStyle(color)
            .labelStyle(.titleAndIcon)
            .font(.callout)
    }
}

struct RiskBadge: View {
    let risk: ActionRisk

    var color: Color {
        switch risk {
        case .readOnly: return .green
        case .hostWrite: return .blue
        case .deviceChange: return .orange
        case .highImpact: return .red
        }
    }

    var body: some View {
        Label(risk.label, systemImage: risk.symbolName)
            .font(.caption.weight(.semibold))
            .padding(.horizontal, 8)
            .padding(.vertical, 3)
            .foregroundStyle(color)
            .background(color.opacity(0.12), in: Capsule())
            .help(risk.explanation)
    }
}

/// Shows exactly which device the page will act on, with its kind clearly labelled.
struct TargetHeader: View {
    @Environment(AppModel.self) private var model
    var allowedKinds: Set<DeviceKind> = [.physical, .simulator, .demo]

    var body: some View {
        if let device = model.selectedDevice {
            HStack(spacing: 12) {
                Image(systemName: device.family.symbolName)
                    .font(.title2)
                    .frame(width: 32)
                VStack(alignment: .leading, spacing: 2) {
                    HStack(spacing: 6) {
                        Text(device.name).font(.headline)
                        Text(device.kind.label)
                            .font(.caption.weight(.semibold))
                            .padding(.horizontal, 6)
                            .padding(.vertical, 2)
                            .background((device.kind == .simulator ? Color.purple : device.kind == .demo ? Color.orange : Color.blue).opacity(0.15), in: Capsule())
                    }
                    Text("\(device.displayModel) · \(device.displayVersion) · \(device.primaryTransport?.label ?? "Not connected")")
                        .font(.callout)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                if !allowedKinds.contains(device.kind) {
                    Label("Not available for \(device.kind.label.lowercased())s", systemImage: "exclamationmark.triangle")
                        .foregroundStyle(.orange)
                        .font(.callout)
                }
            }
            .padding(12)
            .background(.quaternary.opacity(0.4), in: RoundedRectangle(cornerRadius: 10))
            .accessibilityElement(children: .combine)
            .accessibilityIdentifier("target-header")
        } else {
            NoDeviceView()
        }
    }
}

struct NoDeviceView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Label("No device selected", systemImage: "iphone.slash").font(.headline)
            Text("Connect an iPhone or iPad with a USB cable, unlock it, and tap Trust. Or choose a simulator from the device menu in the toolbar.")
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            HStack {
                Button("Refresh Devices") { Task { await model.refreshDevices() } }
                Button("Use Demo Mode") { model.demoMode = true }
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.quaternary.opacity(0.4), in: RoundedRectangle(cornerRadius: 10))
    }
}

/// Confirms an action according to its risk. Device-changing actions need a phrase bound to the
/// target's UDID; high-impact ones also need a backup acknowledgement.
struct ConfirmationSheet: View {
    let title: String
    let detail: String
    let requirement: ConfirmationRequirement
    let target: DeviceTarget?
    let commandPreview: String?
    let onConfirm: () -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var typed = ""
    @State private var backupAcknowledged = false

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                Text(title).font(.title2.bold())
                Spacer()
                RiskBadge(risk: requirement.risk)
            }
            Text(detail).fixedSize(horizontal: false, vertical: true)
            if let target {
                InfoRow("Target", "\(target.name) — \(target.kind.label)")
                InfoRow("UDID", target.udid, monospaced: true)
            }
            if let commandPreview {
                GroupBox("Exact command") {
                    Text(commandPreview)
                        .font(.caption.monospaced())
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
            if requirement.requiresBackupAcknowledgement {
                Toggle("I have a current backup and understand this cannot be undone", isOn: $backupAcknowledged)
            }
            if let phrase = requirement.phrase {
                VStack(alignment: .leading, spacing: 6) {
                    Text("Type **\(phrase)** to confirm. The code is the end of this device's UDID, so the confirmation cannot apply to a different device.")
                        .font(.callout)
                        .fixedSize(horizontal: false, vertical: true)
                    TextField(phrase, text: $typed)
                        .textFieldStyle(.roundedBorder)
                        .font(.body.monospaced())
                        .accessibilityIdentifier("confirmation-field")
                }
            }
            HStack {
                Spacer()
                Button("Cancel", role: .cancel) { dismiss() }
                    .keyboardShortcut(.cancelAction)
                Button(requirement.risk == .readOnly ? "Run" : "Continue") {
                    dismiss()
                    onConfirm()
                }
                .keyboardShortcut(.defaultAction)
                .disabled(!requirement.isSatisfied(typedPhrase: typed, backupAcknowledged: backupAcknowledged))
                .accessibilityIdentifier("confirm-button")
            }
        }
        .padding(20)
        .frame(width: 520)
    }
}

/// A sheet presenting an error with its plain message, recovery, and copyable details.
struct ErrorSheet: View {
    let error: PresentedError
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Label(error.title, systemImage: "exclamationmark.triangle.fill")
                .font(.title2.bold())
                .foregroundStyle(.orange)
            Text(error.message).font(.body).fixedSize(horizontal: false, vertical: true)
            if let recovery = error.recovery {
                Text(recovery).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
            if let details = error.details, !details.isEmpty {
                DisclosureGroup("Technical details") {
                    ScrollView {
                        Text(details)
                            .font(.caption.monospaced())
                            .textSelection(.enabled)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                    .frame(maxHeight: 180)
                    Button("Copy Details") { Pasteboard.copy(details) }
                        .controlSize(.small)
                }
            }
            HStack {
                Spacer()
                Button("OK") { dismiss() }.keyboardShortcut(.defaultAction)
            }
        }
        .padding(20)
        .frame(width: 520)
        .accessibilityIdentifier("error-sheet")
    }
}

enum Pasteboard {
    static func copy(_ text: String) {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(text, forType: .string)
    }
}

/// Native open/save panels.
@MainActor
enum FilePanels {
    /// Content types for file extensions (empty means any file).
    static func contentTypes(_ extensions: [String]) -> [UTType] {
        extensions.compactMap { fileExtension -> UTType? in
            switch fileExtension {
            case "app": return .applicationBundle
            case "swift": return .swiftSource
            default: return UTType(filenameExtension: fileExtension)
            }
        }
    }

    static func chooseFile(title: String, allowedExtensions: [String], directory: URL? = nil) -> URL? {
        let panel = NSOpenPanel()
        panel.title = title
        panel.canChooseFiles = true
        panel.canChooseDirectories = allowedExtensions.contains("app")
        panel.treatsFilePackagesAsDirectories = false
        panel.allowsMultipleSelection = false
        panel.allowedContentTypes = contentTypes(allowedExtensions)
        if let directory { panel.directoryURL = directory }
        return panel.runModal() == .OK ? panel.url : nil
    }

    static func chooseFiles(title: String, allowedExtensions: [String]) -> [URL] {
        let panel = NSOpenPanel()
        panel.title = title
        panel.canChooseFiles = true
        panel.allowsMultipleSelection = true
        panel.allowedContentTypes = contentTypes(allowedExtensions)
        return panel.runModal() == .OK ? panel.urls : []
    }

    static func chooseFolder(title: String, directory: URL? = nil, canCreate: Bool = true) -> URL? {
        let panel = NSOpenPanel()
        panel.title = title
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.canCreateDirectories = canCreate
        panel.allowsMultipleSelection = false
        if let directory { panel.directoryURL = directory }
        return panel.runModal() == .OK ? panel.url : nil
    }

    static func save(title: String, suggestedName: String, allowedExtension: String, directory: URL? = nil) -> URL? {
        let panel = NSSavePanel()
        panel.title = title
        panel.nameFieldStringValue = suggestedName
        panel.allowedContentTypes = allowedExtension.isEmpty ? [] : contentTypes([allowedExtension])
        panel.canCreateDirectories = true
        if let directory { panel.directoryURL = directory }
        return panel.runModal() == .OK ? panel.url : nil
    }

    static func reveal(_ url: URL) {
        NSWorkspace.shared.activateFileViewerSelecting([url])
    }
}

/// A read-only, selectable monospaced text block for raw output.
struct RawOutputView: View {
    let text: String
    var maxHeight: CGFloat = 320

    var body: some View {
        ScrollView([.vertical, .horizontal]) {
            Text(text.isEmpty ? "No output." : text)
                .font(.caption.monospaced())
                .textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(8)
        }
        .frame(maxHeight: maxHeight)
        .background(Color(nsColor: .textBackgroundColor), in: RoundedRectangle(cornerRadius: 6))
        .overlay(RoundedRectangle(cornerRadius: 6).strokeBorder(.separator))
    }
}
