import Foundation
import Testing
@testable import DeviceKit
import DeviceTestSupport
import ToolkitCore

@Suite("CoreDevice JSON")
struct CoreDeviceParsingTests {
    @Test func parsesClassicAndFlattenedDeviceRecords() throws {
        let devices = CoreDeviceClient.parseDevices(Fixture.json("coredevice/list-devices.json"))
        #expect(devices.count == 3)

        let phone = devices[0]
        #expect(phone.udid == "00008110-001234560ABC801E")
        #expect(phone.name == "Test iPhone")
        #expect(phone.marketingName == "iPhone 15 Pro")
        #expect(phone.architecture == "arm64e")
        #expect(phone.osVersion == "26.0")
        #expect(phone.buildVersion == "23A341")
        #expect(phone.developerMode == .enabled)
        #expect(phone.pairingState == .paired)
        #expect(phone.transport == .usb)
        #expect(phone.ddiServicesAvailable == true)
        #expect(phone.ecid == "1234567890123456")
        #expect(phone.capabilities.contains("Install Application"))

        let pad = devices[1]
        #expect(pad.udid == nil)
        #expect(pad.transport == .network)
        #expect(pad.pairingState == .unpaired)
        #expect(pad.developerMode == .disabled)
        let padDevice = pad.device()
        #expect(padDevice.udid == "9F7A5E30-1111-4222-8333-444444444444")
        #expect(padDevice.family == .iPad)
        #expect(padDevice.target.coreDeviceSelector == "9F7A5E30-1111-4222-8333-444444444444")

        let modern = devices[2]
        #expect(modern.udid == "00008120-000000000000AAAA")
        #expect(modern.name == "New Schema iPhone")
        #expect(modern.osVersion == "27.0")
        #expect(modern.transport == .usb)
        #expect(modern.visibilityClass == "default")
    }

    @Test func mergedDeviceExplainsItself() {
        let device = CoreDeviceClient.parseDevices(Fixture.json("coredevice/list-devices.json"))[0].device()
        #expect(device.kind == .physical)
        #expect(device.displayModel == "iPhone 15 Pro")
        #expect(device.displayVersion == "iOS 26.0 (23A341)")
        #expect(device.osMajorVersion == 26)
        #expect(device.target.confirmationSuffix == "BC801E")
        #expect(device.target.shortLabel == "Test iPhone (…BC801E)")
        #expect(device.supportsCoreDevice)
        #expect(!device.supportsLockdownServices)
    }

    @Test func parsesAppsProcessesAndLockState() {
        let apps = CoreDeviceClient.parseApps(Fixture.json("coredevice/apps.json"))
        #expect(apps.map(\.name) == ["Demo", "Safari"])
        #expect(apps[0].isBuiltByDeveloper == true)
        #expect(apps[1].isRemovable == false)

        let processes = CoreDeviceClient.parseProcesses(Fixture.json("coredevice/processes.json"))
        #expect(processes.map(\.pid) == [1, 118, 912])
        #expect(processes[0].name == "launchd")
        #expect(processes[2].name == "My App")

        let lock = CoreDeviceClient.parseLockState(Fixture.json("coredevice/lockstate.json"))
        #expect(lock.passcodeRequired == true)
        #expect(lock.summary.hasPrefix("Locked"))
    }

    @Test func malformedJSONNeverCrashes() {
        #expect(CoreDeviceClient.parseDevices(.array([])).isEmpty)
        #expect(CoreDeviceClient.parseDevices(["result": ["devices": "nope"]]).isEmpty)
        #expect(CoreDeviceClient.parseApps(.null).isEmpty)
        #expect(CoreDeviceClient.parseProcesses(["result": ["runningProcesses": [["processIdentifier": 1.5]]]]).isEmpty)
        #expect(CoreDeviceRecord(json: ["identifier": ""]) == nil)
    }
}

extension JSONValue: ExpressibleByDictionaryLiteral, ExpressibleByArrayLiteral, ExpressibleByStringLiteral, ExpressibleByFloatLiteral, ExpressibleByIntegerLiteral {
    public init(integerLiteral value: Int64) { self = .integer(value) }
    public init(dictionaryLiteral elements: (String, JSONValue)...) {
        self = .object(Dictionary(elements, uniquingKeysWith: { $1 }))
    }
    public init(arrayLiteral elements: JSONValue...) { self = .array(elements) }
    public init(stringLiteral value: String) { self = .string(value) }
    public init(floatLiteral value: Double) { self = .number(value) }
}

@Suite("CoreDevice client")
struct CoreDeviceClientTests {
    func target() -> DeviceTarget {
        DeviceTarget(kind: .physical, udid: "00008110-001234560ABC801E", name: "Test iPhone", osVersion: "26.0", usbmuxDeviceID: nil, coreDeviceIdentifier: "3C0B", transport: .usb)
    }

