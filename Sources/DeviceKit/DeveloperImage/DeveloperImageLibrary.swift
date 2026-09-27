import Foundation
import ToolkitCore

/// Where a developer image on this Mac came from.
public enum DeveloperImageOrigin: String, Sendable, Hashable, Codable {
    /// Installed by Xcode (`/Library/Developer/DeveloperDiskImages`, or an Xcode app's DeviceSupport).
    case xcode
    /// A folder the user chose.
    case userFolder
}

/// One build identity of a personalized image's `BuildManifest.plist`: the components Apple signs
/// for one chip/board combination.
public struct DeveloperImageBuildIdentity: Sendable, Hashable {
    public var chipID: Int
    public var boardID: Int
    public var productType: String?
    public var variant: String?
    public var imagePath: String
    public var trustCachePath: String
    /// The identity's `Manifest` dictionary, used to build the signing request.
    public var manifest: [String: PlistValue]
}

/// A personalized developer image (iOS 17 and later) on this Mac: a folder with
/// `BuildManifest.plist`, the personalized DMG, and its trust cache.
public struct PersonalizedImageSource: Sendable, Hashable {
    public var directory: URL
    public var origin: DeveloperImageOrigin
    /// Xcode's build of the image (for example 27A266a), when known.
    public var buildVersion: String?
    public var supportedProductTypes: [String]
    public var identities: [DeveloperImageBuildIdentity]

    public var displayName: String {
        (origin == .xcode ? "Xcode developer image" : "Developer image in \(directory.lastPathComponent)") + (buildVersion.map { " (\($0))" } ?? "")
    }

    /// The identity for a device's chip and board, preferring the personalized-DMG variant.
    public func identity(chipID: Int, boardID: Int) -> DeveloperImageBuildIdentity? {
        identities.first { $0.chipID == chipID && $0.boardID == boardID }
    }

    public func supports(productType: String?) -> Bool? {
        guard let productType, !supportedProductTypes.isEmpty else { return nil }
        return supportedProductTypes.contains(productType)
    }

    /// The image and trust-cache files for an identity. Paths in the manifest are relative to the
    /// folder; folders in the older flat layout use `Image.dmg` and `Image.dmg.trustcache`.
    public func files(for identity: DeveloperImageBuildIdentity) throws -> (image: URL, trustCache: URL) {
        let image = try DeveloperImageLibrary.resolve(identity.imagePath, fallback: "Image.dmg", in: directory)
        let trustCache = try DeveloperImageLibrary.resolve(identity.trustCachePath, fallback: "Image.dmg.trustcache", in: directory)
        return (image, trustCache)
    }
}

/// A legacy Developer Disk Image (iOS 16 and earlier): `DeveloperDiskImage.dmg` plus its
/// detached signature, for one iOS `major.minor` version.
public struct LegacyImageSource: Sendable, Hashable {
    public var version: String
    public var image: URL
    public var signature: URL
    public var origin: DeveloperImageOrigin

    public var displayName: String { "Developer Disk Image for iOS \(version)" + (origin == .userFolder ? " (\(image.deletingLastPathComponent().lastPathComponent))" : "") }
}

/// Finds and validates developer images on this Mac. It never downloads anything: images come
/// from Xcode or from a folder the user chooses.
public enum DeveloperImageLibrary {
    /// Where Xcode 16 and later install the iOS developer image.
    public static let xcodePersonalizedImage = URL(fileURLWithPath: "/Library/Developer/DeveloperDiskImages/iOS_DDI", isDirectory: true)
    static let maximumImageSize = 2 << 30
    static let maximumSmallFileSize = 64 << 20

    // MARK: Personalized images

    /// Xcode's installed image plus any user folders that contain a personalized image.
    public static func personalizedSources(userFolders: [URL] = [], xcodeImage: URL = xcodePersonalizedImage) -> [PersonalizedImageSource] {
        var sources: [PersonalizedImageSource] = []
        if let source = try? personalizedSource(at: xcodeImage, origin: .xcode) { sources.append(source) }
        for folder in userFolders {
            if let source = try? personalizedSource(at: folder, origin: .userFolder) { sources.append(source) }
        }
        return sources
    }

