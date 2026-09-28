import CryptoKit
import DeviceTestSupport
import Foundation
import Testing
@testable import DeviceKit
import ToolkitCore

// MARK: - Fixtures

/// A personalized image folder in Xcode's layout (Restore/BuildManifest.plist, image, trust cache).
struct PersonalizedFixture {
    static let chipID = 0xFFF1
    static let boardID = 0x0A
    let root: URL
    let image = Data((0..<3_000_000).map { UInt8(truncatingIfNeeded: $0 &* 31) })
    let trustCache = Data("trust-cache".utf8)

    init(productTypes: [String] = ["iPhone99,1"], flatLayout: Bool = false) throws {
        root = try SecureFileIO.makeTemporaryDirectory(prefix: "ddi-personalized")
        let restore = flatLayout ? root : root.appendingPathComponent("Restore", isDirectory: true)
        try FileManager.default.createDirectory(at: restore.appendingPathComponent("Firmware"), withIntermediateDirectories: true)
        let imageName = flatLayout ? "Image.dmg" : "001-00001-001.dmg"
        let trustName = flatLayout ? "Image.dmg.trustcache" : "Firmware/001-00001-001.dmg.trustcache"
        try image.write(to: restore.appendingPathComponent(imageName))
        try trustCache.write(to: restore.appendingPathComponent(trustName))
        let rules: PlistValue = [
            ["Actions": ["EPRO": false], "Conditions": ["ApCurrentProductionMode": false, "ApRequiresImage4": true]],
            ["Actions": ["EPRO": true], "Conditions": ["ApCurrentProductionMode": true, "ApRequiresImage4": true]],
            ["Actions": ["ESEC": true, "Ignored": 255], "Conditions": ["ApRawSecurityMode": true]],
        ]
        func entry(_ path: String, trusted: Bool) -> PlistValue {
            ["Digest": .data(Data(repeating: 0xAB, count: 48)), "Trusted": .boolean(trusted), "Info": ["Path": .string(path), "RestoreRequestRules": rules]]
        }
        // In the flat layout the manifest still names Xcode's file names; the library falls back to Image.dmg.
        let manifestImagePath = flatLayout ? "renamed.dmg" : imageName
        let manifestTrustPath = flatLayout ? "Firmware/renamed.trustcache" : trustName
        let identity: PlistValue = [
            "ApChipID": .string("0x" + String(Self.chipID, radix: 16)),
            "ApBoardID": .string("0x" + String(Self.boardID, radix: 16)),
            "Ap,ProductType": "iPhone99,1",
            "Info": ["Variant": "Customer iOS Developer PDI"],
            "Manifest": [
                "LoadableTrustCache": entry(manifestTrustPath, trusted: true),
                "PersonalizedDMG": entry(manifestImagePath, trusted: true),
                "Untrusted": entry("x", trusted: false),
            ],
        ]
        let other: PlistValue = [
            "ApChipID": "0x1", "ApBoardID": "0x2",
            "Manifest": ["LoadableTrustCache": entry(trustName, trusted: true), "PersonalizedDMG": entry(imageName, trusted: true)],
        ]
        let cryptexOnly: PlistValue = ["Manifest": ["Cryptex1,GenericDmg": entry("c.dmg", trusted: true)]]
        let manifest: PlistValue = [
            "ProductBuildVersion": "99A1",
            "SupportedProductTypes": .array(productTypes.map { .string($0) }),
            "BuildIdentities": [identity, other, cryptexOnly],
        ]
        try manifest.encoded(format: .xml).write(to: restore.appendingPathComponent("BuildManifest.plist"))
    }

    func remove() { try? FileManager.default.removeItem(at: root) }
}

/// A folder of legacy images in Xcode's DeviceSupport layout.
struct LegacyFixture {
    let root: URL
    let image = Data(repeating: 0x5A, count: 1_500_000)
    let signature = Data("legacy-signature".utf8)

