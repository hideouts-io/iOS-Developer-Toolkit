import AppKit
import DeviceKit
import SwiftUI
import ToolkitFeatures

/// Renders the app's own window for documentation and GUI verification:
///
///     "iOS Developer Toolkit" -capture-screenshots <folder>
///         [-demo-mode] [-ui-testing] [-window-size WxH] [-only overview,apps]
///         [-populate-demo YES] [-select-booted-simulator YES] [-start-simulator-log YES]
///         [-scroll-fraction 0.0–1.0] [-show-sheet reconnect|shortcuts] [-select-physical-device YES]
///
/// Every flag takes a value: AppKit reads arguments as `-key value` pairs, and a lone flag would
/// swallow the next argument, leaving a stray path that macOS treats as a file to open (which
/// stops SwiftUI from creating the main window).
///
/// It visits the requested workspaces, writes one PNG each plus window-geometry.txt, and quits
/// (always within two minutes). Rendering uses AppKit's view caching, so no Screen Recording
/// permission is involved. Vibrancy cannot be cached, so the sidebar uses a plain list style
/// while capturing.
@MainActor
enum ScreenshotHarness {
    static var isCapturing: Bool {
        ProcessInfo.processInfo.arguments.contains("-capture-screenshots")
    }

    static func runIfRequested(model: AppModel) {
        let arguments = ProcessInfo.processInfo.arguments
        guard let index = arguments.firstIndex(of: "-capture-screenshots"), arguments.indices.contains(index + 1) else { return }
        let folder = URL(fileURLWithPath: arguments[index + 1], isDirectory: true)
        func value(_ flag: String) -> String? {
            arguments.firstIndex(of: flag).flatMap { arguments.indices.contains($0 + 1) ? arguments[$0 + 1] : nil }
        }
        var size: CGSize?
        if let text = value("-window-size") {
            let parts = text.split(separator: "x").compactMap { Double($0) }
            if parts.count == 2 { size = CGSize(width: parts[0], height: parts[1]) }
        }
        let only = value("-only").map { Set($0.split(separator: ",").map(String.init)) }
        let workspaces = Workspace.allCases.filter { only?.contains($0.rawValue) ?? true }

        // Hard stop so an unexpected state can never leave the harness running.
        Task { @MainActor in
            try? await Task.sleep(for: .seconds(120))
            try? "Timed out".write(to: folder.appendingPathComponent("TIMEOUT"), atomically: true, encoding: .utf8)
            NSApp.terminate(nil)
            // A presented sheet can block termination; never leave the harness running.
            try? await Task.sleep(for: .seconds(5))
            exit(3)
        }

        Task { @MainActor in
            try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
            try? await Task.sleep(for: .seconds(2))
            guard let window = NSApp.windows.first(where: { $0.isVisible && $0.contentView != nil && $0.frame.width > 300 }) else {
                try? "No visible window".write(to: folder.appendingPathComponent("window-geometry.txt"), atomically: true, encoding: .utf8)
                NSApp.terminate(nil)
                return
            }
            if let size {
                window.setContentSize(size)
                try? await Task.sleep(for: .milliseconds(500))
            }
            if value("-populate-demo") == "YES" {
                await populateDemo(model)
            }
            if value("-select-booted-simulator") == "YES" {
                await selectBootedSimulator(model, startLog: value("-start-simulator-log") == "YES")
            }
            if value("-select-physical-device") == "YES" {
                await selectPhysicalDevice(model)
            }
            var report = [
                "initial frame: \(window.frame)",
                "screen: \(window.screen?.visibleFrame ?? .zero)",
                "minSize: \(window.minSize)",
                "selected: \(model.selectedDevice.map { "\($0.name) (\($0.kind.rawValue))" } ?? "none")",
            ]
            for workspace in workspaces {
                model.workspace = workspace
                try? await Task.sleep(for: .milliseconds(900))
                if let fraction = value("-scroll-fraction").flatMap(Double.init) {
                    scrollContent(of: window, to: fraction)
                    try? await Task.sleep(for: .milliseconds(400))
                }
                render(window, to: folder.appendingPathComponent("\(workspace.rawValue).png"))
                let sidebarWidth = sidebar(in: window)?.frame.width ?? 0
                let contentHeight = window.contentView?.frame.height ?? 0
                let layoutHeight = window.contentView?.subviews.first?.frame.height ?? 0
                let overflow = layoutHeight > contentHeight + 1 ? " OVERFLOW(\(layoutHeight) > \(contentHeight))" : ""
                report.append("\(workspace.rawValue): window \(window.frame.size) sidebar \(sidebarWidth)\(sidebarWidth < 190 ? " SQUEEZED" : "")\(overflow)")
                if value("-dump-views") == "YES" { report.append(dump(window.contentView, depth: 0)) }
            }
            if let sheetName = value("-show-sheet"), let presented = sheetBinding(sheetName, model) {
                presented.wrappedValue = true
                try? await Task.sleep(for: .milliseconds(900))
                if let sheet = window.attachedSheet {
                    render(sheet, to: folder.appendingPathComponent("sheet-\(sheetName).png"))
                    report.append("sheet-\(sheetName): \(sheet.frame.size)")
                } else {
                    report.append("sheet-\(sheetName): not shown")
                }
                presented.wrappedValue = false
                try? await Task.sleep(for: .milliseconds(500))
            }
            try? report.joined(separator: "\n").write(to: folder.appendingPathComponent("window-geometry.txt"), atomically: true, encoding: .utf8)
            model.logs.stopAll()
            try? await Task.sleep(for: .milliseconds(300))
            NSApp.terminate(nil)
            try? await Task.sleep(for: .seconds(5))
            exit(0)
        }
    }

