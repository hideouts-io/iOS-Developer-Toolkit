import Foundation

/// Resources bundled with the feature library.
public enum ToolkitResources {
    /// Public-domain Natural Earth 1:110m land map, equirectangular (2:1).
    public static var worldMapURL: URL? {
        Bundle.module.url(forResource: "location-world-map", withExtension: "png")
    }
}
