import Foundation
import ToolkitCore

/// Validation, parsing, and geometry for Location Lab. Nothing here contacts a network
/// service: map links are parsed only when their coordinates are visible in the URL.
public enum LocationLab {
    public static let maximumGPXBytes: Int64 = 64 * 1024 * 1024
    public static let maximumRoutePoints = 100_000
    public static let savedLocationsVersion = 1

    // MARK: Validation

    public static func validate(latitude: String, longitude: String) throws -> Coordinates {
        Coordinates(
            latitude: try validateComponent(latitude, label: "Latitude", range: -90...90),
            longitude: try validateComponent(longitude, label: "Longitude", range: -180...180)
        )
    }

    public static func validate(_ coordinates: Coordinates) throws -> Coordinates {
        try validate(latitude: String(coordinates.latitude), longitude: String(coordinates.longitude))
    }

    static func validateComponent(_ text: String, label: String, range: ClosedRange<Double>) throws -> Double {
        let trimmed = text.trimmingCharacters(in: .whitespaces)
        guard let value = Double(trimmed) else {
            throw ToolkitError.invalidInput("\(label) must be a decimal number, for example 34.0522.")
        }
        guard value.isFinite else { throw ToolkitError.invalidInput("\(label) must be a finite number.") }
        guard range.contains(value) else {
            throw ToolkitError.invalidInput("\(label) must be between \(Int(range.lowerBound)) and \(Int(range.upperBound)).")
        }
        return value
    }

    // MARK: Input parsing

