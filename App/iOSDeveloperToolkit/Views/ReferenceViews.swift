import AppKit
import DeviceKit
import SwiftUI
import ToolkitCore
import ToolkitFeatures

// MARK: - Session activity

struct ActivityView: View {
    @Environment(AppModel.self) private var model
    @State private var selection: OperationRecord.ID?

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(Workspace.activity.subtitle).font(.title3).foregroundStyle(.secondary)
            Text("Only kept in memory for this session (up to \(OperationJournal.defaultCapacity) entries). Raw output is never stored — only its size and SHA-256. Exported manifests can contain device identifiers and paths from the command line, so review them before sharing.")
                .font(.callout)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            Table(model.journalRecords.reversed(), selection: $selection) {
                TableColumn("Finished") { Text($0.finishedAt.formatted(date: .omitted, time: .standard)).monospacedDigit() }
                    .width(min: 70, ideal: 80)
                TableColumn("Operation", value: \.title)
                TableColumn("Target", value: \.target)
                TableColumn("Outcome") { record in
                    Text(record.outcome.label).foregroundStyle(record.outcome == .succeeded ? Color.green : (record.outcome == .cancelled ? Color.secondary : Color.orange))
                }
                .width(min: 80, ideal: 100)
                TableColumn("Duration") { Text(String(format: "%.1f s", Double($0.durationMilliseconds) / 1000)).monospacedDigit() }
                    .width(min: 60, ideal: 70)
            }
            .overlay {
                if model.journalRecords.isEmpty {
                    ContentUnavailableView("Nothing Yet", systemImage: "clock", description: Text("Operations appear here as you run them."))
                }
            }
            if let id = selection, let record = model.journalRecords.first(where: { $0.id == id }) {
                Card(title: record.title, systemImage: "doc.text.magnifyingglass") {
                    InfoRow("Workspace", record.workspace)
                    InfoRow("Mechanism", record.transport, monospaced: true)
                    if !record.argv.isEmpty { InfoRow("Arguments", record.argv.joined(separator: " "), monospaced: true) }
                    if let message = record.errorMessage { InfoRow("Error", message) }
                    if !record.outputPaths.isEmpty { InfoRow("Output", record.outputPaths.joined(separator: "\n"), monospaced: true) }
                    HStack {
                        Button("Copy Manifest") { if let data = try? record.manifestJSON() { Pasteboard.copy(String(decoding: data, as: UTF8.self)) } }
                        Button("Save Manifest…") { save(record) }
                    }
                }
            }
        }
        .padding(20)
    }

    private func save(_ record: OperationRecord) {
        guard let url = FilePanels.save(title: "Save operation manifest", suggestedName: "operation-\(record.id).json", allowedExtension: "json") else { return }
        do {
            try SecureFileIO.writeNewFile(try record.manifestJSON(), to: url)
        } catch {
            model.present(error)
        }
    }
}

// MARK: - Tool reference

struct ToolReferenceView: View {
    @Environment(AppModel.self) private var model
    @State private var selection: ToolReference.Topic?
    @State private var expanded: [ToolReference.Topic: [ToolReference.Topic]] = [:]
    @State private var helpText: [ToolReference.Topic: String] = [:]
    @State private var loading = false

