import DeviceKit
import Foundation
import ToolkitCore

/// A scripted `webinspectord`: answers the identifier handshake, reports one Safari application,
/// and sends a two-page listing when asked. It can refuse the first sessions by dropping them, as
/// the device does when Web Inspector is off or a session starts too soon after the previous one.
public final class FakeWebInspector: @unchecked Sendable {
    private let lock = NSLock()
    private var refusalsLeft: Int
    private var _sessions = 0
    private var _connectionIdentifiers: Set<String> = []
    private var _selectors: [String] = []

    public init(refusals: Int = 0) {
        refusalsLeft = refusals
    }

    public var sessions: Int { lock.withLock { _sessions } }
    public var connectionIdentifiers: Set<String> { lock.withLock { _connectionIdentifiers } }
    public var selectors: [String] { lock.withLock { _selectors } }

    public func register(on server: FakeDeviceServer) {
        server.register(service: WebInspector.serviceName) { [self] channel in
            let refuse: Bool = lock.withLock {
                _sessions += 1
                if refusalsLeft > 0 { refusalsLeft -= 1; return true }
                return false
            }
            if refuse { return }
            let messages = PlistMessageConnection(channel: channel)
            while let request = try? await messages.receive(timeout: 5) {
                let selector = request["__selector"]?.stringValue ?? ""
                lock.withLock {
                    _selectors.append(selector)
                    if let id = request["__argument"]?["WIRConnectionIdentifierKey"]?.stringValue { _connectionIdentifiers.insert(id) }
                }
                switch selector {
                case "_rpc_reportIdentifier:":
                    try await messages.send(["__selector": "_rpc_reportCurrentState:", "__argument": ["WIRAutomationAvailabilityKey": "WIRAutomationAvailabilityNotAvailable"]])
                case "_rpc_getConnectedApplications:":
                    try await messages.send(["__selector": "_rpc_reportConnectedApplicationList:", "__argument": ["WIRApplicationDictionaryKey": [
                        "PID:120": ["WIRApplicationIdentifierKey": "PID:120", "WIRApplicationBundleIdentifierKey": "com.apple.mobilesafari", "WIRApplicationNameKey": "Safari", "WIRIsApplicationActiveKey": 1],
                    ]]])
                case "_rpc_forwardGetListing:":
                    try await messages.send(["__selector": "_rpc_applicationSentListing:", "__argument": [
                        "WIRApplicationIdentifierKey": "PID:120",
                        "WIRListingKey": [
                            "1": ["WIRPageIdentifierKey": 1, "WIRTitleKey": "Example Domain", "WIRURLKey": "https://example.com/", "WIRTypeKey": "WIRTypeWebPage"],
                            "2": ["WIRPageIdentifierKey": 2, "WIRTypeKey": "WIRTypeJavaScript"],
                        ],
                    ]])
                default:
                    break
                }
            }
        }
    }
}
