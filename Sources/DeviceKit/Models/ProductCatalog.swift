import Foundation

/// Marketing names for hardware identifiers. CoreDevice supplies `marketingName` directly, so
/// this table is only a fallback for devices seen through usbmuxd on a Mac without Xcode.
/// Unknown identifiers are shown as-is rather than guessed.
public enum ProductCatalog {
    public static func marketingName(for productType: String) -> String? {
        table[productType]
    }

    static let table: [String: String] = [
        // iPhone
        "iPhone11,2": "iPhone XS", "iPhone11,4": "iPhone XS Max", "iPhone11,6": "iPhone XS Max", "iPhone11,8": "iPhone XR",
        "iPhone12,1": "iPhone 11", "iPhone12,3": "iPhone 11 Pro", "iPhone12,5": "iPhone 11 Pro Max", "iPhone12,8": "iPhone SE (2nd generation)",
        "iPhone13,1": "iPhone 12 mini", "iPhone13,2": "iPhone 12", "iPhone13,3": "iPhone 12 Pro", "iPhone13,4": "iPhone 12 Pro Max",
        "iPhone14,2": "iPhone 13 Pro", "iPhone14,3": "iPhone 13 Pro Max", "iPhone14,4": "iPhone 13 mini", "iPhone14,5": "iPhone 13",
        "iPhone14,6": "iPhone SE (3rd generation)", "iPhone14,7": "iPhone 14", "iPhone14,8": "iPhone 14 Plus",
        "iPhone15,2": "iPhone 14 Pro", "iPhone15,3": "iPhone 14 Pro Max", "iPhone15,4": "iPhone 15", "iPhone15,5": "iPhone 15 Plus",
        "iPhone16,1": "iPhone 15 Pro", "iPhone16,2": "iPhone 15 Pro Max",
        "iPhone17,1": "iPhone 16 Pro", "iPhone17,2": "iPhone 16 Pro Max", "iPhone17,3": "iPhone 16", "iPhone17,4": "iPhone 16 Plus", "iPhone17,5": "iPhone 16e",
        "iPhone18,1": "iPhone 17 Pro", "iPhone18,2": "iPhone 17 Pro Max", "iPhone18,3": "iPhone 17", "iPhone18,4": "iPhone Air",
        // iPad
        "iPad11,1": "iPad mini (5th generation)", "iPad11,2": "iPad mini (5th generation)",
        "iPad11,3": "iPad Air (3rd generation)", "iPad11,4": "iPad Air (3rd generation)",
        "iPad11,6": "iPad (8th generation)", "iPad11,7": "iPad (8th generation)",
        "iPad12,1": "iPad (9th generation)", "iPad12,2": "iPad (9th generation)",
        "iPad13,1": "iPad Air (4th generation)", "iPad13,2": "iPad Air (4th generation)",
        "iPad13,4": "iPad Pro 11-inch (3rd generation)", "iPad13,5": "iPad Pro 11-inch (3rd generation)",
        "iPad13,6": "iPad Pro 11-inch (3rd generation)", "iPad13,7": "iPad Pro 11-inch (3rd generation)",
        "iPad13,8": "iPad Pro 12.9-inch (5th generation)", "iPad13,9": "iPad Pro 12.9-inch (5th generation)",
        "iPad13,10": "iPad Pro 12.9-inch (5th generation)", "iPad13,11": "iPad Pro 12.9-inch (5th generation)",
        "iPad13,16": "iPad Air (5th generation)", "iPad13,17": "iPad Air (5th generation)",
        "iPad13,18": "iPad (10th generation)", "iPad13,19": "iPad (10th generation)",
        "iPad14,1": "iPad mini (6th generation)", "iPad14,2": "iPad mini (6th generation)",
        "iPad14,3": "iPad Pro 11-inch (4th generation)", "iPad14,4": "iPad Pro 11-inch (4th generation)",
        "iPad14,5": "iPad Pro 12.9-inch (6th generation)", "iPad14,6": "iPad Pro 12.9-inch (6th generation)",
        "iPad14,8": "iPad Air 11-inch (M2)", "iPad14,9": "iPad Air 11-inch (M2)",
        "iPad14,10": "iPad Air 13-inch (M2)", "iPad14,11": "iPad Air 13-inch (M2)",
        "iPad15,3": "iPad Air 11-inch (M3)", "iPad15,4": "iPad Air 11-inch (M3)",
        "iPad15,5": "iPad Air 13-inch (M3)", "iPad15,6": "iPad Air 13-inch (M3)",
        "iPad15,7": "iPad (A16)", "iPad15,8": "iPad (A16)",
        "iPad16,1": "iPad mini (A17 Pro)", "iPad16,2": "iPad mini (A17 Pro)",
        "iPad16,3": "iPad Pro 11-inch (M4)", "iPad16,4": "iPad Pro 11-inch (M4)",
        "iPad16,5": "iPad Pro 13-inch (M4)", "iPad16,6": "iPad Pro 13-inch (M4)",
        // iPod
        "iPod9,1": "iPod touch (7th generation)",
    ]
}