    @Test func invokesDevicectlWithExplicitTargetAndJSON() async throws {
        let runner = ScriptedCommandRunner { _ in .init(jsonFile: Fixture.data("coredevice/apps.json")) }
        let client = CoreDeviceClient(runner: runner)
        let apps = try await client.apps(target())
        #expect(apps.count == 2)
        let request = try #require(runner.recorded.first)
        #expect(request.executable.path == "/usr/bin/xcrun")
        #expect(Array(request.arguments.prefix(4)) == ["devicectl", "device", "info", "apps"])
        let deviceIndex = try #require(request.arguments.firstIndex(of: "--device"))
        #expect(request.arguments[deviceIndex + 1] == "00008110-001234560ABC801E")
        #expect(request.arguments.contains("--json-output"))
        #expect(request.arguments.contains("--timeout"))
    }

    @Test(arguments: [
        ("coredevice/error-not-found.json", ToolkitError.Kind.deviceNotFound),
        ("coredevice/error-developer-mode.json", ToolkitError.Kind.developerModeDisabled),
        ("coredevice/error-locked.json", ToolkitError.Kind.deviceLocked),
    ])
    func failuresBecomeActionableErrors(fixture: String, kind: ToolkitError.Kind) async throws {
        let runner = ScriptedCommandRunner { _ in .init(exitCode: 1, standardError: Data("ERROR: failed".utf8), jsonFile: Fixture.data(fixture)) }
        let client = CoreDeviceClient(runner: runner)
        do {
            _ = try await client.lockState(target())
            Issue.record("expected failure")
        } catch let error as ToolkitError {
            #expect(error.kind == kind)
            #expect(error.recovery != nil)
            #expect(error.technicalDetail?.contains("Exit status: 1") == true)
            #expect(!error.message.contains("exit"))
        }
    }

    @Test func missingJSONWithFailureStillExplains() async throws {
        let runner = ScriptedCommandRunner { _ in .init(exitCode: 72, standardError: Data("xcrun: error: unable to find utility \"devicectl\"".utf8)) }
        do {
            _ = try await CoreDeviceClient(runner: runner).listDevices()
            Issue.record("expected failure")
        } catch let error as ToolkitError {
            #expect(error.kind == .toolMissing)
            #expect(error.recovery?.contains("Xcode") == true)
        }
    }

    @Test func inputValidationHappensBeforeLaunching() async throws {
        let runner = ScriptedCommandRunner { _ in .init() }
        let client = CoreDeviceClient(runner: runner)
        await #expect(throws: ToolkitError.self) { _ = try await client.uninstall(bundleIdentifier: "not a bundle; rm -rf", on: target()) }
        await #expect(throws: ToolkitError.self) { _ = try await client.setLocation(latitude: 91, longitude: 0, on: target()) }
        await #expect(throws: ToolkitError.self) { _ = try await client.setLocation(latitude: .nan, longitude: 0, on: target()) }
        await #expect(throws: ToolkitError.self) { _ = try await client.terminate(pid: 0, on: target(), force: false) }
        await #expect(throws: ToolkitError.self) { _ = try await client.screenshot(target(), to: URL(fileURLWithPath: "/tmp/x.jpg")) }
        #expect(runner.recorded.isEmpty)
    }

