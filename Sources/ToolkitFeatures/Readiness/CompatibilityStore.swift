import DeviceKit
import Foundation
import ToolkitCore

/// One completed Readiness Check against a real device, stored locally with a one-way
/// fingerprint instead of the device's identifier or name.
public struct CompatibilityObservation: Codable, Sendable, Hashable, Identifiable {
    public var id: String { fingerprint + ISO8601.string(observedAt) }
    public var fingerprint: String
    public var observedAt: Date
    public var model: String
    public var productType: String?
    public var osVersion: String?
    public var buildVersion: String?
    public var connection: String
    public var toolkitVersion: String
    public var states: [String: CapabilityState]

    enum CodingKeys: String, CodingKey {
        case fingerprint, model, connection, states
        case observedAt = "observed_at"
        case productType = "product_type"
        case osVersion = "os_version"
        case buildVersion = "build_version"
        case toolkitVersion = "toolkit_version"
    }

    public init(device: Device, results: [CapabilityResult], observedAt: Date = Date()) {
        fingerprint = Sanitizer.fingerprint(device.udid)
        self.observedAt = observedAt
        model = device.marketingName ?? device.productType ?? device.family.rawValue
        productType = device.productType
        osVersion = device.osVersion
        buildVersion = device.buildVersion
        connection = device.primaryTransport?.label ?? "Unknown"
        toolkitVersion = ToolkitVersion.current
        states = Dictionary(results.map { ($0.id, $0.state) }, uniquingKeysWith: { $1 })
    }
}

/// Append-only local history at ~/Library/Application Support/iOS Developer Toolkit/Compatibility.
public struct CompatibilityStore: Sendable {
    public let url: URL

    public init(url: URL = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/iOS Developer Toolkit/Compatibility/observations-v2.jsonl")) {
        self.url = url
    }

    public func append(_ observation: CompatibilityObservation) throws {
        try SecureFileIO.createPrivateDirectory(at: url.deletingLastPathComponent())
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        encoder.dateEncodingStrategy = .iso8601
        var line = try encoder.encode(observation)
        line.append(0x0A)
        try SecureFileIO.append(line, to: url, synchronize: true)
    }

    /// Loads observations, skipping malformed lines rather than failing the whole history.
    public func load() -> [CompatibilityObservation] {
        guard let text = try? String(contentsOf: url, encoding: .utf8) else { return [] }
        let decoder = JSONOutput.decoder()
        return text.split(separator: "\n").compactMap { try? decoder.decode(CompatibilityObservation.self, from: Data($0.utf8)) }
    }

    /// The newest observation for each device.
    public static func latest(_ observations: [CompatibilityObservation]) -> [CompatibilityObservation] {
        var newest: [String: CompatibilityObservation] = [:]
        for observation in observations where (newest[observation.fingerprint]?.observedAt ?? .distantPast) <= observation.observedAt {
            newest[observation.fingerprint] = observation
        }
        return newest.values.sorted { ($0.model, $0.osVersion ?? "") < ($1.model, $1.osVersion ?? "") }
    }

    /// A sanitized, shareable report. Fingerprints, names, and identifiers are omitted.
    public static func renderJSON(_ observations: [CompatibilityObservation]) throws -> Data {
        let rows: [[String: Any]] = latest(observations).map { observation in
            [
                "model": observation.model,
                "product_type": observation.productType ?? NSNull(),
                "os_version": observation.osVersion ?? NSNull(),
                "build_version": observation.buildVersion ?? NSNull(),
                "connection": observation.connection,
                "observed_at": ISO8601.string(observation.observedAt),
                "toolkit_version": observation.toolkitVersion,
                "states": observation.states.mapValues(\.rawValue),
            ]
        }
        let document: [String: Any] = [
            "schema_version": 2,
            "generated_at": ISO8601.string(Date()),
            "toolkit_version": ToolkitVersion.current,
            "notice": "Observed readiness on locally tested devices. Not a prediction for untested hardware or builds. Device names, identifiers, and fingerprints are omitted.",
            "devices": rows,
        ]
        return try JSONSerialization.data(withJSONObject: document, options: [.prettyPrinted, .sortedKeys])
    }

    public static func renderMarkdown(_ observations: [CompatibilityObservation]) -> String {
        let rows = CapabilityRow.rows(for: .physical)
        var lines = [
            "# Real-device readiness report",
            "",
            "Generated \(ISO8601.string(Date())) by iOS Developer Toolkit \(ToolkitVersion.current). Observed results only; untested devices and builds are not predicted. Names, identifiers, and fingerprints are omitted.",
            "",
            "| Model | iOS | Build | Connection | " + rows.map(\.title).joined(separator: " | ") + " |",
            "|" + String(repeating: "---|", count: 4 + rows.count),
        ]
        for observation in latest(observations) {
            let cells = rows.map { observation.states[$0.rawValue]?.label ?? "—" }
            lines.append("| \(observation.model) | \(observation.osVersion ?? "—") | \(observation.buildVersion ?? "—") | \(observation.connection) | " + cells.joined(separator: " | ") + " |")
        }
        return lines.joined(separator: "\n") + "\n"
    }
}
