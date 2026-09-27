import DeviceKit
import Foundation
import Observation
import ToolkitCore
import ToolkitFeatures

@Observable
@MainActor
final class LocationModel {
    var latitudeText = "37.3349"
    var longitudeText = "-122.0090"
    var linkText = ""
    var nudgeMetres: Double = 100
    var savedLocations: [SavedLocation] = []
    var newPlaceName = ""

    var waypointsText = "37.3349,-122.0090\n37.3318,-122.0312"
    var travelPreset: TravelPreset = .walk
    var customSpeedKmh: Double = 25
    var routeIntervalSeconds = 2
    var routeTraversals = 1
    var generatedRoute: GeneratedRoute?

    var gpxInspection: GPXInspection?
    var ignoreRecordedTiming = false
    var fixedIntervalSeconds: Double = 1
    var jitterMilliseconds = 0

    var playbackProgress: (index: Int, total: Int)?
    var playbackTarget: DeviceTarget?
    /// The device whose location this app last changed, kept for Clear even if the selection moves.
    var lastSimulatedTarget: DeviceTarget?
    private var playbackTask: Task<Void, Never>?

    let evidenceDirectory = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Documents/iOS Developer Toolkit Location Logs")

    init() {
        // Screenshot mode shows sample places so personal saved places never appear in docs.
        savedLocations = ScreenshotHarness.isCapturing
            ? [SavedLocation(name: "Apple Park", coordinates: Coordinates(latitude: 37.3349, longitude: -122.0090)),
               SavedLocation(name: "London Eye", coordinates: Coordinates(latitude: 51.5033, longitude: -0.1196))]
            : (try? LocationLab.loadSavedLocations()) ?? []
    }

    var coordinates: Coordinates? {
        try? LocationLab.validate(latitude: latitudeText, longitude: longitudeText)
    }

    var speedKmh: Double { travelPreset.speedKmh ?? customSpeedKmh }

    func show(_ coordinates: Coordinates) {
        latitudeText = String(format: "%.6f", coordinates.latitude)
        longitudeText = String(format: "%.6f", coordinates.longitude)
    }

    func importLink(app: AppModel) {
        do {
            show(try LocationLab.parseLocationInput(linkText))
            linkText = ""
        } catch {
            app.present(error)
        }
    }

    func nudge(_ direction: CompassDirection, app: AppModel) {
        do {
            let origin = try LocationLab.validate(latitude: latitudeText, longitude: longitudeText)
            show(try Geodesy.move(origin, bearing: direction.bearing, distance: nudgeMetres))
        } catch {
            app.present(error)
        }
    }

    func savePlace(app: AppModel) {
        do {
            let coordinates = try LocationLab.validate(latitude: latitudeText, longitude: longitudeText)
            savedLocations = try LocationLab.adding(newPlaceName, coordinates, to: savedLocations)
            try LocationLab.storeSavedLocations(savedLocations)
            newPlaceName = ""
        } catch {
            app.present(error)
        }
    }

    func removePlace(_ place: SavedLocation, app: AppModel) {
        savedLocations.removeAll { $0 == place }
        do { try LocationLab.storeSavedLocations(savedLocations) } catch { app.present(error) }
    }

    private func log(_ event: String, status: String, target: DeviceTarget, mechanism: String, coordinates: Coordinates? = nil, gpx: GPXInspection? = nil, detail: String) {
        try? LocationLab.append(LocationEvidenceEvent(event: event, status: status, deviceIdentifier: target.udid, deviceName: target.name, deviceKind: target.kind.rawValue, osVersion: target.osVersion, mechanism: mechanism, latitude: coordinates?.latitude, longitude: coordinates?.longitude, gpxPath: gpx?.url.path, gpxSHA256: gpx?.sha256, detail: detail), to: evidenceDirectory)
    }

    // MARK: Device operations

    func setLocation(app: AppModel, target: DeviceTarget) async {
        let coordinates: Coordinates
        do { coordinates = try LocationLab.validate(latitude: latitudeText, longitude: longitudeText) } catch { app.present(error); return }
        let controller = app.executor.location
        let mechanism = controller.mechanism(for: target).rawValue
        let succeeded = await app.run("Set simulated location", workspace: .location, target: target, transport: mechanism) { _ in
            try await controller.set(latitude: coordinates.latitude, longitude: coordinates.longitude, on: target)
            return true
        }
        log("set", status: succeeded == true ? "succeeded" : "failed", target: target, mechanism: mechanism, coordinates: coordinates, detail: "")
        if succeeded == true {
            lastSimulatedTarget = target
            app.statusMessage = "\(target.name) now reports \(coordinates.formatted)."
        }
    }