    init(versions: [String]) throws {
        root = try SecureFileIO.makeTemporaryDirectory(prefix: "ddi-legacy")
        for version in versions {
            let folder = root.appendingPathComponent(version, isDirectory: true)
            try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
            try image.write(to: folder.appendingPathComponent("DeveloperDiskImage.dmg"))
            try signature.write(to: folder.appendingPathComponent("DeveloperDiskImage.dmg.signature"))
        }
    }

    func remove() { try? FileManager.default.removeItem(at: root) }
}

/// A stateful image mounter: it behaves like the device service, validating uploads and mounts.
final class FakeImageMounter: @unchecked Sendable {
    let lock = NSLock()
    var mounted: [DeveloperImageKind: Data] = [:]
    var storedManifests: [Data: Data] = [:]
    var uploads: [(kind: String, size: Int, signature: Data)] = []
    var mountRequests: [PlistValue] = []
    var mountError: (String, String)?
    let nonce = Data(repeating: 0x11, count: 32)

    func register(on server: FakeDeviceServer) {
        server.register(service: ImageMounter.serviceName) { [self] channel in
            let messages = PlistMessageConnection(channel: channel)
            while let request = try? await messages.receive(timeout: 5) {
                guard let reply = try await handle(request, channel: channel, messages: messages) else { return }
                try await messages.send(reply)
            }
        }
    }

    func state<T>(_ body: () -> T) -> T { lock.withLock(body) }

    func handle(_ request: PlistValue, channel: DeviceChannel, messages: PlistMessageConnection) async throws -> PlistValue? {
        switch request["Command"]?.stringValue {
        case "Hangup":
            return nil
        case "LookupImage":
            let kind = DeveloperImageKind(rawValue: request["ImageType"]?.stringValue ?? "")
            let signature = state { kind.flatMap { mounted[$0] } }
            return ["ImageSignature": .array(signature.map { [.data($0)] } ?? [])]
        case "CopyDevices":
            let entries = state { mounted.keys.map { kind -> PlistValue in ["MountPath": .string(kind.mountPath), "ImageType": .string(kind.rawValue)] } }
            return ["EntryList": .array(entries)]
        case "QueryPersonalizationIdentifiers":
            return ["PersonalizationIdentifiers": [
                "ChipID": .integer(Int64(PersonalizedFixture.chipID)),
                "BoardId": .integer(Int64(PersonalizedFixture.boardID)),
                "UniqueChipID": .integer(0x1234_5678_9ABC),
                "Ap,OSLongVersion": "99.0",
            ]]
        case "QueryNonce":
            return ["PersonalizationNonce": .data(nonce)]
        case "QueryPersonalizationManifest":
            let digest = request["ImageSignature"]?.dataValue ?? Data()
            if let manifest = state({ storedManifests[digest] }) { return ["ImageSignature": .data(manifest)] }
            return ["Error": "MissingManifest"]
        case "ReceiveBytes":
            let size = request["ImageSize"]?.intValue ?? 0
            try await messages.send(["Status": "ReceiveBytesAck"])
            _ = try await channel.read(exactly: size, timeout: 10)
            state { uploads.append((request["ImageType"]?.stringValue ?? "", size, request["ImageSignature"]?.dataValue ?? Data())) }
            return ["Status": "Complete"]
        case "MountImage":
            if let (error, detail) = state({ mountError }) { return ["Error": .string(error), "DetailedError": .string(detail)] }
            guard let kind = DeveloperImageKind(rawValue: request["ImageType"]?.stringValue ?? ""), let signature = request["ImageSignature"]?.dataValue else {
                return ["Error": "MissingImageType"]
            }
            let uploaded = state { uploads.last }
            guard uploaded?.signature == signature else { return ["Error": "ImageMountFailed", "DetailedError": "signature mismatch"] }
            if kind == .personalized && request["ImageTrustCache"]?.dataValue == nil { return ["Error": "ImageMountFailed", "DetailedError": "missing trust cache"] }
            if state({ mounted[kind] != nil }) { return ["Error": "ImageMountFailed", "DetailedError": "Image is already mounted"] }
            state {
                mounted[kind] = signature
                mountRequests.append(request)
            }
            return ["Status": "Complete"]
        case "UnmountImage":
            let path = request["MountPath"]?.stringValue
            let removed: Bool = state {
                guard let kind = mounted.keys.first(where: { $0.mountPath == path }) else { return false }
                mounted[kind] = nil
                return true
            }
            return removed ? ["Status": "Complete"] : ["Error": "InternalError", "DetailedError": .string("There is no matching entry in the device map for \(path ?? "")")]
        default:
            return ["Error": "UnknownCommand"]
        }
    }
}

