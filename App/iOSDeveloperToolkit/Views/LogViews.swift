import AppKit
import DeviceKit
import SwiftUI
import ToolkitCore
import ToolkitFeatures

struct LiveLogsView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            TargetHeader(allowedKinds: [.physical, .simulator])
            if let device = model.selectedDevice {
                let kinds = LogStreamKind.available(for: device.kind)
                HStack(spacing: 10) {
                    ForEach(kinds) { kind in
                        Button {
                            model.logs.start(kind, target: device.target, app: model)
                        } label: {
                            Label("Start \(kind.title)", systemImage: "play.fill")
                        }
                        .help(kind.summary)
                        .accessibilityIdentifier("start-\(kind.rawValue)")
                    }
                    if kinds.isEmpty {
                        Text("Live logs are not available for the demo device.").foregroundStyle(.secondary)
                    }
                    Spacer()
                }
                if !kinds.isEmpty {
                    Text(kinds.map(\.summary).joined(separator: " "))
                        .font(.callout)
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            if model.logs.sessions.isEmpty {
                ContentUnavailableView("No Log Streams", systemImage: "text.alignleft", description: Text("Start a stream above. Every byte is saved to a private spool on this Mac, even while the view is paused or filtered."))
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                Picker("Stream", selection: Binding(get: { model.logs.selectedSession?.id }, set: { model.logs.selectedSessionID = $0 })) {
                    ForEach(model.logs.sessions) { session in
                        Text(session.title).tag(Optional(session.id))
                    }
                }
                .pickerStyle(.segmented)
                .labelsHidden()
                if let session = model.logs.selectedSession {
                    LogSessionView(session: session)
                        .frame(maxHeight: .infinity)
                        .toolbar {
                            ToolbarItem {
                                Button {
                                    openWindow(id: "log-window", value: session.id)
                                } label: {
                                    Label("Open in Window", systemImage: "macwindow.badge.plus")
                                }
                                .help("Open this stream in its own window")
                            }
                        }
                }
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .padding(20)
    }
}

/// The working view for one log stream.
struct LogSessionView: View {
    @Environment(AppModel.self) private var model
    @Bindable var session: LogSession
    @State private var selection: Set<Int> = []
    @State private var isMarkingFinding = false
    @State private var isReviewingFindings = false

    var body: some View {
        let lines = session.visibleLines
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 10) {
                Label(session.state.label, systemImage: session.state.isActive ? "dot.radiowaves.left.and.right" : "stop.circle")
                    .foregroundStyle(session.state.isActive ? .green : .secondary)
                    .lineLimit(1)
                Text("\(session.totalLines) lines · \(ByteFormatting.string(session.rawBytes)) saved")
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(.secondary)
                Spacer()
                Toggle("Pause view", isOn: $session.isPaused)
                    .toggleStyle(.button)
                    .help("Pauses the view only; capture continues.")
                Toggle("Follow", isOn: $session.followTail)
                    .toggleStyle(.button)
                if session.state.isActive {
                    Button("Stop") { session.stop() }
                        .accessibilityIdentifier("stop-log")
                } else {
                    Button("Close") { model.logs.close(session) }
                }
            }
            HStack(spacing: 8) {
                Image(systemName: "line.3.horizontal.decrease.circle").foregroundStyle(.secondary)
                TextField("Filter", text: $session.filter.text)
                    .textFieldStyle(.roundedBorder)
                    .onChange(of: session.filter) { session.validateFilter() }
                Toggle("Regex", isOn: $session.filter.isRegularExpression)
                Toggle("Match case", isOn: $session.filter.isCaseSensitive)
                if let error = session.filterError {
                    Text(error).font(.caption).foregroundStyle(.red)
                }
            }
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 0) {
                    ForEach(Array(lines.enumerated()), id: \.offset) { index, line in
                        LogLineRow(line: line)
                            .padding(.horizontal, 6)
                            .padding(.vertical, 1)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .background(selection.contains(index) ? Color.accentColor.opacity(0.22) : Color.clear)
                            .contentShape(Rectangle())
                            .onTapGesture { select(index) }
                    }
                }
            }
            .frame(minHeight: 120, maxHeight: .infinity)
            .font(.caption.monospaced())
            .background(Color(nsColor: .textBackgroundColor), in: RoundedRectangle(cornerRadius: 6))
            .overlay(RoundedRectangle(cornerRadius: 6).strokeBorder(.separator))
            // Keeps the newest line in view without scrolling any enclosing view.
            .defaultScrollAnchor(session.followTail && !session.isPaused ? .bottom : .top)
            .accessibilityLabel("Log lines")
            .overlay {
                if lines.isEmpty {
                    Text(session.state == .starting ? "Connecting…" : (session.filter.isEmpty ? "Waiting for log messages…" : "No lines match the filter."))
                        .foregroundStyle(.secondary)
                }
            }
            if let index = selection.max(), lines.indices.contains(index) {
                ScrollView {
                    Text(lines[index].rendered)
                        .font(.caption.monospaced())
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                .frame(height: 54)
                .padding(6)
                .background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: 6))
            }
            HStack {
                TextField("Case or ticket reference", text: $session.investigationReference)
                    .textFieldStyle(.roundedBorder)
                    .frame(maxWidth: 220)
                    .onSubmit {
                        let capture = session.capture
                        let reference = session.investigationReference
                        Task { try? await capture.setInvestigationReference(reference) }
                    }
                Button("Mark Finding…") { isMarkingFinding = true }
                    .disabled(selection.isEmpty)
                Button("Findings (\(session.findings.count))") { isReviewingFindings = true }
                    .disabled(session.findings.isEmpty)
                Spacer()
                Button("Copy Visible") { Pasteboard.copy(lines.map(\.rendered).joined(separator: "\n")) }
                Menu("Save") {
                    Button("Save Complete Raw Capture…") { saveRaw() }
                    Button("Save Filtered Lines…") { saveFiltered() }
                    Button("Export Evidence Bundle…") { exportBundle() }
                    Divider()
                    Button("Show Spool in Finder") { FilePanels.reveal(session.capture.spoolURL) }
                }
                .fixedSize()
            }
        }
        .sheet(isPresented: $isMarkingFinding) {
            FindingSheet(session: session, selectedText: selection.sorted().compactMap { lines.indices.contains($0) ? lines[$0].rendered : nil }.joined(separator: "\n"))
        }
        .sheet(isPresented: $isReviewingFindings) {
            FindingsReviewSheet(findings: session.findings, capture: session.capture)
        }
    }

    /// Click selects one line; Shift-click extends; Command-click toggles.
    private func select(_ index: Int) {
        let flags = NSEvent.modifierFlags
        if flags.contains(.shift), let anchor = selection.min() {
            selection = Set(min(anchor, index)...max(anchor, index))
        } else if flags.contains(.command) {
            if selection.contains(index) { selection.remove(index) } else { selection.insert(index) }
        } else {
            selection = [index]
        }
    }

    private func saveRaw() {
        guard let url = FilePanels.save(title: "Save complete raw capture", suggestedName: session.capture.spoolURL.lastPathComponent, allowedExtension: session.kind.spoolExtension) else { return }
        let capture = session.capture
        Task {
            do {
                _ = try await capture.exportRaw(to: url)
                session.hasUnsavedData = false
                model.statusMessage = "Saved the raw capture with its metadata and findings."
            } catch { model.present(error) }
        }
    }

    private func saveFiltered() {
        guard let url = FilePanels.save(title: "Save filtered lines", suggestedName: "\(session.kind.rawValue)-filtered.log", allowedExtension: "log") else { return }
        let capture = session.capture
        let filter = session.filter
        Task {
            do {
                let count = try await capture.exportFiltered(to: url, filter: filter)
                model.statusMessage = "Saved \(count) matching lines."
            } catch { model.present(error) }
        }
    }

    private func exportBundle() {
        guard let folder = FilePanels.chooseFolder(title: "Choose a folder for the evidence bundle") else { return }
        let capture = session.capture
        Task {
            do {
                let bundle = try await capture.exportEvidenceBundle(into: folder)
                session.hasUnsavedData = false
                FilePanels.reveal(bundle)
            } catch { model.present(error) }
        }
    }
}

