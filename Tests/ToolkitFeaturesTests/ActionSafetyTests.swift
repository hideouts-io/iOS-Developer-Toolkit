import Foundation
import Testing
import DeviceKit
@testable import ToolkitFeatures
import ToolkitCore

@Suite("Action safety")
struct ActionSafetyTests {
    let target = DeviceTarget(kind: .physical, udid: "00008110-001234560ABC801E", name: "Phone", osVersion: "26.0", usbmuxDeviceID: 1, coreDeviceIdentifier: nil, transport: .usb)

    @Test func confirmationsAreBoundToTheTargetDevice() {
        let read = ConfirmationRequirement.make(for: .readOnly, target: target)
        #expect(read.phrase == nil && !read.requiresReview)
        #expect(read.isSatisfied(typedPhrase: "", backupAcknowledged: false))

        let write = ConfirmationRequirement.make(for: .hostWrite, target: target)
        #expect(write.phrase == nil && write.requiresReview)

        let change = ConfirmationRequirement.make(for: .deviceChange, target: target)
        #expect(change.phrase == "RUN BC801E")
        #expect(change.isSatisfied(typedPhrase: " RUN BC801E ", backupAcknowledged: false))
        #expect(!change.isSatisfied(typedPhrase: "run bc801e", backupAcknowledged: false))
        #expect(!change.isSatisfied(typedPhrase: "RUN FFFFFF", backupAcknowledged: false))

        let high = ConfirmationRequirement.make(for: .highImpact, target: target)
        #expect(high.phrase == "IRREVERSIBLE BC801E")
        #expect(!high.isSatisfied(typedPhrase: "IRREVERSIBLE BC801E", backupAcknowledged: false))
        #expect(high.isSatisfied(typedPhrase: "IRREVERSIBLE BC801E", backupAcknowledged: true))
    }

    @Test func argumentSplittingNeverUsesAShell() throws {
        #expect(try ArgumentSplitter.split("device info apps") == ["device", "info", "apps"])
        #expect(try ArgumentSplitter.split(#"device process openURL "https://a.example/x y" 'it''s'"#) == ["device", "process", "openURL", "https://a.example/x y", "its"])
        #expect(try ArgumentSplitter.split(#"a\ b "c\"d" ''"#) == ["a b", "c\"d", ""])
        #expect(try ArgumentSplitter.split("list devices | rm -rf / ; $(id)") == ["list", "devices", "|", "rm", "-rf", "/", ";", "$(id)"])
        #expect(throws: ToolkitError.self) { try ArgumentSplitter.split(#"unterminated "quote"#) }
        #expect(throws: ToolkitError.self) { try ArgumentSplitter.split(#"trailing\"#) }
    }

    @Test func advancedModeClassifiesAndBinds() throws {
        #expect(AdvancedCommandPolicy.risk(for: ["list", "devices"]) == .readOnly)
        #expect(AdvancedCommandPolicy.risk(for: ["device", "info", "apps"]) == .readOnly)
        #expect(AdvancedCommandPolicy.risk(for: ["device", "copy", "from"]) == .hostWrite)
        #expect(AdvancedCommandPolicy.risk(for: ["device", "process", "launch"]) == .deviceChange)
        #expect(AdvancedCommandPolicy.risk(for: ["device", "reboot"]) == .highImpact)
        #expect(AdvancedCommandPolicy.risk(for: ["manage", "ddis", "clean"]) == .highImpact)

        #expect(try AdvancedCommandPolicy.bind(["device", "info", "apps"], to: target) == ["device", "info", "apps", "--device", target.udid])
        #expect(try AdvancedCommandPolicy.bind(["device", "info", "apps", "--device", target.udid], to: target) == ["device", "info", "apps", "--device", target.udid])
        #expect(try AdvancedCommandPolicy.bind(["list", "devices"], to: nil) == ["list", "devices"])
        #expect(throws: ToolkitError.self) { try AdvancedCommandPolicy.bind(["device", "reboot", "--device", "OTHER-DEVICE"], to: target) }
        #expect(throws: ToolkitError.self) { try AdvancedCommandPolicy.bind(["device", "info", "apps"], to: nil) }
        #expect(throws: ToolkitError.self) { try AdvancedCommandPolicy.bind(["list", "devices", "--json-output", "/tmp/x"], to: target) }
        #expect(throws: ToolkitError.self) { try AdvancedCommandPolicy.bind(["/bin/sh", "-c", "id"], to: target) }
        let (arguments, risk) = try ActionExecutor.prepareAdvanced("device reboot", target: target)
        #expect(arguments.suffix(2) == ["--device", target.udid])
        #expect(risk == .highImpact)
    }

