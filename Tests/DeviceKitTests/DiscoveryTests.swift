import Foundation
import Testing
@testable import DeviceKit
import ToolkitCore

@Suite("Device discovery")
struct DiscoveryTests {
    let coreDevice = CoreDeviceClient.parseDevices(Fixture.json("coredevice/list-devices.json"))

    @Test func mergesUSBMuxAndCoreDeviceByUDID() {
        let mux = USBMuxDevice(deviceID: 4, udid: "00008110001234560ABC801E", connectionType: "USB")
        let devices = DeviceMerger.merge(usbmux: [mux], enrichment: [:], coreDevice: coreDevice, simulators: [])
        let phone = try! #require(devices.first { $0.udid == "00008110-001234560ABC801E" })
        #expect(phone.usbmuxDeviceID == 4)
        #expect(phone.sources == [.usbmux, .coreDevice])
        #expect(phone.name == "Test iPhone")
        #expect(phone.supportsLockdownServices && phone.supportsCoreDevice)
        #expect(devices.filter { $0.udid == "00008110-001234560ABC801E" }.count == 1)
    }

    @Test func usbmuxOnlyDevicesUseLockdownEnrichment() {
        let mux = USBMuxDevice(deviceID: 9, udid: "00008030000000000000002E", connectionType: "USB")
        let enrichment = LockdownEnrichment(name: "Lab iPhone", productType: "iPhone14,5", productVersion: "17.5", buildVersion: "21F79", pairingState: .paired, developerMode: .disabled)
        let devices = DeviceMerger.merge(usbmux: [mux], enrichment: [DeviceMerger.key(mux.udid): enrichment], coreDevice: [], simulators: [])
        #expect(devices.count == 1)
        #expect(devices[0].name == "Lab iPhone")
        #expect(devices[0].marketingName == "iPhone 13")
        #expect(devices[0].developerMode == .disabled)
        #expect(devices[0].sources == [.usbmux])
        #expect(!devices[0].supportsCoreDevice)
    }

    @Test func unpairedLockdownOverridesUnknownPairing() {
        let mux = USBMuxDevice(deviceID: 9, udid: "00008030000000000000002E", connectionType: "USB")
        let devices = DeviceMerger.merge(usbmux: [mux], enrichment: [DeviceMerger.key(mux.udid): LockdownEnrichment(pairingState: .unpaired)], coreDevice: [], simulators: [])
        #expect(devices[0].pairingState == .unpaired)
    }

    @Test func offlineCoreDeviceEntriesAreHidden() {
        var offline = coreDevice[0]
        offline.transportType = nil
        let devices = DeviceMerger.merge(usbmux: [], enrichment: [:], coreDevice: [offline], simulators: [])
        #expect(devices.isEmpty)
    }

    @Test func multipleDevicesStaySeparateAndSimulatorsFollowPhysical() {
        let muxA = USBMuxDevice(deviceID: 1, udid: "00008110001234560ABC801E", connectionType: "USB")
        let muxB = USBMuxDevice(deviceID: 2, udid: "00008030000000000000002E", connectionType: "USB")
        let muxBWifi = USBMuxDevice(deviceID: 3, udid: "00008030000000000000002E", connectionType: "Network")
        let simulator = SimulatorRecord(udid: "SIM-1", name: "iPhone 17 Pro", state: .booted, isAvailable: true, availabilityError: nil, deviceTypeIdentifier: nil, runtime: nil, runtimeIdentifier: "com.apple.CoreSimulator.SimRuntime.iOS-26-3", dataPath: nil, logPath: nil)
        let unavailable = SimulatorRecord(udid: "SIM-2", name: "Old", state: .shutdown, isAvailable: false, availabilityError: "runtime missing", deviceTypeIdentifier: nil, runtime: nil, runtimeIdentifier: "x", dataPath: nil, logPath: nil)
        let devices = DeviceMerger.merge(usbmux: [muxA, muxB, muxBWifi], enrichment: [:], coreDevice: [], simulators: [simulator, unavailable])
        #expect(devices.count == 3)
        #expect(devices.last?.kind == .simulator)
        let second = try! #require(devices.first { $0.udid == "00008030-000000000000002E" })
        #expect(second.transports == [.usb, .network])
        #expect(second.usbmuxDeviceID == 2)
        #expect(Set(devices.map(\.id)).count == 3)
    }

    @Test func coordinatorPublishesAttachedDeviceWithLockdownDetails() async throws {
        let server = try FakeDeviceServer()
        server.listenSendsDetach = false
        server.domainValues["com.apple.security.mac.amfi"] = ["DeveloperModeStatus": true]
        try await server.start()
        defer { Task { await server.stop() } }
        let discovery = DeviceDiscovery(configuration: .init(usbmux: server.client, coreDevice: nil, simulators: nil))
        await discovery.start()
        var found: Device?
        for await snapshot in await discovery.updates() {
            if let device = snapshot.physicalDevices.first, device.pairingState == .paired {
                found = device
                break
            }
        }
        await discovery.stop()
        let device = try #require(found)
        #expect(device.name == "Test iPhone")
        #expect(device.marketingName == "iPhone 15 Pro")
        #expect(device.developerMode == .enabled)
        #expect(device.osVersion == "18.2")
        #expect(device.architecture == "arm64e")
    }

    @Test func coordinatorReportsMissingDaemon() async throws {
        let discovery = DeviceDiscovery(configuration: .init(usbmux: USBMuxClient(socketPath: "/tmp/nope-\(UUID().uuidString)"), coreDevice: nil, simulators: nil))
        await discovery.start()
        var status: SourceStatus = .notChecked
        for await snapshot in await discovery.updates() where snapshot.usbmux != .notChecked {
            status = snapshot.usbmux
            break
        }
        await discovery.stop()
        #expect(!status.isAvailable)
        #expect(status.summary.contains("usbmuxd"))
    }
}
