import DeviceKit
import SwiftUI
import ToolkitCore
import ToolkitFeatures

// MARK: - Backup

struct BackupView: View {
    @Environment(AppModel.self) private var model
    @State private var confirmation: PendingConfirmation?

    var body: some View {
        @Bindable var backup = model.backup
        WorkspacePage(workspace: .backup) {
            TargetHeader(allowedKinds: [.physical])
            if let device = model.selectedDevice, device.kind == .physical {
                if !device.supportsLockdownServices {
                    Label("Backups need a USB (or Wi-Fi sync) connection. This device is only reachable through Xcode's network connection.", systemImage: "cable.connector")
                        .foregroundStyle(.orange)
                }
                Card(title: "Encryption", systemImage: "lock", subtitle: "Encrypted backups also include saved passwords, Health, and Wi-Fi data, and cannot be read without the password. Encryption is a setting stored on the device.") {
                    HStack {
                        Button("Check Encryption") { Task { await backup.checkEncryption(app: model, target: device.target) } }
                        if backup.encryptionCheckedFor == device.target, let enabled = backup.encryptionEnabled {
                            Label(enabled ? "Backups are encrypted" : "Backup encryption is off", systemImage: enabled ? "lock.fill" : "lock.open")
                                .foregroundStyle(enabled ? .green : .orange)
                        }
                    }
                    if backup.encryptionCheckedFor == device.target, backup.encryptionEnabled == false {
                        VStack(alignment: .leading, spacing: 8) {
                            Text("Turn on encryption with a new backup password. Store it safely — an encrypted backup cannot be restored without it. Do not reuse the device passcode or an account password.")
                                .font(.callout)
                                .fixedSize(horizontal: false, vertical: true)
                            SecureField("New backup password (8+ characters)", text: $backup.newPassword)
                                .textFieldStyle(.roundedBorder)
                                .frame(maxWidth: 320)
                            SecureField("Confirm password", text: $backup.confirmPassword)
                                .textFieldStyle(.roundedBorder)
                                .frame(maxWidth: 320)
                            if !backup.confirmPassword.isEmpty && backup.newPassword != backup.confirmPassword {
                                Text("The passwords do not match.").font(.caption).foregroundStyle(.red)
                            }
                            Button("Turn On Encryption…") {
                                confirmation = PendingConfirmation(title: "Turn on backup encryption", detail: "This changes a setting on \(device.name). The device may ask for its passcode. The password is sent only over the encrypted device connection and is never written to disk or logs.", requirement: .make(for: .deviceChange, target: device.target), target: device.target) {
                                    Task { await backup.enableEncryption(app: model, target: device.target) }
                                }
                            }
                            .disabled(!backup.passwordsValid || backup.isRunning)
                        }
                    }
                }
                Card(title: "Back up", systemImage: "externaldrive.badge.timemachine") {
                    HStack {
                        Text("Save to").foregroundStyle(.secondary)
                        Text(backup.destination.path).font(.callout.monospaced()).lineLimit(1).truncationMode(.middle)
                        Button("Change…") {
                            if let url = FilePanels.chooseFolder(title: "Choose where to save backups", directory: backup.destination) { backup.destination = url }
                        }
                    }
                    Toggle("Require encrypted backup", isOn: $backup.requireEncryption)
                    Toggle("Always make a full backup", isOn: $backup.forceFullBackup)
                        .help("Otherwise, an existing backup of this device in the folder is updated incrementally.")
                    HStack {
                        Button("Start Backup…") {
                            confirmation = PendingConfirmation(title: "Back up \(device.name)", detail: "The backup is written to \(backup.destination.path)/\(device.udid). Keep the device unlocked and connected until it finishes.", requirement: .make(for: .hostWrite, target: device.target), target: device.target) {
                                Task { await backup.startBackup(app: model, target: device.target) }
                            }
                        }
                        .disabled(backup.isRunning || !device.supportsLockdownServices)
                        .accessibilityIdentifier("start-backup")
                        if let progress = backup.progress {
                            ProgressView(value: progress).frame(maxWidth: 240)
                            Text("\(Int(progress * 100))%").monospacedDigit()
                        }
                        if backup.isRunning {
                            Button("Stop") { backup.activeOperation?.cancel() }
                        }
                    }
                    if !backup.log.isEmpty {
                        RawOutputView(text: backup.log.joined(separator: "\n"), maxHeight: 140)
                    }
                    if let last = backup.lastBackup {
                        Button("Show Backup in Finder") { FilePanels.reveal(last) }
                    }
                }
            }
            Card(title: "Analyze or acquire with separate tools", systemImage: "wrench.and.screwdriver", subtitle: "MVT (consented spyware-indicator checks on a decrypted backup) and UFADE (advanced logical acquisitions) are separate projects you install yourself.") {
                Button("Open External Tools") { model.workspace = .externalTools }
            }
        }
        .sheet(item: $confirmation) { pending in
            ConfirmationSheet(title: pending.title, detail: pending.detail, requirement: pending.requirement, target: pending.target, commandPreview: nil, onConfirm: pending.action)
        }
    }
}

