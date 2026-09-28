import Foundation
import Security
import ToolkitCore

/// A decoded provisioning profile (`embedded.mobileprovision` or a profile listed on a device).
public struct ProvisioningProfileSummary: Sendable, Hashable, Codable {
    public enum Status: String, Sendable, Codable {
        case absent, decoded, invalid
    }

    public var status: Status
    public var name: String?
    public var uuid: String?
    public var teamIdentifiers: [String]
    public var teamName: String?
    public var applicationIdentifier: String?
    public var creationDate: Date?
    public var expirationDate: Date?
    public var provisionedDevices: [String]
    public var provisionsAllDevices: Bool
    public var getTaskAllow: Bool?
    /// How many developer certificates the profile allows to sign (`DeveloperCertificates`). Only a
    /// count; the certificates themselves are not kept.
    public var allowedSignerCount: Int
    public var signatureVerified: Bool?
    public var detail: String

    enum CodingKeys: String, CodingKey {
        case status, name, uuid, teamIdentifiers, teamName, applicationIdentifier, creationDate, expirationDate
        case provisionedDevices, provisionsAllDevices, getTaskAllow, signatureVerified, detail
        /// The JSON key is unchanged so `idt inspect-ipa --json` output stays compatible.
        case allowedSignerCount = "developerCertificateCount"
    }

    public static let absent = ProvisioningProfileSummary(status: .absent, name: nil, uuid: nil, teamIdentifiers: [], teamName: nil, applicationIdentifier: nil, creationDate: nil, expirationDate: nil, provisionedDevices: [], provisionsAllDevices: false, getTaskAllow: nil, allowedSignerCount: 0, signatureVerified: nil, detail: "The package has no embedded provisioning profile.")

    public var isExpired: Bool {
        guard let expirationDate else { return false }
        return expirationDate < Date()
    }

    public var profileKind: String {
        if provisionsAllDevices { return "Enterprise (all devices)" }
        if !provisionedDevices.isEmpty { return getTaskAllow == true ? "Development" : "Ad Hoc" }
        return status == .decoded ? "App Store / distribution" : "—"
    }

    /// Whether a device UDID is listed (ignoring hyphen differences).
    public func includes(udid: String) -> Bool {
        if provisionsAllDevices { return true }
        let normalized = udid.replacingOccurrences(of: "-", with: "").uppercased()
        return provisionedDevices.contains { $0.replacingOccurrences(of: "-", with: "").uppercased() == normalized }
    }
}

public enum ProvisioningProfileDecoder {
    public static let maximumProfileBytes = 20 * 1024 * 1024

    /// Decodes a CMS-signed profile without shelling out to `security cms`.
    public static func decode(_ data: Data) -> ProvisioningProfileSummary {
        guard !data.isEmpty, data.count <= maximumProfileBytes else {
            return invalid("The profile is empty or larger than 20 MB.")
        }
        var decoderReference: CMSDecoder?
        guard CMSDecoderCreate(&decoderReference) == errSecSuccess, let decoder = decoderReference else {
            return invalid("The profile decoder could not start.")
        }
        let updateStatus = data.withUnsafeBytes { raw -> OSStatus in
            guard let base = raw.baseAddress else { return errSecParam }
            return CMSDecoderUpdateMessage(decoder, base, data.count)
        }
        guard updateStatus == errSecSuccess, CMSDecoderFinalizeMessage(decoder) == errSecSuccess else {
            return invalid("The profile is not a valid signed (CMS) document.")
        }
        var contentReference: CFData?
        guard CMSDecoderCopyContent(decoder, &contentReference) == errSecSuccess, let content = contentReference as Data? else {
            return invalid("The profile has no content.")
        }
        var signerCount = 0
        CMSDecoderGetNumSigners(decoder, &signerCount)
        var verified: Bool?
        if signerCount > 0 {
            var signerStatus = CMSSignerStatus.unsigned
            var certificateStatus: OSStatus = 0
            let policy = SecPolicyCreateBasicX509()
            if CMSDecoderCopySignerStatus(decoder, 0, policy, true, &signerStatus, nil, &certificateStatus) == errSecSuccess {
                verified = signerStatus == .valid
            }
        }
        return summarize(plist: content, signatureVerified: verified)
    }