    /// Demo content for documentation screenshots. Everything shown is labelled demo data.
    static func populateDemo(_ model: AppModel) async {
        guard model.demoMode else { return }
        let demo = DemoMode.device
        model.selectedDeviceID = demo.id
        await model.apps.refresh(app: model, device: demo)
        model.readinessResults[demo.id] = CapabilityRow.rows(for: .physical).map { row in
            switch row {
            case .developerServices: return row.result(.attention, "Personalization required: a compatible image is on this Mac (demo data).")
            case .lockState: return row.result(.attention, "Locked — unlock the device to continue (demo data).")
            default: return row.result(.ready, "Ready (demo data).")
            }
        }
    }

    /// The sheets the harness can render.
    static func sheetBinding(_ name: String, _ model: AppModel) -> Binding<Bool>? {
        switch name {
        case "reconnect": return Binding(get: { model.isReconnectGuidePresented }, set: { model.isReconnectGuidePresented = $0 })
        case "shortcuts": return Binding(get: { model.isShortcutReferencePresented }, set: { model.isShortcutReferencePresented = $0 })
        default: return nil
        }
    }

    /// Selects the first physical device connected by USB (waits up to 30 seconds for discovery).
    static func selectPhysicalDevice(_ model: AppModel) async {
        for _ in 0..<30 {
            if let device = model.physicalDevices.first(where: { $0.kind == .physical && $0.transports.contains(.usb) }) {
                model.selectedDeviceID = device.id
                try? await Task.sleep(for: .seconds(3))
                return
            }
            try? await Task.sleep(for: .seconds(1))
        }
    }

    /// Selects the first running simulator and optionally starts a real simulator log stream.
    static func selectBootedSimulator(_ model: AppModel, startLog: Bool) async {
        for _ in 0..<30 {
            if let simulator = model.simulatorDevices.first(where: { $0.simulatorState == .booted }) {
                model.selectedDeviceID = simulator.id
                if startLog {
                    model.logs.start(.simulator, target: simulator.target, app: model)
                    try? await Task.sleep(for: .seconds(6))
                }
                return
            }
            try? await Task.sleep(for: .seconds(1))
        }
    }

    /// Scrolls the workspace page (the widest scroll view that is not the sidebar) to a fraction of
    /// its height, so cards below the fold can be rendered.
    static func scrollContent(of window: NSWindow, to fraction: Double) {
        let sidebarScroll = sidebar(in: window)?.enclosingScrollView
        let candidates = scrollViews(in: window.contentView).filter { $0 !== sidebarScroll }
        guard let scrollView = candidates.max(by: { $0.frame.width * $0.frame.height < $1.frame.width * $1.frame.height }),
              let document = scrollView.documentView else { return }
        let range = max(0, document.frame.height - scrollView.contentView.bounds.height)
        let offset = range * min(max(fraction, 0), 1)
        scrollView.contentView.scroll(to: NSPoint(x: 0, y: document.isFlipped ? offset : range - offset))
        scrollView.reflectScrolledClipView(scrollView.contentView)
    }

    static func scrollViews(in view: NSView?) -> [NSScrollView] {
        guard let view else { return [] }
        return ((view as? NSScrollView).map { [$0] } ?? []) + view.subviews.flatMap(scrollViews(in:))
    }

    static func dump(_ view: NSView?, depth: Int) -> String {
        guard let view, depth < 40 else { return "" }
        let line = String(repeating: "  ", count: depth) + "\(type(of: view)) \(view.frame)"
        return ([line] + view.subviews.map { dump($0, depth: depth + 1) }.filter { !$0.isEmpty }).joined(separator: "\n")
    }

    static func sidebar(in window: NSWindow) -> NSTableView? {
        tables(in: window.contentView?.superview ?? window.contentView).first { $0.numberOfRows == Workspace.allCases.count + Workspace.Group.allCases.count }
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
