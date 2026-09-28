import Foundation

/// Redacts identifying values from text that may leave the Mac (support bundles, exported
/// compatibility reports, exported diagnostic logs).
public enum Sanitizer {
    private static let rules: [(NSRegularExpression, String)] = {
        let patterns: [(String, String)] = [
            // Modern UDIDs (00008110-001234560ABC801E) and legacy 40-hex UDIDs.
            (#"\b[0-9A-Fa-f]{8}-[0-9A-Fa-f]{16,}\b"#, "<device-identifier>"),
            (#"\b[0-9A-Fa-f]{40}\b"#, "<device-identifier>"),
            (#"\b[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}\b"#, "<uuid>"),
            (#"\b(?:\d{1,3}\.){3}\d{1,3}\b"#, "<ipv4-address>"),
            (#"\b[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}\b"#, "<mac-address>"),
            (#"\b(?:[0-9A-Fa-f]{1,4}:){4,7}[0-9A-Fa-f]{1,4}\b"#, "<ipv6-address>"),
            (#"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"#, "<email-address>"),
            (#"/(?:Users|private|var|Volumes|Library|Applications|System|opt|tmp)(?:/[^\s\\"']+)+"#, "<local-path>"),
            (#"~/[^\s\\"']+"#, "<local-path>"),
        ]
        return patterns.compactMap { pattern, replacement in
            (try? NSRegularExpression(pattern: pattern)).map { ($0, replacement) }
        }
    }()

    /// - Parameters:
    ///   - value: text to sanitize.
    ///   - redactions: additional literal values (device names, serials) to remove.
    ///   - limit: maximum returned length in characters.
    public static func sanitize(_ value: String, redactions: [String] = [], limit: Int = 8_000) -> String {
        var text = value
        // Literal redactions first (longest first) so that a device name containing an
        // identifier-like substring is removed as a whole.
        for literal in redactions.filter({ !$0.isEmpty }).sorted(by: { $0.count > $1.count }) {
            text = text.replacingOccurrences(of: literal, with: "<redacted>")
        }
        for (expression, replacement) in rules {
            let range = NSRange(text.startIndex..<text.endIndex, in: text)
            text = expression.stringByReplacingMatches(in: text, options: [], range: range, withTemplate: replacement)
        }
        if text.count > limit {
            text = String(text.prefix(limit))
        }
        return text
    }

    /// A one-way fingerprint for correlating observations of the same device locally without
    /// storing its identifier.
    public static func fingerprint(_ identifier: String, salt: String = "iOSDeveloperToolkit.v1") -> String {
        String(SecureFileIO.sha256(of: Data((salt + ":" + identifier).utf8)).prefix(24))
    }
}
