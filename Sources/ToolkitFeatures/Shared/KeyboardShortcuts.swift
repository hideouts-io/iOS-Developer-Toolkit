import Foundation

extension Workspace {
    /// The workspace after this one in sidebar order, wrapping around (⌥⌘→).
    public var next: Workspace {
        let all = Workspace.allCases
        return all[(all.firstIndex(of: self)! + 1) % all.count]
    }

    /// The workspace before this one in sidebar order, wrapping around (⌥⌘←).
    public var previous: Workspace {
        let all = Workspace.allCases
        return all[(all.firstIndex(of: self)! + all.count - 1) % all.count]
    }

    /// Workspaces with a ⌘-number shortcut, in order.
    public static var numbered: [Workspace] { Array(allCases.prefix(9)) }
}

/// The keyboard shortcut reference (⌘/). The app's menu commands use the same keys; a test keeps
/// the numbered workspaces in step with `Workspace.numbered`.
public enum KeyboardShortcutReference {
    public struct Entry: Sendable, Hashable, Identifiable {
        public var id: String { keys + title }
        public let keys: String
        public let title: String
    }

    public struct Section: Sendable, Hashable, Identifiable {
        public var id: String { title }
        public let title: String
        public let entries: [Entry]
    }

    public static var sections: [Section] {
        [
            Section(title: "Navigate", entries: [
                Entry(keys: "⌘K", title: "Command palette: search workspaces, actions, and devices"),
                Entry(keys: "⌥⌘←", title: "Previous workspace"),
                Entry(keys: "⌥⌘→", title: "Next workspace"),
                Entry(keys: "⌃⌘S", title: "Show or hide the sidebar"),
            ] + Workspace.numbered.enumerated().map { index, workspace in
                Entry(keys: "⌘\(index + 1)", title: workspace.title)
            }),
            Section(title: "Devices", entries: [
                Entry(keys: "⌘R", title: "Refresh devices"),
                Entry(keys: "⇧⌘R", title: "Run the Readiness Check for the selected device"),
            ]),
            Section(title: "General", entries: [
                Entry(keys: "⌘,", title: "Settings (folders, workspace profiles, developer tools)"),
                Entry(keys: "⌘/", title: "This list of keyboard shortcuts"),
                Entry(keys: "Return", title: "Confirm the default button in a sheet"),
                Entry(keys: "Esc", title: "Cancel or close a sheet"),
            ]),
        ]
    }
}