/// Records personalization requests and answers like Apple's server.
final class FakeTSS: PersonalizationTransport, @unchecked Sendable {
    let lock = NSLock()
    var requests: [PlistValue] = []
    let ticket = Data("apple-img4-ticket".utf8)
    var reply: String?

    func send(_ body: Data) async throws -> Data {
        lock.withLock { requests.append((try? PlistValue.decode(body)) ?? .dictionary([:])) }
        if let reply { return Data(reply.utf8) }
        let plist = try PlistValue.dictionary(["ApImg4Ticket": .data(ticket)]).encoded(format: .xml)
        return Data("STATUS=0&MESSAGE=SUCCESS&REQUEST_STRING=".utf8) + plist
    }
}

/// Host locations with nothing installed, so tests see only their fixtures (CI runners have older
/// Xcode versions whose legacy images would otherwise be found first).
let isolatedHostLocations = DeveloperImageHostLocations(
    xcodePersonalizedImage: URL(fileURLWithPath: "/nonexistent/idt-tests/iOS_DDI", isDirectory: true),
    applications: URL(fileURLWithPath: "/nonexistent/idt-tests", isDirectory: true)
)

private func withDevice(version: String, developerMode: Bool = true, _ body: (FakeDeviceServer, FakeImageMounter) async throws -> Void) async throws {
    let server = try FakeDeviceServer()
    server.lockdownValues = [
        "ProductVersion": .string(version), "BuildVersion": "99A1", "ProductType": "iPhone99,1",
        "CPUArchitecture": "arm64e", "HardwareModel": "D99AP",
        "ChipID": .integer(Int64(PersonalizedFixture.chipID)), "BoardId": .integer(Int64(PersonalizedFixture.boardID)),
    ]
    server.domainValues["com.apple.security.mac.amfi"] = ["DeveloperModeStatus": .boolean(developerMode)]
    let mounter = FakeImageMounter()
    mounter.register(on: server)
    try await server.start()
    do { try await body(server, mounter) } catch {
        await server.stop()
        throw error
    }
    await server.stop()
}

// MARK: - Tests

@Suite("Developer image library")
struct DeveloperImageLibraryTests {
    @Test func readsPersonalizedImagesInXcodeAndFlatLayouts() throws {
        for flat in [false, true] {
            let fixture = try PersonalizedFixture(flatLayout: flat)
            defer { fixture.remove() }
            let source = try DeveloperImageLibrary.personalizedSource(at: fixture.root, origin: .userFolder)
            #expect(source.buildVersion == "99A1")
            #expect(source.identities.count == 2, "the Cryptex-only identity is not a personalized DMG")
            let identity = try #require(source.identity(chipID: PersonalizedFixture.chipID, boardID: PersonalizedFixture.boardID))
            #expect(source.supports(productType: "iPhone99,1") == true)
            #expect(source.supports(productType: "iPhone1,1") == false)
            let files = try source.files(for: identity)
            #expect(try Data(contentsOf: files.image) == fixture.image)
            #expect(try Data(contentsOf: files.trustCache) == fixture.trustCache)
        }
    }