struct LogLineRow: View {
    let line: LogLine

    var color: Color {
        switch line.level?.lowercased() {
        case "error": return .red
        case "fault": return .purple
        case "debug": return .secondary
        default: return .primary
        }
    }

    var body: some View {
        // Fixed single-line rows keep very busy streams fast to lay out; the full text of the
        // selected line is shown below the list.
        Text(line.rendered)
            .foregroundStyle(color)
            .lineLimit(1)
            .truncationMode(.tail)
            .help(line.rendered)
    }
}

struct FindingSheet: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    let session: LogSession
    let selectedText: String
    @State private var note = ""
    @State private var tags = ""
    @State private var assessment: FindingAssessment = .observation

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Mark Finding").font(.title2.bold())
            Text("A finding is your annotation. It is saved separately from the raw capture and is not a device-generated fact.")
                .font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            RawOutputView(text: selectedText, maxHeight: 120)
            Picker("Assessment", selection: $assessment) {
                ForEach(FindingAssessment.allCases) { Text($0.label).tag($0) }
            }
            TextField("Tags (comma-separated, e.g. network, crash)", text: $tags).textFieldStyle(.roundedBorder)
            TextField("Note", text: $note, axis: .vertical).lineLimit(3...6).textFieldStyle(.roundedBorder)
            HStack {
                Spacer()
                Button("Cancel", role: .cancel) { dismiss() }
                Button("Save Finding") { save() }.keyboardShortcut(.defaultAction).disabled(note.trimmingCharacters(in: .whitespaces).isEmpty)
            }
        }
        .padding(20)
        .frame(width: 520)
    }

    private func save() {
        do {
            let finding = try LiveLogFinding.make(note: note, selectedText: selectedText, stream: session.kind, target: session.target, rawBytesObserved: session.rawBytes, filter: session.filter, assessment: assessment, tags: try LiveLogFinding.parseTags(tags))
            let capture = session.capture
            Task {
                do {
                    try await capture.addFinding(finding)
                    session.findings.append(finding)
                } catch { model.present(error) }
            }
            dismiss()
        } catch {
            model.present(error)
        }
    }
}

