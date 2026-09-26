import Foundation
import Testing
@testable import DeviceKit
@testable import ToolkitFeatures
import ToolkitCore

@Suite("Location Lab")
struct LocationLabTests {
    @Test(arguments: [
        ("34.0522,-118.2437", 34.0522, -118.2437),
        ("(34.0522, -118.2437)", 34.0522, -118.2437),
        ("geo:37.3349,-122.0090", 37.3349, -122.0090),
        ("https://maps.apple.com/?ll=51.5007%2C-0.1246", 51.5007, -0.1246),
        ("https://www.google.com/maps/@35.6586,139.7454,15z", 35.6586, 139.7454),
        ("https://www.google.com/maps/search/?api=1&query=-33.8568%2C151.2153", -33.8568, 151.2153),
        ("https://www.bing.com/maps?cp=47.6~-122.3", 47.6, -122.3),
    ])
    func parsesCoordinatesAndFullMapLinks(input: String, latitude: Double, longitude: Double) throws {
        let parsed = try LocationLab.parseLocationInput(input)
        #expect(abs(parsed.latitude - latitude) < 1e-9)
        #expect(abs(parsed.longitude - longitude) < 1e-9)
    }

    @Test(arguments: ["https://maps.app.goo.gl/short", "https://maps.apple.com/?q=Coffee", "ftp://x/1,2", "", "hello", "91,0"])
    func rejectsLinksWithoutVisibleCoordinates(input: String) {
        #expect(throws: ToolkitError.self) { try LocationLab.parseLocationInput(input) }
    }

    @Test func mapFractionsRoundTrip() throws {
        let original = Coordinates(latitude: 34.0522, longitude: -118.2437)
        let fractions = LocationLab.mapFractions(for: original)
        let restored = try LocationLab.coordinates(forMapFractionX: fractions.x, y: fractions.y)
        #expect(abs(restored.latitude - original.latitude) < 1e-12)
        #expect(abs(restored.longitude - original.longitude) < 1e-12)
        #expect(throws: ToolkitError.self) { try LocationLab.coordinates(forMapFractionX: 1.1, y: 0.5) }
    }

    @Test(arguments: [("nan", "0"), ("91", "0"), ("0", "-181"), ("abc", "1"), ("inf", "0")])
    func rejectsInvalidCoordinates(latitude: String, longitude: String) {
        #expect(throws: ToolkitError.self) { try LocationLab.validate(latitude: latitude, longitude: longitude) }
    }

    @Test func inspectsTrackPointsAndHashes() throws {
        let directory = try SecureFileIO.makeTemporaryDirectory(prefix: "gpx")
        defer { try? FileManager.default.removeItem(at: directory) }
        let file = directory.appendingPathComponent("route.gpx")
        try SecureFileIO.writeNewFile(Data("""
        <?xml version="1.0" encoding="UTF-8"?>
        <gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1">
          <trk><trkseg>
            <trkpt lat="34.0522" lon="-118.2437"><time>2026-08-23T12:00:00Z</time></trkpt>
            <trkpt lat="34.0523" lon="-118.2436" />
            <trkpt lat="34.0524" lon="-118.2435"><time>2026-08-23T12:00:10.500Z</time></trkpt>
          </trkseg></trk>
        </gpx>
        """.utf8), to: file)
        let inspection = try LocationLab.inspectGPX(at: file)
        #expect(inspection.trackPointCount == 3)
        #expect(inspection.timedPointCount == 2)
        #expect(inspection.firstPoint == Coordinates(latitude: 34.0522, longitude: -118.2437))
        #expect(inspection.sha256.count == 64)
        #expect(inspection.recordedDuration == 10.5)
        #expect(inspection.distanceMetres > 20)
    }

    @Test(arguments: [
        #"<gpx version="1.1"><wpt lat="1" lon="2" /></gpx>"#,
        #"<?xml version="1.0"?><!DOCTYPE gpx [<!ENTITY x "y">]><gpx><trk><trkseg><trkpt lat="1" lon="2"/></trkseg></trk></gpx>"#,
        #"<gpx><trk><trkseg><trkpt lat="100" lon="2"/></trkseg></trk></gpx>"#,
        #"<gpx><trk><trkseg><trkpt lat="1"/></trkseg></trk></gpx>"#,
        #"<kml><trkpt lat="1" lon="2"/></kml>"#,
        "not xml",
    ])
    func rejectsUnsafeOrUselessGPX(document: String) {
        #expect(throws: ToolkitError.self) { try LocationLab.parseGPX(Data(document.utf8)) }
    }

