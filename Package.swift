// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "iOSDeveloperToolkit",
    platforms: [.macOS(.v14)],
    products: [
        .library(name: "ToolkitCore", targets: ["ToolkitCore"]),
        .library(name: "DeviceKit", targets: ["DeviceKit"]),
        .library(name: "ToolkitFeatures", targets: ["ToolkitFeatures"]),
        .executable(name: "idt", targets: ["idt"]),
    ],
    dependencies: [
        .package(url: "https://github.com/apple/swift-nio.git", from: "2.70.0"),
        .package(url: "https://github.com/apple/swift-nio-ssl.git", from: "2.27.0"),
        .package(url: "https://github.com/apple/swift-argument-parser.git", from: "1.5.0"),
    ],
    targets: [
        .target(
            name: "ToolkitCore",
            path: "Sources/ToolkitCore"
        ),
        .target(
            name: "DeviceKit",
            dependencies: [
                "ToolkitCore",
                .product(name: "NIOCore", package: "swift-nio"),
                .product(name: "NIOPosix", package: "swift-nio"),
                .product(name: "NIOTLS", package: "swift-nio"),
                .product(name: "NIOSSL", package: "swift-nio-ssl"),
            ],
            path: "Sources/DeviceKit"
        ),
        .target(
            name: "ToolkitFeatures",
            dependencies: ["ToolkitCore", "DeviceKit"],
            path: "Sources/ToolkitFeatures",
            resources: [.process("Resources")]
        ),
        .executableTarget(
            name: "idt",
            dependencies: [
                "ToolkitCore",
                "DeviceKit",
                "ToolkitFeatures",
                .product(name: "ArgumentParser", package: "swift-argument-parser"),
            ],
            path: "Sources/idt"
        ),
        .testTarget(
            name: "ToolkitCoreTests",
            dependencies: ["ToolkitCore"],
            path: "Tests/ToolkitCoreTests"
        ),
        .testTarget(
            name: "DeviceKitTests",
            dependencies: [
                "DeviceKit",
                "ToolkitCore",
                .product(name: "NIOCore", package: "swift-nio"),
                .product(name: "NIOPosix", package: "swift-nio"),
                .product(name: "NIOSSL", package: "swift-nio-ssl"),
            ],
            path: "Tests/DeviceKitTests",
            resources: [.copy("Fixtures")]
        ),
        .testTarget(
            name: "ToolkitFeaturesTests",
            dependencies: ["ToolkitFeatures", "DeviceKit", "ToolkitCore"],
            path: "Tests/ToolkitFeaturesTests",
            resources: [.copy("Fixtures")]
        ),
    ],
    swiftLanguageModes: [.v6]
)
