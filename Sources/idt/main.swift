import ArgumentParser
import DeviceKit
import Foundation
import ToolkitCore
import ToolkitFeatures

@main
struct IDT: AsyncParsableCommand {
    static let configuration = CommandConfiguration(
        commandName: "idt",
        abstract: "iOS Developer Toolkit command-line tools.",
        discussion: "Device identifiers are always explicit: commands that touch a device require --udid.",
        version: ToolkitVersion.current,
        subcommands: [Devices.self, Collect.self, InspectIPA.self, Readiness.self, Toolchain.self, DDI.self]
    )
}

/// Maps errors to plain messages and exit codes.
func report(_ error: Error) -> ExitCode {
    if let toolkitError = error as? ToolkitError {
        var message = "error: \(toolkitError.message)"
        if let recovery = toolkitError.recovery { message += "\n  \(recovery)" }
        if let detail = toolkitError.technicalDetail { message += "\n  details: \(detail.replacingOccurrences(of: "\n", with: "\n  "))" }
        FileHandle.standardError.write(Data((message + "\n").utf8))
    } else {
        FileHandle.standardError.write(Data("error: \(error.localizedDescription)\n".utf8))
    }
    return ExitCode.failure
}

/// Waits for one discovery pass and returns the merged device list.
func discoverDevices(includeSimulators: Bool) async -> DiscoverySnapshot {
    let discovery = DeviceDiscovery(configuration: .init(simulators: includeSimulators ? SimulatorClient() : nil))
    await discovery.refreshAll()
    let usbmux = await discovery.current.usbmux
    if usbmux.isAvailable {
        // Give lockdown enrichment a moment to fill in names and trust state.
        try? await Task.sleep(nanoseconds: 1_500_000_000)
    }
    return await discovery.current
}

func resolve(udid: String) async throws -> Device {
    let snapshot = await discoverDevices(includeSimulators: true)
    let normalized = USBMuxDevice.normalizedUDID(udid)
    guard let device = snapshot.devices.first(where: { $0.udid.caseInsensitiveCompare(normalized) == .orderedSame }) else {
        throw ToolkitError(.deviceNotFound, message: "No connected device or simulator has UDID \(udid).", recovery: "Run `idt devices` to list what is connected.")
    }
    return device
}

struct Devices: AsyncParsableCommand {
    static let configuration = CommandConfiguration(abstract: "List connected devices and simulators.")

    @Flag(help: "Print JSON.") var json = false
    @Flag(help: "Include simulators.") var simulators = false

    func run() async throws {
        let snapshot = await discoverDevices(includeSimulators: simulators)
        if json {
            FileHandle.standardOutput.write(try JSONOutput.encode(snapshot.devices))
            return
        }
        print("usbmuxd: \(snapshot.usbmux.summary) · CoreDevice: \(snapshot.coreDevice.summary)\(simulators ? " · Simulators: \(snapshot.simulators.summary)" : "")")
        if snapshot.devices.isEmpty {
            print("No devices found. Connect a device with USB, unlock it, and tap Trust.")
        }
        for device in snapshot.devices {
            let transport = device.transports.map(\.label).sorted().joined(separator: "+")
            print("\(device.kind == .simulator ? "SIM" : "DEV")  \(device.udid)  \(device.name)  \(device.displayModel)  \(device.displayVersion)  [\(transport)]  trust: \(device.pairingState.label)  developer mode: \(device.developerMode.label)")
        }
    }
}

struct Collect: AsyncParsableCommand {
    static let configuration = CommandConfiguration(abstract: "Collect a hashed evidence case from a physical device.", discussion: "Exit status: 0 complete, 2 finished with coverage gaps, 1 identification failed.")

    @Option(help: "UDID of the device to collect from.") var udid: String
    @Option(help: "Folder in which a new case folder is created.") var outputRoot: String?
    @Option(help: "Existing guided case folder (created in the app) to collect into.") var caseDirectory: String?
    @Option(help: "Stream duration in seconds (0–3600).") var duration = 60
    @Flag(help: "Capture the classic syslog stream.") var includeSyslog = false
    @Flag(help: "Capture the Unified Logging stream.") var includeUnifiedLogs = false
    @Flag(help: "Capture network packets (PCAP).") var includePcap = false
    @Flag(help: "Save a screenshot (needs Xcode).") var includeScreenshot = false
    @Flag(help: "Copy crash reports.") var includeCrashPull = false

    func validate() throws {
        guard (outputRoot == nil) != (caseDirectory == nil) else {
            throw ValidationError("Use exactly one of --output-root or --case-directory.")
        }
    }