    @Test func savedLocationSchemaIsStrictAndCompatible() throws {
        let locations = try LocationLab.parseSavedLocations(Data(#"{"version": 1, "locations": [{"name": "Lab", "latitude": 1.5, "longitude": 2}]}"#.utf8))
        #expect(locations == [SavedLocation(name: "Lab", coordinates: Coordinates(latitude: 1.5, longitude: 2))])
        #expect(throws: ToolkitError.self) { try LocationLab.adding("lab", Coordinates(latitude: 3, longitude: 4), to: locations) }
        #expect(throws: ToolkitError.self) { try LocationLab.parseSavedLocations(Data(#"{"version": 1, "locations": [{"name": "Broken", "latitude": "1", "longitude": 2}]}"#.utf8)) }
        #expect(throws: ToolkitError.self) { try LocationLab.parseSavedLocations(Data(#"{"version": 2, "locations": []}"#.utf8)) }
        #expect(throws: ToolkitError.self) { try LocationLab.adding("bad\u{7}name", Coordinates(latitude: 0, longitude: 0), to: []) }

        let directory = try SecureFileIO.makeTemporaryDirectory(prefix: "saved")
        defer { try? FileManager.default.removeItem(at: directory) }
        let file = directory.appendingPathComponent("locations.json")
        let updated = try LocationLab.adding("Home", Coordinates(latitude: 10, longitude: 20), to: locations)
        try LocationLab.storeSavedLocations(updated, to: file)
        #expect(try LocationLab.loadSavedLocations(from: file) == updated)
        #expect(try LocationLab.loadSavedLocations(from: directory.appendingPathComponent("missing.json")).isEmpty)
    }

    @Test func buildsBoundedTimestampedPingPongRoute() throws {
        let waypoints = try LocationLab.parseRouteWaypoints("34.0522,-118.2437\n\n34.0523,-118.2436")
        let start = ISO8601DateFormatter().date(from: "2026-08-24T00:00:00Z")!
        let route = try LocationLab.buildRoute(waypoints: waypoints, speedKmh: 5, intervalSeconds: 2, traversalCount: 2, startTime: start)
        #expect(route.points.count > 2)
        #expect(abs(route.points.first!.latitude - route.points.last!.latitude) < 1e-9)
        #expect(route.gpxDocument.contains("2026-08-24T00:00:00Z"))
        #expect(route.gpxDocument.components(separatedBy: "<trkpt").count - 1 == route.points.count)
        #expect(route.durationSeconds == (route.points.count - 1) * 2)
        let reparsed = try LocationLab.parseGPX(Data(route.gpxDocument.utf8))
        #expect(reparsed.count == route.points.count)
    }

    @Test func rejectsUnboundedRoutesAndBadWaypoints() throws {
        #expect(throws: ToolkitError.self) {
            try LocationLab.buildRoute(waypoints: [Coordinates(latitude: 0, longitude: 0), Coordinates(latitude: 0, longitude: 179)], speedKmh: 1, intervalSeconds: 1, traversalCount: 20, startTime: Date())
        }
        #expect(throws: ToolkitError.self) { try LocationLab.parseRouteWaypoints("1,2") }
        #expect(throws: ToolkitError.self) { try LocationLab.parseRouteWaypoints("1,2\n3") }
        #expect(throws: ToolkitError.self) {
            try LocationLab.buildRoute(waypoints: [Coordinates(latitude: 0, longitude: 0), Coordinates(latitude: 1, longitude: 1)], speedKmh: 500, intervalSeconds: 1, traversalCount: 1, startTime: Date())
        }
    }

    @Test func nudgesWithoutMutatingInput() throws {
        let origin = Coordinates(latitude: 34.0522, longitude: -118.2437)
        let moved = try Geodesy.move(origin, bearing: CompassDirection.east.bearing, distance: 100)
        #expect(origin == Coordinates(latitude: 34.0522, longitude: -118.2437))
        #expect(abs(moved.latitude - origin.latitude) < 1e-4)
        #expect(moved.longitude > origin.longitude)
        #expect(abs(Geodesy.distance(origin, moved) - 100) < 0.5)
        let wrapped = try Geodesy.move(Coordinates(latitude: 0, longitude: 179.9999), bearing: 90, distance: 1000)
        #expect(wrapped.longitude < -179)
        #expect(throws: ToolkitError.self) { try Geodesy.move(origin, bearing: 0, distance: 200_000) }
    }

    @Test func evidenceEventsAppendAsJSONLines() throws {
        let directory = try SecureFileIO.makeTemporaryDirectory(prefix: "events")
        defer { try? FileManager.default.removeItem(at: directory) }
        for status in ["started", "completed"] {
            try LocationLab.append(LocationEvidenceEvent(event: "set", status: status, deviceIdentifier: "UDID", deviceName: "Phone", deviceKind: "physical", osVersion: "26.0", mechanism: "CoreDevice", latitude: 1, longitude: 2, detail: "ok"), to: directory)
        }
        let lines = try String(contentsOf: LocationLab.eventsURL(in: directory), encoding: .utf8).split(separator: "\n")
        #expect(lines.count == 2)
        let first = try JSONValue.parse(Data(lines[0].utf8))
        #expect(first["device_identifier"]?.string == "UDID")
        #expect(first["ios_version"]?.string == "26.0")
    }
}