    var body: some View {
        HStack(spacing: 0) {
            List(selection: $selection) {
                Section("Apple tools used by this app") {
                    ForEach(ToolReference.roots) { root in
                        topicTree(root)
                    }
                }
                Section("Toolchain") {
                    Button {
                        Task { await model.runToolchainCheck() }
                    } label: {
                        Label("Run Toolchain Check", systemImage: "checkmark.shield")
                    }
                    .buttonStyle(.plain)
                }
            }
            .frame(width: 240)
            Divider()
            VStack(alignment: .leading, spacing: 10) {
                if let selection {
                    HStack {
                        Text(selection.title).font(.title3.monospaced())
                        Spacer()
                        if let command = ToolReference.advancedModeCommand(for: selection) {
                            Button("Use in Advanced Mode") { model.openAdvancedMode(command: command) }
                                .help("Open Advanced Mode with “devicectl \(command)” filled in. Nothing runs until you press Run.")
                        }
                        Button("Copy") { Pasteboard.copy(helpText[selection] ?? "") }
                    }
                    if loading { ProgressView() }
                    RawOutputView(text: helpText[selection] ?? "", maxHeight: .infinity)
                } else if !model.toolchainReport.isEmpty {
                    Text("Toolchain Check").font(.title3)
                    RawOutputView(text: model.toolchainReport, maxHeight: .infinity)
                } else {
                    ContentUnavailableView("Built-in Reference", systemImage: "book", description: Text("Choose a tool to read the exact help text of the version installed on this Mac. Run the Toolchain Check to confirm every command the app uses is available."))
                }
            }
            .padding(16)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .onChange(of: selection) { _, topic in
            guard let topic, helpText[topic] == nil else { return }
            load(topic)
        }
    }

    private func topicTree(_ topic: ToolReference.Topic) -> AnyView {
        if let children = expanded[topic], !children.isEmpty {
            return AnyView(DisclosureGroup {
                ForEach(children) { topicTree($0) }
            } label: {
                Text(topic.path.last ?? topic.title).tag(topic)
            })
        }
        return AnyView(Text(topic.path.last ?? topic.title).tag(topic))
    }

    private func load(_ topic: ToolReference.Topic) {
        let runner = model.runner
        loading = true
        Task {
            let text = await model.run("Help for \(topic.title)", workspace: .help, target: nil, transport: "\(topic.tool.rawValue) help") { _ in
                try await ToolReference.helpText(topic, runner: runner)
            }
            loading = false
            if let text {
                helpText[topic] = text
                expanded[topic] = ToolReference.children(of: topic, helpText: text)
            }
        }
    }
}

// MARK: - Scope & safety

struct SafetyView: View {
    var body: some View {
        WorkspacePage(workspace: .safety) {
            Card(title: "What this app is", systemImage: "checkmark.shield") {
                bullet("A macOS workbench for authorized development, testing, diagnostics, backup, and evidence preservation on devices you own or are allowed to examine.")
                bullet("It uses Apple's own interfaces: the macOS device service (usbmuxd and lockdown), Xcode's CoreDevice, simctl, and Instruments.")
                bullet("It runs without administrator rights and never uses sudo, never reads /var/db/lockdown, and never restarts system services.")
            }
            Card(title: "What it does not do", systemImage: "xmark.shield") {
                bullet("No jailbreak, passcode bypass, sandbox escape, code-signing bypass, or decryption of protected data or traffic.")
                bullet("Developer services (the Developer Disk Image) do not grant root access or unrestricted file system access.")
                bullet("AFC and CoreDevice file views are Apple-defined windows onto specific areas, not full file system acquisitions.")
                bullet("No one-click erase, restore, activation, or supervision. The only restart is a separately confirmed high-impact action.")
            }
            Card(title: "How changes are confirmed", systemImage: "hand.raised") {
                bullet("Read-only actions run immediately.")
                bullet("Actions that save files ask you to review where they go; existing files are never overwritten.")
                bullet("Actions that change a device require typing RUN plus the last six characters of that device's UDID, so a confirmation cannot apply to another device.")
                bullet("High-impact actions also require acknowledging a current backup and typing IRREVERSIBLE plus the same code.")
                bullet("Every operation captures its target when it starts; switching the selection cannot redirect it.")
            }
            Card(title: "Interpreting results", systemImage: "text.magnifyingglass") {
                bullet("A failed or unavailable step is a coverage gap, not proof that something is absent.")
                bullet("Process, network, profile, and log observations need context before drawing conclusions.")
                bullet("Hashes detect later changes; they do not prove when, where, or by whom something was collected.")
                bullet("Findings you mark in logs are your annotations, kept separate from device output.")
            }
            Card(title: "Privacy", systemImage: "lock") {
                bullet("Nothing is uploaded. Captures, backups, cases, and reports stay on this Mac with owner-only permissions.")
                bullet("The app's own log records outcomes, not device content; identifiers are marked private in the unified log.")
                bullet("Support bundles and compatibility reports are sanitized, but review them before sharing.")
            }
        }
    }

