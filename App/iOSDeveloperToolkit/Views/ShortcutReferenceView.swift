import SwiftUI
import ToolkitFeatures

/// Help › Keyboard Shortcuts (⌘/).
struct ShortcutReferenceView: View {
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Label("Keyboard Shortcuts", systemImage: "keyboard").font(.title2.bold())
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    ForEach(KeyboardShortcutReference.sections) { section in
                        VStack(alignment: .leading, spacing: 6) {
                            Text(section.title).font(.headline)
                            ForEach(section.entries) { entry in
                                HStack(alignment: .firstTextBaseline, spacing: 12) {
                                    Text(entry.keys)
                                        .font(.callout.monospaced())
                                        .frame(width: 70, alignment: .leading)
                                    Text(entry.title).font(.callout)
                                }
                                .accessibilityElement(children: .combine)
                            }
                        }
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            HStack {
                Spacer()
                Button("Done") { dismiss() }.keyboardShortcut(.defaultAction)
            }
        }
        .padding(20)
        .frame(width: 480, height: 640)
        .accessibilityIdentifier("shortcut-reference")
    }
}
