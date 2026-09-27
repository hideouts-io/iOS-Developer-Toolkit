import XCTest

/// GUI smoke tests. The app runs with `-ui-testing` (no real device discovery) and
/// `-demo-mode` (a clearly labelled simulated iPhone), so results are deterministic.
final class SmokeUITests: XCTestCase {
    var app: XCUIApplication!

    override func setUpWithError() throws {
        continueAfterFailure = false
        app = XCUIApplication()
        app.launchArguments = ["-ui-testing", "YES", "-demo-mode", "YES", "-ApplePersistenceIgnoreState", "YES"]
        app.launch()
    }

    override func tearDownWithError() throws {
        app.terminate()
    }

    private func attachScreenshot(_ name: String) {
        let attachment = XCTAttachment(screenshot: app.windows.firstMatch.screenshot())
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }

    func testWindowOpensAtUsableSizeOnALaptopDisplay() throws {
        let window = app.windows.firstMatch
        XCTAssertTrue(window.waitForExistence(timeout: 10))
        let frame = window.frame
        XCTAssertGreaterThanOrEqual(frame.width, 900, "Window too narrow: \(frame)")
        XCTAssertGreaterThanOrEqual(frame.height, 560, "Window too short: \(frame)")
        // A 13-inch MacBook Air is 1280×800 points (minus menu bar and Dock).
        XCTAssertLessThanOrEqual(frame.width, 1280, "Window wider than a laptop display: \(frame)")
        XCTAssertLessThanOrEqual(frame.height, 760, "Window taller than a laptop display: \(frame)")
        attachScreenshot("overview")
    }

    func testDemoModeIsClearlyLabelled() throws {
        XCTAssertTrue(app.staticTexts.matching(NSPredicate(format: "value CONTAINS 'Demo Mode'")).firstMatch.waitForExistence(timeout: 10) || app.otherElements["demo-banner"].exists || app.staticTexts["demo-banner"].exists)
    }

    func testEveryWorkspaceOpens() throws {
        let workspaces = ["overview", "device", "readiness", "apps", "installApp", "location", "liveLogs", "actions", "backup", "evidence", "externalTools", "activity", "help", "safety"]
        for workspace in workspaces {
            let item = app.descendants(matching: .any)["sidebar-\(workspace)"]
            XCTAssertTrue(item.waitForExistence(timeout: 5), "Missing sidebar item \(workspace)")
            item.click()
            // Each workspace shows the selected target or its own content without crashing.
            XCTAssertTrue(app.windows.firstMatch.exists)
            attachScreenshot(workspace)
        }
    }

    func testDemoActionsAreBlockedWithExplanation() throws {
        app.descendants(matching: .any)["sidebar-actions"].click()
        let action = app.descendants(matching: .any)["action-battery"]
        XCTAssertTrue(action.waitForExistence(timeout: 5))
        action.click()
        let run = app.buttons["run-action"]
        XCTAssertTrue(run.waitForExistence(timeout: 5))
        XCTAssertFalse(run.isEnabled, "Actions must be disabled in Demo Mode")
        attachScreenshot("action-detail")
    }

    func testCommandPaletteNavigates() throws {
        app.typeKey("k", modifierFlags: .command)
        let search = app.textFields["palette-search"]
        XCTAssertTrue(search.waitForExistence(timeout: 5))
        search.typeText("Location Lab\r")
        XCTAssertTrue(app.textFields["latitude-field"].waitForExistence(timeout: 5))
        attachScreenshot("location-lab")
    }

    func testLocationLabValidatesInput() throws {
        app.descendants(matching: .any)["sidebar-location"].click()
        let latitude = app.textFields["latitude-field"]
        XCTAssertTrue(latitude.waitForExistence(timeout: 5))
        latitude.doubleClick()
        latitude.typeKey("a", modifierFlags: .command)
        latitude.typeText("123")
        XCTAssertTrue(app.staticTexts.matching(NSPredicate(format: "value CONTAINS 'latitude from -90 to 90'")).firstMatch.waitForExistence(timeout: 5))
        XCTAssertFalse(app.buttons["set-location"].isEnabled)
    }

    func testMinimumSizeKeepsControlsReachable() throws {
        let window = app.windows.firstMatch
        XCTAssertTrue(window.waitForExistence(timeout: 10))
        // Drag the bottom-right corner inward as far as it goes.
        let corner = window.coordinate(withNormalizedOffset: CGVector(dx: 1, dy: 1)).withOffset(CGVector(dx: -2, dy: -2))
        corner.press(forDuration: 0.2, thenDragTo: window.coordinate(withNormalizedOffset: CGVector(dx: 0.1, dy: 0.1)))
        XCTAssertGreaterThanOrEqual(window.frame.width, 899)
        XCTAssertGreaterThanOrEqual(window.frame.height, 559)
        app.descendants(matching: .any)["sidebar-location"].click()
        XCTAssertTrue(app.buttons["set-location"].waitForExistence(timeout: 5))
        attachScreenshot("minimum-size-location")
    }
}