// MARK: - Evidence

struct EvidenceView: View {
    @Environment(AppModel.self) private var model
    @State private var confirmation: PendingConfirmation?

    var body: some View {
        @Bindable var evidence = model.evidence
        WorkspacePage(workspace: .evidence) {
            TargetHeader(allowedKinds: [.physical])
            Text("Collects snapshots (device information, apps, profiles, diagnostics, crash inventory), optional timed streams, and optional artifacts into a new case folder with a manifest and SHA-256 hashes. A failed step is recorded as a coverage gap, never skipped silently.")
                .font(.callout)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            if let device = model.selectedDevice, device.kind == .physical {
                Card(title: "Case", systemImage: "folder.badge.person.crop") {
                    HStack {
                        Text("Cases folder").foregroundStyle(.secondary)
                        Text(evidence.outputRoot.path).font(.callout.monospaced()).lineLimit(1).truncationMode(.middle)
                        Button("Change…") {
                            if let url = FilePanels.chooseFolder(title: "Choose where cases are created", directory: evidence.outputRoot) { evidence.outputRoot = url }
                        }
                    }
                    if let intake = evidence.activeIntake {
                        Label("Collecting into guided case “\(intake.title)”", systemImage: "folder.fill").foregroundStyle(.green)
                    } else {
                        DisclosureGroup("Guided case (optional)") {
                            VStack(alignment: .leading, spacing: 8) {
                                TextField("Case title", text: $evidence.caseTitle).textFieldStyle(.roundedBorder)
                                TextField("Purpose and scope", text: $evidence.casePurpose, axis: .vertical).lineLimit(2...4).textFieldStyle(.roundedBorder)
                                Toggle("I own this device or am authorized to examine it", isOn: $evidence.authorized)
                                Button("Create Case") { evidence.createCase(app: model, target: device.target) }
                                    .disabled(evidence.caseTitle.trimmingCharacters(in: .whitespaces).isEmpty || !evidence.authorized)
                                Text("The intake records your stated purpose and authorization. It does not establish chain of custody.")
                                    .font(.caption).foregroundStyle(.secondary)
                            }
                            .padding(.top, 6)
                        }
                    }
                }
                Card(title: "What to collect", systemImage: "slider.horizontal.3") {
                    Stepper("Stream for \(evidence.options.durationSeconds) seconds", value: $evidence.options.durationSeconds, in: 0...3600, step: 15)
                    Toggle("Unified Logs stream", isOn: $evidence.options.includeUnifiedLogs)
                    Toggle("Classic syslog stream", isOn: $evidence.options.includeClassicSyslog)
                    Toggle("Network packet capture (PCAP)", isOn: $evidence.options.includePacketCapture)
                    Toggle("Screenshot", isOn: $evidence.options.includeScreenshot)
                    Toggle("Copy crash reports", isOn: $evidence.options.includeCrashReports)
                    Text("Logs, packet captures, screenshots, and crash reports can contain private information.").font(.caption).foregroundStyle(.secondary)
                    ReadinessStatusView(requirements: evidence.options.requirements, device: device, subject: "this collection")
                }
                HStack {
                    Button("Start Collection…") {
                        confirmation = PendingConfirmation(title: "Collect evidence from \(device.name)", detail: "Information from the device is copied into a new case folder on this Mac. Nothing on the device is changed.", requirement: .make(for: .hostWrite, target: device.target), target: device.target) {
                            Task { await evidence.collect(app: model, device: device) }
                        }
                    }
                    .disabled(evidence.isCollecting)
                    .accessibilityIdentifier("start-collection")
                    if evidence.isCollecting {
                        Button("Stop and Finalize") { evidence.stop() }
                        ProgressView().controlSize(.small)
                        Text(evidence.currentStep ?? "").font(.callout).foregroundStyle(.secondary)
                        if let remaining = evidence.secondsRemaining { Text("\(remaining)s").monospacedDigit() }
                    }
                }
            }
            if !evidence.steps.isEmpty {
                Card(title: evidence.manifest.map { "Outcome: \($0.outcome.rawValue.capitalized)" } ?? "Progress", systemImage: "list.bullet.clipboard") {
                    ForEach(evidence.steps) { step in
                        HStack(alignment: .firstTextBaseline) {
                            Image(systemName: step.status == .succeeded ? "checkmark.circle.fill" : step.status == .unavailable ? "minus.circle" : "xmark.circle.fill")
                                .foregroundStyle(step.status == .succeeded ? .green : step.status == .unavailable ? .secondary : .orange)
                            Text(step.title)
                            Spacer()
                            Text(step.detail).font(.caption).foregroundStyle(.secondary).lineLimit(1).frame(maxWidth: 360, alignment: .trailing)
                        }
                    }
                    if let folder = evidence.lastCaseFolder, !evidence.isCollecting {
                        Button("Show Case in Finder") { FilePanels.reveal(folder) }
                    }
                }
            }
        }
        .sheet(item: $confirmation) { pending in
            ConfirmationSheet(title: pending.title, detail: pending.detail, requirement: pending.requirement, target: pending.target, commandPreview: nil, onConfirm: pending.action)
        }
    }
}

