import DeviceKit
import SwiftUI
import ToolkitCore
import ToolkitFeatures

struct ReadinessView: View {
    @Environment(AppModel.self) private var model
    @State private var selectedRow: CapabilityResult.ID?
    @State private var isRunning = false
    @State private var history: [CompatibilityObservation] = []

    var body: some View {
        WorkspacePage(workspace: .readiness) {
            TargetHeader()
            if let device = model.selectedDevice {
                let results = model.readiness(for: device)
                HStack {
                    Button {
                        isRunning = true
                        Task {
                            await model.runReadiness(for: device)
                            isRunning = false
                            history = CompatibilityStore().load()
                        }
                    } label: {
                        Label("Run Readiness Check", systemImage: "play.fill")
                    }
                    .keyboardShortcut("r", modifiers: [.command, .shift])
                    .disabled(isRunning || device.kind == .demo)
                    .accessibilityIdentifier("run-readiness")
                    if isRunning { ProgressView().controlSize(.small) }
                    Spacer()
                    Button("Copy Report") { Pasteboard.copy(report(device, results)) }
                }
                Text("The check only reads state. It never mounts images, changes settings, or unlocks anything. Results are a snapshot; run it again after you change something.")
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                Card(title: "Results", systemImage: "checklist") {
                    ForEach(results) { result in
                        ReadinessRow(result: result, isExpanded: selectedRow == result.id) {
                            selectedRow = selectedRow == result.id ? nil : result.id
                        }
                        if result.id != results.last?.id { Divider() }
                    }
                }
                compatibility
            }
        }
        .onAppear { history = CompatibilityStore().load() }
    }

    private var compatibility: some View {
        Card(title: "Tested devices", systemImage: "rectangle.stack", subtitle: "A local history of completed checks. Devices are stored by a one-way fingerprint, never by name or UDID.") {
            let latest = CompatibilityStore.latest(history)
            if latest.isEmpty {
                Text("No physical devices have been checked yet.").foregroundStyle(.secondary)
            } else {
                ForEach(latest) { observation in
                    HStack {
                        Text(observation.model).font(.callout.weight(.medium))
                        Text("\(observation.osVersion ?? "—") (\(observation.buildVersion ?? "—")) · \(observation.connection)").font(.callout).foregroundStyle(.secondary)
                        Spacer()
                        let ready = observation.states.values.filter { $0 == .ready }.count
                        Text("\(ready)/\(observation.states.count) ready").font(.callout.monospacedDigit())
                        Text(observation.observedAt, style: .date).font(.caption).foregroundStyle(.tertiary)
                    }
                }
                HStack {
                    Button("Export Sanitized JSON…") { export(json: true) }
                    Button("Export Sanitized Markdown…") { export(json: false) }
                }
            }
        }
    }

    private func export(json: Bool) {
        guard let url = FilePanels.save(title: "Export readiness report", suggestedName: "readiness-report.\(json ? "json" : "md")", allowedExtension: json ? "json" : "md") else { return }
        do {
            let data = json ? try CompatibilityStore.renderJSON(history) : Data(CompatibilityStore.renderMarkdown(history).utf8)
            try SecureFileIO.writeNewFile(data, to: url)
            model.statusMessage = "Saved the sanitized report."
        } catch {
            model.present(error)
        }
    }

    private func report(_ device: Device, _ results: [CapabilityResult]) -> String {
        var lines = ["Readiness Check — \(device.displayModel), \(device.displayVersion), \(device.kind.label)", "Checked: \(Date().formatted())", ""]
        for result in results {
            lines.append("[\(result.state.label)] \(result.layer) › \(result.title): \(result.summary)")
            if !result.remediation.isEmpty { lines.append("    Next step: \(result.remediation)") }
        }
        return Sanitizer.sanitize(lines.joined(separator: "\n"), redactions: [device.name], limit: 20_000)
    }
}

struct ReadinessRow: View {
    let result: CapabilityResult
    let isExpanded: Bool
    let toggle: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Button(action: toggle) {
                HStack(alignment: .firstTextBaseline) {
                    StateBadge(state: result.state).frame(width: 150, alignment: .leading)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(result.title).font(.callout.weight(.medium))
                        Text(result.summary).font(.callout).foregroundStyle(.secondary).lineLimit(isExpanded ? nil : 1)
                    }
                    Spacer()
                    Text(result.layer).font(.caption).foregroundStyle(.tertiary)
                    Image(systemName: isExpanded ? "chevron.up" : "chevron.down").foregroundStyle(.tertiary)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityIdentifier("readiness-\(result.id)")
            if isExpanded {
                if !result.remediation.isEmpty {
                    Label(result.remediation, systemImage: "lightbulb")
                        .font(.callout)
                        .fixedSize(horizontal: false, vertical: true)
                        .padding(.leading, 150)
                }
                if !result.evidence.isEmpty {
                    DisclosureGroup("Evidence") {
                        RawOutputView(text: result.evidence, maxHeight: 140)
                    }
                    .padding(.leading, 150)
                }
            }
        }
        .padding(.vertical, 2)
    }
}
