import DeviceKit
import SwiftUI
import ToolkitFeatures

/// Guided reconnect: the steps, then a 30-second watch on device discovery with a clear outcome.
struct ReconnectGuideView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @State private var startedAt: Date?
    @State private var now = Date()

    private var windowSeconds: Double { Double(ReconnectGuide.window.components.seconds) }

    private var outcome: ReconnectGuide.Outcome? {
        guard let startedAt else { return nil }
        return ReconnectGuide.evaluate(devices: model.allDevices, timeElapsed: now.timeIntervalSince(startedAt) >= windowSeconds)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Label("Reconnect a device", systemImage: "cable.connector").font(.title2.bold())
            VStack(alignment: .leading, spacing: 8) {
                ForEach(Array(ReconnectGuide.steps.enumerated()), id: \.offset) { index, step in
                    Text("\(index + 1). \(step)").fixedSize(horizontal: false, vertical: true)
                }
            }
            Text(ReconnectGuide.boundary)
                .font(.callout)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            if let outcome {
                GroupBox {
                    VStack(alignment: .leading, spacing: 6) {
                        HStack(spacing: 8) {
                            if outcome.isFinished {
                                Image(systemName: symbol(for: outcome)).foregroundStyle(color(for: outcome))
                            } else {
                                ProgressView().controlSize(.small)
                            }
                            Text(outcome.headline).font(.callout.weight(.semibold))
                            Spacer()
                            if !outcome.isFinished, let startedAt {
                                Text("\(max(0, Int(windowSeconds - now.timeIntervalSince(startedAt))))s")
                                    .monospacedDigit()
                                    .foregroundStyle(.secondary)
                            }
                        }
                        if let next = outcome.nextStep {
                            Text(next).font(.callout).fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
                .accessibilityIdentifier("reconnect-outcome")
            }
            HStack {
                Spacer()
                Button("Close") { dismiss() }
                if case .connected = outcome, let device = connectedDevice {
                    Button("Use \(device.name)") {
                        model.selectedDeviceID = device.id
                        dismiss()
                    }
                    .keyboardShortcut(.defaultAction)
                } else {
                    Button(startedAt == nil ? "Start Watching" : "Watch Again") { start() }
                        .keyboardShortcut(.defaultAction)
                        .disabled(outcome.map { !$0.isFinished } ?? false)
                        .accessibilityIdentifier("reconnect-start")
                }
            }
        }
        .padding(22)
        .frame(width: 560)
        .task(id: startedAt) {
            guard startedAt != nil else { return }
            while !Task.isCancelled {
                now = Date()
                if outcome?.isFinished == true { break }
                try? await Task.sleep(for: .milliseconds(500))
            }
        }
    }

    private var connectedDevice: Device? {
        model.allDevices.first { $0.kind == .physical && $0.transports.contains(.usb) && $0.pairingState == .paired }
    }

    private func start() {
        startedAt = Date()
        now = Date()
        Task { await model.refreshDevices() }
    }

    private func symbol(for outcome: ReconnectGuide.Outcome) -> String {
        switch outcome {
        case .connected: return "checkmark.circle.fill"
        case .awaitingTrust: return "hand.tap"
        case .timedOut, .waiting: return "exclamationmark.triangle"
        }
    }

    private func color(for outcome: ReconnectGuide.Outcome) -> Color {
        switch outcome {
        case .connected: return .green
        case .awaitingTrust: return .blue
        case .timedOut, .waiting: return .orange
        }
    }
}
