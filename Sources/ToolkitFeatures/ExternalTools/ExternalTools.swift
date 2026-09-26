import Foundation
import ToolkitCore

/// A user-selected external executable, pinned by SHA-256 at validation time.
public struct ValidatedExecutable: Sendable, Hashable, Codable {
    public var path: String
    public var sha256: String
    public var version: String

    /// Confirms the file has not changed since it was validated.
    public func revalidate() throws {
        let url = URL(fileURLWithPath: path)
        try ExecutableValidator.validate(url)
        guard try SecureFileIO.sha256(of: url) == sha256 else {
            throw ToolkitError(.permissionDenied, message: "\(url.lastPathComponent) changed after it was validated.", recovery: "Validate the installation again before running it.")
        }
    }
}

enum ExternalToolSupport {
    static func resolveExecutable(_ path: String, label: String) throws -> URL {
        let expanded = (path as NSString).expandingTildeInPath
        guard expanded.hasPrefix("/") else { throw ToolkitError.invalidInput("\(label) must be an absolute path.") }
        let url = URL(fileURLWithPath: expanded).resolvingSymlinksInPath()
        try ExecutableValidator.validate(url)
        return url
    }

    static func stripANSI(_ text: String) -> String {
        text.replacingOccurrences(of: #"\u001B\[[0-?]*[ -/]*[@-~]"#, with: "", options: .regularExpression)
    }

    static func discover(named name: String, extra: [String]) -> [String] {
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        let candidates = ["/opt/homebrew/bin/\(name)", "/usr/local/bin/\(name)", "\(home)/.local/bin/\(name)"] + extra
        var seen = Set<String>()
        return candidates.compactMap { path in
            guard FileManager.default.isExecutableFile(atPath: path) else { return nil }
            let resolved = URL(fileURLWithPath: path).resolvingSymlinksInPath().path
            return seen.insert(resolved).inserted ? resolved : nil
        }
    }
}

// MARK: - MVT (Mobile Verification Toolkit)

/// A consented, isolated handoff to a separately installed `mvt-ios`.
public enum MVTConnector {
    public static let repositoryURL = URL(string: "https://github.com/mvt-project/mvt")!
    public static let backupGuideURL = URL(string: "https://docs.mvt.re/en/latest/ios/backup/check/")!
    public static let setupCommands = ["brew install python3 pipx sqlite3", "pipx ensurepath", "pipx install mvt"]
    /// Variables that could supply a password, indicators, or an API key implicitly.
    public static let environmentKeysToRemove = ["MVT_ANDROID_BACKUP_PASSWORD", "MVT_HASH_FILES", "MVT_IOS_BACKUP_PASSWORD", "MVT_PROFILE", "MVT_STIX2", "MVT_VT_API_KEY"]

    public static func discover() -> [String] {
        ExternalToolSupport.discover(named: "mvt-ios", extra: [])
    }

    public static func validate(executablePath: String, runner: CommandRunning) async throws -> ValidatedExecutable {
        let url = try ExternalToolSupport.resolveExecutable(executablePath, label: "The mvt-ios path")
        let result = try await runner.run(CommandRequest(executable: url, arguments: ["--disable-update-check", "--disable-indicator-update-check", "version"], environment: environment(configDirectory: nil, allowNetwork: false), timeout: 60, displayName: "mvt-ios version"))
        guard result.succeeded else {
            throw ToolkitError(.commandFailed, message: "mvt-ios did not report its version.", recovery: "Reinstall MVT with pipx and try again.", technicalDetail: result.technicalSummary)
        }
        return ValidatedExecutable(path: url.path, sha256: try SecureFileIO.sha256(of: url), version: try parseVersion(result.standardOutputText + result.standardErrorText))
    }

    public static func parseVersion(_ output: String) throws -> String {
        let text = ExternalToolSupport.stripANSI(output)
        guard let range = text.range(of: #"(?im)^\s*Version:\s*([A-Za-z0-9][A-Za-z0-9._+-]*)\s*$"#, options: .regularExpression) else {
            throw ToolkitError(.commandFailed, message: "The mvt-ios version output was not recognized.")
        }
        return text[range].components(separatedBy: ":").last?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
    }