    @Test func catalogIsCompleteAndConsistent() throws {
        let ids = ActionCatalog.all.map(\.id)
        #expect(Set(ids).count == ids.count)
        for action in ActionCatalog.all {
            #expect(ActionCatalog.categories.contains(action.category), "\(action.id)")
            #expect(!action.kinds.isEmpty)
            #expect(!action.summary.isEmpty)
            if action.risk == .hostWrite {
                #expect(action.parameters.contains { $0.kind == .outputFile || $0.kind == .outputDirectory } || action.id == "host-ddis-update", "\(action.id) should ask where to save")
            }
        }
        #expect(ActionCatalog.all.filter { $0.risk == .highImpact }.map(\.id).sorted() == ["reboot", "sim-erase"])
        #expect(ActionCatalog.actions(for: .simulator).allSatisfy { $0.supports(.simulator) })
        #expect(!ActionCatalog.all.contains { $0.mechanism.localizedCaseInsensitiveContains("go-ios") || $0.mechanism.localizedCaseInsensitiveContains("ipsw") })
    }

    @Test func parameterValidation() throws {
        let file = ActionParameter(id: "o", label: "PNG file", help: "", kind: .outputFile, fileExtension: "png")
        #expect(throws: ToolkitError.self) { try file.validate("/nonexistent-folder/x.png") }
        #expect(throws: ToolkitError.self) { try file.validate(NSTemporaryDirectory() + "x.jpg") }
        #expect(try file.validate(NSTemporaryDirectory() + "unique-\(UUID().uuidString).png").hasSuffix(".png"))
        let url = ActionParameter(id: "u", label: "URL", help: "", kind: .url)
        #expect(try url.validate("https://example.com") == "https://example.com")
        #expect(try url.validate("myapp://open/item") == "myapp://open/item")
        #expect(throws: ToolkitError.self) { try url.validate("https://") }
        #expect(throws: ToolkitError.self) { try url.validate("not a url") }
        let pid = ActionParameter(id: "p", label: "Process ID", help: "", kind: .processIdentifier)
        #expect(throws: ToolkitError.self) { try pid.validate("-4") }
        let template = ActionParameter(id: "t", label: "Template", help: "", kind: .template, choices: ["A"])
        #expect(throws: ToolkitError.self) { try template.validate("B") }
    }

    @Test func instrumentsRequestsAreBounded() throws {
        let output = URL(fileURLWithPath: NSTemporaryDirectory()).appendingPathComponent("rec-\(UUID().uuidString).trace")
        let request = try InstrumentsRecorder.request(template: "Activity Monitor", target: target, durationSeconds: 5, output: output)
        #expect(request.arguments.starts(with: ["xctrace", "record"]))
        #expect(request.arguments.contains(target.udid))
        #expect(request.arguments.contains("5s"))
        #expect(throws: ToolkitError.self) { try InstrumentsRecorder.request(template: "Unknown", target: target, durationSeconds: 5, output: output) }
        #expect(throws: ToolkitError.self) { try InstrumentsRecorder.request(template: "Network", target: target, durationSeconds: 0, output: output) }
    }

