import Foundation
import OSLog
import ToolkitCore

/// An inspectable page (Safari tab, web view, JavaScript context) reported by Web Inspector.
public struct WebInspectorPage: Sendable, Hashable, Codable, Identifiable {
    public var id: String
    public var title: String?
    public var url: String?
    /// `WIRTypeWeb`, `WIRTypeWebPage`, `WIRTypeJavaScript`, `WIRTypeServiceWorker`, …
    public var type: String

    public var kindLabel: String {
        switch type {
        case "WIRTypeWeb", "WIRTypeWebPage", "WIRTypePage": return "Web page"
        case "WIRTypeJavaScript": return "JavaScript context"
        case "WIRTypeServiceWorker": return "Service worker"
        case "WIRTypeAutomation": return "Automation session"
        case "WIRTypeITML": return "TVML page"
        default: return type
        }
    }
}

/// An application that exposes inspectable content, with its pages.
public struct WebInspectorApplication: Sendable, Hashable, Codable, Identifiable {
    public var id: String
    public var bundleIdentifier: String?
    public var name: String?
    public var isActive: Bool?
    public var pages: [WebInspectorPage]
}

/// `com.apple.webinspector`: lists the Safari tabs and web views that apps allow to be inspected,
/// over lockdown and without Xcode.
///
/// This is Apple's private WebKit remote-inspector RPC (the one Safari's Develop menu uses). Messages
/// are plists `{__selector, __argument}`; the device answers `_rpc_reportConnectedApplicationList:`
/// and `_rpc_applicationSentListing:`. The device refuses a session by dropping the connection when
/// Web Inspector is off in Safari settings, when a new session starts less than about ten seconds
/// after the previous one, or while it is still starting up — so a refusal is retried until a
/// deadline before it is reported. Read-only: nothing is inspected, launched, or automated.
public enum WebInspector {
    public static let serviceName = "com.apple.webinspector"
    static let logger = ToolkitLog.logger(.deviceCommunication)

    public static func openPages(
        on target: DeviceTarget,
        usbmux: USBMuxClient = USBMuxClient(),
        handshakeDeadline: Duration = .seconds(15),
        retryInterval: Duration = .milliseconds(1500),
        listingWindow: Duration = .seconds(2)
    ) async throws -> [WebInspectorApplication] {
        let clock = ContinuousClock()
        let deadline = clock.now + handshakeDeadline
        var lastError: Error?
        while true {
            do {
                return try await DeviceSession.with(target, usbmux: usbmux) { session in
                    try await listPages(session, listingWindow: listingWindow)
                }
            } catch let error as HandshakeRefused {
                lastError = error.underlying
            }
            if clock.now + retryInterval >= deadline { break }
            try await Task.sleep(for: retryInterval)
        }
        throw ToolkitError(
            .serviceUnavailable,
            message: "Safari Web Inspector did not answer.",
            recovery: "Turn on Web Inspector on the device: Settings › Apps › Safari › Advanced › Web Inspector (Settings › Safari › Advanced before iOS 18). If it is already on, wait ten seconds and try again — the device accepts a new inspection session only about every ten seconds.",
            technicalDetail: lastError.map { String(describing: $0) }
        )
    }

    /// A refusal during the handshake (retryable), as opposed to a failure after it.
    struct HandshakeRefused: Error {
        var underlying: Error
    }

