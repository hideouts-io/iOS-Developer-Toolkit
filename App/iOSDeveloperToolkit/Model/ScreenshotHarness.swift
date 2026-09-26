import AppKit
import SwiftUI
import ToolkitFeatures

/// Renders the app's own window for documentation and GUI verification:
///   "iOS Developer Toolkit" -capture-screenshots <folder> [-demo-mode] [-window-size WxH]
/// It visits every workspace, writes one PNG per workspace plus window-geometry.txt, and quits.
/// Rendering uses AppKit's view caching, so no Screen Recording permission is involved.
@MainActor
enum ScreenshotHarness {
    static func runIfRequested(model: AppModel) {
        let arguments = ProcessInfo.processInfo.arguments
        guard let index = arguments.firstIndex(of: "-capture-screenshots"), arguments.indices.contains(index + 1) else { return }
        let folder = URL(fileURLWithPath: arguments[index + 1], isDirectory: true)
        var size: CGSize?
        if let sizeIndex = arguments.firstIndex(of: "-window-size"), arguments.indices.contains(sizeIndex + 1) {
            let parts = arguments[sizeIndex + 1].split(separator: "x").compactMap { Double($0) }
            if parts.count == 2 { size = CGSize(width: parts[0], height: parts[1]) }
        }
        Task { @MainActor in
            try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
            try? await Task.sleep(for: .seconds(2))
            guard let window = NSApp.windows.first(where: { $0.isVisible && $0.contentView != nil && $0.title != "" && $0.frame.width > 300 }) ?? NSApp.windows.first(where: { $0.isVisible }) else {
                try? "No visible window".write(to: folder.appendingPathComponent("window-geometry.txt"), atomically: true, encoding: .utf8)
                NSApp.terminate(nil)
                return
            }
            if let size {
                window.setContentSize(size)
                try? await Task.sleep(for: .milliseconds(500))
            }
            var report = ["initial frame: \(window.frame)", "screen: \(window.screen?.visibleFrame ?? .zero)", "minSize: \(window.minSize)", "contentMinSize: \(window.contentMinSize)"]
            for workspace in Workspace.allCases {
                model.workspace = workspace
                try? await Task.sleep(for: .milliseconds(900))
                render(window, to: folder.appendingPathComponent("\(workspace.rawValue).png"))
                report.append("\(workspace.rawValue): content \(window.contentView?.frame.size ?? .zero), fitting \(window.contentView?.fittingSize ?? .zero)")
            }
            model.workspace = .overview
            for table in tables(in: window.contentView?.superview ?? window.contentView) {
                report.append("\(type(of: table)): rows=\(table.numberOfRows) frame=\(table.frame) visibleRect=\(table.visibleRect)")
            }
            try? report.joined(separator: "\n").write(to: folder.appendingPathComponent("window-geometry.txt"), atomically: true, encoding: .utf8)
            NSApp.terminate(nil)
        }
    }

    static func tables(in view: NSView?) -> [NSTableView] {
        guard let view else { return [] }
        return (view as? NSTableView).map { [$0] } ?? view.subviews.flatMap(tables(in:))
    }

    static func render(_ window: NSWindow, to url: URL) {
        guard let view = window.contentView?.superview ?? window.contentView else { return }
        guard let representation = view.bitmapImageRepForCachingDisplay(in: view.bounds) else { return }
        view.cacheDisplay(in: view.bounds, to: representation)
        if let data = representation.representation(using: .png, properties: [:]) {
            try? data.write(to: url)
        }
    }
}
