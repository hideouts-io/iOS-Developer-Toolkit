import DeviceKit
import Foundation

/// The guided reconnect (0.3.x “Reconnect & Retry…”): step-by-step instructions, then a window
/// in which the app watches macOS's device service for a USB device to appear. Discovery is
/// event-driven, so nothing is polled or restarted; this only interprets what arrives.
public enum ReconnectGuide {
    public static let window: Duration = .seconds(30)

    public static let steps = [
        "Unlock the iPhone or iPad and keep it on the Home Screen.",
        "If iOS asks, allow the accessory to connect. Where available, check Settings › Privacy & Security › Wired Accessories if the Mac is not allowed.",
        "Disconnect the cable and reconnect it directly to the Mac with a data-capable cable. Avoid hubs.",
        "Click Allow if macOS asks to connect the accessory, then tap Trust on the device and enter its passcode.",
    ]

    /// What the app never does while reconnecting.
    public static let boundary = "The toolkit never uses sudo, deletes pairing records, restarts macOS's device service, or changes the device."

    public enum Outcome: Equatable, Sendable {
        case waiting
        /// A trusted device is connected over USB.
        case connected(name: String)
        /// A device is connected but has not trusted this Mac yet.
        case awaitingTrust(name: String)
        case timedOut

        public var headline: String {
            switch self {
            case .waiting: return "Watching for the device…"
            case .connected(let name): return "\(name) is connected and trusts this Mac."
            case .awaitingTrust(let name): return "\(name) is connected. Tap Trust on the device."
            case .timedOut: return "No device appeared."
            }
        }

        public var nextStep: String? {
            switch self {
            case .waiting, .connected: return nil
            case .awaitingTrust: return "Unlock the device and tap Trust, then enter its passcode. You can also select the device in the Finder sidebar and click Trust."
            case .timedOut: return "Unlock the device, try another data-capable cable or Mac port without a hub, and complete Trust in the Finder sidebar. Connection diagnostics on the Device page shows whether macOS's device service is answering; if it is not, restart the Mac."
            }
        }

        public var isFinished: Bool { self != .waiting }
    }

    /// Interprets the devices currently seen over USB. A device already connected and trusted when
    /// the window opened counts as connected; `timeElapsed` ends the window.
    public static func evaluate(devices: [Device], timeElapsed: Bool) -> Outcome {
        let usb = devices.filter { $0.kind == .physical && $0.transports.contains(.usb) }
        if let trusted = usb.first(where: { $0.pairingState == .paired }) {
            return .connected(name: trusted.name)
        }
        if let untrusted = usb.first {
            return timeElapsed || untrusted.pairingState == .unpaired ? .awaitingTrust(name: untrusted.name) : .waiting
        }
        return timeElapsed ? .timedOut : .waiting
    }
}
