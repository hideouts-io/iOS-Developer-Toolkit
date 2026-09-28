import Foundation
import ToolkitCore

public struct Coordinates: Codable, Sendable, Hashable {
    public var latitude: Double
    public var longitude: Double

    public init(latitude: Double, longitude: Double) {
        self.latitude = latitude
        self.longitude = longitude
    }

    public var formatted: String {
        String(format: "%.6f, %.6f", latitude, longitude)
    }
}

public struct SavedLocation: Codable, Sendable, Hashable, Identifiable {
    public var id: String { name.lowercased() }
    public var name: String
    public var coordinates: Coordinates

    public init(name: String, coordinates: Coordinates) {
        self.name = name
        self.coordinates = coordinates
    }
}

public enum CompassDirection: String, CaseIterable, Sendable, Identifiable {
    case north = "N", northEast = "NE", east = "E", southEast = "SE", south = "S", southWest = "SW", west = "W", northWest = "NW"

    public var id: String { rawValue }

    public var bearing: Double {
        switch self {
        case .north: return 0
        case .northEast: return 45
        case .east: return 90
        case .southEast: return 135
        case .south: return 180
        case .southWest: return 225
        case .west: return 270
        case .northWest: return 315
        }
    }

    public var symbolName: String {
        switch self {
        case .north: return "arrow.up"
        case .northEast: return "arrow.up.right"
        case .east: return "arrow.right"
        case .southEast: return "arrow.down.right"
        case .south: return "arrow.down"
        case .southWest: return "arrow.down.left"
        case .west: return "arrow.left"
        case .northWest: return "arrow.up.left"
        }
    }
}

/// Speed presets for generated routes (km/h).
public enum TravelPreset: String, CaseIterable, Sendable, Identifiable {
    case walk = "Walk", run = "Run", bicycle = "Bicycle", urbanDrive = "Urban drive", highway = "Highway", custom = "Custom"

    public var id: String { rawValue }

    public var speedKmh: Double? {
        switch self {
        case .walk: return 5
        case .run: return 10
        case .bicycle: return 18
        case .urbanDrive: return 40
        case .highway: return 100
        case .custom: return nil
        }
    }
}

public struct GeneratedRoute: Sendable, Hashable {
    public var points: [Coordinates]
    public var distanceMetres: Double
    public var durationSeconds: Int
    public var speedKmh: Double
    public var intervalSeconds: Int
    public var traversalCount: Int
    public var gpxDocument: String
}

public struct GPXPoint: Sendable, Hashable {
    public var coordinates: Coordinates
    public var time: Date?
}

public struct GPXInspection: Sendable, Hashable {
    public var url: URL
    public var sizeBytes: Int64
    public var points: [GPXPoint]
    public var sha256: String

    public var trackPointCount: Int { points.count }
    public var timedPointCount: Int { points.filter { $0.time != nil }.count }
    public var firstPoint: Coordinates? { points.first?.coordinates }
    public var lastPoint: Coordinates? { points.last?.coordinates }

    public var distanceMetres: Double {
        zip(points, points.dropFirst()).reduce(0) { $0 + Geodesy.distance($1.0.coordinates, $1.1.coordinates) }
    }

    /// Duration from the first to the last timestamp, when the track has times.
    public var recordedDuration: TimeInterval? {
        let times = points.compactMap(\.time)
        guard let first = times.first, let last = times.last, last > first else { return nil }
        return last.timeIntervalSince(first)
    }
}

/// One structured Location Lab event appended to `location-events.jsonl`.
public struct LocationEvidenceEvent: Codable, Sendable, Hashable {
    public var event: String
    public var status: String
    public var timestamp: Date
    public var deviceIdentifier: String
    public var deviceName: String
    public var deviceKind: String
    public var osVersion: String?
    public var mechanism: String
    public var latitude: Double?
    public var longitude: Double?
    public var gpxPath: String?
    public var gpxSHA256: String?
    public var detail: String

    public init(event: String, status: String, timestamp: Date = Date(), deviceIdentifier: String, deviceName: String, deviceKind: String, osVersion: String?, mechanism: String, latitude: Double? = nil, longitude: Double? = nil, gpxPath: String? = nil, gpxSHA256: String? = nil, detail: String) {
        self.event = event
        self.status = status
        self.timestamp = timestamp
        self.deviceIdentifier = deviceIdentifier
        self.deviceName = deviceName
        self.deviceKind = deviceKind
        self.osVersion = osVersion
        self.mechanism = mechanism
        self.latitude = latitude
        self.longitude = longitude
        self.gpxPath = gpxPath
        self.gpxSHA256 = gpxSHA256
        self.detail = detail
    }

    enum CodingKeys: String, CodingKey {
        case event, status, timestamp, detail, latitude, longitude, mechanism
        case deviceIdentifier = "device_identifier"
        case deviceName = "device_name"
        case deviceKind = "device_kind"
        case osVersion = "ios_version"
        case gpxPath = "gpx_path"
        case gpxSHA256 = "gpx_sha256"
    }
}
