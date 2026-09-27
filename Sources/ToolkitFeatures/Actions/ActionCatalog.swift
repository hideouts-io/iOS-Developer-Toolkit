import DeviceKit
import Foundation
import ToolkitCore

/// Prerequisites an action needs; each maps to a Capability Matrix row.
public enum ActionRequirement: String, Sendable, Hashable, CaseIterable {
    case trustedDevice = "pairing-trust"
    case lockdownConnection = "device-connection"
    case coreDevice = "coredevice"
    case developerMode = "developer-mode"
    case developerServices = "developer-services"
    case simulatorRunning = "simulator-running"
    case xcode = "xcode-tools"

    public var label: String {
        switch self {
        case .trustedDevice: return "Device trusts this Mac"
        case .lockdownConnection: return "Connected by USB or Wi-Fi"
        case .coreDevice: return "Xcode device service (CoreDevice)"
        case .developerMode: return "Developer Mode on"
        case .developerServices: return "Developer services (DDI) available"
        case .simulatorRunning: return "Simulator running"
        case .xcode: return "Xcode installed"
        }
    }
}

public enum ActionParameterKind: String, Sendable, Hashable {
    case bundleIdentifier, url, processIdentifier, latitude, longitude, devicePath, outputFile, outputDirectory, duration, template, text
}

public struct ActionParameter: Sendable, Hashable, Identifiable {
    public var id: String
    public var label: String
    public var help: String
    public var kind: ActionParameterKind
    public var defaultValue: String
    public var choices: [String]
    public var fileExtension: String?

    public init(id: String, label: String, help: String, kind: ActionParameterKind, defaultValue: String = "", choices: [String] = [], fileExtension: String? = nil) {
        self.id = id
        self.label = label
        self.help = help
        self.kind = kind
        self.defaultValue = defaultValue
        self.choices = choices
        self.fileExtension = fileExtension
    }

    /// Validates and normalizes one value before anything is launched.
    public func validate(_ raw: String) throws -> String {
        let value = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !value.isEmpty else { throw ToolkitError.invalidInput("\(label) is required.") }
        switch kind {
        case .bundleIdentifier:
            try BundleIdentifier.validate(value)
        case .url:
            guard let url = URL(string: value), let scheme = url.scheme?.lowercased(), !scheme.isEmpty, url.host != nil || scheme != "http" && scheme != "https" else {
                throw ToolkitError.invalidInput("\(label) must be a complete URL such as https://example.com.")
            }
        case .processIdentifier:
            guard let pid = Int(value), pid > 0 else { throw ToolkitError.invalidInput("\(label) must be a positive process number.") }
        case .latitude:
            _ = try LocationLab.validate(latitude: value, longitude: "0")
        case .longitude:
            _ = try LocationLab.validate(latitude: "0", longitude: value)
        case .devicePath:
            guard value.hasPrefix("/"), !value.contains("\0") else { throw ToolkitError.invalidInput("\(label) must start with /.") }
        case .outputFile:
            let url = URL(fileURLWithPath: (value as NSString).expandingTildeInPath)
            guard FileManager.default.fileExists(atPath: url.deletingLastPathComponent().path) else {
                throw ToolkitError.invalidInput("The folder for \(label.lowercased()) does not exist.")
            }
            guard !FileManager.default.fileExists(atPath: url.path) else {
                throw ToolkitError.invalidInput("\(url.lastPathComponent) already exists. Choose a new name; files are never overwritten.")
            }
            if let fileExtension, url.pathExtension.lowercased() != fileExtension {
                throw ToolkitError.invalidInput("\(label) must end in .\(fileExtension).")
            }
            return url.path
        case .outputDirectory:
            let url = URL(fileURLWithPath: (value as NSString).expandingTildeInPath)
            var isDirectory: ObjCBool = false
            guard FileManager.default.fileExists(atPath: url.path, isDirectory: &isDirectory), isDirectory.boolValue else {
                throw ToolkitError.invalidInput("Choose an existing folder for \(label.lowercased()).")
            }
            return url.path
        case .duration:
            guard let seconds = Int(value), (1...3600).contains(seconds) else { throw ToolkitError.invalidInput("\(label) must be between 1 and 3600 seconds.") }
        case .template:
            guard choices.contains(value) else { throw ToolkitError.invalidInput("Choose one of the listed options for \(label).") }
        case .text:
            guard value.count <= 500 else { throw ToolkitError.invalidInput("\(label) is too long.") }
        }
        return value
    }
}