    private func bullet(_ text: String) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text("•")
            Text(text).fixedSize(horizontal: false, vertical: true)
        }
    }
}

// MARK: - Settings

struct SettingsView: View {
    @Environment(AppModel.self) private var model
    @State private var profileMessage: String?

    var body: some View {
        @Bindable var model = model
        TabView {
            Form {
                Toggle("Demo Mode (show a simulated iPhone)", isOn: $model.demoMode)
                LabeledContent("Backups folder", value: model.backup.destination.path)
                LabeledContent("Evidence cases folder", value: model.evidence.outputRoot.path)
                LabeledContent("Location event log", value: model.location.evidenceDirectory.path)
                LabeledContent("Live log spool", value: LogCapture.defaultDirectory().path)
            }
            .padding()
            .tabItem { Label("General", systemImage: "gearshape") }

            Form {
                Text("A workspace profile shares workflow defaults with your team. It never contains device identity, paths, coordinates, passwords, or captured data, and importing one only changes defaults. Profiles exported by version 0.3.x can be imported too.")
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                HStack {
                    Button("Export Profile…") { exportProfile() }
                    Button("Import Profile…") { importProfile() }
                }
                if let profileMessage { Text(profileMessage).font(.callout) }
            }
            .padding()
            .tabItem { Label("Profiles", systemImage: "person.2") }

            Form {
                if let tools = model.developerTools {
                    LabeledContent("Developer directory", value: tools.developerDirectory ?? "Not set")
                    LabeledContent("Xcode", value: tools.xcodeVersion ?? "Not installed")
                    LabeledContent("devicectl", value: tools.devicectl.isAvailable ? "Available" : "Missing")
                    LabeledContent("simctl", value: tools.simctl.isAvailable ? "Available" : "Missing")
                    LabeledContent("xctrace", value: tools.xctrace.isAvailable ? "Available" : "Missing")
                } else {
                    ProgressView()
                }
                Text("Features that need Xcode: developer services, screenshots, location simulation on iOS 17+, launching apps, Instruments, simulators. Everything that uses USB services (logs, backups, diagnostics, apps, packet capture) works without Xcode.")
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .padding()
            .tabItem { Label("Developer Tools", systemImage: "hammer") }
        }
        .frame(width: 560, height: 320)
    }

    private func exportProfile() {
        var profile = WorkspaceProfile(name: "Team defaults", defaultWorkspace: model.workspace, actionCategory: model.actionsCategory, selectedAction: model.selectedActionID, developerImageMechanism: model.developerImage.mechanism)
        profile.apps = .init(calculateSizes: model.apps.calculateSizes, includeSystemApps: model.apps.includeSystemApps, installAsDeveloperPackage: model.install.installAsDeveloperPackage)
        profile.backup = .init(forceFullBackup: model.backup.forceFullBackup, requireEncryption: model.backup.requireEncryption)
        profile.evidence = model.evidence.options
        profile.location = .init(timingJitterMilliseconds: model.location.jitterMilliseconds, ignoreRecordedTiming: model.location.ignoreRecordedTiming, routeSpeedKmh: Int(model.location.speedKmh), routeIntervalSeconds: model.location.routeIntervalSeconds, routeTraversals: model.location.routeTraversals)
        guard let url = FilePanels.save(title: "Export workspace profile", suggestedName: "workspace-profile.json", allowedExtension: "json") else { return }
        do {
            try SecureFileIO.writeNewFile(try profile.encoded(), to: url)
            profileMessage = "Exported.\n" + profile.preview
        } catch {
            model.present(error)
        }
    }

    private func importProfile() {
        guard let url = FilePanels.chooseFile(title: "Import workspace profile", allowedExtensions: ["json"]) else { return }
        do {
            let imported = try WorkspaceProfile.importing(try Data(contentsOf: url))
            let profile = imported.profile
            let alert = NSAlert()
            alert.messageText = "Apply “\(profile.name)”?"
            let translation = imported.legacyVersion.map { version in
                "\n\nThis profile was exported by version \(version). How its settings carry over:\n" + imported.notes.map { "• \($0)" }.joined(separator: "\n")
            } ?? ""
            alert.informativeText = profile.preview + translation + "\n\nOnly defaults change. Nothing runs."
            alert.addButton(withTitle: "Apply")
            alert.addButton(withTitle: "Cancel")
            guard alert.runModal() == .alertFirstButtonReturn else { return }
            guard model.operations.isEmpty else { throw ToolkitError.invalidInput("Wait for running operations to finish before importing a profile.") }
            model.workspace = profile.defaultWorkspace
            model.actionsCategory = profile.actionCategory
            model.selectedActionID = profile.selectedAction
            if let mechanism = profile.developerImageMechanism { model.developerImage.mechanism = mechanism }
            model.apps.calculateSizes = profile.apps.calculateSizes
            model.apps.includeSystemApps = profile.apps.includeSystemApps
            model.install.installAsDeveloperPackage = profile.apps.installAsDeveloperPackage
            model.backup.forceFullBackup = profile.backup.forceFullBackup
            model.backup.requireEncryption = profile.backup.requireEncryption
            model.evidence.options = profile.evidence
            model.location.jitterMilliseconds = profile.location.timingJitterMilliseconds
            model.location.ignoreRecordedTiming = profile.location.ignoreRecordedTiming
            model.location.travelPreset = .custom
            model.location.customSpeedKmh = Double(profile.location.routeSpeedKmh)
            model.location.routeIntervalSeconds = profile.location.routeIntervalSeconds
            model.location.routeTraversals = profile.location.routeTraversals
            profileMessage = imported.legacyVersion == nil ? "Applied “\(profile.name)”." : "Applied “\(profile.name)” from version \(imported.legacyVersion ?? "")."
        } catch {
            model.present(error)
        }
    }
}

// MARK: - Diagnostic log

struct DiagnosticLogView: View {
    @Environment(AppModel.self) private var model
    @State private var entries: [DiagnosticLogEntry] = []
    @State private var search = ""
    @State private var error: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text("The app's own log (last hour). Device identifiers appear as <private>.").foregroundStyle(.secondary)
                Spacer()
                TextField("Filter", text: $search).textFieldStyle(.roundedBorder).frame(width: 200)
                Button("Refresh", action: load)
                Button("Export Sanitized…", action: export)
            }
            if let error { Text(error).foregroundStyle(.red) }
            Table(entries.filter { search.isEmpty || $0.message.localizedCaseInsensitiveContains(search) || $0.category.localizedCaseInsensitiveContains(search) }) {
                TableColumn("Time") { Text($0.date.formatted(date: .omitted, time: .standard)).monospacedDigit() }.width(80)
                TableColumn("Level", value: \.level).width(60)
                TableColumn("Category", value: \.category).width(130)
                TableColumn("Message", value: \.message)
            }
        }
        .padding(12)
        .onAppear(perform: load)
    }

    private func load() {
        do {
            entries = try DiagnosticLogReader.recentEntries().reversed()
            error = nil
        } catch {
            self.error = "The log could not be read: \(error.localizedDescription)"
        }
    }

    private func export() {
        guard let url = FilePanels.save(title: "Export diagnostic log", suggestedName: "iOS-Developer-Toolkit-log.txt", allowedExtension: "txt") else { return }
        let redactions = model.allDevices.flatMap { [$0.name, $0.udid, $0.serialNumber ?? ""] }
        do {
            try SecureFileIO.writeNewFile(Data(Sanitizer.sanitize(DiagnosticLogReader.render(entries.reversed()), redactions: redactions, limit: 5_000_000).utf8), to: url)
        } catch {
            model.present(error)
        }
    }
}

