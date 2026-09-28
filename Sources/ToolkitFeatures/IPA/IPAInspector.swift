import Foundation
import ToolkitCore

public struct IPAInspection: Sendable, Hashable, Codable {
    public var packagePath: String
    public var packageSHA256: String
    public var appName: String
    public var bundleIdentifier: String
    public var version: String
    public var build: String
    public var minimumOSVersion: String?
    public var executableName: String
    public var supportedPlatforms: [String]
    public var provisioning: ProvisioningProfileSummary
    public var signature: CodeSignatureSummary

    /// Whether the toolkit will offer installation. iOS still makes the final decision.
    public var isInstallable: Bool {
        signature.status == .valid && provisioning.status != .invalid
    }

    public var installabilityExplanation: String {
        switch signature.status {
        case .missing:
            return "The app is not signed, so iOS will refuse to install it. Sign it in Xcode with a profile that includes the device."
        case .invalid:
            return "The app's signature is broken (files were changed after signing), so iOS will refuse to install it."
        case .valid:
            if provisioning.status == .invalid { return "The embedded provisioning profile is damaged." }
            if provisioning.isExpired { return "The signature is valid, but the provisioning profile has expired; iOS will likely refuse to launch the app." }
            return "The signature is valid. iOS will still check that the provisioning profile covers the device."
        }
    }

    /// A readable report (also used by the command-line tool).
    public var report: String {
        let dateFormatter = ISO8601DateFormatter()
        func text(_ value: String?) -> String { value ?? "not declared" }
        return [
            "App: \(appName)",
            "Bundle identifier: \(bundleIdentifier)",
            "Version: \(version) (\(build))",
            "Minimum iOS: \(text(minimumOSVersion))",
            "Executable: \(executableName)",
            "Package SHA-256: \(packageSHA256)",
            "",
            "Code signature: \(signature.status.rawValue)",
            "Signing identifier: \(text(signature.identifier))",
            "Signing team: \(text(signature.teamIdentifier))",
            "Authorities: \(signature.authorities.isEmpty ? "not declared" : signature.authorities.joined(separator: " → "))",
            "Verification detail: \(signature.detail)",
            "",
            "Provisioning profile: \(provisioning.status.rawValue)",
            "Profile name: \(text(provisioning.name))",
            "Profile type: \(provisioning.profileKind)",
            "Application identifier: \(text(provisioning.applicationIdentifier))",
            "Teams: \(provisioning.teamIdentifiers.isEmpty ? "not declared" : provisioning.teamIdentifiers.joined(separator: ", "))",
            "Expires: \(provisioning.expirationDate.map(dateFormatter.string(from:)) ?? "not declared")",
            "Provisioned devices: \(provisioning.provisionedDevices.count)",
            "All devices: \(provisioning.provisionsAllDevices)",
            "Debugging allowed (get-task-allow): \(provisioning.getTaskAllow.map(String.init) ?? "not declared")",
            "Developer certificates: \(provisioning.allowedSignerCount)",
            "",
            "Assessment: \(installabilityExplanation)",
        ].joined(separator: "\n")
    }
}

/// Inspects an `.ipa` package locally before any installation is offered.
public enum IPAInspector {
    public static let maximumInfoPlistBytes: UInt64 = 10 * 1024 * 1024

    public static func inspect(_ url: URL) throws -> IPAInspection {
        guard url.pathExtension.lowercased() == "ipa" else {
            throw ToolkitError.invalidInput("Choose an .ipa package.")
        }
        guard FileManager.default.isReadableFile(atPath: url.path) else {
            throw ToolkitError.fileSystem("The package could not be read.", path: url.path)
        }
        let archive = try ZipArchive(url: url)
        let infoCandidates = archive.entries.filter {
            let parts = $0.name.split(separator: "/")
            return parts.count == 3 && parts[0] == "Payload" && parts[1].hasSuffix(".app") && parts[2] == "Info.plist"
        }
        guard infoCandidates.count == 1, let infoEntry = infoCandidates.first else {
            throw ToolkitError.invalidInput("The package must contain exactly one Payload/<name>.app/Info.plist (found \(infoCandidates.count)).")
        }
        let info = try PlistValue.decode(try archive.data(for: infoEntry, limit: maximumInfoPlistBytes))
        guard info.dictionaryValue != nil else { throw ToolkitError.invalidInput("The app's Info.plist is not a dictionary.") }
        func required(_ key: String) throws -> String {
            guard let value = info[key]?.stringValue, !value.trimmingCharacters(in: .whitespaces).isEmpty else {
                throw ToolkitError.invalidInput("The app's Info.plist is missing \(key).")
            }
            return value
        }
        let appRoot = String(infoEntry.name.dropLast("/Info.plist".count))
        let bundleIdentifier = try required("CFBundleIdentifier")

        let provisioning: ProvisioningProfileSummary
        if let profileEntry = archive.entry(named: "\(appRoot)/embedded.mobileprovision") {
            provisioning = ProvisioningProfileDecoder.decode(try archive.data(for: profileEntry, limit: UInt64(ProvisioningProfileDecoder.maximumProfileBytes)))
        } else {
            provisioning = .absent
        }

        let signature: CodeSignatureSummary
        if archive.entry(named: "\(appRoot)/_CodeSignature/CodeResources") == nil {
            signature = CodeSignatureSummary(status: .missing, identifier: nil, teamIdentifier: nil, authorities: [], detail: "_CodeSignature/CodeResources is absent.")
        } else {
            let scratch = try SecureFileIO.makeTemporaryDirectory(prefix: "idt-ipa")
            defer { try? FileManager.default.removeItem(at: scratch) }
            try archive.extract(prefix: appRoot + "/", to: scratch)
            signature = CodeSignatureInspector.inspect(bundle: try SecureFileIO.safeChild(of: scratch, relativePath: appRoot))
        }

        return IPAInspection(
            packagePath: url.path,
            packageSHA256: try SecureFileIO.sha256(of: url),
            appName: try info["CFBundleDisplayName"]?.stringValue ?? required("CFBundleName"),
            bundleIdentifier: bundleIdentifier,
            version: try required("CFBundleShortVersionString"),
            build: try required("CFBundleVersion"),
            minimumOSVersion: info["MinimumOSVersion"]?.stringValue,
            executableName: try required("CFBundleExecutable"),
            supportedPlatforms: info["CFBundleSupportedPlatforms"]?.arrayValue?.compactMap(\.stringValue) ?? [],
            provisioning: provisioning,
            signature: signature
        )
    }
}