    @Test func rejectsFoldersWithoutAnImageAndSymbolicLinks() throws {
        let empty = try SecureFileIO.makeTemporaryDirectory(prefix: "ddi-empty")
        defer { try? FileManager.default.removeItem(at: empty) }
        #expect(throws: ToolkitError.self) { try DeveloperImageLibrary.personalizedSource(at: empty, origin: .userFolder) }
        let target = empty.appendingPathComponent("real")
        try Data("x".utf8).write(to: target)
        let link = empty.appendingPathComponent("link")
        try FileManager.default.createSymbolicLink(at: link, withDestinationURL: target)
        #expect(throws: ToolkitError.self) { try DeveloperImageLibrary.readValidatedFile(link, limit: 10) }
        #expect(throws: ToolkitError.self) { try DeveloperImageLibrary.readValidatedFile(target, limit: 0) }
    }

    @Test func findsLegacyImagesByExactVersion() throws {
        let fixture = try LegacyFixture(versions: ["15.5", "16.4 (20E247)", "not-a-version"])
        defer { fixture.remove() }
        let sources = DeveloperImageLibrary.legacySources(userFolders: [fixture.root], applications: fixture.root)
        #expect(Set(sources.map(\.version)) == ["15.5", "16.4"])
        #expect(DeveloperImageLibrary.legacyImage(forVersion: "16.4.1", in: sources)?.version == "16.4")
        #expect(DeveloperImageLibrary.legacyImage(forVersion: "16.3", in: sources) == nil)
        // A folder that holds the image directly takes its version from the folder name.
        let direct = DeveloperImageLibrary.legacySources(userFolders: [fixture.root.appendingPathComponent("15.5")], applications: fixture.root)
        #expect(direct.map(\.version) == ["15.5"])
        #expect(DeveloperImageLibrary.majorMinor("16") == "16.0")
        #expect(DeveloperImageLibrary.majorMinor("iPhone") == nil)
    }

    /// Legacy images shipped by older Xcode versions installed on this machine (for example on CI
    /// runners) are found with their version, and their files are readable.
    @Test(.enabled(if: !DeveloperImageLibrary.legacySources().isEmpty))
    func installedLegacyImagesAreUsable() throws {
        let sources = DeveloperImageLibrary.legacySources()
        for source in sources {
            #expect(DeveloperImageLibrary.majorMinor(source.version) == source.version)
            #expect(source.origin == .xcode)
            #expect(try DeveloperImageLibrary.readImage(source.image).count > 1_000_000)
            #expect(try DeveloperImageLibrary.readSmallFile(source.signature).count > 0)
        }
        let first = try #require(sources.first)
        #expect(DeveloperImageLibrary.legacyImage(forVersion: first.version + ".1", in: sources)?.version == first.version)
    }

    /// The image Xcode installs on this Mac has a build identity for current iPhones.
    @Test(.enabled(if: FileManager.default.fileExists(atPath: DeveloperImageLibrary.xcodePersonalizedImage.appendingPathComponent("Restore/BuildManifest.plist").path)))
    func xcodeInstalledImageIsUsable() throws {
        let source = try DeveloperImageLibrary.personalizedSource(at: DeveloperImageLibrary.xcodePersonalizedImage, origin: .xcode)
        #expect(source.identities.count > 50)
        #expect(source.supportedProductTypes.contains("iPhone18,1"))
        let identity = try #require(source.identities.first { $0.productType == "iPhone18,1" })
        let files = try source.files(for: identity)
        #expect(try DeveloperImageLibrary.readImage(files.image).count > 1_000_000)
        #expect(try DeveloperImageLibrary.readSmallFile(files.trustCache).count > 0)
        let request = ImagePersonalization.request(identity: identity, identifiers: PersonalizationIdentifiers(chipID: identity.chipID, boardID: identity.boardID, ecid: 1), nonce: Data(count: 32))
        #expect(request["PersonalizedDMG"]?["Digest"]?.dataValue?.count == 48)
        #expect(request["LoadableTrustCache"] != nil)
    }
}