// MARK: - Command palette

struct CommandPaletteView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @State private var query = ""
    @FocusState private var focused: Bool

    private struct Entry: Identifiable {
        let id: String
        let title: String
        let subtitle: String
        let symbol: String
        let perform: () -> Void
    }

    private var entries: [Entry] {
        var list: [Entry] = Workspace.allCases.map { workspace in
            Entry(id: "workspace-\(workspace.rawValue)", title: workspace.title, subtitle: workspace.subtitle, symbol: workspace.symbolName) { model.workspace = workspace }
        }
        list += ActionCatalog.actions(for: model.selectedDevice?.kind).map { action in
            Entry(id: "action-\(action.id)", title: action.title, subtitle: "\(action.category) · \(action.risk.label)", symbol: action.risk.symbolName) {
                model.openAction(action.id)
            }
        }
        list.append(Entry(id: "refresh", title: "Refresh Devices", subtitle: "Look for devices again", symbol: "arrow.clockwise") { Task { await model.refreshDevices() } })
        if let device = model.selectedDevice, device.kind != .demo {
            list.append(Entry(id: "readiness", title: "Run Readiness Check", subtitle: device.name, symbol: "checklist") {
                model.workspace = .readiness
                Task { await model.runReadiness(for: device) }
            })
        }
        for device in model.allDevices {
            list.append(Entry(id: "device-\(device.id)", title: "Select \(device.name)", subtitle: "\(device.kind.label) · \(device.displayVersion)", symbol: device.family.symbolName) { model.selectedDeviceID = device.id })
        }
        guard !query.isEmpty else { return list }
        return list.filter { $0.title.localizedCaseInsensitiveContains(query) || $0.subtitle.localizedCaseInsensitiveContains(query) }
    }

    var body: some View {
        VStack(spacing: 0) {
            TextField("Search workspaces, actions, and devices", text: $query)
                .textFieldStyle(.plain)
                .font(.title3)
                .padding(14)
                .focused($focused)
                .onSubmit { entries.first.map(choose) }
                .accessibilityIdentifier("palette-search")
            Divider()
            List(entries) { entry in
                Button { choose(entry) } label: {
                    HStack {
                        Image(systemName: entry.symbol).frame(width: 22)
                        VStack(alignment: .leading) {
                            Text(entry.title)
                            Text(entry.subtitle).font(.caption).foregroundStyle(.secondary)
                        }
                        Spacer()
                    }
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
            }
            .listStyle(.plain)
        }
        .frame(width: 560, height: 420)
        .onAppear { focused = true }
        .onExitCommand { dismiss() }
    }

    private func choose(_ entry: Entry) {
        dismiss()
        entry.perform()
    }
}

