import Foundation
import Testing
@testable import DeviceKit
@testable import ToolkitFeatures
import ToolkitCore

/// End-to-end checks against a real simulator. Opt in with IDT_SIMULATOR_TESTS=1 because
/// booting a simulator takes time and changes local simulator state.
@Suite("Real simulator (opt-in)", .serialized, .enabled(if: ProcessInfo.processInfo.environment["IDT_SIMULATOR_TESTS"] == "1"))
struct RealSimulatorTests {
    @Test(.timeLimit(.minutes(5)))
    func simulatorWorkflowEndToEnd() async throws {
        let client = SimulatorClient()
        let records = try await client.list()
        let record = try #require(records.first { $0.isAvailable && $0.device.family == .iPhone }, "No available iPhone simulator")
        let target = record.device.target
        let wasBooted = record.state == .booted
        try await client.boot(target)

        // Wait for the boot to settle.
        var booted = false
        for _ in 0..<60 {
            if try await client.list().first(where: { $0.udid == target.udid })?.state == .booted { booted = true; break }
            try await Task.sleep(for: .seconds(1))
        }
        #expect(booted)

        let location = LocationController(simulators: client)
        try await location.set(latitude: 51.5007, longitude: -0.1246, on: target)
        try await location.startRoute([(51.5007, -0.1246), (51.5010, -0.1200)], speedMetresPerSecond: 5, intervalSeconds: 1, on: target)
        try await location.clear(on: target)

        let directory = try SecureFileIO.makeTemporaryDirectory(prefix: "sim-e2e")
        defer { try? FileManager.default.removeItem(at: directory) }
        let screenshot = directory.appendingPathComponent("screen.png")
        try await client.screenshot(target, to: screenshot)
        let png = try Data(contentsOf: screenshot)
        #expect(png.starts(with: [0x89, 0x50, 0x4E, 0x47]))

        let apps = try await client.apps(target)
        #expect(apps.contains { $0.bundleIdentifier == "com.apple.mobilesafari" })

        // Live logs: open the stream, collect for a few seconds, then stop.
        let capture = try LogCapture(kind: .simulator, target: target, directory: directory.appendingPathComponent("logs"))
        let stream = try await LiveLogSource.open(.simulator, target: target)
        let collector = Task { () -> Int in
            var lines = 0
            for try await chunk in stream {
                try await capture.append(chunk)
                lines += chunk.lines.count
            }
            return lines
        }
        try await Task.sleep(for: .seconds(2))
        _ = try await client.launch(bundleIdentifier: "com.apple.Preferences", on: target, terminateExisting: true)
        try await Task.sleep(for: .seconds(4))
        collector.cancel()
        let lines = (try? await collector.value) ?? 0
        try await capture.finish(reason: "test complete")
        let metadata = await capture.currentMetadata
        #expect(metadata.rawBytes > 0, "no log bytes captured (\(lines) lines)")
        #expect(metadata.rawSHA256?.count == 64)

        let executor = ActionExecutor()
        let openURL = try await executor.execute(try #require(ActionCatalog.descriptor("open-url")), target: target, values: ["url": "https://example.com"])
        #expect(openURL.summary.contains("example.com"))

        let readiness = await CapabilityProbe().run(for: record.device)
        #expect(readiness.first { $0.id == "simulator-running" }?.state == .ready)
        #expect(readiness.first { $0.id == "xcode-tools" }?.state == .ready)

        // Install, list, launch, and remove a real (minimal) simulator app.
        let fixture = try await Self.buildFixtureApp(in: directory)
        try await client.install(appAt: fixture, on: target)
        #expect(try await client.apps(target).contains { $0.bundleIdentifier == Self.fixtureBundleID })
        let launched = try await client.launch(bundleIdentifier: Self.fixtureBundleID, on: target, terminateExisting: true)
        #expect(launched.contains(Self.fixtureBundleID))
        try await client.uninstall(bundleIdentifier: Self.fixtureBundleID, on: target)
        #expect(try await !client.apps(target).contains { $0.bundleIdentifier == Self.fixtureBundleID })

        if !wasBooted { try await client.shutdown(target) }
    }

    static let fixtureBundleID = "io.hideouts.idt.simulator-fixture"

    /// Compiles a minimal iOS-simulator app with Xcode's swiftc and signs it ad hoc.
    static func buildFixtureApp(in directory: URL) async throws -> URL {
        let runner = ProcessCommandRunner()
        let app = directory.appendingPathComponent("Fixture.app", isDirectory: true)
        try FileManager.default.createDirectory(at: app, withIntermediateDirectories: true)
        let source = directory.appendingPathComponent("main.swift")
        try Data("import Foundation\nwhile true { sleep(1) }\n".utf8).write(to: source)
        #if arch(arm64)
        let triple = "arm64-apple-ios17.0-simulator"
        #else
        let triple = "x86_64-apple-ios17.0-simulator"
        #endif
        let compile = try await runner.run(CommandRequest(executable: try AppleTool.xcrun.locate(), arguments: ["--sdk", "iphonesimulator", "swiftc", "-target", triple, "-o", app.appendingPathComponent("Fixture").path, source.path], timeout: 300))
        try #require(compile.succeeded, "swiftc failed: \(compile.standardErrorText)")
        let info: PlistValue = [
            "CFBundleIdentifier": .string(fixtureBundleID), "CFBundleExecutable": "Fixture", "CFBundleName": "Fixture",
            "CFBundlePackageType": "APPL", "CFBundleVersion": "1", "CFBundleShortVersionString": "1.0",
            "MinimumOSVersion": "17.0", "CFBundleSupportedPlatforms": ["iPhoneSimulator"], "UIDeviceFamily": [1, 2],
        ]
        try info.encoded(format: .xml).write(to: app.appendingPathComponent("Info.plist"))
        let sign = try await runner.run(CommandRequest(executable: try AppleTool.codesign.locate(), arguments: ["--force", "--sign", "-", app.path], timeout: 60))
        try #require(sign.succeeded, "codesign failed: \(sign.standardErrorText)")
        return app
    }
}
