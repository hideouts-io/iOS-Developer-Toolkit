import Foundation
import ToolkitCore

/// The two kinds of developer image iOS accepts through the image mounter.
public enum DeveloperImageKind: String, Sendable, Hashable, Codable, CaseIterable {
    /// iOS 17 and later: an Apple-personalized image (signed per device through Apple's TSS
    /// server), mounted at `/System/Developer`.
    case personalized = "Personalized"
    /// iOS 16 and earlier: `DeveloperDiskImage.dmg` with its detached `.signature`, mounted at
    /// `/Developer`.
    case legacy = "Developer"

    public var mountPath: String {
        switch self {
        case .personalized: return "/System/Developer"
        case .legacy: return "/Developer"
        }
    }

    public var label: String {
        switch self {
        case .personalized: return "Personalized developer image (iOS 17 and later)"
        case .legacy: return "Developer Disk Image (iOS 16 and earlier)"
        }
    }

    /// The kind a device needs, from its iOS major version (unknown versions are treated as modern).
    public static func required(forMajorVersion major: Int?) -> DeveloperImageKind {
        guard let major else { return .personalized }
        return major >= 17 ? .personalized : .legacy
    }
}

public struct MountedImage: Sendable, Hashable {
    public var mountPath: String?
    public var imageType: String?
    public var isMounted: Bool?
    public var raw: PlistValue

    /// Whether this entry is a developer image (personalized or legacy).
    public var isDeveloperImage: Bool {
        if let mountPath, mountPath == DeveloperImageKind.personalized.mountPath || mountPath == DeveloperImageKind.legacy.mountPath {
            return true
        }
        let type = (imageType ?? "").lowercased()
        return type.contains("developer") || type == "ddi" || type.hasSuffix(".ddi")
    }
}

/// `com.apple.mobile.mobile_image_mounter`, the lockdown service iOS uses for developer images.
///
/// This is a private MobileDevice service (not documented by Apple). It is the same service Xcode
/// uses on iOS 16 and earlier and for personalized images on iOS 17 and later; the message
/// formats here follow the behaviour of the open-source implementations the 0.3.x app relied on.
/// Everything that talks to it lives in this type so the rest of the app never sees raw replies.
public struct ImageMounter: Sendable {
    public static let serviceName = "com.apple.mobile.mobile_image_mounter"
    public static let personalizedImageType = "DeveloperDiskImage"
    /// Chunk size for uploads; keeps progress responsive without many tiny writes.
    static let uploadChunkSize = 1 << 20

    let connection: ServiceConnection

    public static func open(_ session: DeviceSession) async throws -> ImageMounter {
        ImageMounter(connection: try await session.openService(serviceName))
    }

    // MARK: Queries

    public func mountedImages() async throws -> [MountedImage] {
        let reply = try await command(["Command": "CopyDevices"], operation: "list mounted images")
        return (reply["EntryList"]?.arrayValue ?? []).map { entry in
            MountedImage(
                mountPath: entry["MountPath"]?.stringValue,
                imageType: entry["DiskImageType"]?.stringValue ?? entry["ImageType"]?.stringValue ?? entry["PersonalizedImageType"]?.stringValue,
                isMounted: entry["IsMounted"]?.boolValue,
                raw: entry
            )
        }
    }

    public func developerModeStatus() async throws -> Bool? {
        try await command(["Command": "QueryDeveloperModeStatus"], operation: "read Developer Mode status")["DeveloperModeStatus"]?.boolValue
    }

    /// The signatures of mounted images of `kind`; empty when none is mounted.
    public func lookup(_ kind: DeveloperImageKind) async throws -> [Data] {
        let reply = try await connection.messages.request(["Command": "LookupImage", "ImageType": .string(kind.rawValue)], timeout: 60)
        if reply["Error"] != nil {
            let failure = Self.classify(reply)
            if failure == .notMounted { return [] }
            throw Self.error(for: failure, reply: reply, operation: "check the mounted developer image")
        }
        if reply["ImagePresent"]?.boolValue == false { return [] }
        switch reply["ImageSignature"] {
        case .array(let values): return values.compactMap(\.dataValue)
        case .data(let value): return [value]
        default: return []
        }
    }

