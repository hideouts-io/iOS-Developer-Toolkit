import AppKit
import DeviceKit
import SwiftUI
import ToolkitCore
import ToolkitFeatures

struct LocationLabView: View {
    @Environment(AppModel.self) private var model
    @State private var confirmation: PendingConfirmation?
    @State private var contentWidth: CGFloat = 0

    var body: some View {
        @Bindable var location = model.location
        WorkspacePage(workspace: .location) {
            TargetHeader(allowedKinds: [.physical, .simulator])
            Text("Simulated locations are reported to apps on the device until cleared. GPS hardware is not changed, and nothing here hides simulation from apps. No map service or geocoder is contacted.")
                .font(.callout)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            if let device = model.selectedDevice, device.kind != .demo {
                mechanismNote(device)
            }
            Group {
                // Two columns when the page is wide enough; one column otherwise.
                if contentWidth >= 720 {
                    HStack(alignment: .top, spacing: 16) {
                        VStack(alignment: .leading, spacing: 16) { mapCard; routeCard; gpxCard }
                        VStack(alignment: .leading, spacing: 16) { coordinateCard; savedPlacesCard }
                            .frame(width: 320)
                    }
                } else {
                    VStack(alignment: .leading, spacing: 16) {
                        coordinateCard
                        mapCard
                        savedPlacesCard
                        routeCard
                        gpxCard
                    }
                }
            }
            .background(GeometryReader { proxy in
                Color.clear
                    .onAppear { contentWidth = proxy.size.width }
                    .onChange(of: proxy.size.width) { _, width in contentWidth = width }
            })
        }
        .sheet(item: $confirmation) { pending in
            ConfirmationSheet(title: pending.title, detail: pending.detail, requirement: pending.requirement, target: pending.target, commandPreview: nil, onConfirm: pending.action)
        }
    }

    private var mapCard: some View {
        @Bindable var location = model.location
        return Card(title: "Choose a point", systemImage: "map") {
            WorldMapView(selection: location.coordinates) { coordinates in
                location.show(coordinates)
            }
            .aspectRatio(2, contentMode: .fit)
            .frame(maxWidth: 560)
            HStack {
                TextField("Paste latitude,longitude or a map link", text: $location.linkText)
                    .textFieldStyle(.roundedBorder)
                    .onSubmit { location.importLink(app: model) }
                Button("Use") { location.importLink(app: model) }.disabled(location.linkText.isEmpty)
            }
        }
    }

    private func mechanismNote(_ device: Device) -> some View {
        let mechanism = model.executor.location.mechanism(for: device.target)
        return Label("Uses \(mechanism.rawValue).\(mechanism == .coreDevice ? " Needs Developer Mode and Xcode." : mechanism == .legacyService ? " Needs the developer image mounted by Xcode." : "")", systemImage: "info.circle")
            .font(.callout)
            .foregroundStyle(.secondary)
    }

    private var coordinateCard: some View {
        @Bindable var location = model.location
        return Card(title: "Coordinate", systemImage: "location") {
            Grid(alignment: .leading, horizontalSpacing: 8, verticalSpacing: 8) {
                GridRow {
                    Text("Latitude").foregroundStyle(.secondary)
                    TextField("-90 to 90", text: $location.latitudeText).textFieldStyle(.roundedBorder).accessibilityIdentifier("latitude-field")
                }
                GridRow {
                    Text("Longitude").foregroundStyle(.secondary)
                    TextField("-180 to 180", text: $location.longitudeText).textFieldStyle(.roundedBorder).accessibilityIdentifier("longitude-field")
                }
            }
            if location.coordinates == nil {
                Label("Enter a latitude from -90 to 90 and a longitude from -180 to 180.", systemImage: "exclamationmark.triangle").font(.caption).foregroundStyle(.orange)
            }
            Text("Nudge").font(.callout.weight(.medium))
            HStack(alignment: .center, spacing: 12) {
                NudgePad { direction in location.nudge(direction, app: model) }
                VStack(alignment: .leading) {
                    Picker("Distance", selection: $location.nudgeMetres) {
                        ForEach([1.0, 10, 100, 1_000, 10_000, 100_000], id: \.self) { metres in
                            Text(metres >= 1_000 ? "\(Int(metres / 1_000)) km" : "\(Int(metres)) m").tag(metres)
                        }
                    }
                    .labelsHidden()
                    .frame(width: 100)
                }
            }
            HStack {
                Button("Set Location") { confirmSet() }
                    .disabled(location.coordinates == nil || !canSimulate)
                    .accessibilityIdentifier("set-location")
                Button("Clear") { confirmClear() }
                    .disabled(!canSimulate)
            }
            if let tracked = location.lastSimulatedTarget {
                Label("\(tracked.name) is using a simulated location.", systemImage: "location.fill")
                    .font(.caption)
                    .foregroundStyle(.orange)
            }
        }
    }

