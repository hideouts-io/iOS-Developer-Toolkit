import CryptoKit
import Foundation
import OSLog
import ToolkitCore

/// The developer-image state shown to users.
public enum DeveloperImageState: String, Sendable, Hashable, Codable, CaseIterable {
    /// Simulators and the demo device do not use developer images.
    case notRequired
    /// A compatible developer image is mounted; developer services can start.
    case mounted
    /// A compatible image is on this Mac and can be mounted right away.
    case available
    /// A compatible image is on this Mac, but Apple must personalize it for this device first
    /// (needs an internet connection).
    case personalizationRequired
    /// No compatible image is on this Mac.
    case missing
    /// An image is available or mounted, but it does not fit this device or iOS version.
    case incompatible
    /// Something on the device must change first (trust, Developer Mode, unlock, USB).
    case blocked
    /// The state could not be determined, or the last mount attempt failed.
    case failed

    public var label: String {
        switch self {
        case .notRequired: return "Not required"
        case .mounted: return "Mounted"
        case .available: return "Available"
        case .personalizationRequired: return "Personalization required"
        case .missing: return "Missing"
        case .incompatible: return "Incompatible"
        case .blocked: return "Needs attention"
        case .failed: return "Failed"
        }
    }

    public var symbolName: String {
        switch self {
        case .notRequired: return "minus.circle"
        case .mounted: return "checkmark.circle.fill"
        case .available: return "arrow.down.circle"
        case .personalizationRequired: return "signature"
        case .missing: return "questionmark.circle"
        case .incompatible: return "exclamationmark.triangle"
        case .blocked: return "hand.raised"
        case .failed: return "xmark.octagon"
        }
    }

    /// Whether a mount can be started from this state.
    public var canMount: Bool { self == .available || self == .personalizationRequired }
}

/// How an image is mounted.
public enum DeveloperImageMechanism: String, Sendable, Hashable, Codable, CaseIterable {
    /// Let the toolkit choose (Xcode's device service when it can reach the device on iOS 17+,
    /// otherwise the built-in image mounter client).
    case automatic
    /// `devicectl device info ddiServices --auto-mount-ddis` — Apple's documented tool.
    case coreDevice
    /// The built-in client for the device's image mounter service over USB.
    case native

    public var label: String {
        switch self {
        case .automatic: return "Automatic"
        case .coreDevice: return "Xcode device service (devicectl)"
        case .native: return "Built-in (image mounter over USB)"
        }
    }
}

/// Facts about the device that decide which image it needs.
public struct DeveloperImageDeviceFacts: Sendable, Hashable, Codable {
    public var productVersion: String?
    public var buildVersion: String?
    public var productType: String?
    public var architecture: String?
    public var hardwareModel: String?
    public var chipID: Int?
    public var boardID: Int?
    public var developerModeEnabled: Bool?

    public init(productVersion: String? = nil, buildVersion: String? = nil, productType: String? = nil, architecture: String? = nil, hardwareModel: String? = nil, chipID: Int? = nil, boardID: Int? = nil, developerModeEnabled: Bool? = nil) {
        self.productVersion = productVersion
        self.buildVersion = buildVersion
        self.productType = productType
        self.architecture = architecture
        self.hardwareModel = hardwareModel
        self.chipID = chipID
        self.boardID = boardID
        self.developerModeEnabled = developerModeEnabled
    }

    public var majorVersion: Int? { productVersion.flatMap { Int($0.split(separator: ".").first ?? "") } }
    public var requiredKind: DeveloperImageKind { .required(forMajorVersion: majorVersion) }
}

/// What was found on the device's image mounter.
public struct DeveloperImageObservation: Sendable, Hashable {
    /// Signatures of mounted images of the required kind (empty: none mounted).
    public var mountedSignatures: [Data]
    /// Signatures of mounted images of the other kind.
    public var otherKindMounted: Bool
    public var mountedImages: [MountedImage]
    /// Whether the device already holds a personalization manifest for the host image (nil: not checked).
    public var manifestOnDevice: Bool?