    func clear(app: AppModel, target: DeviceTarget) async {
        stopPlayback()
        let controller = app.executor.location
        let mechanism = controller.mechanism(for: target).rawValue
        let succeeded = await app.run("Clear simulated location", workspace: .location, target: target, transport: mechanism) { _ in
            try await controller.clear(on: target)
            return true
        }
        log("clear", status: succeeded == true ? "succeeded" : "failed", target: target, mechanism: mechanism, detail: "")
        if succeeded == true {
            if lastSimulatedTarget == target { lastSimulatedTarget = nil }
            app.statusMessage = "\(target.name) uses its real location again."
        }
    }

    // MARK: Routes

    func buildRoute(app: AppModel) {
        do {
            generatedRoute = try LocationLab.buildRoute(waypoints: try LocationLab.parseRouteWaypoints(waypointsText), speedKmh: speedKmh, intervalSeconds: routeIntervalSeconds, traversalCount: routeTraversals, startTime: Date())
        } catch {
            generatedRoute = nil
            app.present(error)
        }
    }

    /// Starts constant-speed movement handled by the device service (iOS 17+ and simulators).
    func startNativeRoute(app: AppModel, target: DeviceTarget) async {
        let waypoints: [Coordinates]
        do { waypoints = try LocationLab.parseRouteWaypoints(waypointsText) } catch { app.present(error); return }
        let controller = app.executor.location
        let speed = speedKmh / 3.6
        let interval = Double(routeIntervalSeconds)
        let mechanism = controller.mechanism(for: target).rawValue
        let succeeded = await app.run("Start simulated route", workspace: .location, target: target, transport: mechanism) { _ in
            try await controller.startRoute(waypoints.map { ($0.latitude, $0.longitude) }, speedMetresPerSecond: speed, intervalSeconds: interval, on: target)
            return true
        }
        log("route", status: succeeded == true ? "succeeded" : "failed", target: target, mechanism: mechanism, coordinates: waypoints.first, detail: "\(waypoints.count) waypoints at \(Int(speedKmh)) km/h")
        if succeeded == true {
            lastSimulatedTarget = target
            app.statusMessage = "\(target.name) is moving along the route."
        }
    }

    // MARK: GPX

    func inspectGPX(_ url: URL, app: AppModel) {
        let accessed = url.startAccessingSecurityScopedResource()
        defer { if accessed { url.stopAccessingSecurityScopedResource() } }
        do {
            gpxInspection = try LocationLab.inspectGPX(at: url)
        } catch {
            gpxInspection = nil
            app.present(error)
        }
    }

    var isPlaying: Bool { playbackTask != nil }

    func startPlayback(app: AppModel, target: DeviceTarget) {
        guard let inspection = gpxInspection, playbackTask == nil else { return }
        let timing: PlaybackTiming = ignoreRecordedTiming ? .fixedInterval(seconds: fixedIntervalSeconds) : .recorded(jitterMilliseconds: jitterMilliseconds)
        let playback: GPXPlayback
        do {
            playback = try GPXPlayback(points: inspection.points, timing: timing, target: target, controller: app.executor.location)
        } catch {
            app.present(error)
            return
        }
        playbackTarget = target
        lastSimulatedTarget = target
        let mechanism = app.executor.location.mechanism(for: target).rawValue
        log("gpx-play", status: "started", target: target, mechanism: mechanism, coordinates: inspection.firstPoint, gpx: inspection, detail: "\(inspection.trackPointCount) points")
        playbackTask = Task { [weak self] in
            _ = await app.run("GPX playback", workspace: .location, target: target, transport: mechanism, outputPaths: [inspection.url.path]) { operation in
                try await playback.run { index, total in
                    operation.report("Point \(index) of \(total)", progress: Double(index) / Double(total))
                    Task { @MainActor in self?.playbackProgress = (index, total) }
                }
                return true
            }
            self?.log("gpx-play", status: "finished", target: target, mechanism: mechanism, gpx: inspection, detail: "")
            self?.playbackTask = nil
            self?.playbackProgress = nil
        }
    }

    func stopPlayback() {
        playbackTask?.cancel()
        playbackTask = nil
        playbackProgress = nil
    }
}