    static func summarize(plist data: Data, signatureVerified: Bool?) -> ProvisioningProfileSummary {
        guard let plist = try? PlistValue.decode(data), plist.dictionaryValue != nil else {
            return invalid("The profile content is not a property list.")
        }
        let entitlements = plist["Entitlements"] ?? .dictionary([:])
        return ProvisioningProfileSummary(
            status: .decoded,
            name: plist["Name"]?.stringValue,
            uuid: plist["UUID"]?.stringValue,
            teamIdentifiers: plist["TeamIdentifier"]?.arrayValue?.compactMap(\.stringValue) ?? [],
            teamName: plist["TeamName"]?.stringValue,
            applicationIdentifier: entitlements["application-identifier"]?.stringValue ?? entitlements["com.apple.application-identifier"]?.stringValue,
            creationDate: plist["CreationDate"]?.dateValue,
            expirationDate: plist["ExpirationDate"]?.dateValue,
            provisionedDevices: plist["ProvisionedDevices"]?.arrayValue?.compactMap(\.stringValue) ?? [],
            provisionsAllDevices: plist["ProvisionsAllDevices"]?.boolValue ?? false,
            getTaskAllow: entitlements["get-task-allow"]?.boolValue,
            allowedSignerCount: plist["DeveloperCertificates"]?.arrayValue?.count ?? 0,
            signatureVerified: signatureVerified,
            detail: "Decoded with Security.framework (CMS)."
        )
    }

    static func invalid(_ detail: String) -> ProvisioningProfileSummary {
        var summary = ProvisioningProfileSummary.absent
        summary.status = .invalid
        summary.detail = detail
        return summary
    }
}

/// The result of verifying an app bundle's code signature.
public struct CodeSignatureSummary: Sendable, Hashable, Codable {
    public enum Status: String, Sendable, Codable {
        case valid, invalid, missing
    }

    public var status: Status
    public var identifier: String?
    public var teamIdentifier: String?
    public var authorities: [String]
    public var detail: String
}

public enum CodeSignatureInspector {
    /// Verifies with `SecStaticCode` (the API `codesign --verify --deep --strict` uses).
    public static func inspect(bundle url: URL) -> CodeSignatureSummary {
        var codeReference: SecStaticCode?
        guard SecStaticCodeCreateWithPath(url as CFURL, [], &codeReference) == errSecSuccess, let code = codeReference else {
            return CodeSignatureSummary(status: .missing, identifier: nil, teamIdentifier: nil, authorities: [], detail: "The app bundle could not be opened for signature checking.")
        }
        var errors: Unmanaged<CFError>?
        let flags = SecCSFlags(rawValue: kSecCSCheckAllArchitectures | kSecCSStrictValidate | kSecCSCheckNestedCode)
        let status = SecStaticCodeCheckValidityWithErrors(code, flags, nil, &errors)

        var informationReference: CFDictionary?
        SecCodeCopySigningInformation(code, SecCSFlags(rawValue: kSecCSSigningInformation), &informationReference)
        let information = (informationReference as? [String: Any]) ?? [:]
        let identifier = information[kSecCodeInfoIdentifier as String] as? String
        let team = information[kSecCodeInfoTeamIdentifier as String] as? String
        let certificates = (information[kSecCodeInfoCertificates as String] as? [SecCertificate]) ?? []
        let authorities = certificates.compactMap { SecCertificateCopySubjectSummary($0) as String? }

        switch status {
        case errSecSuccess:
            return CodeSignatureSummary(status: .valid, identifier: identifier, teamIdentifier: team, authorities: authorities, detail: "The signature is intact and every nested component is signed.")
        case errSecCSUnsigned:
            return CodeSignatureSummary(status: .missing, identifier: nil, teamIdentifier: nil, authorities: [], detail: "The app is not signed.")
        default:
            let message = errors.map { CFErrorCopyDescription($0.takeRetainedValue()) as String } ?? "OSStatus \(status)"
            return CodeSignatureSummary(status: .invalid, identifier: identifier, teamIdentifier: team, authorities: authorities, detail: message)
        }
    }
}
