import DeviceKit
import Foundation
import ToolkitCore

/// How recorded GPX timing is replayed.
public enum PlaybackTiming: Sendable, Hashable {
    /// Follows the file's timestamps, with optional ± jitter in milliseconds.
    case recorded(jitterMilliseconds: Int)
    /// Ignores timestamps and moves one point per interval.
    case fixedInterval(seconds: Double)
}

public enum PlaybackPlanner {
    /// Offsets (seconds from start) for each point. Points without timestamps follow the
    /// previous point by one second. Offsets never decrease and are at least 0.5 s apart so the
    /// device service is not flooded.
    public static func offsets(for points: [GPXPoint], timing: PlaybackTiming, random: @Sendable (ClosedRange<Double>) -> Double = { Double.random(in: $0) }) throws -> [TimeInterval] {
        guard !points.isEmpty else { return [] }
        switch timing {
        case .fixedInterval(let seconds):
            guard seconds.isFinite, seconds >= 0.5, seconds <= 3600 else {
                throw ToolkitError.invalidInput("The playback interval must be between 0.5 seconds and 1 hour.")
            }
            return points.indices.map { Double($0) * seconds }
        case .recorded(let jitter):
            guard (0...60_000).contains(jitter) else {
                throw ToolkitError.invalidInput("Timing randomness must be between 0 and 60,000 milliseconds.")
            }
            let start = points.first(where: { $0.time != nil })?.time
            var offsets: [TimeInterval] = []
            var previous: TimeInterval = -0.5
            for point in points {
                var offset: TimeInterval
                if let time = point.time, let start {
                    offset = time.timeIntervalSince(start)
                } else {
                    offset = previous + 1
                }
                if jitter > 0 && !offsets.isEmpty {
                    offset += random(-Double(jitter)...Double(jitter)) / 1000
                }
                offset = max(offset, previous + 0.5)
                offsets.append(offset)
                previous = offset
            }
            return offsets
        }
    }
}

/// Replays GPX points on one target by sending each point at its scheduled time.
public actor GPXPlayback {
    public enum State: Sendable, Equatable {
        case idle
        case playing(index: Int, total: Int)
        case finished
        case stopped
        case failed(String)
    }

    public nonisolated let target: DeviceTarget
    private let controller: LocationController
    private let points: [GPXPoint]
    private let offsets: [TimeInterval]
    private(set) public var state: State = .idle

    public init(points: [GPXPoint], timing: PlaybackTiming, target: DeviceTarget, controller: LocationController) throws {
        self.points = points
        self.offsets = try PlaybackPlanner.offsets(for: points, timing: timing)
        self.target = target
        self.controller = controller
    }

    public var expectedDuration: TimeInterval { offsets.last ?? 0 }

    /// Plays to completion or until the task is cancelled. Progress reports (index, total).
    public func run(progress: @escaping @Sendable (Int, Int) -> Void) async throws {
        let start = ContinuousClock.now
        for (index, point) in points.enumerated() {
            let due = start + .milliseconds(Int64(offsets[index] * 1000))
            do {
                try await Task.sleep(until: due, clock: .continuous)
            } catch {
                state = .stopped
                throw ToolkitError.cancelled("GPX playback")
            }
            do {
                try await controller.set(latitude: point.coordinates.latitude, longitude: point.coordinates.longitude, on: target)
            } catch {
                state = .failed((error as? ToolkitError)?.message ?? error.localizedDescription)
                throw error
            }
            state = .playing(index: index + 1, total: points.count)
            progress(index + 1, points.count)
        }
        state = .finished
    }
}
