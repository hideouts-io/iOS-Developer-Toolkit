import DeviceKit
import Foundation
import Observation
import ToolkitCore

/// Developer-image (DDI) state for the Device page: per-device status, the folders of developer
/// images the user added, and the preferred mount mechanism.
@Observable
@MainActor
final class DeveloperImageModel {
    private(set) var statuses: [String: DeveloperImageStatus] = [:]
    private(set) var checking: Set<String> = []
    private(set) var working: Set<String> = []

    /// Folders with developer images, in addition to the one Xcode installs.
    var folders: [URL] {
        didSet { UserDefaults.standard.set(folders.map(\.path), forKey: "developerImageFolders") }
    }

    var mechanism: DeveloperImageMechanism {
        didSet { UserDefaults.standard.set(mechanism.rawValue, forKey: "developerImageMechanism") }
    }

    init() {
        folders = (UserDefaults.standard.stringArray(forKey: "developerImageFolders") ?? []).map { URL(fileURLWithPath: $0, isDirectory: true) }
        mechanism = UserDefaults.standard.string(forKey: "developerImageMechanism").flatMap(DeveloperImageMechanism.init(rawValue:)) ?? .automatic
    }

    func status(for device: Device) -> DeveloperImageStatus? { statuses[device.id] }
    func isBusy(_ device: Device) -> Bool { checking.contains(device.id) || working.contains(device.id) }

    private func manager(_ app: AppModel) -> DeveloperImageManager {
        DeveloperImageManager(coreDevice: CoreDeviceClient(runner: app.runner))
    }

    /// Reads the device's state without changing anything.
    func refresh(_ device: Device, app: AppModel) async {
        guard !checking.contains(device.id) else { return }
        checking.insert(device.id)
        defer { checking.remove(device.id) }
        if device.kind == .demo {
            statuses[device.id] = DeveloperImageStatus(state: .personalizationRequired, headline: "A compatible image is on this Mac; Apple must personalize it (demo data).", explanation: "In Demo Mode nothing is read from a device. On a real iPhone this shows the mounted image, the iOS version and chip used to choose the image, and whether Apple's personalization is needed.", requiredKind: .personalized, facts: DeveloperImageDeviceFacts(productVersion: "26.0", buildVersion: "23A341", productType: "iPhone17,1", architecture: "arm64e"), hostImage: "Xcode developer image (demo data)", recommendedMechanism: .native)
            return
        }
        let manager = manager(app)
        let target = device.target
        let folders = folders
        statuses[device.id] = await manager.status(for: target, userFolders: folders)
    }

    func mount(_ device: Device, app: AppModel) async {
        let manager = manager(app)
        let target = device.target
        let folders = folders
        let mechanism = mechanism
        let failure = LockedValue<Error?>(nil)
        working.insert(device.id)
        defer { working.remove(device.id) }
        let result = await app.run("Mount developer image", workspace: .device, target: target, transport: mechanism.label) { operation in
            do {
                return try await manager.mount(target, mechanism: mechanism, userFolders: folders) { progress in
                    operation.report(progress.step, progress: progress.fraction)
                }
            } catch {
                failure.withLock { $0 = error }
                throw error
            }
        }
        if let result {
            statuses[device.id] = result
            app.statusMessage = result.headline
        } else if let error = failure.current {
            var status = DeveloperImageStatus.failure(error, facts: statuses[device.id]?.facts)
            if status.state != .blocked { status.explanation = "The last mount attempt failed. " + status.explanation }
            statuses[device.id] = status
        }
    }

    func unmount(_ device: Device, app: AppModel) async {
        let manager = manager(app)
        let target = device.target
        let folders = folders
        working.insert(device.id)
        defer { working.remove(device.id) }
        if let result = await app.run("Unmount developer image", workspace: .device, target: target, transport: "mobile_image_mounter UnmountImage", { _ in
            try await manager.unmount(target, userFolders: folders)
        }) {
            statuses[device.id] = result
            app.statusMessage = "The developer image is no longer mounted."
        }
    }

    /// Adds a folder after checking that it holds a developer image. Returns a message for the user.
    func addFolder(_ url: URL) -> String {
        if (try? DeveloperImageLibrary.personalizedSource(at: url, origin: .userFolder)) != nil
            || !DeveloperImageLibrary.legacySources(userFolders: [url], applications: URL(fileURLWithPath: "/nonexistent")).isEmpty {
            if !folders.contains(url) { folders.append(url) }
            return "Added \(url.lastPathComponent). Check the device again to use it."
        }
        return "\(url.lastPathComponent) does not contain a developer image. Choose a folder with BuildManifest.plist and the image (iOS 17 and later), or DeveloperDiskImage.dmg and its .signature (iOS 16 and earlier, in a folder named for the iOS version)."
    }

    func removeFolder(_ url: URL) {
        folders.removeAll { $0 == url }
    }
}
