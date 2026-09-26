import DeviceKit
import Foundation
import ToolkitCore

/// Local case metadata recorded before a collection. It documents the operator's stated
/// purpose and authorization; it is not a chain-of-custody record.
public struct CaseIntake: Codable, Sendable, Hashable {
    public var title: String
    public var purpose: String
    public var targetUDID: String
    public var targetName: String
    public var authorizationAcknowledgedAt: Date
    public var createdAt: Date

    enum CodingKeys: String, CodingKey {
        case title, purpose
        case targetUDID = "target_udid"
        case targetName = "target_name"
        case authorizationAcknowledgedAt = "authorization_acknowledged_at"
        case createdAt = "created_at"
    }
}

public enum CaseWorkflow {
    public static let intakeFileName = "case-intake.json"
    public static let manifestFileName = "manifest.json"
    public static let subfolders = ["snapshots", "streams", "artifacts"]

    public static func folderName(for target: DeviceTarget, at date: Date) -> String {
        let fragment = String(target.udid.filter { $0.isLetter || $0.isNumber }.suffix(12))
        return "ios-case-\(ISO8601.compactUTC(date))-\(fragment.isEmpty ? "DEVICE" : fragment)"
    }

    /// Creates a new case folder (owner-only permissions) with its standard subfolders.
    public static func createCaseFolder(in root: URL, target: DeviceTarget, at date: Date = Date()) throws -> URL {
        try SecureFileIO.createPrivateDirectory(at: root)
        let folder = root.appendingPathComponent(folderName(for: target, at: date))
        do {
            try SecureFileIO.createNewPrivateDirectory(at: folder)
        } catch {
            throw ToolkitError.fileSystem("A case for this device already exists for this second. Try again.", path: folder.path)
        }
        for name in subfolders {
            try SecureFileIO.createNewPrivateDirectory(at: folder.appendingPathComponent(name))
        }
        return folder
    }

    public static func createGuidedCase(in root: URL, target: DeviceTarget, title: String, purpose: String, authorized: Bool) throws -> (URL, CaseIntake) {
        guard authorized else {
            throw ToolkitError.invalidInput("Confirm that you own the device or are authorized to examine it before creating a case.")
        }
        let normalizedTitle = title.split(whereSeparator: \.isWhitespace).joined(separator: " ")
        guard !normalizedTitle.isEmpty else { throw ToolkitError.invalidInput("Enter a case title.") }
        guard normalizedTitle.count <= 120 else { throw ToolkitError.invalidInput("The case title must be 120 characters or fewer.") }
        let normalizedPurpose = purpose.trimmingCharacters(in: .whitespacesAndNewlines)
        guard normalizedPurpose.count <= 2_000 else { throw ToolkitError.invalidInput("The purpose must be 2,000 characters or fewer.") }
        let now = Date()
        let intake = CaseIntake(title: normalizedTitle, purpose: normalizedPurpose, targetUDID: target.udid, targetName: target.name, authorizationAcknowledgedAt: now, createdAt: now)
        let folder = try createCaseFolder(in: root, target: target, at: now)
        let document: [String: Any] = [
            "schema_version": 2,
            "application": "iOS Developer Toolkit",
            "case": try JSONSerialization.jsonObject(with: JSONOutput.encode(intake)),
            "limitations": [
                "The intake records the operator acknowledgement; it does not establish chain of custody.",
                "Hashes are written after collection and detect later changes to the finalized case files.",
            ],
        ]
        try SecureFileIO.writeNewFile(try JSONSerialization.data(withJSONObject: document, options: [.prettyPrinted, .sortedKeys]), to: folder.appendingPathComponent(intakeFileName))
        return (folder, intake)
    }

    /// Checks that a guided case belongs to `target`, is intact, and has not been finalized.
    public static func validateForCollection(_ folder: URL, target: DeviceTarget) throws {
        guard FileManager.default.fileExists(atPath: folder.path) else { throw ToolkitError.fileSystem("The case folder does not exist.", path: folder.path) }
        guard !FileManager.default.fileExists(atPath: folder.appendingPathComponent(manifestFileName).path) else {
            throw ToolkitError.invalidInput("This case has already been collected. Create a new case for another collection.")
        }
        guard let data = try? Data(contentsOf: folder.appendingPathComponent(intakeFileName)), let document = try? JSONValue.parse(data) else {
            throw ToolkitError.invalidInput("The case intake file is missing or unreadable.")
        }
        guard document["case"]?["target_udid"]?.string == target.udid else {
            throw ToolkitError.invalidInput("This case was created for a different device.")
        }
        for name in subfolders where !FileManager.default.fileExists(atPath: folder.appendingPathComponent(name).path) {
            throw ToolkitError.invalidInput("The case folder is missing its \(name) folder.")
        }
    }
}