    public func personalizationIdentifiers() async throws -> PlistValue {
        try await command(
            ["Command": "QueryPersonalizationIdentifiers", "PersonalizedImageType": .string(Self.personalizedImageType)],
            operation: "read personalization identifiers"
        )["PersonalizationIdentifiers"] ?? .dictionary([:])
    }

    public func personalizationNonce() async throws -> Data {
        let reply = try await command(
            ["Command": "QueryNonce", "PersonalizedImageType": .string(Self.personalizedImageType)],
            operation: "read the personalization nonce"
        )
        guard let nonce = reply["PersonalizationNonce"]?.dataValue, !nonce.isEmpty else {
            throw ToolkitError(.protocolViolation, message: "The device did not provide a personalization nonce.", recovery: "Unlock the device, reconnect it, and try again.", technicalDetail: reply.prettyJSONString())
        }
        return nonce
    }

    /// A personalization manifest the device already holds for an image with this SHA-384
    /// digest, or nil when the image must be personalized by Apple first.
    public func personalizationManifest(imageDigest: Data) async throws -> Data? {
        let reply = try await connection.messages.request([
            "Command": "QueryPersonalizationManifest",
            "PersonalizedImageType": .string(Self.personalizedImageType),
            "ImageType": .string(Self.personalizedImageType),
            "ImageSignature": .data(imageDigest),
        ], timeout: 60)
        if let manifest = reply["ImageSignature"]?.dataValue, !manifest.isEmpty { return manifest }
        if reply["Error"] != nil {
            let failure = Self.classify(reply)
            if failure == .deviceLocked || failure == .developerModeDisabled {
                throw Self.error(for: failure, reply: reply, operation: "check the personalization manifest")
            }
        }
        return nil
    }

    // MARK: Changes

    public enum MountOutcome: Sendable, Equatable {
        case mounted
        case alreadyMounted
    }

    /// Sends the image bytes to the device (`ReceiveBytes`).
    public func upload(_ kind: DeveloperImageKind, image: Data, signature: Data, progress: @Sendable (Double) -> Void = { _ in }) async throws {
        let ack = try await connection.messages.request([
            "Command": "ReceiveBytes",
            "ImageType": .string(kind.rawValue),
            "ImageSize": .integer(Int64(image.count)),
            "ImageSignature": .data(signature),
        ], timeout: 60)
        guard ack["Status"]?.stringValue == "ReceiveBytesAck" else {
            throw Self.error(for: Self.classify(ack), reply: ack, operation: "start the image upload")
        }
        var offset = 0
        while offset < image.count {
            try Task.checkCancellation()
            let end = min(offset + Self.uploadChunkSize, image.count)
            try await connection.channel.write(image.subdata(in: offset..<end))
            offset = end
            progress(Double(offset) / Double(max(image.count, 1)))
        }
        let done = try await connection.messages.receive(timeout: 300)
        guard done["Status"]?.stringValue == "Complete" else {
            throw Self.error(for: Self.classify(done), reply: done, operation: "upload the image")
        }
    }

    /// Mounts an uploaded image (`MountImage`). An image that is already mounted is not an error.
    public func mount(_ kind: DeveloperImageKind, signature: Data, trustCache: Data? = nil) async throws -> MountOutcome {
        var request: [String: PlistValue] = [
            "Command": "MountImage",
            "ImageType": .string(kind.rawValue),
            "ImageSignature": .data(signature),
        ]
        if let trustCache { request["ImageTrustCache"] = .data(trustCache) }
        let reply = try await connection.messages.request(.dictionary(request), timeout: 300)
        if reply["Status"]?.stringValue == "Complete" { return .mounted }
        let failure = Self.classify(reply)
        if failure == .alreadyMounted { return .alreadyMounted }
        throw Self.error(for: failure, reply: reply, operation: "mount the developer image")
    }

    /// Unmounts the image of `kind`. Returns false when nothing was mounted there.
    @discardableResult
    public func unmount(_ kind: DeveloperImageKind) async throws -> Bool {
        let reply = try await connection.messages.request(["Command": "UnmountImage", "MountPath": .string(kind.mountPath)], timeout: 60)
        guard reply["Error"] != nil else { return true }
        let failure = Self.classify(reply)
        if failure == .notMounted { return false }
        throw Self.error(for: failure, reply: reply, operation: "unmount the developer image")
    }