@Suite("GPX playback")
struct PlaybackTests {
    func point(_ offset: TimeInterval?) -> GPXPoint {
        GPXPoint(coordinates: Coordinates(latitude: 1, longitude: 1), time: offset.map { Date(timeIntervalSince1970: 1_000 + $0) })
    }

    @Test func recordedTimingIsMonotonicAndSpaced() throws {
        let offsets = try PlaybackPlanner.offsets(for: [point(0), point(5), point(5.1), point(nil), point(3)], timing: .recorded(jitterMilliseconds: 0))
        #expect(offsets == [0, 5, 5.5, 6.5, 7])
    }

    @Test func jitterIsBoundedAndNeverReorders() throws {
        let offsets = try PlaybackPlanner.offsets(for: (0..<20).map { point(Double($0)) }, timing: .recorded(jitterMilliseconds: 400), random: { $0.upperBound })
        #expect(offsets[0] == 0)
        #expect(zip(offsets, offsets.dropFirst()).allSatisfy { $1 >= $0 + 0.5 })
        #expect(throws: ToolkitError.self) { try PlaybackPlanner.offsets(for: [point(0)], timing: .recorded(jitterMilliseconds: 70_000)) }
    }

    @Test func fixedInterval() throws {
        #expect(try PlaybackPlanner.offsets(for: [point(nil), point(nil), point(nil)], timing: .fixedInterval(seconds: 2)) == [0, 2, 4])
        #expect(throws: ToolkitError.self) { try PlaybackPlanner.offsets(for: [point(nil)], timing: .fixedInterval(seconds: 0.1)) }
    }

    @Test func playsEveryPointOnTheCapturedTargetOnly() async throws {
        let runner = ScriptedRunner()
        let controller = LocationController(coreDevice: CoreDeviceClient(runner: runner), simulators: SimulatorClient(runner: runner))
        let simulator = DeviceTarget(kind: .simulator, udid: "SIM-A", name: "Sim", osVersion: "26.3", usbmuxDeviceID: nil, coreDeviceIdentifier: nil, transport: .local)
        let points = [GPXPoint(coordinates: Coordinates(latitude: 1, longitude: 2), time: nil), GPXPoint(coordinates: Coordinates(latitude: 3, longitude: 4), time: nil)]
        let playback = try GPXPlayback(points: points, timing: .fixedInterval(seconds: 0.5), target: simulator, controller: controller)
        let progress = LockedValue<[Int]>([])
        try await playback.run { index, _ in progress.withLock { $0.append(index) } }
        #expect(progress.current == [1, 2])
        #expect(await playback.state == .finished)
        let requests = runner.requests.current
        #expect(requests.count == 2)
        #expect(requests.allSatisfy { $0.arguments.contains("SIM-A") && $0.arguments.contains("location") })
    }

    @Test func mechanismSelection() {
        let controller = LocationController()
        let modern = DeviceTarget(kind: .physical, udid: "A", name: "A", osVersion: "17.0", usbmuxDeviceID: 1, coreDeviceIdentifier: nil, transport: .usb)
        let legacy = DeviceTarget(kind: .physical, udid: "B", name: "B", osVersion: "16.7.10", usbmuxDeviceID: 1, coreDeviceIdentifier: nil, transport: .usb)
        let simulator = DeviceTarget(kind: .simulator, udid: "C", name: "C", osVersion: "26.3", usbmuxDeviceID: nil, coreDeviceIdentifier: nil, transport: .local)
        #expect(controller.mechanism(for: modern) == .coreDevice)
        #expect(controller.mechanism(for: legacy) == .legacyService)
        #expect(controller.mechanism(for: simulator) == .simulator)
        #expect(LegacyLocationSimulation.encodeSet(latitude: 1.5, longitude: -2).count == 4 + 4 + 3 + 4 + 4)
        #expect(LegacyLocationSimulation.encodeClear() == Data([0, 0, 0, 1]))
    }
}

/// Minimal recording runner for feature tests.
final class ScriptedRunner: CommandRunning, @unchecked Sendable {
    let requests = LockedValue<[CommandRequest]>([])
    var reply: @Sendable (CommandRequest) -> (Int32, String) = { _ in (0, "") }

    func run(_ request: CommandRequest) async throws -> CommandResult {
        requests.withLock { $0.append(request) }
        let (code, output) = reply(request)
        if let index = request.arguments.firstIndex(of: "--json-output") {
            try Data(#"{"info":{"outcome":"success"},"result":{}}"#.utf8).write(to: URL(fileURLWithPath: request.arguments[index + 1]))
        }
        return CommandResult(request: request, termination: .exited(code), standardOutput: Data(output.utf8), standardError: Data(), startedAt: Date(), finishedAt: Date())
    }

    func stream(_ request: CommandRequest) -> AsyncThrowingStream<CommandStreamEvent, Error> {
        AsyncThrowingStream { continuation in
            Task {
                do {
                    continuation.yield(.finished(try await self.run(request)))
                    continuation.finish()
                } catch { continuation.finish(throwing: error) }
            }
        }
    }
}