    private static let number = #"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"#
    private static let pairExpression = try! NSRegularExpression(pattern: #"^\s*\(?\s*("# + number + #")\s*,\s*("# + number + #")\s*\)?\s*$"#)
    private static let googlePathExpression = try! NSRegularExpression(pattern: "/@(" + number + "),(" + number + ")(?:[,/]|$)")

    static func parsePair(_ text: String) throws -> Coordinates? {
        let decoded = text.removingPercentEncoding ?? text
        let range = NSRange(decoded.startIndex..<decoded.endIndex, in: decoded)
        guard let match = pairExpression.firstMatch(in: decoded, range: range),
              let latitudeRange = Range(match.range(at: 1), in: decoded),
              let longitudeRange = Range(match.range(at: 2), in: decoded)
        else { return nil }
        return try validate(latitude: String(decoded[latitudeRange]), longitude: String(decoded[longitudeRange]))
    }

    /// Accepts `latitude,longitude`, `geo:` URIs, and full Apple Maps / Google Maps links that
    /// contain coordinates. Short links are rejected rather than resolved over the network.
    public static func parseLocationInput(_ input: String) throws -> Coordinates {
        let text = input.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else {
            throw ToolkitError.invalidInput("Enter latitude,longitude or paste an Apple Maps, Google Maps, or geo: link.")
        }
        if let direct = try parsePair(text) { return direct }
        guard let components = URLComponents(string: text), let scheme = components.scheme?.lowercased() else {
            throw unsupportedInput()
        }
        if scheme == "geo" {
            let body = String(text.dropFirst(4)).split(separator: ";").first.map(String.init) ?? ""
            if let coordinates = try parsePair(body.split(separator: "?").first.map(String.init) ?? body) { return coordinates }
            throw unsupportedInput()
        }
        guard scheme == "http" || scheme == "https" else { throw unsupportedInput() }
        let items = components.queryItems ?? []
        for key in ["ll", "coordinate", "query", "q", "destination", "daddr", "center", "sll"] {
            for item in items where item.name == key {
                if let value = item.value, let coordinates = try parsePair(value) { return coordinates }
            }
        }
        for item in items where item.name == "cp" {
            if let value = item.value, let coordinates = try parsePair(value.replacingOccurrences(of: "~", with: ",")) { return coordinates }
        }
        let path = components.percentEncodedPath.removingPercentEncoding ?? components.path
        let range = NSRange(path.startIndex..<path.endIndex, in: path)
        if let match = googlePathExpression.firstMatch(in: path, range: range),
           let latitudeRange = Range(match.range(at: 1), in: path),
           let longitudeRange = Range(match.range(at: 2), in: path) {
            return try validate(latitude: String(path[latitudeRange]), longitude: String(path[longitudeRange]))
        }
        throw ToolkitError.invalidInput("The map link does not contain visible coordinates. Open short links in a browser first, then copy the full Apple Maps or Google Maps address, or paste latitude,longitude directly.")
    }

    private static func unsupportedInput() -> ToolkitError {
        ToolkitError.invalidInput("Enter latitude,longitude or an Apple Maps, Google Maps, or geo: link that contains coordinates.")
    }

    public static func parseRouteWaypoints(_ text: String) throws -> [Coordinates] {
        var points: [Coordinates] = []
        for (index, rawLine) in text.split(separator: "\n", omittingEmptySubsequences: false).enumerated() {
            let line = rawLine.trimmingCharacters(in: .whitespaces)
            if line.isEmpty { continue }
            let parts = line.split(separator: ",", omittingEmptySubsequences: false).map { $0.trimmingCharacters(in: .whitespaces) }
            guard parts.count == 2 else {
                throw ToolkitError.invalidInput("Waypoint line \(index + 1) must contain latitude,longitude.")
            }
            do {
                points.append(try validate(latitude: parts[0], longitude: parts[1]))
            } catch let error as ToolkitError {
                throw ToolkitError.invalidInput("Waypoint line \(index + 1): \(error.message)")
            }
        }
        guard points.count >= 2 else { throw ToolkitError.invalidInput("A route needs at least two latitude,longitude waypoints.") }
        return points
    }

    // MARK: Offline map projection (equirectangular)

    public static func mapFractions(for coordinates: Coordinates) -> (x: Double, y: Double) {
        ((coordinates.longitude + 180) / 360, (90 - coordinates.latitude) / 180)
    }

    public static func coordinates(forMapFractionX x: Double, y: Double) throws -> Coordinates {
        guard x.isFinite, y.isFinite, (0...1).contains(x), (0...1).contains(y) else {
            throw ToolkitError.invalidInput("Choose a point inside the map.")
        }
        return Coordinates(latitude: 90 - y * 180, longitude: x * 360 - 180)
    }

    // MARK: Routes

    public static func buildRoute(waypoints: [Coordinates], speedKmh: Double, intervalSeconds: Int, traversalCount: Int, startTime: Date) throws -> GeneratedRoute {
        guard waypoints.count >= 2 else { throw ToolkitError.invalidInput("A route needs at least two waypoints.") }
        guard speedKmh.isFinite, speedKmh > 0, speedKmh <= 300 else { throw ToolkitError.invalidInput("Route speed must be greater than 0 and at most 300 km/h.") }
        guard (1...60).contains(intervalSeconds) else { throw ToolkitError.invalidInput("The point interval must be between 1 and 60 seconds.") }
        guard (1...20).contains(traversalCount) else { throw ToolkitError.invalidInput("The number of traversals must be between 1 and 20.") }
        let validated = try waypoints.map(validate)
        let metresPerStep = speedKmh * 1000 / 3600 * Double(intervalSeconds)
        var points: [Coordinates] = []
        var distance = 0.0
        for traversal in 0..<traversalCount {
            let path = traversal % 2 == 0 ? validated : Array(validated.reversed())
            var sampled: [Coordinates] = [path[0]]
            for (start, end) in zip(path, path.dropFirst()) {
                let segment = Geodesy.distance(start, end)
                let steps = max(1, Int((segment / metresPerStep).rounded(.up)))
                if points.count + sampled.count + steps > maximumRoutePoints {
                    throw ToolkitError.invalidInput("The route would exceed \(maximumRoutePoints) points. Increase the speed or interval, or reduce the traversals.")
                }
                sampled += Geodesy.interpolate(from: start, to: end, steps: steps)
                distance += segment
            }
            points += points.isEmpty ? sampled : Array(sampled.dropFirst())
        }
        let duration = max(0, points.count - 1) * intervalSeconds
        return GeneratedRoute(
            points: points,
            distanceMetres: distance,
            durationSeconds: duration,
            speedKmh: speedKmh,
            intervalSeconds: intervalSeconds,
            traversalCount: traversalCount,
            gpxDocument: gpx(points: points, start: startTime, interval: intervalSeconds)
        )
    }

    static func gpx(points: [Coordinates], start: Date, interval: Int) -> String {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        var lines = [
            #"<?xml version="1.0" encoding="UTF-8"?>"#,
            #"<gpx version="1.1" creator="iOS Developer Toolkit" xmlns="http://www.topografix.com/GPX/1/1">"#,
            "  <trk><name>Toolkit QA route</name><trkseg>",
        ]
        for (index, point) in points.enumerated() {
            let time = formatter.string(from: start.addingTimeInterval(TimeInterval(index * interval)))
            lines.append(String(format: #"    <trkpt lat="%.9f" lon="%.9f"><time>%@</time></trkpt>"#, point.latitude, point.longitude, time))
        }
        lines += ["  </trkseg></trk>", "</gpx>"]
        return lines.joined(separator: "\n") + "\n"
    }

    // MARK: GPX inspection

    public static func inspectGPX(at url: URL) throws -> GPXInspection {
        guard url.pathExtension.lowercased() == "gpx" else {
            throw ToolkitError.invalidInput("Route files must use the .gpx extension.")
        }
        guard let size = SecureFileIO.fileSize(url) else {
            throw ToolkitError.fileSystem("The GPX file could not be read.", path: url.path)
        }
        guard size > 0 else { throw ToolkitError.invalidInput("The GPX file is empty.") }
        guard size <= maximumGPXBytes else { throw ToolkitError.invalidInput("The GPX file is larger than the 64 MB limit.") }
        let data: Data
        do { data = try Data(contentsOf: url) } catch { throw ToolkitError.fileSystem("The GPX file could not be read.", path: url.path, underlying: error) }
        let points = try parseGPX(data)
        return GPXInspection(url: url, sizeBytes: size, points: points, sha256: SecureFileIO.sha256(of: data))
    }

    public static func parseGPX(_ data: Data) throws -> [GPXPoint] {
        let prefix = String(decoding: data.prefix(16_384), as: UTF8.self).uppercased()
        guard !prefix.contains("<!DOCTYPE"), !prefix.contains("<!ENTITY") else {
            throw ToolkitError.invalidInput("GPX files that contain DTD or entity declarations are not accepted.")
        }
        let delegate = GPXParserDelegate()
        let parser = XMLParser(data: data)
        parser.shouldResolveExternalEntities = false
        parser.shouldProcessNamespaces = true
        parser.delegate = delegate
        guard parser.parse() else {
            if let error = delegate.error { throw error }
            throw ToolkitError.invalidInput("The GPX file is not valid XML: \(parser.parserError?.localizedDescription ?? "unknown error").")
        }
        if let error = delegate.error { throw error }
        guard delegate.sawGPXRoot else { throw ToolkitError.invalidInput("The file is not a GPX document.") }
        guard !delegate.points.isEmpty else {
            throw ToolkitError.invalidInput("The GPX file needs at least one track point (trkpt). Waypoints and routes alone cannot be replayed.")
        }
        return delegate.points
    }

    // MARK: Saved locations (same JSON schema as earlier releases)

    public static func savedLocationsURL(home: URL = FileManager.default.homeDirectoryForCurrentUser) -> URL {
        home.appendingPathComponent("Library/Application Support/iOS Developer Toolkit/locations.json")
    }

    public static func validateLocationName(_ name: String) throws -> String {
        let trimmed = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { throw ToolkitError.invalidInput("Enter a name for the saved location.") }
        guard trimmed.count <= 80 else { throw ToolkitError.invalidInput("Saved location names must be 80 characters or fewer.") }
        guard !trimmed.unicodeScalars.contains(where: { $0.value < 32 }) else {
            throw ToolkitError.invalidInput("Saved location names cannot contain control characters.")
        }
        return trimmed
    }

    public static func parseSavedLocations(_ data: Data) throws -> [SavedLocation] {
        let document: JSONValue
        do { document = try JSONValue.parse(data) } catch { throw ToolkitError.invalidInput("The saved locations file is not valid JSON.") }
        guard document["version"]?.int == savedLocationsVersion else {
            throw ToolkitError.invalidInput("The saved locations file uses an unsupported version.")
        }
        guard let records = document["locations"]?.array else {
            throw ToolkitError.invalidInput("The saved locations file has no locations list.")
        }
        var names = Set<String>()
        return try records.map { record in
            guard let rawName = record["name"]?.string else { throw ToolkitError.invalidInput("Each saved location needs a name.") }
            let name = try validateLocationName(rawName)
            guard names.insert(name.lowercased()).inserted else { throw ToolkitError.invalidInput("Saved location names must be unique: \(name).") }
            guard let latitude = numeric(record["latitude"]), let longitude = numeric(record["longitude"]) else {
                throw ToolkitError.invalidInput("Saved location \(name) needs numeric latitude and longitude.")
            }
            return SavedLocation(name: name, coordinates: try validate(Coordinates(latitude: latitude, longitude: longitude)))
        }
    }

    /// Accepts JSON numbers only (a quoted "1.5" is rejected, as in earlier releases).
    private static func numeric(_ value: JSONValue?) -> Double? {
        switch value {
        case .number(let number)?: return number
        case .integer(let number)?: return Double(number)
        default: return nil
        }
    }

    public static func encodeSavedLocations(_ locations: [SavedLocation]) throws -> Data {
        let document: [String: Any] = [
            "version": savedLocationsVersion,
            "locations": locations.map { ["name": $0.name, "latitude": $0.coordinates.latitude, "longitude": $0.coordinates.longitude] },
        ]
        var data = try JSONSerialization.data(withJSONObject: document, options: [.prettyPrinted, .sortedKeys])
        data.append(0x0A)
        return data
    }

    public static func adding(_ name: String, _ coordinates: Coordinates, to locations: [SavedLocation]) throws -> [SavedLocation] {
        let validated = try validateLocationName(name)
        guard !locations.contains(where: { $0.name.lowercased() == validated.lowercased() }) else {
            throw ToolkitError.invalidInput("A saved location named “\(validated)” already exists.")
        }
        return locations + [SavedLocation(name: validated, coordinates: try validate(coordinates))]
    }

    public static func loadSavedLocations(from url: URL = savedLocationsURL()) throws -> [SavedLocation] {
        guard FileManager.default.fileExists(atPath: url.path) else { return [] }
        let data: Data
        do { data = try Data(contentsOf: url) } catch { throw ToolkitError.fileSystem("Saved locations could not be read.", path: url.path, underlying: error) }
        return try parseSavedLocations(data)
    }

    public static func storeSavedLocations(_ locations: [SavedLocation], to url: URL = savedLocationsURL()) throws {
        try SecureFileIO.writeAtomically(try encodeSavedLocations(locations), to: url)
    }

    // MARK: Evidence

    public static func eventsURL(in directory: URL) -> URL {
        directory.appendingPathComponent("location-events.jsonl")
    }

    public static func append(_ event: LocationEvidenceEvent, to directory: URL) throws {
        try SecureFileIO.createPrivateDirectory(at: directory)
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
        encoder.dateEncodingStrategy = .iso8601
        var line = try encoder.encode(event)
        line.append(0x0A)
        try SecureFileIO.append(line, to: eventsURL(in: directory), synchronize: true)
    }
}

/// Great-circle math.
public enum Geodesy {
    public static let earthRadiusMetres = 6_371_008.8

    public static func distance(_ start: Coordinates, _ end: Coordinates) -> Double {
        let lat1 = start.latitude * .pi / 180
        let lat2 = end.latitude * .pi / 180
        let deltaLat = lat2 - lat1
        let deltaLon = (end.longitude - start.longitude) * .pi / 180
        let a = pow(sin(deltaLat / 2), 2) + cos(lat1) * cos(lat2) * pow(sin(deltaLon / 2), 2)
        return 2 * earthRadiusMetres * asin(min(1, sqrt(a)))
    }

    /// Moves `origin` by `distance` metres along `bearing` degrees (clockwise from north).
    public static func move(_ origin: Coordinates, bearing: Double, distance: Double) throws -> Coordinates {
        guard bearing.isFinite else { throw ToolkitError.invalidInput("The direction must be a finite number.") }
        guard distance.isFinite, distance > 0, distance <= 100_000 else {
            throw ToolkitError.invalidInput("The nudge distance must be greater than 0 and at most 100,000 metres.")
        }
        let angular = distance / earthRadiusMetres
        let theta = bearing * .pi / 180
        let lat1 = origin.latitude * .pi / 180
        let lon1 = origin.longitude * .pi / 180
        let lat2 = asin(sin(lat1) * cos(angular) + cos(lat1) * sin(angular) * cos(theta))
        let lon2 = lon1 + atan2(sin(theta) * sin(angular) * cos(lat1), cos(angular) - sin(lat1) * sin(lat2))
        let normalized = (lon2 * 180 / .pi + 540).truncatingRemainder(dividingBy: 360) - 180
        return Coordinates(latitude: lat2 * 180 / .pi, longitude: normalized)
    }

    static func interpolate(from start: Coordinates, to end: Coordinates, steps: Int) -> [Coordinates] {
        let deltaLongitude = (end.longitude - start.longitude + 540).truncatingRemainder(dividingBy: 360) - 180
        return (1...steps).map { index in
            let fraction = Double(index) / Double(steps)
            let longitude = (start.longitude + deltaLongitude * fraction + 540).truncatingRemainder(dividingBy: 360) - 180
            return Coordinates(latitude: start.latitude + (end.latitude - start.latitude) * fraction, longitude: longitude)
        }
    }
}

private final class GPXParserDelegate: NSObject, XMLParserDelegate {
    var points: [GPXPoint] = []
    var error: ToolkitError?
    var sawGPXRoot = false
    private var depth = 0
    private var current: Coordinates?
    private var currentTime: Date?
    private var readingTime = false
    private var timeText = ""
    private let formatter: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        return formatter
    }()
    private let fractionalFormatter: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter
    }()

    func parser(_ parser: XMLParser, didStartElement elementName: String, namespaceURI: String?, qualifiedName: String?, attributes: [String: String] = [:]) {
        depth += 1
        if depth == 1 { sawGPXRoot = elementName == "gpx" }
        if elementName == "trkpt" {
            guard let latitude = attributes["lat"], let longitude = attributes["lon"] else {
                error = ToolkitError.invalidInput("Every GPX track point needs lat and lon attributes.")
                parser.abortParsing()
                return
            }
            do {
                current = try LocationLab.validate(latitude: latitude, longitude: longitude)
                currentTime = nil
            } catch let validationError as ToolkitError {
                error = ToolkitError.invalidInput("GPX track point \(points.count + 1): \(validationError.message)")
                parser.abortParsing()
            } catch {}
        } else if elementName == "time", current != nil {
            readingTime = true
            timeText = ""
        }
    }

    func parser(_ parser: XMLParser, foundCharacters string: String) {
        if readingTime { timeText += string }
    }

    func parser(_ parser: XMLParser, didEndElement elementName: String, namespaceURI: String?, qualifiedName: String?) {
        depth -= 1
        if elementName == "time", readingTime {
            readingTime = false
            let text = timeText.trimmingCharacters(in: .whitespacesAndNewlines)
            currentTime = formatter.date(from: text) ?? fractionalFormatter.date(from: text)
        } else if elementName == "trkpt", let current {
            points.append(GPXPoint(coordinates: current, time: currentTime))
            self.current = nil
            if points.count > LocationLab.maximumRoutePoints * 10 {
                error = ToolkitError.invalidInput("The GPX file has too many points to replay.")
                parser.abortParsing()
            }
        }
    }
}