@Suite("Developer image personalization")
struct ImagePersonalizationTests {
    @Test func buildsTheSigningRequest() throws {
        let fixture = try PersonalizedFixture()
        defer { fixture.remove() }
        let source = try DeveloperImageLibrary.personalizedSource(at: fixture.root, origin: .userFolder)
        let identity = try #require(source.identity(chipID: PersonalizedFixture.chipID, boardID: PersonalizedFixture.boardID))
        let identifiers = PersonalizationIdentifiers(chipID: PersonalizedFixture.chipID, boardID: PersonalizedFixture.boardID, ecid: 0xDEAD_BEEF, additional: ["Ap,OSLongVersion": "99.0"])
        let nonce = Data(repeating: 7, count: 32)
        let request = ImagePersonalization.request(identity: identity, identifiers: identifiers, nonce: nonce, requestID: UUID(uuidString: "00000000-0000-0000-0000-000000000001")!)
        #expect(request["ApChipID"]?.intValue == PersonalizedFixture.chipID)
        #expect(request["ApBoardID"]?.intValue == PersonalizedFixture.boardID)
        #expect(request["ApECID"]?.intValue == 0xDEAD_BEEF)
        #expect(request["ApNonce"]?.dataValue == nonce)
        #expect(request["SepNonce"]?.dataValue == Data(count: 20))
        #expect(request["@ApImg4Ticket"]?.boolValue == true)
        #expect(request["@UUID"]?.stringValue == "00000000-0000-0000-0000-000000000001")
        #expect(request["Ap,OSLongVersion"]?.stringValue == "99.0")
        #expect(request["Untrusted"] == nil, "untrusted components are not signed")
        let dmg = try #require(request["PersonalizedDMG"])
        #expect(dmg["Info"] == nil)
        #expect(dmg["Digest"]?.dataValue?.count == 48)
        #expect(dmg["EPRO"]?.boolValue == true, "production-mode rule applied")
        #expect(dmg["ESEC"]?.boolValue == true, "security-mode rule applied")
        #expect(dmg["Ignored"] == nil, "255 means leave unchanged")
    }

    @Test func parsesAppleRepliesIntoTicketsOrClearErrors() throws {
        let plist = try PlistValue.dictionary(["ApImg4Ticket": .data(Data([1, 2, 3]))]).encoded(format: .xml)
        #expect(try ImagePersonalization.ticket(fromResponse: Data("STATUS=0&MESSAGE=SUCCESS&REQUEST_STRING=".utf8) + plist) == Data([1, 2, 3]))
        do {
            _ = try ImagePersonalization.ticket(fromResponse: Data("STATUS=94&MESSAGE=This device isn't eligible for the requested build.".utf8))
            Issue.record("expected a rejection")
        } catch let error as ToolkitError {
            #expect(error.message.contains("would not sign"))
            #expect(error.technicalDetail?.contains("STATUS=94") == true)
        }
        #expect(throws: ToolkitError.self) { try ImagePersonalization.ticket(fromResponse: Data("<html>".utf8)) }
        let noTicket = try PlistValue.dictionary(["Other": "x"]).encoded(format: .xml)
        #expect(throws: ToolkitError.self) { try ImagePersonalization.ticket(fromResponse: Data("STATUS=0&MESSAGE=SUCCESS&REQUEST_STRING=".utf8) + noTicket) }
    }

    @Test func identifiersRequireChipBoardAndECID() throws {
        #expect(throws: ToolkitError.self) { try PersonalizationIdentifiers(["ChipID": 1]) }
        let parsed = try PersonalizationIdentifiers(["ChipID": 0x8150, "BoardId": 4, "UniqueChipID": 99, "Ap,X": "y", "Other": 1])
        #expect(parsed.chipID == 0x8150 && parsed.boardID == 4 && parsed.ecid == 99)
        #expect(Array(parsed.additional.keys) == ["Ap,X"])
    }
}