    private var savedPlacesCard: some View {
        @Bindable var location = model.location
        return Card(title: "Saved places", systemImage: "bookmark", subtitle: "Stored only on this Mac.") {
            if location.savedLocations.isEmpty {
                Text("No saved places yet.").foregroundStyle(.secondary)
            }
            ForEach(location.savedLocations) { place in
                HStack {
                    Button(place.name) { location.show(place.coordinates) }
                        .buttonStyle(.link)
                    Spacer()
                    Text(place.coordinates.formatted).font(.caption.monospaced()).foregroundStyle(.secondary)
                    Button(role: .destructive) { location.removePlace(place, app: model) } label: { Image(systemName: "trash") }
                        .buttonStyle(.borderless)
                        .accessibilityLabel("Remove \(place.name)")
                }
            }
            HStack {
                TextField("Name", text: $location.newPlaceName).textFieldStyle(.roundedBorder)
                Button("Save Current") { location.savePlace(app: model) }
                    .disabled(location.newPlaceName.trimmingCharacters(in: .whitespaces).isEmpty || location.coordinates == nil)
            }
        }
    }

    private var routeCard: some View {
        @Bindable var location = model.location
        return Card(title: "Route", systemImage: "point.topleft.down.to.point.bottomright.curvepath", subtitle: "One latitude,longitude pair per line. Move along it at a constant speed, or save it as a timed GPX track.") {
            TextEditor(text: $location.waypointsText)
                .font(.callout.monospaced())
                .frame(height: 70)
                .overlay(RoundedRectangle(cornerRadius: 4).strokeBorder(.separator))
            HStack {
                Picker("Speed", selection: $location.travelPreset) {
                    ForEach(TravelPreset.allCases) { preset in
                        Text(preset.speedKmh.map { "\(preset.rawValue) (\(Int($0)) km/h)" } ?? preset.rawValue).tag(preset)
                    }
                }
                .frame(maxWidth: 220)
                if location.travelPreset == .custom {
                    TextField("km/h", value: $location.customSpeedKmh, format: .number).frame(width: 60)
                }
                Stepper("Every \(location.routeIntervalSeconds)s", value: $location.routeIntervalSeconds, in: 1...60)
                Stepper("× \(location.routeTraversals)", value: $location.routeTraversals, in: 1...20)
                    .help("Traversals: repeat the route back and forth")
            }
            HStack {
                Button("Start Moving") { confirmRoute() }.disabled(!canSimulate)
                Button("Build GPX") { location.buildRoute(app: model) }
                if let route = location.generatedRoute {
                    Button("Save GPX…") { saveGPX(route) }
                    Text("\(route.points.count) points · \(Measurement(value: route.distanceMetres / 1_000, unit: UnitLength.kilometers).formatted(.measurement(width: .abbreviated, usage: .road))) · \(Duration.seconds(route.durationSeconds).formatted(.units(allowed: [.hours, .minutes, .seconds], width: .abbreviated)))")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
        }
    }

    private var gpxCard: some View {
        @Bindable var location = model.location
        return Card(title: "GPX playback", systemImage: "play.circle", subtitle: "Replays a GPX track point by point on the selected device. Files with DTDs or entities are refused.") {
            HStack {
                Button("Choose GPX…") {
                    if let url = FilePanels.chooseFile(title: "Choose a GPX track", allowedExtensions: ["gpx"]) {
                        location.inspectGPX(url, app: model)
                    }
                }
                if let inspection = location.gpxInspection {
                    Text("\(inspection.url.lastPathComponent): \(inspection.trackPointCount) points, \(inspection.timedPointCount) timed")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
            }
            if location.gpxInspection != nil {
                Toggle("Ignore recorded timing", isOn: $location.ignoreRecordedTiming)
                if location.ignoreRecordedTiming {
                    Stepper("One point every \(location.fixedIntervalSeconds, specifier: "%.1f") s", value: $location.fixedIntervalSeconds, in: 0.5...60, step: 0.5)
                } else {
                    Stepper("Timing randomness ±\(location.jitterMilliseconds) ms", value: $location.jitterMilliseconds, in: 0...5_000, step: 100)
                }
                HStack {
                    if location.isPlaying {
                        Button("Stop Playback") { location.stopPlayback() }
                        if let progress = location.playbackProgress {
                            ProgressView(value: Double(progress.index), total: Double(progress.total))
                            Text("\(progress.index)/\(progress.total)").font(.caption.monospacedDigit())
                        }
                    } else {
                        Button("Play on Device") { confirmPlayback() }.disabled(!canSimulate)
                    }
                }
                if let inspection = location.gpxInspection {
                    DisclosureGroup("File details") {
                        InfoRow("SHA-256", inspection.sha256, monospaced: true)
                        InfoRow("Size", ByteFormatting.string(inspection.sizeBytes))
                        InfoRow("Distance", String(format: "%.2f km", inspection.distanceMetres / 1_000))
                        if let duration = inspection.recordedDuration {
                            InfoRow("Recorded duration", Duration.seconds(duration).formatted(.units(allowed: [.hours, .minutes, .seconds], width: .abbreviated)))
                        }
                    }
                }
            }
        }
    }

    private var canSimulate: Bool {
        guard let device = model.selectedDevice else { return false }
        return device.kind == .physical || device.kind == .simulator
    }

    private func confirmSet() {
        guard let device = model.selectedDevice, let coordinates = model.location.coordinates else { return }
        confirmation = PendingConfirmation(title: "Set simulated location", detail: "\(device.name) will report \(coordinates.formatted) to apps until you clear it.", requirement: .make(for: .deviceChange, target: device.target), target: device.target) {
            Task { await model.location.setLocation(app: model, target: device.target) }
        }
    }

    private func confirmClear() {
        guard let device = model.selectedDevice else { return }
        Task { await model.location.clear(app: model, target: device.target) }
    }

    private func confirmRoute() {
        guard let device = model.selectedDevice else { return }
        confirmation = PendingConfirmation(title: "Start simulated movement", detail: "\(device.name) will move along the route at \(Int(model.location.speedKmh)) km/h until you clear the location.", requirement: .make(for: .deviceChange, target: device.target), target: device.target) {
            Task { await model.location.startNativeRoute(app: model, target: device.target) }
        }
    }

    private func confirmPlayback() {
        guard let device = model.selectedDevice else { return }
        confirmation = PendingConfirmation(title: "Play GPX track", detail: "\(device.name) will follow the track. Stop playback and clear the location when finished.", requirement: .make(for: .deviceChange, target: device.target), target: device.target) {
            model.location.startPlayback(app: model, target: device.target)
        }
    }

    private func saveGPX(_ route: GeneratedRoute) {
        guard let url = FilePanels.save(title: "Save route", suggestedName: "route.gpx", allowedExtension: "gpx") else { return }
        do {
            try SecureFileIO.writeNewFile(Data(route.gpxDocument.utf8), to: url, mode: 0o644)
            model.location.inspectGPX(url, app: model)
        } catch {
            model.present(error)
        }
    }
}

struct NudgePad: View {
    let action: (CompassDirection) -> Void

    var body: some View {
        Grid(horizontalSpacing: 2, verticalSpacing: 2) {
            GridRow { button(.northWest); button(.north); button(.northEast) }
            GridRow { button(.west); Image(systemName: "location.circle").foregroundStyle(.secondary).frame(width: 28, height: 24); button(.east) }
            GridRow { button(.southWest); button(.south); button(.southEast) }
        }
    }

    private func button(_ direction: CompassDirection) -> some View {
        Button { action(direction) } label: {
            Image(systemName: direction.symbolName).frame(width: 20, height: 16)
        }
        .help("Move \(direction.rawValue)")
        .accessibilityLabel("Nudge \(direction.rawValue)")
    }
}

/// An offline equirectangular world map; click to choose a coordinate.
struct WorldMapView: View {
    let selection: Coordinates?
    let onSelect: (Coordinates) -> Void
    private let image: NSImage? = ToolkitResources.worldMapURL.flatMap(NSImage.init(contentsOf:))

    var body: some View {
        GeometryReader { geometry in
            let size = fittedSize(in: geometry.size)
            ZStack(alignment: .topLeading) {
                if let image {
                    Image(nsImage: image)
                        .resizable()
                        .frame(width: size.width, height: size.height)
                } else {
                    Rectangle().fill(.quaternary).frame(width: size.width, height: size.height)
                }
                if let selection {
                    let fractions = LocationLab.mapFractions(for: selection)
                    Image(systemName: "mappin.circle.fill")
                        .font(.title2)
                        .foregroundStyle(.red)
                        .background(Circle().fill(.white).padding(3))
                        .position(x: fractions.x * size.width, y: fractions.y * size.height)
                        .accessibilityHidden(true)
                }
            }
            .frame(width: size.width, height: size.height)
            .clipShape(RoundedRectangle(cornerRadius: 6))
            .contentShape(Rectangle())
            .onTapGesture(coordinateSpace: .local) { point in
                if let coordinates = try? LocationLab.coordinates(forMapFractionX: point.x / size.width, y: point.y / size.height) {
                    onSelect(coordinates)
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .accessibilityElement()
        .accessibilityLabel("World map. Use the latitude and longitude fields to enter a coordinate with the keyboard.")
    }

    private func fittedSize(in available: CGSize) -> CGSize {
        let width = min(available.width, available.height * 2)
        return CGSize(width: width, height: width / 2)
    }
}