    public init(mountedSignatures: [Data] = [], otherKindMounted: Bool = false, mountedImages: [MountedImage] = [], manifestOnDevice: Bool? = nil) {
        self.mountedSignatures = mountedSignatures
        self.otherKindMounted = otherKindMounted
        self.mountedImages = mountedImages
        self.manifestOnDevice = manifestOnDevice
    }
}

/// The evaluated developer-image status of one device.
public struct DeveloperImageStatus: Sendable, Hashable {
    public var state: DeveloperImageState
    public var headline: String
    public var explanation: String
    public var remediation: String?
    public var requiredKind: DeveloperImageKind?
    public var facts: DeveloperImageDeviceFacts?
    public var mountedImages: [MountedImage]
    /// The host image that would be (or was) used.
    public var hostImage: String?
    public var recommendedMechanism: DeveloperImageMechanism?
    public var technicalDetail: String?
    public var checkedAt: Date

    public init(state: DeveloperImageState, headline: String, explanation: String, remediation: String? = nil, requiredKind: DeveloperImageKind? = nil, facts: DeveloperImageDeviceFacts? = nil, mountedImages: [MountedImage] = [], hostImage: String? = nil, recommendedMechanism: DeveloperImageMechanism? = nil, technicalDetail: String? = nil, checkedAt: Date = Date()) {
        self.state = state
        self.headline = headline
        self.explanation = explanation
        self.remediation = remediation
        self.requiredKind = requiredKind
        self.facts = facts
        self.mountedImages = mountedImages
        self.hostImage = hostImage
        self.recommendedMechanism = recommendedMechanism
        self.technicalDetail = technicalDetail
        self.checkedAt = checkedAt
    }

    /// A status for an error, with the error's plain-language message and recovery.
    public static func failure(_ error: Error, facts: DeveloperImageDeviceFacts? = nil) -> DeveloperImageStatus {
        let toolkitError = error as? ToolkitError
        let state: DeveloperImageState
        switch toolkitError?.kind {
        case .deviceLocked, .developerModeDisabled, .notPaired, .pairingPending: state = .blocked
        default: state = .failed
        }
        return DeveloperImageStatus(
            state: state,
            headline: toolkitError?.message ?? "The developer image could not be checked.",
            explanation: state == .blocked ? "The device needs attention before a developer image can be checked or mounted." : "The last developer-image operation did not complete.",
            remediation: toolkitError?.recovery ?? "Reconnect the device and check again.",
            requiredKind: facts?.requiredKind,
            facts: facts,
            technicalDetail: toolkitError?.technicalDetail ?? error.localizedDescription
        )
    }
}

/// Where developer images can come from on this Mac.
public struct DeveloperImageHostInventory: Sendable, Hashable {
    public var personalized: [PersonalizedImageSource]
    public var legacy: [LegacyImageSource]
    /// Whether Xcode's device service can be used for this device (Xcode installed and the device visible to CoreDevice).
    public var coreDeviceAvailable: Bool

    public init(personalized: [PersonalizedImageSource] = [], legacy: [LegacyImageSource] = [], coreDeviceAvailable: Bool = false) {
        self.personalized = personalized
        self.legacy = legacy
        self.coreDeviceAvailable = coreDeviceAvailable
    }

    public static func discover(userFolders: [URL], coreDeviceAvailable: Bool, locations: DeveloperImageHostLocations = .system) -> DeveloperImageHostInventory {
        DeveloperImageHostInventory(
            personalized: DeveloperImageLibrary.personalizedSources(userFolders: userFolders, xcodeImage: locations.xcodePersonalizedImage),
            legacy: DeveloperImageLibrary.legacySources(userFolders: userFolders, applications: locations.applications),
            coreDeviceAvailable: coreDeviceAvailable
        )
    }