// MARK: - Support bundle

@MainActor
enum SupportBundleExporter {
    static func export(model: AppModel) {
        guard let url = FilePanels.save(title: "Create support bundle", suggestedName: "iOS-Developer-Toolkit-support.zip", allowedExtension: "zip") else { return }
        let device = model.selectedDevice
        var counts: [String: Int] = [:]
        for result in model.readiness(for: device) { counts[result.state.rawValue, default: 0] += 1 }
        var tools: [String: String] = [:]
        if let status = model.developerTools {
            tools["xcode"] = status.xcodeVersion ?? "not installed"
            tools["devicectl"] = status.devicectl.isAvailable ? "available" : "missing"
            tools["simctl"] = status.simctl.isAvailable ? "available" : "missing"
            tools["xctrace"] = status.xctrace.isAvailable ? "available" : "missing"
        }
        let context = SupportBundleContext(
            workspace: model.workspace.title,
            detectedDeviceCount: model.snapshot.devices.count,
            selectedDeviceKind: device?.kind.rawValue,
            discoveryStatus: ["usbmux": model.snapshot.usbmux.summary, "coreDevice": model.snapshot.coreDevice.summary, "simulators": model.snapshot.simulators.summary],
            capabilityCounts: counts,
            toolchainReport: model.toolchainReport,
            developerTools: tools,
            statuses: ["lastStatus": model.statusMessage ?? ""],
            redactions: model.allDevices.flatMap { [$0.name, $0.udid, $0.serialNumber ?? "", $0.ecid ?? ""] },
            diagnosticLog: (try? DiagnosticLogReader.recentEntries()) ?? []
        )
        do {
            try SupportBundle.write(to: url, context: context)
            FilePanels.reveal(url)
        } catch {
            model.present(error)
        }
    }
}