    /// Resolves a decrypted iTunes-style backup (a folder with Manifest.db and Info.plist, or a
    /// parent containing exactly one such folder). Encrypted backups are refused.
    public static func resolveBackup(_ url: URL) throws -> URL {
        func isBackup(_ folder: URL) -> Bool {
            FileManager.default.fileExists(atPath: folder.appendingPathComponent("Manifest.db").path)
                && FileManager.default.fileExists(atPath: folder.appendingPathComponent("Info.plist").path)
        }
        var backup = url.standardizedFileURL
        if !isBackup(backup) {
            let children = ((try? FileManager.default.contentsOfDirectory(at: backup, includingPropertiesForKeys: [.isDirectoryKey])) ?? []).filter(isBackup)
            guard children.count == 1, let only = children.first else {
                throw ToolkitError.invalidInput(children.isEmpty ? "No backup (a folder with Manifest.db and Info.plist) was found there." : "Several backups were found; choose one backup folder.")
            }
            backup = only
        }
        let manifest = backup.appendingPathComponent("Manifest.plist")
        if let data = try? Data(contentsOf: manifest), let plist = try? PlistValue.decode(data), plist["IsEncrypted"]?.boolValue == true {
            throw ToolkitError(.unsupported, message: "This backup is encrypted.", recovery: "Decrypt a protected working copy with MVT outside the toolkit (see MVT's backup guide), then choose that copy. The toolkit never asks for backup passwords.")
        }
        return backup
    }

    public struct AnalysisRequest: Sendable {
        public var executable: ValidatedExecutable
        public var backup: URL
        public var output: URL
        public var indicatorFiles: [URL]
        public var fast: Bool
        public var hashes: Bool
        public var allowNetwork: Bool

        public init(executable: ValidatedExecutable, backup: URL, output: URL, indicatorFiles: [URL], fast: Bool, hashes: Bool, allowNetwork: Bool) {
            self.executable = executable
            self.backup = backup
            self.output = output
            self.indicatorFiles = indicatorFiles
            self.fast = fast
            self.hashes = hashes
            self.allowNetwork = allowNetwork
        }
    }

    public static func validate(_ request: AnalysisRequest) throws {
        try request.executable.revalidate()
        guard !FileManager.default.fileExists(atPath: request.output.path) else {
            throw ToolkitError.invalidInput("The result folder already exists. Choose a new name so results cannot mix with an earlier run.")
        }
        let backupPath = request.backup.standardizedFileURL.path
        guard !request.output.standardizedFileURL.path.hasPrefix(backupPath + "/") else {
            throw ToolkitError.invalidInput("The result folder cannot be inside the source backup.")
        }
        for file in request.indicatorFiles {
            guard ["json", "stix", "stix2"].contains(file.pathExtension.lowercased()), FileManager.default.isReadableFile(atPath: file.path) else {
                throw ToolkitError.invalidInput("Indicator files must be readable .stix, .stix2, or .json files.")
            }
        }
    }

    public static func arguments(for request: AnalysisRequest) -> [String] {
        var arguments = ["--disable-update-check", "--disable-indicator-update-check", "check-backup", "--output", request.output.path]
        if request.fast { arguments.append("--fast") }
        if request.hashes { arguments.append("--hashes") }
        for file in request.indicatorFiles { arguments += ["--iocs", file.path] }
        arguments.append(request.backup.path)
        return arguments
    }

    public static func environment(configDirectory: URL?, allowNetwork: Bool) -> [String: String] {
        var environment = CommandEnvironment.minimal()
        for key in environmentKeysToRemove { environment[key] = nil }
        if let configDirectory { environment["MVT_CONFIG_FOLDER"] = configDirectory.path }
        environment["MVT_NETWORK_ACCESS_ALLOWED"] = allowNetwork ? "true" : "false"
        environment["MVT_NETWORK_TIMEOUT"] = "15"
        environment["PYTHONUNBUFFERED"] = "1"
        return environment
    }

    public static func analysisRequest(_ request: AnalysisRequest, configDirectory: URL) throws -> CommandRequest {
        try validate(request)
        return CommandRequest(
            executable: URL(fileURLWithPath: request.executable.path),
            arguments: arguments(for: request),
            environment: environment(configDirectory: configDirectory, allowNetwork: request.allowNetwork),
            timeout: nil,
            outputLimit: 8 * 1024 * 1024,
            displayName: "mvt-ios check-backup"
        )
    }
}

// MARK: - UFADE

/// Launches a separately installed UFADE checkout (GPL-3.0) in its own Python environment.
/// The toolkit neither imports nor bundles UFADE.
public enum UFADEConnector {
    public static let repositoryURL = URL(string: "https://github.com/prosch88/UFADE")!
    public static let setupCommands = [
        "brew install python@3.11 python-tk@3.11",
        "git clone --recurse-submodules https://github.com/prosch88/UFADE.git",
        "cd UFADE",
        "python3.11 -m venv .venv",
        ".venv/bin/python -m pip install --upgrade pip",
        ".venv/bin/python -m pip install -r requirements.txt",
    ]
    static let runtimeImports = ["tkinter", "customtkinter", "PIL", "pandas", "pymobiledevice3", "iOSbackup", "paramiko", "cryptography"]

    public struct Installation: Sendable, Hashable {
        public var checkout: URL
        public var python: ValidatedExecutable
        public var ufadeVersion: String
    }