    /// The first personalized source with a build identity for this chip and board.
    public func personalizedMatch(chipID: Int?, boardID: Int?) -> (source: PersonalizedImageSource, identity: DeveloperImageBuildIdentity)? {
        guard let chipID, let boardID else { return nil }
        for source in personalized {
            if let identity = source.identity(chipID: chipID, boardID: boardID) { return (source, identity) }
        }
        return nil
    }
}

/// Decides the developer-image state. Pure, so every rule is unit-tested.
public enum DeveloperImageEvaluator {
    public static func evaluate(facts: DeveloperImageDeviceFacts, observation: DeveloperImageObservation, host: DeveloperImageHostInventory) -> DeveloperImageStatus {
        let kind = facts.requiredKind
        var status = DeveloperImageStatus(state: .missing, headline: "", explanation: "", requiredKind: kind, facts: facts, mountedImages: observation.mountedImages)
        let version = facts.productVersion.map { "iOS \($0)" } ?? "this iOS version"

        if !observation.mountedSignatures.isEmpty {
            status.state = .mounted
            status.headline = "A developer image is mounted."
            status.explanation = "\(kind.label) is mounted at \(kind.mountPath). Developer services can start; nothing needs to be mounted again."
            return status
        }
        if observation.otherKindMounted {
            status.state = .incompatible
            status.headline = "The mounted developer image does not match \(version)."
            status.explanation = "An image of the other kind is mounted. \(version) needs a \(kind.label.lowercased())."
            status.remediation = "Unmount the current image (or restart the device), then mount the matching image."
            return status
        }
        if let major = facts.majorVersion, major >= 16, facts.developerModeEnabled == false {
            status.state = .blocked
            status.headline = "Developer Mode is off."
            status.explanation = "iOS 16 and later only mount developer images when Developer Mode is on."
            status.remediation = "Turn on Settings › Privacy & Security › Developer Mode, restart the device, and confirm."
            return status
        }

        switch kind {
        case .personalized:
            if let match = host.personalizedMatch(chipID: facts.chipID, boardID: facts.boardID) {
                status.hostImage = match.source.displayName
                if match.source.supports(productType: facts.productType) == false {
                    status.state = .incompatible
                    status.headline = "The developer image on this Mac does not list this device model."
                    status.explanation = "\(match.source.displayName) does not include \(facts.productType ?? "this model") in its supported devices."
                    status.remediation = "Update Xcode, open it once to install its device support, and check again."
                    return status
                }
                status.recommendedMechanism = host.coreDeviceAvailable ? .coreDevice : .native
                if observation.manifestOnDevice == true {
                    status.state = .available
                    status.headline = "A compatible developer image is ready to mount."
                    status.explanation = "\(match.source.displayName) supports this device, and the device already holds Apple's personalization for it, so mounting does not need the internet."
                } else {
                    status.state = .personalizationRequired
                    status.headline = "A compatible developer image is on this Mac; Apple must personalize it."
                    status.explanation = "iOS 17 and later only mount images signed by Apple for this specific device. Mounting sends the device's chip, board, and ECID with a one-time nonce to Apple's signing server (as Xcode does) and needs an internet connection."
                }
                return status
            }
            if !host.personalized.isEmpty, facts.chipID != nil {
                status.state = .incompatible
                status.hostImage = host.personalized.first?.displayName
                status.headline = "The developer image on this Mac does not support this device."
                status.explanation = "No build identity in \(host.personalized.first?.displayName ?? "the image") matches this device's chip (\(hex(facts.chipID))) and board (\(hex(facts.boardID)))."
                status.remediation = "Update Xcode — newer devices need newer device support — then check again."
                return status
            }
            if host.coreDeviceAvailable {
                status.state = .personalizationRequired
                status.recommendedMechanism = .coreDevice
                status.headline = "Xcode's device service can prepare the developer image."
                status.explanation = "Xcode chooses, personalizes, and mounts the matching image. This needs an internet connection."
                return status
            }
            status.state = .missing
            status.headline = "No developer image for \(version) is on this Mac."
            status.explanation = "Xcode installs the image in /Library/Developer/DeveloperDiskImages. Without it, developer services cannot start."
            status.remediation = "Install Xcode and open it once (or run “Update this Mac's developer images” in Actions). You can also choose a folder that contains a developer image."
            return status

        case .legacy:
            if let source = DeveloperImageLibrary.legacyImage(forVersion: facts.productVersion, in: host.legacy) {
                status.state = .available
                status.hostImage = source.displayName
                status.recommendedMechanism = .native
                status.headline = "A matching Developer Disk Image is ready to mount."
                status.explanation = "\(source.displayName) matches \(version). Mounting uploads it to the device over USB."
                return status
            }
            let others = Set(host.legacy.map(\.version)).sorted { $0.compare($1, options: .numeric) == .orderedAscending }
            status.state = others.isEmpty ? .missing : .incompatible
            status.headline = others.isEmpty
                ? "No Developer Disk Image for \(version) is on this Mac."
                : "The Developer Disk Images on this Mac are for other iOS versions."
            status.explanation = "iOS 16 and earlier need DeveloperDiskImage.dmg and its signature for exactly iOS \(DeveloperImageLibrary.majorMinor(facts.productVersion ?? "") ?? "?")."
                + (others.isEmpty ? "" : " Found: iOS \(others.joined(separator: ", "))." )
                + " Current Xcode versions no longer include these images."
            status.remediation = "Choose a folder containing DeveloperDiskImage.dmg and DeveloperDiskImage.dmg.signature for this version (for example from an older Xcode's Platforms/iPhoneOS.platform/DeviceSupport), or connect the device to an Xcode version that supports it once."
            return status
        }
    }