    static func listPages(_ session: DeviceSession, listingWindow: Duration) async throws -> [WebInspectorApplication] {
        let connectionID = UUID().uuidString.uppercased()
        let service: ServiceConnection
        do {
            service = try await session.openService(serviceName)
        } catch {
            throw HandshakeRefused(underlying: error)
        }
        defer { Task { await service.close() } }
        let messages = service.messages

        func send(_ selector: String, _ argument: [String: PlistValue] = [:]) async throws {
            var argument = argument
            argument["WIRConnectionIdentifierKey"] = .string(connectionID)
            try await messages.send(["__selector": .string(selector), "__argument": .dictionary(argument)])
        }

        var state = ListingState()
        do {
            try await send("_rpc_reportIdentifier:")
            state.apply(try await messages.receive(timeout: 5))
        } catch {
            throw HandshakeRefused(underlying: error)
        }
        try await send("_rpc_getConnectedApplications:")
        let clock = ContinuousClock()
        let end = clock.now + listingWindow
        var requested: Set<String> = []
        while clock.now < end {
            for appID in state.applications.keys where !requested.contains(appID) {
                try await send("_rpc_forwardGetListing:", ["WIRApplicationIdentifierKey": .string(appID)])
                requested.insert(appID)
            }
            let remaining = end - clock.now
            let seconds = Double(remaining.components.seconds) + Double(remaining.components.attoseconds) / 1e18
            guard seconds > 0.05 else { break }
            do {
                state.apply(try await messages.receive(timeout: seconds))
            } catch let error as ToolkitError where error.kind == .timedOut {
                break
            }
        }
        logger.info("Web Inspector listed \(state.applications.count, privacy: .public) applications")
        return state.result
    }

    /// Accumulates the device's reports.
    struct ListingState {
        var applications: [String: WebInspectorApplication] = [:]

        mutating func apply(_ message: PlistValue) {
            let argument = message["__argument"] ?? .dictionary([:])
            switch message["__selector"]?.stringValue {
            case "_rpc_reportConnectedApplicationList:":
                var updated: [String: WebInspectorApplication] = [:]
                for (id, app) in argument["WIRApplicationDictionaryKey"]?.dictionaryValue ?? [:] {
                    updated[id] = Self.application(id: id, app, pages: applications[id]?.pages ?? [])
                }
                applications = updated
            case "_rpc_applicationConnected:", "_rpc_applicationUpdated:":
                if let id = argument["WIRApplicationIdentifierKey"]?.stringValue {
                    applications[id] = Self.application(id: id, argument, pages: applications[id]?.pages ?? [])
                }
            case "_rpc_applicationDisconnected:":
                if let id = argument["WIRApplicationIdentifierKey"]?.stringValue { applications[id] = nil }
            case "_rpc_applicationSentListing:":
                guard let id = argument["WIRApplicationIdentifierKey"]?.stringValue else { return }
                // A listing is the application's complete set of pages, not a delta.
                let pages = (argument["WIRListingKey"]?.dictionaryValue ?? [:]).map { key, page in
                    WebInspectorPage(
                        id: page["WIRPageIdentifierKey"].map { $0.intValue.map(String.init) ?? $0.stringValue ?? key } ?? key,
                        title: page["WIRTitleKey"]?.stringValue,
                        url: page["WIRURLKey"]?.stringValue,
                        type: page["WIRTypeKey"]?.stringValue ?? "unknown"
                    )
                }.sorted { ($0.title ?? "", $0.id) < ($1.title ?? "", $1.id) }
                var app = applications[id] ?? WebInspectorApplication(id: id, bundleIdentifier: nil, name: nil, isActive: nil, pages: [])
                app.pages = pages
                applications[id] = app
            default:
                break
            }
        }

        static func application(id: String, _ plist: PlistValue, pages: [WebInspectorPage]) -> WebInspectorApplication {
            WebInspectorApplication(
                id: id,
                bundleIdentifier: plist["WIRApplicationBundleIdentifierKey"]?.stringValue,
                name: plist["WIRApplicationNameKey"]?.stringValue,
                isActive: plist["WIRIsApplicationActiveKey"]?.boolValue ?? plist["WIRIsApplicationActiveKey"]?.intValue.map { $0 != 0 },
                pages: pages
            )
        }

        /// Applications with inspectable pages first, by name.
        var result: [WebInspectorApplication] {
            applications.values.sorted {
                ($0.pages.isEmpty ? 1 : 0, $0.name ?? $0.bundleIdentifier ?? $0.id) < ($1.pages.isEmpty ? 1 : 0, $1.name ?? $1.bundleIdentifier ?? $1.id)
            }
        }
    }
}