    public static func validate(checkout: URL, python: String, runner: CommandRunning) async throws -> Installation {
        let script = checkout.appendingPathComponent("ufade.py")
        for required in ["ufade.py", "LICENSE", "requirements.txt"] where !FileManager.default.fileExists(atPath: checkout.appendingPathComponent(required).path) {
            throw ToolkitError.invalidInput("The UFADE folder is missing \(required).")
        }
        let license = (try? String(contentsOf: checkout.appendingPathComponent("LICENSE"), encoding: .utf8)) ?? ""
        guard license.contains("GNU GENERAL PUBLIC LICENSE"), license.contains("Version 3") else {
            throw ToolkitError.invalidInput("The folder does not contain UFADE's expected GPL-3.0 license.")
        }
        let source = (try? String(contentsOf: script, encoding: .utf8)) ?? ""
        guard let versionRange = source.range(of: #"(?m)^u_version\s*=\s*["']([^"']+)["']"#, options: .regularExpression) else {
            throw ToolkitError.invalidInput("UFADE's version declaration was not found in ufade.py.")
        }
        let version = source[versionRange].components(separatedBy: CharacterSet(charactersIn: "\"'"))[1]
        let pythonURL = try ExternalToolSupport.resolveExecutable(python, label: "The UFADE Python path")
        let versionResult = try await runner.run(CommandRequest(executable: pythonURL, arguments: ["-c", "import sys; print('.'.join(map(str, sys.version_info[:3])))"], timeout: 15, displayName: "UFADE Python version"))
        let pythonVersion = versionResult.standardOutputText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard versionResult.succeeded, pythonVersion.hasPrefix("3.11.") else {
            throw ToolkitError(.unsupported, message: "UFADE needs Python 3.11 (found \(pythonVersion.isEmpty ? "none" : pythonVersion)).", recovery: "Create UFADE's own Python 3.11 environment with the setup commands.")
        }
        let imports = try await runner.run(CommandRequest(executable: pythonURL, arguments: ["-c", "import " + runtimeImports.joined(separator: ", ")], workingDirectory: checkout, timeout: 60, displayName: "UFADE dependency check"))
        guard imports.succeeded else {
            throw ToolkitError(.commandFailed, message: "UFADE's Python environment is incomplete.", recovery: "Run the setup commands inside the UFADE folder, then validate again.", technicalDetail: imports.technicalSummary)
        }
        return Installation(checkout: checkout, python: ValidatedExecutable(path: pythonURL.path, sha256: try SecureFileIO.sha256(of: pythonURL), version: pythonVersion), ufadeVersion: version)
    }

    /// Starts UFADE's own window. It controls device selection, passwords, and output.
    public static func launch(_ installation: Installation, workingDirectory: URL) throws -> Int32 {
        try installation.python.revalidate()
        return try ProcessCommandRunner().launchDetached(CommandRequest(
            executable: URL(fileURLWithPath: installation.python.path),
            arguments: [installation.checkout.appendingPathComponent("ufade.py").path],
            environment: CommandEnvironment.minimal(adding: ["PYTHONUNBUFFERED": "1"]),
            workingDirectory: workingDirectory,
            timeout: nil,
            displayName: "UFADE"
        ))
    }
}

// MARK: - idb Companion

/// Optional adapter for Meta's idb Companion: validation plus one read-only inventory probe.
public enum IDBCompanionConnector {
    public static let repositoryURL = URL(string: "https://github.com/facebook/idb")!
    public static let setupCommand = "brew install facebook/fb/idb-companion"
    public static let environmentKeysToRemove = ["IDB_UDID", "IDB_COMPANION", "IDB_COMPANION_PATH"]

    public static func discover() -> [String] {
        ExternalToolSupport.discover(named: "idb_companion", extra: [])
    }

    public static func validate(executablePath: String, runner: CommandRunning) async throws -> ValidatedExecutable {
        let url = try ExternalToolSupport.resolveExecutable(executablePath, label: "The idb_companion path")
        let result = try await runner.run(CommandRequest(executable: url, arguments: ["--version"], environment: environment(), timeout: 30, displayName: "idb_companion --version"))
        guard result.succeeded else {
            throw ToolkitError(.commandFailed, message: "idb_companion did not report its version.", technicalDetail: result.technicalSummary)
        }
        let text = ExternalToolSupport.stripANSI(result.standardOutputText + result.standardErrorText)
        let version = text.split(separator: "\n").first.map { String($0).trimmingCharacters(in: .whitespaces) } ?? "unknown"
        return ValidatedExecutable(path: url.path, sha256: try SecureFileIO.sha256(of: url), version: version)
    }

    public static func probeRequest(_ executable: ValidatedExecutable) throws -> CommandRequest {
        try executable.revalidate()
        return CommandRequest(executable: URL(fileURLWithPath: executable.path), arguments: ["--list", "1"], environment: environment(), timeout: 30, displayName: "idb_companion --list 1")
    }

    static func environment() -> [String: String] {
        var environment = CommandEnvironment.minimal()
        for key in environmentKeysToRemove { environment[key] = nil }
        return environment
    }
}