/// Plain-language explanations of technical fields, shown as help text next to each value.
public enum DeviceField: String, CaseIterable, Sendable, Identifiable {
    case name
    case model
    case hardwareIdentifier
    case hardwareModel
    case udid
    case osVersion
    case buildNumber
    case architecture
    case connection
    case pairing
    case developerMode
    case developerServices
    case serialNumber
    case ecid
    case coreDeviceIdentifier
    case simulatorRuntime
    case simulatorState

    public var id: String { rawValue }

    public var title: String {
        switch self {
        case .name: return "Device name"
        case .model: return "Model"
        case .hardwareIdentifier: return "Hardware identifier"
        case .hardwareModel: return "Board model"
        case .udid: return "UDID"
        case .osVersion: return "System version"
        case .buildNumber: return "Build number"
        case .architecture: return "Architecture"
        case .connection: return "Connection"
        case .pairing: return "Trust (pairing)"
        case .developerMode: return "Developer Mode"
        case .developerServices: return "Developer services"
        case .serialNumber: return "Serial number"
        case .ecid: return "ECID"
        case .coreDeviceIdentifier: return "CoreDevice identifier"
        case .simulatorRuntime: return "Simulator runtime"
        case .simulatorState: return "Simulator state"
        }
    }

    public var explanation: String {
        switch self {
        case .name:
            return "The name set in Settings › General › About. Anyone can change it, so it is not a reliable identifier."
        case .model:
            return "The product name Apple uses when selling the device, such as “iPhone 15 Pro”."
        case .hardwareIdentifier:
            return "Apple's internal model code, such as “iPhone16,1”. Tools and crash reports use this instead of the marketing name."
        case .hardwareModel:
            return "The logic-board identifier, such as “D83AP”. Useful when matching firmware or repair information."
        case .udid:
            return "Unique Device Identifier. Xcode, provisioning profiles, and every tool in this app use it to address exactly one device. Treat it as private."
        case .osVersion:
            return "The installed iOS or iPadOS version. Many developer features depend on it, for example Developer Mode exists only on iOS 16 and later."
        case .buildNumber:
            return "Apple's exact build of the system, such as “23A341”. Two devices on the same version can still run different builds."
        case .architecture:
            return "The processor instruction set. Modern iPhones and iPads use arm64e; apps must be built for it to run."
        case .connection:
            return "How this Mac reaches the device right now. USB is the most reliable. Network connections require the device to have been paired over USB first."
        case .pairing:
            return "Whether the device has trusted this Mac (the “Trust This Computer?” prompt). Nothing except basic detection works until the device trusts the Mac."
        case .developerMode:
            return "An iOS 16+ setting (Settings › Privacy & Security › Developer Mode) that allows development features such as running your own apps, location simulation, and developer services. Turning it on requires a restart."
        case .developerServices:
            return "Whether Xcode's developer services (the Developer Disk Image) are available on the device. They are installed automatically when needed and are required for screenshots, location simulation, and process control."
        case .serialNumber:
            return "The hardware serial number printed on the device and box. It is personally identifying; avoid sharing it."
        case .ecid:
            return "Exclusive Chip ID, a unique number burned into the processor. Apple uses it to personalize firmware and developer images."
        case .coreDeviceIdentifier:
            return "The identifier Xcode's CoreDevice service assigns to this device on this Mac."
        case .simulatorRuntime:
            return "The iOS version of the simulated device. Simulator runtimes are installed through Xcode › Settings › Components."
        case .simulatorState:
            return "Whether the simulator is running. Most actions need a running (booted) simulator."
        }
    }

    /// Fields that should be hidden behind a disclosure because they are identifying.
    public var isSensitive: Bool {
        switch self {
        case .udid, .serialNumber, .ecid, .coreDeviceIdentifier: return true
        default: return false
        }
    }
}