    static func hex(_ value: Int?) -> String {
        value.map { "0x" + String($0, radix: 16, uppercase: true) } ?? "unknown"
    }
}

/// Checks, mounts, and unmounts developer images for a physical device.
public struct DeveloperImageManager: Sendable {
    public let usbmux: USBMuxClient
    public let coreDevice: CoreDeviceClient
    public let transport: PersonalizationTransport
    public let locations: DeveloperImageHostLocations
    static let logger = ToolkitLog.logger(.deviceCommunication)

    public init(usbmux: USBMuxClient = USBMuxClient(), coreDevice: CoreDeviceClient = CoreDeviceClient(), transport: PersonalizationTransport = AppleTSSTransport(), locations: DeveloperImageHostLocations = .system) {
        self.usbmux = usbmux
        self.coreDevice = coreDevice
        self.transport = transport
        self.locations = locations
    }

    // MARK: Status

    /// Reads the device and evaluates its developer-image state. Never changes the device.
    public func status(for target: DeviceTarget, userFolders: [URL] = []) async -> DeveloperImageStatus {
        switch target.kind {
        case .simulator, .demo:
            return DeveloperImageStatus(state: .notRequired, headline: "Not required.", explanation: target.kind == .simulator ? "Simulators include developer services; no developer image is needed." : "The demo device is simulated.")
        case .physical:
            break
        }
        let host = DeveloperImageHostInventory.discover(userFolders: userFolders, coreDeviceAvailable: target.coreDeviceIdentifier != nil, locations: locations)
        guard target.usbmuxDeviceID != nil else {
            return coreDeviceOnlyStatus(target: target, host: host)
        }
        do {
            return try await DeviceSession.with(target, usbmux: usbmux) { session in
                var gathered = try await gatherFacts(session)
                do {
                    let mounter = try await ImageMounter.open(session)
                    defer { Task { await mounter.close() } }
                    if gathered.requiredKind == .personalized, gathered.chipID == nil || gathered.boardID == nil,
                       let identifiers = try? PersonalizationIdentifiers(await mounter.personalizationIdentifiers()) {
                        // Some devices do not report ChipID/BoardId through lockdown; the image mounter does.
                        gathered.chipID = identifiers.chipID
                        gathered.boardID = identifiers.boardID
                    }
                    let observation = try await observe(mounter, facts: gathered, host: host)
                    return DeveloperImageEvaluator.evaluate(facts: gathered, observation: observation, host: host)
                } catch {
                    return .failure(error, facts: gathered)
                }
            }
        } catch {
            return .failure(error)
        }
    }