public struct ActionDescriptor: Sendable, Hashable, Identifiable {
    public var id: String
    public var title: String
    public var category: String
    public var summary: String
    public var notes: String
    public var risk: ActionRisk
    public var kinds: Set<DeviceKind>
    public var requirements: [ActionRequirement]
    public var parameters: [ActionParameter]
    public var mechanism: String
    public var replacesLegacy: String?

    public func supports(_ kind: DeviceKind) -> Bool { kinds.contains(kind) }
}

public enum ActionCatalog {
    static func documents(_ name: String) -> String {
        FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Documents").appendingPathComponent(name).path
    }

    public static let categories = ["Device Basics", "Apps & Files", "Developer Services", "Capture & Instruments", "Network & Discovery", "Device Actions", "Simulator"]

    public static let all: [ActionDescriptor] = [
        // Device Basics
        ActionDescriptor(id: "device-details", title: "Device details (CoreDevice)", category: "Device Basics", summary: "Everything Xcode's device service knows about the device.", notes: "A modern, versioned record. Compare with the lockdown values when investigating differences.", risk: .readOnly, kinds: [.physical], requirements: [.xcode, .coreDevice], parameters: [], mechanism: "devicectl device info details", replacesLegacy: "developer core-device get-device-info"),
        ActionDescriptor(id: "lockdown-values", title: "Lockdown values", category: "Device Basics", summary: "Identity and configuration values the device shares with a trusted Mac.", notes: "Lockdown is the gateway for most device services. Values describe exposed state, not unrestricted iOS internals.", risk: .readOnly, kinds: [.physical], requirements: [.lockdownConnection, .trustedDevice], parameters: [], mechanism: "lockdownd GetValue (native)", replacesLegacy: "lockdown info"),
        ActionDescriptor(id: "activation-state", title: "Activation state", category: "Device Basics", summary: "Whether the device is activated with Apple.", notes: "Read-only. Activation changes are intentionally not offered.", risk: .readOnly, kinds: [.physical], requirements: [.trustedDevice], parameters: [], mechanism: "lockdownd GetValue ActivationState (native)", replacesLegacy: "activation state"),
        ActionDescriptor(id: "developer-mode-status", title: "Developer Mode status", category: "Device Basics", summary: "Whether Developer Mode is on.", notes: "Developer Mode is needed for most developer services. It does not install developer services by itself.", risk: .readOnly, kinds: [.physical], requirements: [.trustedDevice], parameters: [], mechanism: "AMFI via lockdownd (native)", replacesLegacy: "amfi developer-mode-status"),
        ActionDescriptor(id: "diagnostics", title: "Diagnostics overview", category: "Device Basics", summary: "Battery gauge, Wi-Fi, NAND, and HDMI diagnostics.", notes: "Available values vary by hardware and iOS version; a missing value is a coverage limit, not proof of absence.", risk: .readOnly, kinds: [.physical], requirements: [.trustedDevice], parameters: [], mechanism: "diagnostics_relay All (native)", replacesLegacy: "diagnostics info"),
        ActionDescriptor(id: "battery", title: "Battery snapshot", category: "Device Basics", summary: "Charge, charging state, cycle count, temperature, and estimated health.", notes: "Readings come from the IOPMPowerSource registry entry and are device-dependent.", risk: .readOnly, kinds: [.physical], requirements: [.trustedDevice], parameters: [], mechanism: "diagnostics_relay IORegistry (native)", replacesLegacy: "diagnostics battery single"),
        ActionDescriptor(id: "ioregistry", title: "IORegistry snapshot", category: "Device Basics", summary: "The device's hardware registry as exposed to diagnostics.", notes: "Large. This is the phone's registry view, not kernel access.", risk: .readOnly, kinds: [.physical], requirements: [.trustedDevice], parameters: [], mechanism: "diagnostics_relay IORegistry (native)", replacesLegacy: "diagnostics ioregistry"),
        ActionDescriptor(id: "mobilegestalt", title: "MobileGestalt values", category: "Device Basics", summary: "Hardware and configuration answers for a known key set.", notes: "Recent iOS versions answer only some keys (others report MobileGestaltDeprecated).", risk: .readOnly, kinds: [.physical], requirements: [.trustedDevice], parameters: [], mechanism: "diagnostics_relay MobileGestalt (native)", replacesLegacy: "diagnostics mg"),
        ActionDescriptor(id: "processes", title: "Running processes", category: "Device Basics", summary: "Process IDs and names running on the device.", notes: "A point-in-time view. Works over USB without Xcode; network-only devices use Xcode's device service.", risk: .readOnly, kinds: [.physical], requirements: [.trustedDevice], parameters: [], mechanism: "os_trace_relay PidList (native); devicectl device info processes for network-only devices", replacesLegacy: "processes ps / dvt proclist / core-device list-processes"),
        ActionDescriptor(id: "lock-state", title: "Lock state", category: "Device Basics", summary: "Whether a passcode is currently required.", notes: "Reports service-visible state only; it never unlocks anything.", risk: .readOnly, kinds: [.physical], requirements: [.xcode, .coreDevice], parameters: [], mechanism: "devicectl device info lockState", replacesLegacy: "core-device get-lockstate"),
        ActionDescriptor(id: "displays", title: "Displays", category: "Device Basics", summary: "Display identifiers and properties.", notes: "", risk: .readOnly, kinds: [.physical], requirements: [.xcode, .coreDevice], parameters: [], mechanism: "devicectl device info displays", replacesLegacy: "core-device get-display-info"),
        ActionDescriptor(id: "configuration-profiles", title: "Configuration profiles", category: "Device Basics", summary: "Installed configuration (MDM, VPN, Wi-Fi…) profiles.", notes: "A listed profile shows configuration state, not who uses it. Installing and removing are not offered.", risk: .readOnly, kinds: [.physical], requirements: [.trustedDevice], parameters: [], mechanism: "MCInstall GetProfileList (native); devicectl device profile list for network-only devices", replacesLegacy: "profile list"),
        ActionDescriptor(id: "provisioning-profiles", title: "Provisioning profiles", category: "Device Basics", summary: "Developer and enterprise provisioning profiles, decoded.", notes: "Profiles describe what apps may run; they are not evidence that an app ran.", risk: .readOnly, kinds: [.physical], requirements: [.trustedDevice], parameters: [], mechanism: "misagent CopyAll + CMS decoding (native)", replacesLegacy: "provision list"),
        ActionDescriptor(id: "orientation", title: "Screen orientation", category: "Device Basics", summary: "Current interface orientation.", notes: "", risk: .readOnly, kinds: [.physical], requirements: [.trustedDevice], parameters: [], mechanism: "springboardservices (native)", replacesLegacy: "springboard orientation"),
        ActionDescriptor(id: "icon-metrics", title: "Home Screen icon metrics", category: "Device Basics", summary: "Home Screen layout metrics.", notes: "Values vary by device class and display mode.", risk: .readOnly, kinds: [.physical], requirements: [.trustedDevice], parameters: [], mechanism: "springboardservices (native)", replacesLegacy: "springboard homescreen-icon-metrics"),

        // Apps & Files
        ActionDescriptor(id: "app-query", title: "Query one app", category: "Apps & Files", summary: "All installation attributes for one bundle identifier.", notes: "The app must be visible to the installation service.", risk: .readOnly, kinds: [.physical], requirements: [.trustedDevice], parameters: [ActionParameter(id: "bundle", label: "Bundle identifier", help: "For example com.apple.mobilesafari", kind: .bundleIdentifier, defaultValue: "com.apple.mobilesafari")], mechanism: "installation_proxy Browse (native)", replacesLegacy: "apps query"),
        ActionDescriptor(id: "media-list", title: "List Media folder", category: "Apps & Files", summary: "List a folder in the device's Media area (photos, downloads, recordings).", notes: "AFC is limited to /var/mobile/Media; it is not access to the whole file system or to app containers.", risk: .readOnly, kinds: [.physical], requirements: [.trustedDevice], parameters: [ActionParameter(id: "path", label: "Folder", help: "Path inside Media, for example /DCIM", kind: .devicePath, defaultValue: "/")], mechanism: "AFC (native)", replacesLegacy: "afc ls"),
        ActionDescriptor(id: "crash-list", title: "Crash report inventory", category: "Apps & Files", summary: "Crash, hang, and diagnostic reports available on the device.", notes: "Availability depends on retention; a missing report is not proof an event did not happen.", risk: .readOnly, kinds: [.physical], requirements: [.trustedDevice], parameters: [], mechanism: "crashreportcopymobile (native AFC)", replacesLegacy: "crash ls"),
        ActionDescriptor(id: "crash-pull", title: "Copy crash reports", category: "Apps & Files", summary: "Copy every available crash report into a new folder on this Mac.", notes: "Reports can contain personal information; review before sharing.", risk: .hostWrite, kinds: [.physical], requirements: [.trustedDevice], parameters: [ActionParameter(id: "folder", label: "Destination folder", help: "A new folder is created inside it.", kind: .outputDirectory, defaultValue: documents(""))], mechanism: "crashreportcopymobile (native AFC)", replacesLegacy: "crash pull"),

        // Developer Services
        ActionDescriptor(id: "ddi-status", title: "Developer image status", category: "Developer Services", summary: "Whether a compatible developer image is mounted, ready on this Mac, or needs Apple's personalization.", notes: "Checks without changing anything. Reports the iOS version, build, model, chip, and board used to choose the image.", risk: .readOnly, kinds: [.physical], requirements: [.trustedDevice], parameters: [], mechanism: "mobile_image_mounter LookupImage + host image check (native)", replacesLegacy: "mounter lookup / mounter list"),
        ActionDescriptor(id: "ddi-prepare", title: "Mount developer image", category: "Developer Services", summary: "Mount the developer image this device needs. Does nothing if a compatible one is already mounted.", notes: "iOS 17 and later: Apple personalizes the image for this device, which needs the internet and sends the device's chip, board, and ECID with a one-time nonce to Apple (as Xcode does). iOS 16 and earlier: uses DeveloperDiskImage.dmg for that exact version. Needs Developer Mode and an unlocked device.", risk: .deviceChange, kinds: [.physical], requirements: [.trustedDevice, .developerMode], parameters: [ActionParameter(id: "mechanism", label: "Mount with", help: "Automatic uses Xcode's device service when it can reach the device, otherwise the built-in client over USB.", kind: .template, defaultValue: DeveloperImageMechanism.automatic.label, choices: DeveloperImageMechanism.allCases.map(\.label))], mechanism: "Xcode device service (devicectl) or mobile_image_mounter (native) with Apple personalization", replacesLegacy: "mounter auto-mount / cryptex auto-install"),
        ActionDescriptor(id: "mounted-images", title: "Mounted developer images", category: "Developer Services", summary: "Images the device reports as mounted.", notes: "", risk: .readOnly, kinds: [.physical], requirements: [.trustedDevice], parameters: [], mechanism: "mobile_image_mounter CopyDevices (native)", replacesLegacy: "mounter list"),
        ActionDescriptor(id: "personalization", title: "Personalization identifiers", category: "Developer Services", summary: "Identifiers Apple uses to personalize developer images for this device.", notes: "Device-specific and sensitive.", risk: .readOnly, kinds: [.physical], requirements: [.trustedDevice], parameters: [], mechanism: "mobile_image_mounter (native)", replacesLegacy: "mounter query-personalization-identifiers"),
        ActionDescriptor(id: "ddi-unmount", title: "Unmount developer image", category: "Developer Services", summary: "Remove the mounted developer image until it is needed again.", notes: "Restarting the device also removes it.", risk: .deviceChange, kinds: [.physical], requirements: [.trustedDevice], parameters: [], mechanism: "mobile_image_mounter UnmountImage /System/Developer or /Developer (native)", replacesLegacy: "mounter umount"),
        ActionDescriptor(id: "host-ddis-update", title: "Update this Mac's developer images", category: "Developer Services", summary: "Refresh the Developer Disk Images Xcode keeps on this Mac.", notes: "Uses the images from the selected Xcode.", risk: .hostWrite, kinds: [.physical, .simulator], requirements: [.xcode], parameters: [], mechanism: "devicectl manage ddis update", replacesLegacy: "ios-local-ddi (local Xcode DDI)"),
        ActionDescriptor(id: "preferred-ddi", title: "Preferred developer image", category: "Developer Services", summary: "Which developer image CoreDevice will use for iOS.", notes: "", risk: .readOnly, kinds: [.physical, .simulator], requirements: [.xcode], parameters: [], mechanism: "devicectl list preferredDDI", replacesLegacy: nil),

        // Capture & Instruments
        ActionDescriptor(id: "screenshot", title: "Screenshot", category: "Capture & Instruments", summary: "Save the current screen as a PNG.", notes: "May contain notifications and other private content.", risk: .hostWrite, kinds: [.physical, .simulator], requirements: [.xcode], parameters: [ActionParameter(id: "output", label: "PNG file", help: "Where to save the screenshot.", kind: .outputFile, defaultValue: documents("device-screenshot.png"), fileExtension: "png")], mechanism: "devicectl device capture screenshot / simctl io screenshot", replacesLegacy: "developer dvt screenshot"),
        ActionDescriptor(id: "sysdiagnose", title: "Sysdiagnose", category: "Capture & Instruments", summary: "Collect Apple's full diagnostic archive from the device (takes several minutes).", notes: "Contains extensive personal and system data.", risk: .hostWrite, kinds: [.physical], requirements: [.xcode, .coreDevice], parameters: [ActionParameter(id: "folder", label: "Destination folder", help: "The archive is saved inside this folder.", kind: .outputDirectory, defaultValue: documents(""))], mechanism: "devicectl device sysdiagnose", replacesLegacy: "syslog collect"),
        ActionDescriptor(id: "instruments", title: "Instruments recording", category: "Capture & Instruments", summary: "Record performance, energy, network, or system activity for a fixed time.", notes: "Open the .trace in Instruments. Replaces the DVT telemetry streams (sysmon, energy, graphics, netstat, KDebug).", risk: .hostWrite, kinds: [.physical, .simulator], requirements: [.xcode, .developerMode], parameters: [ActionParameter(id: "template", label: "Template", help: "What to record.", kind: .template, defaultValue: "Activity Monitor", choices: InstrumentsRecorder.templates.map(\.name)), ActionParameter(id: "duration", label: "Seconds", help: "Recording length (1–3600).", kind: .duration, defaultValue: "15"), ActionParameter(id: "output", label: "Trace file", help: "Where to save the recording.", kind: .outputFile, defaultValue: documents("recording.trace"), fileExtension: "trace")], mechanism: "xcrun xctrace record", replacesLegacy: "developer dvt sysmon/energy/graphics/netstat/core-profile-session"),
        ActionDescriptor(id: "packet-capture", title: "Packet capture", category: "Capture & Instruments", summary: "Record the device's network packets for a fixed time into a .pcap file.", notes: "Open the file in Wireshark or with tcpdump -r. Encrypted traffic stays encrypted, but a capture still shows which hosts were contacted and when.", risk: .hostWrite, kinds: [.physical], requirements: [.trustedDevice], parameters: [ActionParameter(id: "duration", label: "Seconds", help: "Capture length (1–3600).", kind: .duration, defaultValue: "30"), ActionParameter(id: "output", label: "Capture file", help: "Where to save the capture.", kind: .outputFile, defaultValue: documents("capture.pcap"), fileExtension: "pcap")], mechanism: "com.apple.pcapd (native)", replacesLegacy: "pcap"),

        // Network & Discovery
        ActionDescriptor(id: "web-tabs", title: "Safari and web view tabs", category: "Network & Discovery", summary: "Pages that Safari and other apps allow to be inspected: titles and addresses.", notes: "Needs Web Inspector turned on (Settings › Apps › Safari › Advanced). Titles and addresses can be private. The device accepts a new session about every ten seconds.", risk: .readOnly, kinds: [.physical], requirements: [.trustedDevice], parameters: [], mechanism: "com.apple.webinspector (native)", replacesLegacy: "webinspector opened-tabs"),
        ActionDescriptor(id: "bonjour", title: "Discover devices on the network", category: "Network & Discovery", summary: "List Apple devices advertising pairing and developer services nearby.", notes: "Discovery shows advertisements only; it does not prove pairing or authorization.", risk: .readOnly, kinds: [.physical, .simulator], requirements: [], parameters: [], mechanism: "Network.framework Bonjour browsing", replacesLegacy: "bonjour rsd / remote browse"),
        ActionDescriptor(id: "rvi", title: "Remote Virtual Interfaces", category: "Network & Discovery", summary: "List existing rvictl packet-capture interfaces.", notes: "Read-only; interfaces are not created or removed.", risk: .readOnly, kinds: [.physical, .simulator], requirements: [.xcode], parameters: [], mechanism: "rvictl -l", replacesLegacy: nil),

        // Device Actions
        ActionDescriptor(id: "launch-app", title: "Launch app", category: "Device Actions", summary: "Start an app by bundle identifier (restarting it if already running).", notes: "Changes what is in the foreground.", risk: .deviceChange, kinds: [.physical, .simulator], requirements: [.xcode, .developerMode], parameters: [ActionParameter(id: "bundle", label: "Bundle identifier", help: "App to launch.", kind: .bundleIdentifier, defaultValue: "com.apple.mobilesafari")], mechanism: "devicectl device process launch / simctl launch", replacesLegacy: "developer dvt launch"),
        ActionDescriptor(id: "terminate", title: "Stop a process", category: "Device Actions", summary: "Ask a process to stop (SIGTERM).", notes: "Process numbers are short-lived; confirm with a fresh process list first.", risk: .deviceChange, kinds: [.physical], requirements: [.xcode, .coreDevice, .developerMode], parameters: [ActionParameter(id: "pid", label: "Process ID", help: "From the Running processes action.", kind: .processIdentifier)], mechanism: "devicectl device process terminate", replacesLegacy: "developer dvt kill"),
        ActionDescriptor(id: "open-url", title: "Open URL", category: "Device Actions", summary: "Open a URL (web page or app link) on the device.", notes: "Opening a web URL makes a network request from the device.", risk: .deviceChange, kinds: [.physical, .simulator], requirements: [.xcode], parameters: [ActionParameter(id: "url", label: "URL", help: "For example https://example.com", kind: .url, defaultValue: "https://example.com")], mechanism: "devicectl device process openURL / simctl openurl", replacesLegacy: "webinspector launch"),
        ActionDescriptor(id: "set-location", title: "Set simulated location", category: "Device Actions", summary: "Report a fixed location to apps until cleared.", notes: "Does not change GPS hardware. Use Location Lab for routes and GPX playback.", risk: .deviceChange, kinds: [.physical, .simulator], requirements: [.developerMode], parameters: [ActionParameter(id: "latitude", label: "Latitude", help: "-90 to 90", kind: .latitude, defaultValue: "34.0522"), ActionParameter(id: "longitude", label: "Longitude", help: "-180 to 180", kind: .longitude, defaultValue: "-118.2437")], mechanism: "devicectl simulate location / simctl location", replacesLegacy: "developer dvt simulate-location set"),
        ActionDescriptor(id: "clear-location", title: "Clear simulated location", category: "Device Actions", summary: "Return to the device's real location.", notes: "", risk: .deviceChange, kinds: [.physical, .simulator], requirements: [.developerMode], parameters: [], mechanism: "devicectl simulate location clear / simctl location clear", replacesLegacy: "developer dvt simulate-location clear"),
        ActionDescriptor(id: "reboot", title: "Restart device", category: "Device Actions", summary: "Restart the device.", notes: "Interrupts everything running on the device.", risk: .highImpact, kinds: [.physical], requirements: [.xcode, .coreDevice], parameters: [], mechanism: "devicectl device reboot", replacesLegacy: "diagnostics restart (Advanced Mode only)"),

        // Simulator
        ActionDescriptor(id: "sim-boot", title: "Start simulator", category: "Simulator", summary: "Boot the simulator.", notes: "", risk: .deviceChange, kinds: [.simulator], requirements: [.xcode], parameters: [], mechanism: "simctl boot", replacesLegacy: nil),
        ActionDescriptor(id: "sim-open", title: "Show in Simulator app", category: "Simulator", summary: "Open Simulator.app on this simulator.", notes: "", risk: .readOnly, kinds: [.simulator], requirements: [.xcode], parameters: [], mechanism: "open -a Simulator", replacesLegacy: nil),
        ActionDescriptor(id: "sim-shutdown", title: "Shut down simulator", category: "Simulator", summary: "Stop the simulator.", notes: "", risk: .deviceChange, kinds: [.simulator], requirements: [.xcode], parameters: [], mechanism: "simctl shutdown", replacesLegacy: nil),
        ActionDescriptor(id: "sim-dark", title: "Switch to Dark appearance", category: "Simulator", summary: "Set the simulator to Dark Mode.", notes: "", risk: .deviceChange, kinds: [.simulator], requirements: [.xcode, .simulatorRunning], parameters: [], mechanism: "simctl ui appearance dark", replacesLegacy: nil),
        ActionDescriptor(id: "sim-light", title: "Switch to Light appearance", category: "Simulator", summary: "Set the simulator to Light Mode.", notes: "", risk: .deviceChange, kinds: [.simulator], requirements: [.xcode, .simulatorRunning], parameters: [], mechanism: "simctl ui appearance light", replacesLegacy: nil),
        ActionDescriptor(id: "sim-erase", title: "Erase simulator", category: "Simulator", summary: "Delete all content and settings in the simulator.", notes: "The simulator must be shut down.", risk: .highImpact, kinds: [.simulator], requirements: [.xcode], parameters: [], mechanism: "simctl erase", replacesLegacy: nil),
    ]

    public static func descriptor(_ id: String) -> ActionDescriptor? {
        all.first { $0.id == id }
    }

    public static func actions(for kind: DeviceKind?) -> [ActionDescriptor] {
        guard let kind else { return all.filter { $0.requirements.allSatisfy { $0 == .xcode } && $0.kinds.count > 1 } }
        return all.filter { $0.supports(kind) }
    }

    /// Validates all parameters for `action`.
    public static func validate(_ action: ActionDescriptor, values: [String: String]) throws -> [String: String] {
        var validated: [String: String] = [:]
        for parameter in action.parameters {
            validated[parameter.id] = try parameter.validate(values[parameter.id] ?? parameter.defaultValue)
        }
        return validated
    }
}