@Suite("Developer image state")
struct DeveloperImageEvaluatorTests {
    let modern = DeveloperImageDeviceFacts(productVersion: "26.1", productType: "iPhone99,1", chipID: PersonalizedFixture.chipID, boardID: PersonalizedFixture.boardID, developerModeEnabled: true)
    let legacy = DeveloperImageDeviceFacts(productVersion: "15.5", productType: "iPhone99,1")

    func host(personalized: Bool = false, productTypes: [String] = ["iPhone99,1"], legacyVersions: [String] = [], coreDevice: Bool = false) throws -> (DeveloperImageHostInventory, [() -> Void]) {
        var cleanups: [() -> Void] = []
        var sources: [PersonalizedImageSource] = []
        if personalized {
            let fixture = try PersonalizedFixture(productTypes: productTypes)
            cleanups.append(fixture.remove)
            sources.append(try DeveloperImageLibrary.personalizedSource(at: fixture.root, origin: .userFolder))
        }
        var legacySources: [LegacyImageSource] = []
        if !legacyVersions.isEmpty {
            let fixture = try LegacyFixture(versions: legacyVersions)
            cleanups.append(fixture.remove)
            legacySources = DeveloperImageLibrary.legacySources(userFolders: [fixture.root], applications: fixture.root)
        }
        return (DeveloperImageHostInventory(personalized: sources, legacy: legacySources, coreDeviceAvailable: coreDevice), cleanups)
    }

    func state(_ facts: DeveloperImageDeviceFacts, _ observation: DeveloperImageObservation = DeveloperImageObservation(), _ host: DeveloperImageHostInventory) -> DeveloperImageState {
        DeveloperImageEvaluator.evaluate(facts: facts, observation: observation, host: host).state
    }

    @Test func coversEveryState() throws {
        let (withImage, c1) = try host(personalized: true)
        let (unlisted, c2) = try host(personalized: true, productTypes: ["iPhone1,1"])
        let (withLegacy, c3) = try host(legacyVersions: ["15.5"])
        let (otherLegacy, c4) = try host(legacyVersions: ["14.8", "16.4"])
        defer { (c1 + c2 + c3 + c4).forEach { $0() } }
        let empty = DeveloperImageHostInventory()

        #expect(state(modern, DeveloperImageObservation(mountedSignatures: [Data([1])]), empty) == .mounted)
        #expect(state(modern, DeveloperImageObservation(otherKindMounted: true), withImage) == .incompatible)
        var off = modern
        off.developerModeEnabled = false
        #expect(state(off, DeveloperImageObservation(), withImage) == .blocked)
        #expect(state(modern, DeveloperImageObservation(manifestOnDevice: true), withImage) == .available)
        #expect(state(modern, DeveloperImageObservation(manifestOnDevice: false), withImage) == .personalizationRequired)
        #expect(state(modern, DeveloperImageObservation(), unlisted) == .incompatible)
        var otherChip = modern
        otherChip.chipID = 0x1234
        #expect(state(otherChip, DeveloperImageObservation(), withImage) == .incompatible)
        #expect(state(modern, DeveloperImageObservation(), DeveloperImageHostInventory(coreDeviceAvailable: true)) == .personalizationRequired)
        #expect(state(modern, DeveloperImageObservation(), empty) == .missing)
        #expect(state(legacy, DeveloperImageObservation(), withLegacy) == .available)
        #expect(state(legacy, DeveloperImageObservation(), otherLegacy) == .incompatible)
        #expect(state(legacy, DeveloperImageObservation(), empty) == .missing)
        // iOS 15 has no Developer Mode; an unknown value must not block it.
        #expect(legacy.developerModeEnabled == nil)

        let missing = DeveloperImageEvaluator.evaluate(facts: modern, observation: DeveloperImageObservation(), host: empty)
        #expect(missing.remediation?.contains("Install Xcode") == true)
        let personalization = DeveloperImageEvaluator.evaluate(facts: modern, observation: DeveloperImageObservation(), host: withImage)
        #expect(personalization.recommendedMechanism == .native)
        #expect(personalization.explanation.contains("internet"))
        #expect(DeveloperImageKind.required(forMajorVersion: 16) == .legacy)
        #expect(DeveloperImageKind.required(forMajorVersion: 17) == .personalized)
        #expect(DeveloperImageKind.required(forMajorVersion: nil) == .personalized)
    }