    func coreDeviceOnlyStatus(target: DeviceTarget, host: DeveloperImageHostInventory) -> DeveloperImageStatus {
        let facts = DeveloperImageDeviceFacts(productVersion: target.osVersion)
        guard host.coreDeviceAvailable else {
            return DeveloperImageStatus(state: .blocked, headline: "Connect the device by USB.", explanation: "The developer image can only be checked over USB, or over the network through Xcode's device service.", remediation: "Connect the device with a USB cable, unlock it, and check again.", requiredKind: facts.requiredKind, facts: facts)
        }
        return DeveloperImageStatus(state: .personalizationRequired, headline: "Xcode's device service can prepare the developer image over the network.", explanation: "This device is reachable only through Xcode's device service, which checks and mounts the image itself.", requiredKind: facts.requiredKind, facts: facts, recommendedMechanism: .coreDevice)
    }

    func gatherFacts(_ session: DeviceSession) async throws -> DeveloperImageDeviceFacts {
        let values = try await session.getValue()
        var facts = DeveloperImageDeviceFacts(
            productVersion: values?["ProductVersion"]?.stringValue,
            buildVersion: values?["BuildVersion"]?.stringValue,
            productType: values?["ProductType"]?.stringValue,
            architecture: values?["CPUArchitecture"]?.stringValue,
            hardwareModel: values?["HardwareModel"]?.stringValue,
            chipID: values?["ChipID"]?.intValue,
            boardID: values?["BoardId"]?.intValue
        )
        if let major = facts.majorVersion, major >= 16 {
            facts.developerModeEnabled = try? await session.getValue(domain: "com.apple.security.mac.amfi", key: "DeveloperModeStatus")?.boolValue
        }
        return facts
    }

    func observe(_ mounter: ImageMounter, facts: DeveloperImageDeviceFacts, host: DeveloperImageHostInventory) async throws -> DeveloperImageObservation {
        let kind = facts.requiredKind
        var observation = DeveloperImageObservation()
        observation.mountedSignatures = try await mounter.lookup(kind)
        observation.mountedImages = (try? await mounter.mountedImages()) ?? []
        if observation.mountedSignatures.isEmpty {
            let other: DeveloperImageKind = kind == .personalized ? .legacy : .personalized
            observation.otherKindMounted = observation.mountedImages.contains { $0.isDeveloperImage && $0.mountPath == other.mountPath }
        }
        if kind == .personalized, observation.mountedSignatures.isEmpty,
           let match = host.personalizedMatch(chipID: facts.chipID, boardID: facts.boardID),
           let files = try? match.source.files(for: match.identity),
           let image = try? DeveloperImageLibrary.readImage(files.image) {
            observation.manifestOnDevice = (try? await mounter.personalizationManifest(imageDigest: Data(SHA384.hash(data: image)))) != nil
        }
        return observation
    }

    // MARK: Mount

    public struct Progress: Sendable {
        public var step: String
        public var fraction: Double?
    }