    /// Reads a personalized image folder: the folder itself, or its `Restore` subfolder (Xcode's
    /// layout), must contain `BuildManifest.plist`.
    public static func personalizedSource(at folder: URL, origin: DeveloperImageOrigin) throws -> PersonalizedImageSource {
        let candidates = [folder.appendingPathComponent("Restore", isDirectory: true), folder]
        guard let directory = candidates.first(where: { FileManager.default.fileExists(atPath: $0.appendingPathComponent("BuildManifest.plist").path) }) else {
            throw ToolkitError(.developerDiskImageUnavailable, message: "\(folder.lastPathComponent) does not contain a personalized developer image.", recovery: "Choose a folder that contains BuildManifest.plist, the image (.dmg), and its trust cache — for example /Library/Developer/DeveloperDiskImages/iOS_DDI.")
        }
        let manifestURL = directory.appendingPathComponent("BuildManifest.plist")
        let manifest = try PlistValue.decode(try readValidatedFile(manifestURL, limit: maximumSmallFileSize))
        var identities: [DeveloperImageBuildIdentity] = []
        for identity in manifest["BuildIdentities"]?.arrayValue ?? [] {
            guard let parsed = parseIdentity(identity) else { continue }
            identities.append(parsed)
        }
        guard !identities.isEmpty else {
            throw ToolkitError(.developerDiskImageUnavailable, message: "The image's BuildManifest.plist lists no personalized developer image.", technicalDetail: manifestURL.path)
        }
        var buildVersion = manifest["ProductBuildVersion"]?.stringValue
        let versionPlist = directory.deletingLastPathComponent().appendingPathComponent("version.plist")
        if let data = try? readValidatedFile(versionPlist, limit: 1 << 20), let version = try? PlistValue.decode(data) {
            buildVersion = version["ProductBuildVersion"]?.stringValue ?? buildVersion
        }
        return PersonalizedImageSource(
            directory: directory,
            origin: origin,
            buildVersion: buildVersion,
            supportedProductTypes: (manifest["SupportedProductTypes"]?.arrayValue ?? []).compactMap(\.stringValue),
            identities: identities
        )
    }

    static func parseIdentity(_ identity: PlistValue) -> DeveloperImageBuildIdentity? {
        guard let chip = parseHex(identity["ApChipID"]), let board = parseHex(identity["ApBoardID"]),
              let manifest = identity["Manifest"]?.dictionaryValue,
              let imagePath = manifest["PersonalizedDMG"]?["Info"]?["Path"]?.stringValue,
              let trustCachePath = manifest["LoadableTrustCache"]?["Info"]?["Path"]?.stringValue else {
            return nil
        }
        return DeveloperImageBuildIdentity(
            chipID: chip,
            boardID: board,
            productType: identity["Ap,ProductType"]?.stringValue,
            variant: identity["Info"]?["Variant"]?.stringValue,
            imagePath: imagePath,
            trustCachePath: trustCachePath,
            manifest: manifest
        )
    }

    /// Parses `"0x8150"`, `"33104"`, or an integer.
    static func parseHex(_ value: PlistValue?) -> Int? {
        if let number = value?.intValue { return number }
        guard let text = value?.stringValue?.trimmingCharacters(in: .whitespaces).lowercased() else { return nil }
        return text.hasPrefix("0x") ? Int(text.dropFirst(2), radix: 16) : Int(text)
    }

    static func resolve(_ relativePath: String, fallback: String, in directory: URL) throws -> URL {
        for candidate in [relativePath, fallback] {
            let url = try SecureFileIO.safeChild(of: directory, relativePath: candidate)
            if FileManager.default.fileExists(atPath: url.path) { return url }
        }
        throw ToolkitError(.developerDiskImageUnavailable, message: "The developer image folder is incomplete.", recovery: "Reinstall Xcode's device support (Xcode › Settings › Components) or choose a complete image folder.", technicalDetail: "Missing \(relativePath) in \(directory.path)")
    }