    @Test func mapsMounterErrorsToPlainLanguage() {
        func classify(_ error: String, _ detail: String = "") -> ImageMounter.Failure {
            ImageMounter.classify(["Error": .string(error), "DetailedError": .string(detail)])
        }
        #expect(classify("DeviceLocked") == .deviceLocked)
        #expect(classify("ImageMountFailed", "Developer mode is not enabled.") == .developerModeDisabled)
        #expect(classify("ImageMountFailed", "Image is already mounted") == .alreadyMounted)
        #expect(classify("InternalError", "There is no matching entry in the device map") == .notMounted)
        #expect(classify("UnknownCommand") == .unsupported)
        #expect(classify("ImageMountFailed", "Failed to verify the personalization manifest") == .signatureRejected)
        #expect(classify("SomethingElse") == .other)
        let error = ImageMounter.error(for: .deviceLocked, reply: ["Error": "DeviceLocked"], operation: "mount the developer image")
        #expect(error.kind == .deviceLocked)
        #expect(error.message == "The device is locked.")
        #expect(error.technicalDetail?.contains("DeviceLocked") == true)
        #expect(!ImageMounter.error(for: .other, reply: ["Error": "X"], operation: "mount the developer image").message.contains("X"))
    }
}

@Suite("Developer image mounting (fake device)", .serialized)
struct DeveloperImageMountTests {
    @Test func personalizesUploadsAndMountsOnceOnIOS17AndLater() async throws {
        let fixture = try PersonalizedFixture()
        defer { fixture.remove() }
        try await withDevice(version: "26.1") { server, mounter in
            let tss = FakeTSS()
            let manager = DeveloperImageManager(usbmux: server.client, transport: tss, locations: isolatedHostLocations)
            let folders = [fixture.root]

            let before = await manager.status(for: server.target, userFolders: folders)
            #expect(before.state == .personalizationRequired, "\(before.headline) \(before.technicalDetail ?? "")")
            #expect(before.facts?.chipID == PersonalizedFixture.chipID)
            #expect(before.facts?.architecture == "arm64e")
            #expect(before.recommendedMechanism == .native)

            let after = try await manager.mount(server.target, userFolders: folders)
            #expect(after.state == .mounted)
            let request = try #require(tss.requests.first)
            #expect(request["ApNonce"]?.dataValue == mounter.nonce)
            #expect(request["ApECID"]?.intValue == 0x1234_5678_9ABC)
            #expect(mounter.uploads.count == 1)
            #expect(mounter.uploads.first?.kind == "Personalized")
            #expect(mounter.uploads.first?.size == fixture.image.count)
            #expect(mounter.uploads.first?.signature == tss.ticket)
            #expect(mounter.mountRequests.first?["ImageTrustCache"]?.dataValue == fixture.trustCache)

            // Already mounted: nothing is uploaded or signed again.
            let again = try await manager.mount(server.target, userFolders: folders)
            #expect(again.state == .mounted)
            #expect(mounter.uploads.count == 1 && tss.requests.count == 1)

            // Unmount, then mount again using the manifest the device kept: no Apple request.
            let unmounted = try await manager.unmount(server.target, userFolders: folders)
            #expect(unmounted.state == .personalizationRequired)
            mounter.state { mounter.storedManifests[Data(SHA384.hash(data: fixture.image))] = tss.ticket }
            #expect(await manager.status(for: server.target, userFolders: folders).state == .available)
            _ = try await manager.mount(server.target, userFolders: folders)
            #expect(tss.requests.count == 1, "the stored personalization was reused")
            #expect(mounter.uploads.count == 2)
        }
    }