    /// Mounts the developer image the device needs. If a compatible image is already mounted,
    /// nothing is changed. Returns the state after the attempt.
    public func mount(_ target: DeviceTarget, mechanism: DeveloperImageMechanism = .automatic, userFolders: [URL] = [], progress: @Sendable (Progress) -> Void = { _ in }) async throws -> DeveloperImageStatus {
        guard target.kind == .physical else {
            return await status(for: target, userFolders: userFolders)
        }
        progress(Progress(step: "Checking the device", fraction: nil))
        let before = await status(for: target, userFolders: userFolders)
        if before.state == .mounted {
            Self.logger.info("Developer image already mounted; not remounting")
            return before
        }
        if before.state == .blocked || before.state == .incompatible || before.state == .missing {
            throw ToolkitError(.developerDiskImageUnavailable, message: before.headline, recovery: before.remediation, technicalDetail: before.technicalDetail)
        }
        if before.state == .failed, target.usbmuxDeviceID != nil {
            throw ToolkitError(.developerDiskImageUnavailable, message: before.headline, recovery: before.remediation, technicalDetail: before.technicalDetail)
        }

        let chosen = resolve(mechanism, for: target, status: before)
        switch chosen {
        case .coreDevice:
            progress(Progress(step: "Xcode's device service is preparing the image", fraction: nil))
            _ = try await coreDevice.ddiServices(target, autoMount: true)
        case .native, .automatic:
            try await mountNatively(target, userFolders: userFolders, progress: progress)
        }
        progress(Progress(step: "Confirming the mount", fraction: 1))
        let after = await status(for: target, userFolders: userFolders)
        if chosen == .coreDevice && target.usbmuxDeviceID == nil {
            return DeveloperImageStatus(state: .mounted, headline: "Xcode's device service prepared the developer image.", explanation: "Developer services can start.", requiredKind: after.requiredKind, facts: after.facts, recommendedMechanism: .coreDevice)
        }
        guard after.state == .mounted else {
            throw ToolkitError(.developerDiskImageUnavailable, message: "The developer image did not mount.", recovery: after.remediation ?? "Keep the device unlocked, reconnect it, and try again.", technicalDetail: "After mount: \(after.state.rawValue) — \(after.headline)")
        }
        return after
    }

    func resolve(_ mechanism: DeveloperImageMechanism, for target: DeviceTarget, status: DeveloperImageStatus) -> DeveloperImageMechanism {
        switch mechanism {
        case .coreDevice:
            return .coreDevice
        case .native:
            return .native
        case .automatic:
            if target.usbmuxDeviceID == nil { return .coreDevice }
            if status.requiredKind == .legacy { return .native }
            return status.recommendedMechanism == .coreDevice ? .coreDevice : .native
        }
    }

    func mountNatively(_ target: DeviceTarget, userFolders: [URL], progress: @Sendable (Progress) -> Void) async throws {
        guard target.usbmuxDeviceID != nil else {
            throw ToolkitError(.unsupported, message: "The built-in mount needs a USB connection.", recovery: "Connect the device with a USB cable, or use Xcode's device service.")
        }
        let host = DeveloperImageHostInventory.discover(userFolders: userFolders, coreDeviceAvailable: false, locations: locations)
        try await DeviceSession.with(target, usbmux: usbmux) { session in
            let facts = try await gatherFacts(session)
            let mounter = try await ImageMounter.open(session)
            defer { Task { await mounter.close() } }
            let kind = facts.requiredKind
            let alreadyMounted = try await mounter.lookup(kind)
            if !alreadyMounted.isEmpty { return }
            switch kind {
            case .legacy:
                guard let source = DeveloperImageLibrary.legacyImage(forVersion: facts.productVersion, in: host.legacy) else {
                    throw ToolkitError(.developerDiskImageUnavailable, message: "No Developer Disk Image for iOS \(facts.productVersion ?? "?") is on this Mac.", recovery: "Choose a folder containing DeveloperDiskImage.dmg and its .signature for this version.")
                }
                let image = try DeveloperImageLibrary.readImage(source.image)
                let signature = try DeveloperImageLibrary.readSmallFile(source.signature)
                progress(Progress(step: "Uploading \(source.displayName)", fraction: 0))
                try await mounter.upload(.legacy, image: image, signature: signature) { progress(Progress(step: "Uploading the image", fraction: $0 * 0.9)) }
                progress(Progress(step: "Mounting", fraction: 0.95))
                _ = try await mounter.mount(.legacy, signature: signature)

            case .personalized:
                let identifiers = try PersonalizationIdentifiers(await mounter.personalizationIdentifiers())
                guard let match = host.personalizedMatch(chipID: identifiers.chipID, boardID: identifiers.boardID) else {
                    throw ToolkitError(.developerDiskImageUnavailable, message: "No developer image on this Mac supports this device.", recovery: "Update Xcode and open it once, or use Xcode's device service.", technicalDetail: "chip \(DeveloperImageEvaluator.hex(identifiers.chipID)) board \(DeveloperImageEvaluator.hex(identifiers.boardID))")
                }
                let files = try match.source.files(for: match.identity)
                let image = try DeveloperImageLibrary.readImage(files.image)
                let trustCache = try DeveloperImageLibrary.readSmallFile(files.trustCache)
                progress(Progress(step: "Checking for an existing personalization", fraction: 0.05))
                let manifest: Data
                if let existing = try await mounter.personalizationManifest(imageDigest: Data(SHA384.hash(data: image))) {
                    manifest = existing
                } else {
                    progress(Progress(step: "Asking Apple to personalize the image", fraction: 0.1))
                    let nonce = try await mounter.personalizationNonce()
                    manifest = try await ImagePersonalization.personalize(identity: match.identity, identifiers: identifiers, nonce: nonce, transport: transport)
                }
                progress(Progress(step: "Uploading the image", fraction: 0.2))
                try await mounter.upload(.personalized, image: image, signature: manifest) { progress(Progress(step: "Uploading the image", fraction: 0.2 + $0 * 0.7)) }
                progress(Progress(step: "Mounting", fraction: 0.95))
                _ = try await mounter.mount(.personalized, signature: manifest, trustCache: trustCache)
            }
            Self.logger.info("Developer image mounted natively (\(kind.rawValue, privacy: .public))")
        }
    }

