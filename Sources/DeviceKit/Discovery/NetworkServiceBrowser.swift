import Foundation
import Network
import ToolkitCore

/// A Bonjour service advertised by an Apple device on the local network.
public struct AdvertisedDeviceService: Sendable, Hashable, Identifiable {
    public var id: String { "\(type)|\(name)|\(domain)" }
    public var name: String
    public var type: String
    public var domain: String
    public var interface: String?

    public var meaning: String {
        switch type {
        case "_apple-mobdev2._tcp": return "Wi-Fi sync / network pairing (usbmuxd over the network)"
        case "_remotepairing._tcp": return "Remote pairing for CoreDevice (iOS 17+ developer services)"
        case "_remoted._tcp": return "Remote Service Discovery (RemoteXPC)"
        default: return type
        }
    }
}

/// Browses for device services with Network.framework for a bounded period. Discovery shows
/// what is advertised; it does not prove pairing or service authorization.
public enum NetworkServiceBrowser {
    public static let defaultTypes = ["_apple-mobdev2._tcp", "_remotepairing._tcp", "_remoted._tcp"]

    public static func browse(types: [String] = defaultTypes, duration: TimeInterval = 5) async -> [AdvertisedDeviceService] {
        let found = LockedValue<Set<AdvertisedDeviceService>>([])
        let queue = DispatchQueue(label: "io.hideouts.iOSDeveloperToolkit.bonjour")
        let browsers = types.map { type -> NWBrowser in
            let parameters = NWParameters()
            parameters.includePeerToPeer = false
            let browser = NWBrowser(for: .bonjour(type: type, domain: "local."), using: parameters)
            browser.browseResultsChangedHandler = { results, _ in
                for result in results {
                    if case .service(let name, let serviceType, let domain, let interface) = result.endpoint {
                        found.withLock { $0.insert(AdvertisedDeviceService(name: name, type: serviceType, domain: domain, interface: interface?.name)) }
                    }
                }
            }
            browser.stateUpdateHandler = { state in
                if case .failed(let error) = state {
                    ToolkitLog.networking.error("Bonjour browse failed: \(error.localizedDescription, privacy: .public)")
                }
            }
            browser.start(queue: queue)
            return browser
        }
        try? await Task.sleep(nanoseconds: UInt64(max(0.5, duration) * 1_000_000_000))
        browsers.forEach { $0.cancel() }
        ToolkitLog.networking.info("Bonjour browse found \(found.current.count, privacy: .public) services")
        return found.current.sorted { ($0.type, $0.name) < ($1.type, $1.name) }
    }
}
