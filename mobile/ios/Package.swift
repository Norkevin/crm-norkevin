// swift-tools-version: 5.9
import PackageDescription

// Foundation contracts can be tested on this Mac without an iPhone SDK.
let package = Package(
    name: "FlowingContracts",
    platforms: [.macOS(.v13), .iOS(.v17)],
    products: [.library(name: "FlowingContracts", targets: ["FlowingContracts"])],
    targets: [
        .target(name: "FlowingContracts", path: "Flowing",
                exclude: ["FlowSession.swift", "FlowViews.swift", "FlowingApp.swift", "Info.plist", "Flowing.entitlements", "Assets.xcassets"],
                sources: ["MobileTypes.swift"]),
        .executableTarget(name: "FlowingContractChecks", dependencies: ["FlowingContracts"], path: "Tests")
    ]
)