    /// Unmounts whichever developer image is mounted (personalized first, then legacy).
    public func unmountDeveloperImage() async throws {
        if try await unmount(.personalized) { return }
        try await unmount(.legacy)
    }

    func command(_ request: PlistValue, operation: String) async throws -> PlistValue {
        let reply = try await connection.messages.request(request, timeout: 60)
        if reply["Error"] != nil {
            throw Self.error(for: Self.classify(reply), reply: reply, operation: operation)
        }
        return reply
    }

    public func close() async {
        _ = try? await connection.messages.request(["Command": "Hangup"], timeout: 5)
        await connection.close()
    }

    // MARK: Errors

    /// What an image-mounter error reply means, independent of its exact wording.
    public enum Failure: String, Sendable, Equatable {
        case deviceLocked
        case developerModeDisabled
        case alreadyMounted
        case notMounted
        case signatureRejected
        case unsupported
        case other
    }

    public static func classify(_ reply: PlistValue) -> Failure {
        let error = reply["Error"]?.stringValue ?? ""
        let detail = (reply["DetailedError"]?.stringValue ?? "").lowercased()
        let combined = (error + " " + detail).lowercased()
        if combined.contains("devicelocked") || combined.contains("device is locked") || combined.contains("passcode") { return .deviceLocked }
        if combined.contains("developer mode is not enabled") || combined.contains("developermode") { return .developerModeDisabled }
        if combined.contains("already mounted") || combined.contains("imagealreadymounted") { return .alreadyMounted }
        if combined.contains("no matching entry") || combined.contains("not mounted") || combined.contains("notmounted") || combined.contains("imagenotpresent") { return .notMounted }
        if error == "UnknownCommand" || error == "UnsupportedCommand" || combined.contains("unsupported") { return .unsupported }
        if combined.contains("signature") || combined.contains("personaliz") || combined.contains("manifest") || combined.contains("img4") || combined.contains("trust cache") { return .signatureRejected }
        return .other
    }

    /// A plain-language error for an image-mounter failure. The raw reply goes only into the
    /// technical detail.
    public static func error(for failure: Failure, reply: PlistValue, operation: String) -> ToolkitError {
        let detail = [reply["Error"]?.stringValue, reply["DetailedError"]?.stringValue, reply["Status"]?.stringValue]
            .compactMap { $0 }.joined(separator: " — ")
        let technical = "mobile_image_mounter: \(operation): \(detail.isEmpty ? reply.prettyJSONString() : detail)"
        switch failure {
        case .deviceLocked:
            return ToolkitError(.deviceLocked, message: "The device is locked.", recovery: "Unlock the device and keep it unlocked until the developer image is mounted.", technicalDetail: technical)
        case .developerModeDisabled:
            return ToolkitError(.developerModeDisabled, message: "Developer Mode is off on the device.", recovery: "Turn it on in Settings › Privacy & Security › Developer Mode, restart, confirm, then try again.", technicalDetail: technical)
        case .alreadyMounted:
            return ToolkitError(.commandFailed, message: "A developer image is already mounted.", recovery: "Nothing to do; developer services can use it.", technicalDetail: technical)
        case .notMounted:
            return ToolkitError(.developerDiskImageUnavailable, message: "No developer image is mounted.", recovery: "Mount the developer image from the Device page.", technicalDetail: technical)
        case .signatureRejected:
            return ToolkitError(.developerDiskImageUnavailable, message: "The device did not accept the developer image's signature.", recovery: "The image may not match this iOS version, or its personalization expired. Update Xcode (it installs current images), keep this Mac online so Apple can personalize the image, and try again.", technicalDetail: technical)
        case .unsupported:
            return ToolkitError(.unsupported, message: "This iOS version does not support that developer-image operation.", recovery: "Check the image type: iOS 17 and later use personalized images; iOS 16 and earlier use DeveloperDiskImage.dmg.", technicalDetail: technical)
        case .other:
            return ToolkitError(.developerDiskImageUnavailable, message: "The device could not \(operation).", recovery: "Keep the device unlocked and connected by USB, then try again. Restarting the device clears a stuck image mount.", technicalDetail: technical)
        }
    }
}