    @Test func mvtArgumentsAndEnvironmentAreIsolated() throws {
        #expect(try MVTConnector.parseVersion("\u{1B}[1mMVT\u{1B}[0m\nVersion: 2.6.1\n") == "2.6.1")
        #expect(throws: ToolkitError.self) { try MVTConnector.parseVersion("nothing") }
        setenv("MVT_VT_API_KEY", "secret", 1)
        defer { unsetenv("MVT_VT_API_KEY") }
        let environment = MVTConnector.environment(configDirectory: URL(fileURLWithPath: "/tmp/cfg"), allowNetwork: false)
        #expect(environment["MVT_VT_API_KEY"] == nil)
        #expect(environment["MVT_NETWORK_ACCESS_ALLOWED"] == "false")
        #expect(environment["MVT_CONFIG_FOLDER"] == "/tmp/cfg")
        let request = MVTConnector.AnalysisRequest(executable: ValidatedExecutable(path: "/x/mvt-ios", sha256: "a", version: "1"), backup: URL(fileURLWithPath: "/b/backup"), output: URL(fileURLWithPath: "/b/out"), indicatorFiles: [URL(fileURLWithPath: "/i.stix2")], fast: true, hashes: false, allowNetwork: false)
        #expect(MVTConnector.arguments(for: request) == ["--disable-update-check", "--disable-indicator-update-check", "check-backup", "--output", "/b/out", "--fast", "--iocs", "/i.stix2", "/b/backup"])
    }

    @Test func mvtBackupResolutionRefusesEncryptedBackups() throws {
        let root = try SecureFileIO.makeTemporaryDirectory(prefix: "mvt")
        defer { try? FileManager.default.removeItem(at: root) }
        let backup = root.appendingPathComponent("00008110-X")
        try FileManager.default.createDirectory(at: backup, withIntermediateDirectories: true)
        try Data().write(to: backup.appendingPathComponent("Manifest.db"))
        try Data().write(to: backup.appendingPathComponent("Info.plist"))
        #expect(try MVTConnector.resolveBackup(root).lastPathComponent == "00008110-X")
        try PlistValue(dictionaryLiteral: ("IsEncrypted", true)).encoded().write(to: backup.appendingPathComponent("Manifest.plist"))
        #expect(throws: ToolkitError.self) { try MVTConnector.resolveBackup(backup) }
        #expect(throws: ToolkitError.self) { try MVTConnector.resolveBackup(root.appendingPathComponent("missing")) }
    }
}

@Suite("UFADE connector (stand-in checkout)")
struct UFADEConnectorTests {
    @Test func validationReportsTheDeveloperImageSubmodule() async throws {
        let checkout = try SecureFileIO.makeTemporaryDirectory(prefix: "ufade")
        defer { try? FileManager.default.removeItem(at: checkout) }
        try Data("import sys\nu_version = \"0.9.8\"\n".utf8).write(to: checkout.appendingPathComponent("ufade.py"))
        try Data("GNU GENERAL PUBLIC LICENSE\nVersion 3, 29 June 2007\n".utf8).write(to: checkout.appendingPathComponent("LICENSE"))
        try Data("pymobiledevice3\n".utf8).write(to: checkout.appendingPathComponent("requirements.txt"))
        // A stand-in interpreter: the runner answers for it, nothing is executed.
        let runner = ScriptedRunner()
        runner.reply = { request in request.displayName == "UFADE Python version" ? (0, "3.11.9\n") : (0, "") }

        // Cloned without --recurse-submodules: the submodule folder exists but is empty.
        try FileManager.default.createDirectory(at: checkout.appendingPathComponent("ufade_developer"), withIntermediateDirectories: true)
        let withoutImages = try await UFADEConnector.validate(checkout: checkout, python: "/usr/bin/true", runner: runner)
        #expect(withoutImages.ufadeVersion == "0.9.8")
        #expect(withoutImages.python.version == "3.11.9")
        #expect(!withoutImages.developerImagesAvailable)

        try FileManager.default.createDirectory(at: checkout.appendingPathComponent("ufade_developer/Developer"), withIntermediateDirectories: true)
        #expect(try await UFADEConnector.validate(checkout: checkout, python: "/usr/bin/true", runner: runner).developerImagesAvailable)
        #expect(UFADEConnector.submoduleCommand == "git submodule update --init --recursive")
    }
}
