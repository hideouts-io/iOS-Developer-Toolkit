// Generates an SPDX 2.3 JSON SBOM for a release from Package.resolved.
//
//   swift scripts/generate-sbom.swift <Package.resolved> <checkouts-dir> <version> <commit> <output.spdx.json>
//
// Licenses are read from each dependency's checked-out LICENSE file (Apache-2.0 or MIT), with
// the bundled BoringSSL licenses added for swift-nio-ssl. Anything unrecognized is recorded as
// NOASSERTION rather than guessed.

import Foundation

let arguments = CommandLine.arguments
guard arguments.count == 6 else {
    FileHandle.standardError.write(Data("usage: generate-sbom.swift <Package.resolved> <checkouts-dir> <version> <commit> <output>\n".utf8))
    exit(64)
}
let resolvedURL = URL(fileURLWithPath: arguments[1])
let checkouts = URL(fileURLWithPath: arguments[2], isDirectory: true)
let version = arguments[3]
let commit = arguments[4]
let outputURL = URL(fileURLWithPath: arguments[5])

struct Resolved: Decodable {
    struct Pin: Decodable {
        struct State: Decodable { var revision: String; var version: String? }
        var identity: String
        var location: String
        var state: State
    }
    var pins: [Pin]
}

func fail(_ message: String) -> Never {
    FileHandle.standardError.write(Data("generate-sbom: \(message)\n".utf8))
    exit(1)
}

func declaredLicense(identity: String) -> String {
    let directory = checkouts.appendingPathComponent(identity, isDirectory: true)
    let names = (try? FileManager.default.contentsOfDirectory(atPath: directory.path)) ?? []
    guard let name = names.first(where: { $0.uppercased().hasPrefix("LICENSE") }),
          let text = try? String(contentsOf: directory.appendingPathComponent(name), encoding: .utf8) else {
        return "NOASSERTION"
    }
    var license: String
    if text.contains("Apache License") && text.contains("Version 2.0") {
        license = "Apache-2.0"
    } else if text.contains("Permission is hereby granted, free of charge") {
        license = "MIT"
    } else {
        return "NOASSERTION"
    }
    if identity == "swift-nio-ssl" {
        // swift-nio-ssl vendors BoringSSL (see its NOTICE.txt).
        license += " AND ISC AND OpenSSL"
    }
    return license
}

func purl(location: String, version: String?) -> String? {
    guard let url = URL(string: location), let host = url.host else { return nil }
    var path = url.path
    if path.hasSuffix(".git") { path.removeLast(4) }
    return "pkg:swift/\(host)\(path)" + (version.map { "@\($0)" } ?? "")
}

guard let data = try? Data(contentsOf: resolvedURL),
      let resolved = try? JSONDecoder().decode(Resolved.self, from: data) else {
    fail("could not read \(resolvedURL.path)")
}

let appID = "SPDXRef-Package-iOSDeveloperToolkit"
var packages: [[String: Any]] = [[
    "SPDXID": appID,
    "name": "iOS Developer Toolkit",
    "versionInfo": version,
    "downloadLocation": "git+https://github.com/hideouts-io/iOS-Developer-Toolkit.git@\(commit)",
    "licenseConcluded": "MIT",
    "licenseDeclared": "MIT",
    "copyrightText": "Copyright (c) 2026 hideouts-io",
    "filesAnalyzed": false,
    "primaryPackagePurpose": "APPLICATION",
    "supplier": "Organization: hideouts-io",
]]
var relationships: [[String: String]] = [[
    "spdxElementId": "SPDXRef-DOCUMENT",
    "relationshipType": "DESCRIBES",
    "relatedSpdxElement": appID,
]]

for pin in resolved.pins.sorted(by: { $0.identity < $1.identity }) {
    let id = "SPDXRef-Package-" + pin.identity.map { $0.isLetter || $0.isNumber || $0 == "." ? String($0) : "-" }.joined()
    var package: [String: Any] = [
        "SPDXID": id,
        "name": pin.identity,
        "versionInfo": pin.state.version ?? pin.state.revision,
        "downloadLocation": "git+\(pin.location)@\(pin.state.revision)",
        "licenseConcluded": "NOASSERTION",
        "licenseDeclared": declaredLicense(identity: pin.identity),
        "copyrightText": "NOASSERTION",
        "filesAnalyzed": false,
        "primaryPackagePurpose": "LIBRARY",
    ]
    if let locator = purl(location: pin.location, version: pin.state.version) {
        package["externalRefs"] = [["referenceCategory": "PACKAGE-MANAGER", "referenceType": "purl", "referenceLocator": locator]]
    }
    packages.append(package)
    relationships.append(["spdxElementId": appID, "relationshipType": "DEPENDS_ON", "relatedSpdxElement": id])
}

let formatter = ISO8601DateFormatter()
formatter.formatOptions = [.withInternetDateTime]
let document: [String: Any] = [
    "spdxVersion": "SPDX-2.3",
    "dataLicense": "CC0-1.0",
    "SPDXID": "SPDXRef-DOCUMENT",
    "name": "iOS-Developer-Toolkit-\(version)",
    "documentNamespace": "https://github.com/hideouts-io/iOS-Developer-Toolkit/spdx/\(version)/\(commit)",
    "creationInfo": [
        "created": formatter.string(from: Date()),
        "creators": ["Tool: scripts/generate-sbom.swift", "Organization: hideouts-io"],
    ],
    "packages": packages,
    "relationships": relationships,
]

do {
    let json = try JSONSerialization.data(withJSONObject: document, options: [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes])
    try json.write(to: outputURL, options: .withoutOverwriting)
} catch {
    fail("could not write \(outputURL.path): \(error.localizedDescription)")
}