    @Test func locationUsesFixedPrecisionArguments() async throws {
        let runner = ScriptedCommandRunner { _ in .init(jsonFile: Data(#"{"info":{"outcome":"success"},"result":{}}"#.utf8)) }
        _ = try await CoreDeviceClient(runner: runner).setLocation(latitude: 34.0522, longitude: -118.2437, on: target())
        let arguments = try #require(runner.recorded.first).arguments
        #expect(arguments.contains("34.05220000"))
        #expect(arguments.contains("-118.24370000"))
    }
}

@Suite("simctl")
struct SimulatorClientTests {
    @Test func parsesDevicesAndRuntimes() throws {
        let devices: JSONValue = [
            "devices": [
                "com.apple.CoreSimulator.SimRuntime.iOS-26-3": [
                    ["udid": "780C6431-EBAF-4AAE-AA6C-E8886DD4D415", "name": "iPhone 17 Pro", "state": "Shutdown", "isAvailable": .bool(true), "deviceTypeIdentifier": "com.apple.CoreSimulator.SimDeviceType.iPhone-17-Pro"],
                    ["udid": "B2", "name": "iPad Pro 13-inch (M5)", "state": "Booted", "isAvailable": .bool(true), "deviceTypeIdentifier": "com.apple.CoreSimulator.SimDeviceType.iPad-Pro-13-inch-M5"],
                ],
                "com.apple.CoreSimulator.SimRuntime.watchOS-12-0": [],
            ],
        ]
        let runtimes: JSONValue = ["runtimes": [["identifier": "com.apple.CoreSimulator.SimRuntime.iOS-26-3", "name": "iOS 26.3", "version": "26.3.1", "buildversion": "23D8133", "platform": "iOS", "isAvailable": .bool(true)]]]
        let records = SimulatorClient.parse(devices: devices, runtimes: runtimes)
        #expect(records.count == 2)
        #expect(records[0].state == .booted)
        #expect(records[0].device.family == .iPad)
        let phone = records[1].device
        #expect(phone.kind == .simulator)
        #expect(phone.osVersion == "26.3.1")
        #expect(phone.simulatorRuntime == "iOS 26.3")
        #expect(phone.pairingState == .notApplicable)
        #expect(phone.transports == [.local])
    }

    @Test func runtimeFallbacksWhenListIsMissing() {
        let devices: JSONValue = ["devices": ["com.apple.CoreSimulator.SimRuntime.iOS-18-4": [["udid": "C3", "name": "iPhone 16", "state": "Weird"]]]]
        let record = SimulatorClient.parse(devices: devices, runtimes: nil)[0]
        #expect(record.state == .unknown)
        #expect(record.device.osVersion == "18.4")
        #expect(record.device.osName == "iOS")
    }

    @Test func parsesOpenStepAppList() throws {
        let text = """
        {
            "com.apple.mobilesafari" = {
                ApplicationType = System;
                CFBundleDisplayName = Safari;
                CFBundleIdentifier = "com.apple.mobilesafari";
                CFBundleShortVersionString = "26.3";
            };
            "com.example.demo" = {
                ApplicationType = User;
                CFBundleName = Demo;
                CFBundleIdentifier = "com.example.demo";
            };
        }
        """
        let apps = try SimulatorClient.parseApps(Data(text.utf8))
        #expect(apps.map(\.name) == ["Demo", "Safari"])
        #expect(apps[1].version == "26.3")
    }

    @Test func refusesPhysicalTargets() async throws {
        let runner = ScriptedCommandRunner { _ in .init() }
        let physical = DeviceTarget(kind: .physical, udid: "X", name: "Phone", osVersion: nil, usbmuxDeviceID: nil, coreDeviceIdentifier: nil, transport: .usb)
        await #expect(throws: ToolkitError.self) { try await SimulatorClient(runner: runner).boot(physical) }
        #expect(runner.recorded.isEmpty)
    }

    @Test func simulatorRejectsIPAInstall() async throws {
        let simulator = DeviceTarget(kind: .simulator, udid: "S", name: "Sim", osVersion: nil, usbmuxDeviceID: nil, coreDeviceIdentifier: nil, transport: .local)
        do {
            try await SimulatorClient(runner: ScriptedCommandRunner { _ in .init() }).install(appAt: URL(fileURLWithPath: "/tmp/App.ipa"), on: simulator)
            Issue.record("expected unsupported")
        } catch let error as ToolkitError {
            #expect(error.kind == .unsupported)
        }
    }

    @Test func errorsAreInterpreted() async throws {
        let runner = ScriptedCommandRunner { _ in .init(exitCode: 164, standardError: Data("Invalid device: S".utf8)) }
        let simulator = DeviceTarget(kind: .simulator, udid: "S", name: "Sim", osVersion: nil, usbmuxDeviceID: nil, coreDeviceIdentifier: nil, transport: .local)
        do {
            try await SimulatorClient(runner: runner).shutdown(simulator)
            Issue.record("expected failure")
        } catch let error as ToolkitError {
            #expect(error.kind == .deviceNotFound)
        }
    }

    /// Runs against the real simctl on this Mac (no simulator is booted by the test).
    @Test(.enabled(if: FileManager.default.isExecutableFile(atPath: "/usr/bin/xcrun")))
    func listsRealSimulators() async throws {
        let records = try await SimulatorClient().list()
        for record in records {
            #expect(!record.udid.isEmpty)
            #expect(record.device.kind == .simulator)
        }
    }

    @Test func ndjsonLogParsing() throws {
        let line = #"{"timestamp":"2026-09-26 10:00:00.123456-0700","messageType":"Error","eventMessage":"Something failed","processImagePath":"/Applications/Demo.app/Demo","processID":42,"subsystem":"com.example","category":"net"}"#
        let parsed = try #require(SimulatorLogParser.parse(line: Substring(line)))
        #expect(parsed.process == "Demo")
        #expect(parsed.pid == 42)
        #expect(parsed.level == "Error")
        #expect(parsed.timestamp != nil)
        #expect(parsed.rendered.contains("[com.example:net]"))
        #expect(SimulatorLogParser.parse(line: "Filtering the log data using ...")?.message.hasPrefix("Filtering") == true)
        #expect(SimulatorLogParser.parse(line: "   ") == nil)
    }
}