// MARK: - External tools

struct ExternalToolsView: View {
    @Environment(AppModel.self) private var model
    @State private var confirmation: PendingConfirmation?

    var body: some View {
        @Bindable var tools = model.externalTools
        WorkspacePage(workspace: .externalTools) {
            Text("These are independent projects you install yourself. The toolkit does not bundle, update, or import them; it validates the file you choose (path and SHA-256) and runs it with a minimal environment. None of them is required.")
                .font(.callout)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            mvtCard(tools)
            ufadeCard(tools)
            idbCard(tools)
        }
        .sheet(item: $confirmation) { pending in
            ConfirmationSheet(title: pending.title, detail: pending.detail, requirement: pending.requirement, target: pending.target, commandPreview: pending.commandPreview, onConfirm: pending.action)
        }
    }

    private func mvtCard(_ tools: ExternalToolsModel) -> some View {
        @Bindable var tools = tools
        return Card(title: "MVT — Mobile Verification Toolkit", systemImage: "shield.checkered", subtitle: "Checks a decrypted iTunes-style backup for published indicators of compromise, with the device owner's consent. A run with no findings does not prove a device is clean.") {
            HStack {
                TextField("Path to mvt-ios", text: $tools.mvtPath).textFieldStyle(.roundedBorder).font(.callout.monospaced())
                Button("Choose…") { if let url = FilePanels.chooseFile(title: "Choose mvt-ios", allowedExtensions: []) { tools.mvtPath = url.path } }
                Button("Validate") { Task { await tools.validateMVT(app: model) } }.disabled(tools.mvtPath.isEmpty)
            }
            if let mvt = tools.mvt {
                Label("MVT \(mvt.version) · SHA-256 \(mvt.sha256.prefix(16))…", systemImage: "checkmark.seal").foregroundStyle(.green).font(.callout)
                HStack {
                    Button("Choose Decrypted Backup…") { tools.mvtBackup = FilePanels.chooseFolder(title: "Choose a decrypted backup folder", canCreate: false) }
                    if let backup = tools.mvtBackup { Text(backup.lastPathComponent).font(.callout.monospaced()) }
                }
                HStack {
                    Text("Results").foregroundStyle(.secondary)
                    TextField("Result folder name", text: $tools.mvtOutputName).textFieldStyle(.roundedBorder)
                    Button("In…") { if let url = FilePanels.chooseFolder(title: "Choose where the result folder is created") { tools.mvtOutputParent = url } }
                }
                HStack {
                    Button("Add Indicator Files…") { tools.mvtIndicators += FilePanels.chooseFiles(title: "Choose STIX2 or JSON indicators", allowedExtensions: ["stix2", "stix", "json"]) }
                    Text(tools.mvtIndicators.isEmpty ? "No indicator files (MVT's defaults are not downloaded)" : "\(tools.mvtIndicators.count) indicator files").font(.caption).foregroundStyle(.secondary)
                }
                Toggle("Fast mode", isOn: $tools.mvtFast)
                Toggle("Hash files", isOn: $tools.mvtHashes)
                Toggle("Allow network access (for example to resolve short URLs)", isOn: $tools.mvtAllowNetwork)
                Toggle("I own this backup or have the owner's explicit consent", isOn: $tools.mvtConsent)
                Toggle("I understand that no findings is not proof that a device is safe", isOn: $tools.mvtNoVerdict)
                HStack {
                    Button("Run Analysis…") {
                        confirmation = PendingConfirmation(title: "Run MVT", detail: "MVT reads the backup and writes results to a new folder. The backup is not modified.", requirement: .make(for: .hostWrite, target: nil), target: nil, commandPreview: tools.mvt.map { "\($0.path) check-backup …" }) {
                            Task { await tools.runMVT(app: model) }
                        }
                    }
                    .disabled(tools.mvtBackup == nil || !tools.mvtConsent || !tools.mvtNoVerdict || tools.mvtRunning)
                    if tools.mvtRunning { ProgressView().controlSize(.small) }
                }
                if !tools.mvtOutput.isEmpty { RawOutputView(text: tools.mvtOutput, maxHeight: 200) }
            }
            DisclosureGroup("Install MVT") {
                RawOutputView(text: MVTConnector.setupCommands.joined(separator: "\n"), maxHeight: 80)
                Link("MVT documentation", destination: MVTConnector.backupGuideURL)
            }
        }
    }

