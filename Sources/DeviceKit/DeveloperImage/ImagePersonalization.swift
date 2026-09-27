import Foundation
import OSLog
import ToolkitCore

/// Sends a personalization (TSS) request and returns the raw response body.
public protocol PersonalizationTransport: Sendable {
    func send(_ body: Data) async throws -> Data
}

/// Apple's signing server. Xcode contacts the same server to personalize developer images; the
/// request carries the device's chip, board, ECID, and a one-time nonce.
public struct AppleTSSTransport: PersonalizationTransport {
    public static let url = URL(string: "https://gs.apple.com/TSS/controller?action=2")!

    public init() {}

    public func send(_ body: Data) async throws -> Data {
        var request = URLRequest(url: Self.url, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 60)
        request.httpMethod = "POST"
        request.httpBody = body
        request.setValue("text/xml; charset=\"utf-8\"", forHTTPHeaderField: "Content-Type")
        request.setValue("no-cache", forHTTPHeaderField: "Cache-Control")
        request.setValue("InetURL/1.0", forHTTPHeaderField: "User-Agent")
        let session = URLSession(configuration: .ephemeral)
        defer { session.finishTasksAndInvalidate() }
        do {
            let (data, response) = try await session.data(for: request)
            if let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
                throw ToolkitError(.serviceUnavailable, message: "Apple's signing server returned an error (HTTP \(http.statusCode)).", recovery: "Try again in a few minutes.", technicalDetail: String(decoding: data.prefix(500), as: UTF8.self))
            }
            return data
        } catch let error as ToolkitError {
            throw error
        } catch {
            throw ToolkitError(.serviceUnavailable, message: "This Mac could not reach Apple's signing server.", recovery: "Personalizing a developer image needs an internet connection to gs.apple.com. Check the network (and any proxy or firewall), then try again.", technicalDetail: error.localizedDescription)
        }
    }
}

/// The identifiers the image mounter reports for personalization.
public struct PersonalizationIdentifiers: Sendable, Hashable {
    public var chipID: Int
    public var boardID: Int
    public var ecid: UInt64
    /// `Ap,*` values the device asks to be included in the request.
    public var additional: [String: PlistValue]

    public init(chipID: Int, boardID: Int, ecid: UInt64, additional: [String: PlistValue] = [:]) {
        self.chipID = chipID
        self.boardID = boardID
        self.ecid = ecid
        self.additional = additional
    }

    public init(_ plist: PlistValue) throws {
        guard let chip = plist["ChipID"]?.intValue, let board = plist["BoardId"]?.intValue ?? plist["BoardID"]?.intValue,
              let ecid = plist["UniqueChipID"]?.uint64Value ?? plist["ECID"]?.uint64Value else {
            throw ToolkitError(.protocolViolation, message: "The device did not report the identifiers needed to personalize the developer image.", recovery: "Unlock the device, reconnect it, and try again.", technicalDetail: "Keys: \((plist.dictionaryValue ?? [:]).keys.sorted().joined(separator: ", "))")
        }
        chipID = chip
        boardID = board
        self.ecid = ecid
        additional = (plist.dictionaryValue ?? [:]).filter { $0.key.hasPrefix("Ap,") }
    }
}

/// Builds and interprets the personalization request for a developer image.
///
/// This reproduces the request `pymobiledevice3` (and Xcode) send for the “Customer iOS Developer
/// PDI” build identities: device identifiers and nonce, production-mode flags, and every trusted
/// component of the identity's manifest with its restore-request rules applied. Apple's reply
/// carries the `ApImg4Ticket` the device needs to mount the image.
public enum ImagePersonalization {
    static let logger = ToolkitLog.logger(.networking)
    static let clientVersion = "libauthinstall-1033.0.2"

    /// Parameters the restore-request rules are evaluated against (a production device).
    static let ruleParameters: [String: Bool] = [
        "ApProductionMode": true,
        "ApSecurityMode": true,
        "ApSupportsImg4": true,
    ]

    public static func request(identity: DeveloperImageBuildIdentity, identifiers: PersonalizationIdentifiers, nonce: Data, requestID: UUID = UUID()) -> PlistValue {
        var request: [String: PlistValue] = [
            "@HostPlatformInfo": "mac",
            "@VersionInfo": .string(clientVersion),
            "@UUID": .string(requestID.uuidString.uppercased()),
            "@ApImg4Ticket": true,
            "@BBTicket": true,
            "ApBoardID": .integer(Int64(identifiers.boardID)),
            "ApChipID": .integer(Int64(identifiers.chipID)),
            "ApECID": .integer(Int64(bitPattern: identifiers.ecid)),
            "ApNonce": .data(nonce),
            "ApProductionMode": true,
            "ApSecurityDomain": 1,
            "ApSecurityMode": true,
            "SepNonce": .data(Data(count: 20)),
            "UID_MODE": false,
        ]
        for (key, value) in identifiers.additional { request[key] = value }
        let fallbackRules = identity.manifest["LoadableTrustCache"]?["Info"]?["RestoreRequestRules"]?.arrayValue ?? []
        for (key, item) in identity.manifest {
            guard let entry = item.dictionaryValue, let info = entry["Info"], entry["Trusted"]?.boolValue == true else { continue }
            var tssEntry = entry
            tssEntry.removeValue(forKey: "Info")
            let rules = info["RestoreRequestRules"]?.arrayValue ?? fallbackRules
            tssEntry = applyRules(rules, to: tssEntry)
            if tssEntry["Digest"] == nil { tssEntry["Digest"] = .data(Data()) }
            request[key] = .dictionary(tssEntry)
        }
        return .dictionary(request)
    }