    func run() async throws {
        do {
            let device = try await resolve(udid: udid)
            let folder: URL
            if let caseDirectory {
                folder = URL(fileURLWithPath: (caseDirectory as NSString).expandingTildeInPath)
                try CaseWorkflow.validateForCollection(folder, target: device.target)
            } else {
                folder = try CaseWorkflow.createCaseFolder(in: URL(fileURLWithPath: ((outputRoot ?? "") as NSString).expandingTildeInPath), target: device.target)
            }
            let options = CollectionOptions(durationSeconds: duration, includeClassicSyslog: includeSyslog, includeUnifiedLogs: includeUnifiedLogs, includePacketCapture: includePcap, includeScreenshot: includeScreenshot, includeCrashReports: includeCrashPull)
            let collector = try EvidenceCollector(device: device, caseFolder: folder, options: options)
            print("Collecting from \(device.name) into \(folder.path)")
            let manifest = await collector.run { event in
                switch event {
                case .stepFinished(let step): print("  [\(step.status.rawValue)] \(step.title)\(step.detail.isEmpty ? "" : " — \(step.detail)")")
                case .streaming(let remaining) where remaining % 10 == 0: print("  streaming… \(remaining)s left")
                case .finalizing: print("  writing manifest and hashes")
                default: break
                }
            }
            print("Outcome: \(manifest.outcome.rawValue)")
            throw ExitCode(manifest.outcome.exitCode)
        } catch let exit as ExitCode {
            throw exit
        } catch {
            throw report(error)
        }
    }
}

struct InspectIPA: AsyncParsableCommand {
    static let configuration = CommandConfiguration(commandName: "inspect-ipa", abstract: "Inspect an .ipa package's contents, provisioning, and signature.")

    @Argument(help: "Path to the .ipa file.") var path: String
    @Flag(help: "Print JSON.") var json = false

    func run() async throws {
        do {
            let inspection = try IPAInspector.inspect(URL(fileURLWithPath: (path as NSString).expandingTildeInPath))
            if json {
                FileHandle.standardOutput.write(try JSONOutput.encode(inspection))
            } else {
                print(inspection.report)
            }
        } catch {
            throw report(error)
        }
    }
}

struct Readiness: AsyncParsableCommand {
    static let configuration = CommandConfiguration(abstract: "Run the read-only Readiness Check for a device or simulator.")

    @Option(help: "UDID of the device or simulator.") var udid: String

    func run() async throws {
        do {
            let device = try await resolve(udid: udid)
            let results = await CapabilityProbe().run(for: device)
            for result in results {
                print("\(result.state.label.padding(toLength: 16, withPad: " ", startingAt: 0)) \(result.title): \(result.summary)")
                if !result.remediation.isEmpty { print("                 → \(result.remediation)") }
            }
        } catch {
            throw report(error)
        }
    }
}

struct Toolchain: AsyncParsableCommand {
    static let configuration = CommandConfiguration(abstract: "Check that the installed Xcode provides every command the toolkit uses.")

    func run() async throws {
        let results = await ToolchainCheck.run(runner: ProcessCommandRunner())
        print(ToolchainCheck.render(results))
        if results.contains(where: { $0.state != .available }) { throw ExitCode(2) }
    }
}

struct DDI: AsyncParsableCommand {
    static let configuration = CommandConfiguration(abstract: "Developer services (Developer Disk Image) management through Xcode's CoreDevice.", subcommands: [Status.self, Prepare.self, UpdateHost.self])

    struct Status: AsyncParsableCommand {
        static let configuration = CommandConfiguration(abstract: "Show developer services status without changing anything.")
        @Option(help: "Device UDID.") var udid: String
        func run() async throws {
            do {
                let device = try await resolve(udid: udid)
                print(try await CoreDeviceClient().ddiServices(device.target, autoMount: false).json.prettyString())
            } catch { throw report(error) }
        }
    }

    struct Prepare: AsyncParsableCommand {
        static let configuration = CommandConfiguration(abstract: "Install the matching personalized developer image on the device.")
        @Option(help: "Device UDID.") var udid: String
        @Option(help: "Type RUN followed by the last six characters of the UDID to confirm.") var confirm: String
        func run() async throws {
            do {
                let device = try await resolve(udid: udid)
                let requirement = ConfirmationRequirement.make(for: .deviceChange, target: device.target)
                guard requirement.isSatisfied(typedPhrase: confirm, backupAcknowledged: false) else {
                    throw ToolkitError.invalidInput("Confirmation must be exactly “\(requirement.phrase ?? "")”.")
                }
                _ = try await CoreDeviceClient().ddiServices(device.target, autoMount: true)
                print("Developer services are ready on \(device.name).")
            } catch { throw report(error) }
        }
    }

    struct UpdateHost: AsyncParsableCommand {
        static let configuration = CommandConfiguration(commandName: "update-host", abstract: "Refresh this Mac's developer images from the selected Xcode.")
        func run() async throws {
            do {
                _ = try await CoreDeviceClient().updateHostDDIs()
                print("This Mac's developer images are up to date.")
            } catch { throw report(error) }
        }
    }
}