    private func ufadeCard(_ tools: ExternalToolsModel) -> some View {
        @Bindable var tools = tools
        return Card(title: "UFADE", systemImage: "externaldrive.badge.person.crop", subtitle: "A separate GPL-3.0 acquisition app with its own Python 3.11 environment. It chooses its own device, asks for its own passwords, and keeps running if you quit the toolkit.") {
            HStack {
                Button("Choose UFADE Folder…") { tools.ufadeCheckout = FilePanels.chooseFolder(title: "Choose the UFADE checkout", canCreate: false) }
                if let checkout = tools.ufadeCheckout { Text(checkout.path).font(.callout.monospaced()).lineLimit(1).truncationMode(.middle) }
            }
            TextField("Python 3.11 (defaults to the checkout's .venv)", text: $tools.ufadePython).textFieldStyle(.roundedBorder).font(.callout.monospaced())
            HStack {
                Button("Validate") { Task { await tools.validateUFADE(app: model) } }.disabled(tools.ufadeCheckout == nil)
                if let ufade = tools.ufade {
                    Label("UFADE \(ufade.ufadeVersion), Python \(ufade.python.version)", systemImage: "checkmark.seal").foregroundStyle(.green)
                    Button("Launch UFADE") { Task { await tools.launchUFADE(app: model) } }
                }
            }
            DisclosureGroup("Install UFADE") {
                RawOutputView(text: UFADEConnector.setupCommands.joined(separator: "\n"), maxHeight: 110)
            }
        }
    }

    private func idbCard(_ tools: ExternalToolsModel) -> some View {
        @Bindable var tools = tools
        return Card(title: "idb Companion", systemImage: "rectangle.connected.to.line.below", subtitle: "Meta's automation companion. Only a read-only inventory is offered here.") {
            HStack {
                TextField("Path to idb_companion", text: $tools.idbPath).textFieldStyle(.roundedBorder).font(.callout.monospaced())
                Button("Validate") { Task { await tools.validateIDB(app: model) } }.disabled(tools.idbPath.isEmpty)
            }
            if let idb = tools.idb {
                HStack {
                    Label(idb.version, systemImage: "checkmark.seal").foregroundStyle(.green)
                    Button("List Targets") { Task { await tools.probeIDB(app: model) } }
                }
            }
            if !tools.idbOutput.isEmpty { RawOutputView(text: tools.idbOutput, maxHeight: 160) }
            Text("Install with: \(IDBCompanionConnector.setupCommand)").font(.caption.monospaced()).foregroundStyle(.secondary).textSelection(.enabled)
        }
    }
}