    /// Applies `RestoreRequestRules`: when every condition matches the production parameters, the
    /// rule's actions are written into the entry (255 means “leave unchanged”).
    static func applyRules(_ rules: [PlistValue], to entry: [String: PlistValue]) -> [String: PlistValue] {
        var entry = entry
        for rule in rules {
            let conditions = rule["Conditions"]?.dictionaryValue ?? [:]
            let fulfilled = !conditions.isEmpty && conditions.allSatisfy { key, value in
                let parameter: Bool?
                switch key {
                case "ApRawProductionMode", "ApCurrentProductionMode": parameter = ruleParameters["ApProductionMode"]
                case "ApRawSecurityMode": parameter = ruleParameters["ApSecurityMode"]
                case "ApRequiresImage4": parameter = ruleParameters["ApSupportsImg4"]
                default: parameter = nil
                }
                guard let parameter, parameter else { return false }
                return value.boolValue == parameter
            }
            guard fulfilled else { continue }
            for (key, value) in rule["Actions"]?.dictionaryValue ?? [:] where value.intValue != 255 {
                entry[key] = value
            }
        }
        return entry
    }

    /// Extracts the ticket from Apple's reply (`STATUS=0&MESSAGE=SUCCESS&REQUEST_STRING=<plist>`).
    public static func ticket(fromResponse body: Data) throws -> Data {
        let text = String(decoding: body, as: UTF8.self)
        let status = field("STATUS", in: text).flatMap { Int($0) }
        let message = field("MESSAGE", in: text) ?? ""
        guard status == 0, message.uppercased() == "SUCCESS", let range = text.range(of: "REQUEST_STRING=") else {
            throw rejection(status: status, message: message, body: text)
        }
        let plist = try PlistValue.decode(Data(text[range.upperBound...].utf8))
        guard let ticket = plist["ApImg4Ticket"]?.dataValue, !ticket.isEmpty else {
            throw ToolkitError(.protocolViolation, message: "Apple's signing server did not return a ticket for the developer image.", technicalDetail: String(text.prefix(500)))
        }
        return ticket
    }

    static func field(_ name: String, in text: String) -> String? {
        for pair in text.split(separator: "&", maxSplits: 3) {
            let parts = pair.split(separator: "=", maxSplits: 1)
            if parts.count == 2, parts[0] == Substring(name) { return String(parts[1]) }
        }
        return nil
    }

    static func rejection(status: Int?, message: String, body: String) -> ToolkitError {
        let technical = "TSS STATUS=\(status.map(String.init) ?? "?") MESSAGE=\(message)"
        switch status {
        case 94:
            return ToolkitError(.developerDiskImageUnavailable, message: "Apple would not sign this developer image for the device.", recovery: "The image is probably older or newer than this iOS version supports. Update Xcode (it installs matching images) or update iOS, then try again.", technicalDetail: technical)
        case 69, 128:
            return ToolkitError(.developerDiskImageUnavailable, message: "Apple's signing server rejected the request.", recovery: "Reconnect the device (this refreshes its one-time nonce) and try again.", technicalDetail: technical)
        default:
            return ToolkitError(.serviceUnavailable, message: "Apple's signing server did not personalize the developer image.", recovery: "Try again in a few minutes. If it keeps failing, use the Xcode device service instead.", technicalDetail: technical + " " + String(body.prefix(300)))
        }
    }

    /// Requests a personalization ticket for `identity` from Apple.
    public static func personalize(identity: DeveloperImageBuildIdentity, identifiers: PersonalizationIdentifiers, nonce: Data, transport: PersonalizationTransport) async throws -> Data {
        let body = try request(identity: identity, identifiers: identifiers, nonce: nonce).encoded(format: .xml)
        logger.info("Requesting developer image personalization from Apple")
        let response = try await transport.send(body)
        let ticket = try ticket(fromResponse: response)
        logger.info("Developer image personalization received (\(ticket.count, privacy: .public) bytes)")
        return ticket
    }
}