    // MARK: Unmount

    /// Unmounts the developer image. Returns the state afterwards.
    public func unmount(_ target: DeviceTarget, userFolders: [URL] = []) async throws -> DeveloperImageStatus {
        guard target.kind == .physical, target.usbmuxDeviceID != nil else {
            throw ToolkitError(.unsupported, message: "Unmounting needs a USB connection.", recovery: "Connect the device with a USB cable, or restart the device (which also removes the image).")
        }
        try await DeviceSession.with(target, usbmux: usbmux) { session in
            let mounter = try await ImageMounter.open(session)
            defer { Task { await mounter.close() } }
            try await mounter.unmountDeveloperImage()
        }
        return await status(for: target, userFolders: userFolders)
    }
}

extension DeveloperImageStatus {
    /// Label/value rows shared by the app and `idt`. The ECID is never shown.
    public var detailRows: [(String, String)] {
        var rows: [(String, String)] = [("State", state.label)]
        if let requiredKind { rows.append(("Image needed", requiredKind.label)) }
        if let facts {
            if let version = facts.productVersion { rows.append(("iOS", version + (facts.buildVersion.map { " (\($0))" } ?? ""))) }
            if let productType = facts.productType { rows.append(("Model", productType + (facts.hardwareModel.map { " · \($0)" } ?? ""))) }
            if let architecture = facts.architecture { rows.append(("Architecture", architecture)) }
            if facts.chipID != nil || facts.boardID != nil {
                rows.append(("Chip / board", "\(DeveloperImageEvaluator.hex(facts.chipID)) / \(DeveloperImageEvaluator.hex(facts.boardID))"))
            }
            if let developerMode = facts.developerModeEnabled { rows.append(("Developer Mode", developerMode ? "On" : "Off")) }
        }
        let developerImages = mountedImages.filter(\.isDeveloperImage)
        if !developerImages.isEmpty {
            rows.append(("Mounted at", developerImages.compactMap(\.mountPath).joined(separator: ", ")))
        }
        if let hostImage { rows.append(("Image on this Mac", hostImage)) }
        if let recommendedMechanism, state.canMount { rows.append(("Will mount with", recommendedMechanism.label)) }
        if let remediation { rows.append(("Next step", remediation)) }
        return rows
    }
}