    @Test func mountsLegacyImagesWithoutPersonalization() async throws {
        let fixture = try LegacyFixture(versions: ["15.5"])
        defer { fixture.remove() }
        try await withDevice(version: "15.5.1") { server, mounter in
            let tss = FakeTSS()
            let manager = DeveloperImageManager(usbmux: server.client, transport: tss, locations: isolatedHostLocations)
            let status = await manager.status(for: server.target, userFolders: [fixture.root])
            #expect(status.state == .available)
            #expect(status.requiredKind == .legacy)
            let mounted = try await manager.mount(server.target, userFolders: [fixture.root])
            #expect(mounted.state == .mounted)
            #expect(mounter.uploads.first?.kind == "Developer")
            #expect(mounter.uploads.first?.signature == fixture.signature)
            #expect(tss.requests.isEmpty)
            _ = try await manager.unmount(server.target, userFolders: [fixture.root])
            #expect(mounter.state { mounter.mounted.isEmpty })
        }
    }

    @Test func reportsBlockedMissingAndDeviceErrors() async throws {
        let fixture = try PersonalizedFixture()
        defer { fixture.remove() }
        try await withDevice(version: "26.1", developerMode: false) { server, mounter in
            let manager = DeveloperImageManager(usbmux: server.client, transport: FakeTSS(), locations: isolatedHostLocations)
            let status = await manager.status(for: server.target, userFolders: [fixture.root])
            #expect(status.state == .blocked)
            #expect(status.headline == "Developer Mode is off.")
            await #expect(throws: ToolkitError.self) { _ = try await manager.mount(server.target, userFolders: [fixture.root]) }
            #expect(mounter.uploads.isEmpty)
        }
        try await withDevice(version: "16.4") { server, _ in
            let manager = DeveloperImageManager(usbmux: server.client, transport: FakeTSS(), locations: isolatedHostLocations)
            let status = await manager.status(for: server.target, userFolders: [])
            // This Mac may have Xcode's modern image, but never a 16.4 legacy image in these folders.
            #expect(status.state == .missing || status.state == .incompatible)
            await #expect(throws: ToolkitError.self) { _ = try await manager.mount(server.target, userFolders: []) }
        }
        try await withDevice(version: "26.1") { server, mounter in
            mounter.mountError = ("DeviceLocked", "The device is locked")
            let manager = DeveloperImageManager(usbmux: server.client, transport: FakeTSS(), locations: isolatedHostLocations)
            do {
                _ = try await manager.mount(server.target, userFolders: [fixture.root])
                Issue.record("expected a locked-device error")
            } catch let error as ToolkitError {
                #expect(error.kind == .deviceLocked)
                #expect(error.recovery?.contains("Unlock") == true)
            }
        }
    }

    @Test func usesImageMounterIdentifiersWhenLockdownOmitsThem() async throws {
        let fixture = try PersonalizedFixture()
        defer { fixture.remove() }
        try await withDevice(version: "26.1") { server, _ in
            server.lockdownValues["ChipID"] = nil
            server.lockdownValues["BoardId"] = nil
            let status = await DeveloperImageManager(usbmux: server.client, transport: FakeTSS(), locations: isolatedHostLocations).status(for: server.target, userFolders: [fixture.root])
            #expect(status.state == .personalizationRequired)
            #expect(status.facts?.chipID == PersonalizedFixture.chipID)
            #expect(status.facts?.boardID == PersonalizedFixture.boardID)
        }
    }

    @Test func simulatorsDoNotNeedAnImage() async {
        let simulator = DeviceTarget(kind: .simulator, udid: "SIM", name: "Sim", osVersion: "26.0", usbmuxDeviceID: nil, coreDeviceIdentifier: nil, transport: .local)
        #expect(await DeveloperImageManager().status(for: simulator).state == .notRequired)
        let networkOnly = DeviceTarget(kind: .physical, udid: "X", name: "Phone", osVersion: "26.0", usbmuxDeviceID: nil, coreDeviceIdentifier: nil, transport: .network)
        #expect(await DeveloperImageManager().status(for: networkOnly).state == .blocked)
    }
}