struct FindingsReviewSheet: View {
    @Environment(\.dismiss) private var dismiss
    let findings: [LiveLogFinding]
    let capture: LogCapture
    @State private var copied = false

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Findings").font(.title2.bold())
            Text("Analyst annotations, kept separate from the raw device output.").foregroundStyle(.secondary)
            List(findings) { finding in
                VStack(alignment: .leading, spacing: 4) {
                    HStack {
                        Text(finding.assessment.label).font(.callout.weight(.semibold))
                        Text(finding.createdAt.formatted(date: .omitted, time: .standard)).font(.caption).foregroundStyle(.secondary)
                        if !finding.tags.isEmpty { Text(finding.tags.joined(separator: ", ")).font(.caption).foregroundStyle(.blue) }
                    }
                    Text(finding.note)
                    Text(finding.selectedText).font(.caption.monospaced()).foregroundStyle(.secondary).lineLimit(4)
                }
            }
            .frame(minHeight: 240)
            HStack {
                Button("Copy Register") {
                    Task {
                        Pasteboard.copy(await capture.findingsRegister)
                        copied = true
                    }
                }
                .help("Copy the capture facts and every finding as Markdown")
                if copied { Text("Copied.").font(.callout).foregroundStyle(.secondary) }
                Spacer()
                Button("Done") { dismiss() }.keyboardShortcut(.defaultAction)
            }
        }
        .padding(20)
        .frame(width: 620, height: 440)
    }
}