    // MARK: Legacy images

    /// Legacy images from every installed Xcode's DeviceSupport folder plus user folders. A user
    /// folder may hold the image directly (version taken from the folder name, for example
    /// "16.4") or version subfolders like Xcode's DeviceSupport.
    public static func legacySources(userFolders: [URL] = [], applications: URL = URL(fileURLWithPath: "/Applications", isDirectory: true)) -> [LegacyImageSource] {
        var sources: [LegacyImageSource] = []
        let xcodes = ((try? FileManager.default.contentsOfDirectory(at: applications, includingPropertiesForKeys: nil)) ?? [])
            .filter { $0.lastPathComponent.hasPrefix("Xcode") && $0.pathExtension == "app" }
        for xcode in xcodes {
            let deviceSupport = xcode.appendingPathComponent("Contents/Developer/Platforms/iPhoneOS.platform/DeviceSupport", isDirectory: true)
            sources += legacyImages(inContainer: deviceSupport, origin: .xcode)
        }
        for folder in userFolders {
            if let direct = legacyImage(in: folder, origin: .userFolder) {
                sources.append(direct)
            } else {
                sources += legacyImages(inContainer: folder, origin: .userFolder)
            }
        }
        return sources
    }

    static func legacyImages(inContainer container: URL, origin: DeveloperImageOrigin) -> [LegacyImageSource] {
        let folders = (try? FileManager.default.contentsOfDirectory(at: container, includingPropertiesForKeys: [.isDirectoryKey])) ?? []
        return folders.compactMap { legacyImage(in: $0, origin: origin) }
    }

    static func legacyImage(in folder: URL, origin: DeveloperImageOrigin) -> LegacyImageSource? {
        let image = folder.appendingPathComponent("DeveloperDiskImage.dmg")
        let signature = folder.appendingPathComponent("DeveloperDiskImage.dmg.signature")
        guard FileManager.default.fileExists(atPath: image.path), FileManager.default.fileExists(atPath: signature.path),
              let version = majorMinor(folder.lastPathComponent) else { return nil }
        return LegacyImageSource(version: version, image: image, signature: signature, origin: origin)
    }

    /// The `major.minor` at the start of a version or folder name ("16.4 (20E247)" → "16.4").
    public static func majorMinor(_ text: String) -> String? {
        let parts = text.split(whereSeparator: { !$0.isNumber && $0 != "." }).first.map(String.init)?.split(separator: ".") ?? []
        guard parts.count >= 2, let major = Int(parts[0]), let minor = Int(parts[1]) else {
            if parts.count == 1, let major = Int(parts[0]) { return "\(major).0" }
            return nil
        }
        return "\(major).\(minor)"
    }

    /// The legacy image for an iOS version: an exact `major.minor` match.
    public static func legacyImage(forVersion version: String?, in sources: [LegacyImageSource]) -> LegacyImageSource? {
        guard let wanted = version.flatMap(majorMinor) else { return nil }
        return sources.first { $0.version == wanted }
    }

    // MARK: Files

    /// Reads a regular file (not a symbolic link) up to `limit` bytes.
    public static func readValidatedFile(_ url: URL, limit: Int) throws -> Data {
        let values = try url.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey, .fileSizeKey])
        guard values.isRegularFile == true, values.isSymbolicLink != true else {
            throw ToolkitError(.developerDiskImageUnavailable, message: "\(url.lastPathComponent) is not a regular file.", technicalDetail: url.path)
        }
        guard let size = values.fileSize, size > 0, size <= limit else {
            throw ToolkitError(.developerDiskImageUnavailable, message: "\(url.lastPathComponent) has an unexpected size.", technicalDetail: "\(url.path): \(values.fileSize ?? -1) bytes")
        }
        return try Data(contentsOf: url, options: .mappedIfSafe)
    }

    static func readImage(_ url: URL) throws -> Data { try readValidatedFile(url, limit: maximumImageSize) }
    static func readSmallFile(_ url: URL) throws -> Data { try readValidatedFile(url, limit: maximumSmallFileSize) }
}
