import CoreServices
import Foundation

/// Watches a directory tree with FSEvents and calls `onChange` (coalesced by `latency`).
/// Used to notice simulators being created, booted, or shut down without polling simctl.
public final class DirectoryWatcher: @unchecked Sendable {
    private let path: String
    private let latency: TimeInterval
    private let onChange: @Sendable () -> Void
    private var stream: FSEventStreamRef?
    private let queue = DispatchQueue(label: "io.hideouts.iOSDeveloperToolkit.fsevents")

    public init(path: String, latency: TimeInterval = 1.0, onChange: @escaping @Sendable () -> Void) {
        self.path = path
        self.latency = latency
        self.onChange = onChange
    }

    deinit {
        stop()
    }

    /// Returns false when the directory does not exist (for example, Xcode is not installed).
    @discardableResult
    public func start() -> Bool {
        guard stream == nil, FileManager.default.fileExists(atPath: path) else { return stream != nil }
        var context = FSEventStreamContext(
            version: 0,
            info: Unmanaged.passUnretained(self).toOpaque(),
            retain: nil,
            release: nil,
            copyDescription: nil
        )
        let callback: FSEventStreamCallback = { _, info, _, _, _, _ in
            guard let info else { return }
            Unmanaged<DirectoryWatcher>.fromOpaque(info).takeUnretainedValue().onChange()
        }
        guard let created = FSEventStreamCreate(
            kCFAllocatorDefault,
            callback,
            &context,
            [path] as CFArray,
            FSEventStreamEventId(kFSEventStreamEventIdSinceNow),
            latency,
            FSEventStreamCreateFlags(kFSEventStreamCreateFlagNoDefer | kFSEventStreamCreateFlagFileEvents)
        ) else { return false }
        FSEventStreamSetDispatchQueue(created, queue)
        guard FSEventStreamStart(created) else {
            FSEventStreamInvalidate(created)
            FSEventStreamRelease(created)
            return false
        }
        stream = created
        return true
    }

    public func stop() {
        guard let stream else { return }
        FSEventStreamStop(stream)
        FSEventStreamInvalidate(stream)
        FSEventStreamRelease(stream)
        self.stream = nil
    }
}
