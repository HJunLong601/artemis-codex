// Native macOS bridge for the physical-device Device Hub driver.
// Compiled on demand with the Xcode toolchain; no app or iPhone project is installed.
import AppKit
import ApplicationServices
import CoreGraphics
import Foundation

struct HubWindow: Encodable {
    let x: Double
    let y: Double
    let width: Double
    let height: Double
    let windowID: CGWindowID
}

func fail(_ message: String) -> Never {
    fputs("Device Hub bridge: \(message)\n", stderr)
    exit(1)
}

func attribute(_ element: AXUIElement, _ name: CFString) -> CFTypeRef? {
    var value: CFTypeRef?
    guard AXUIElementCopyAttributeValue(element, name, &value) == .success else { return nil }
    return value
}

func frame(of window: AXUIElement) -> CGRect? {
    guard let rawPosition = attribute(window, kAXPositionAttribute as CFString),
          let rawSize = attribute(window, kAXSizeAttribute as CFString),
          CFGetTypeID(rawPosition) == AXValueGetTypeID(),
          CFGetTypeID(rawSize) == AXValueGetTypeID() else { return nil }
    let position = rawPosition as! AXValue
    let size = rawSize as! AXValue
    var point = CGPoint.zero
    var dimensions = CGSize.zero
    guard AXValueGetValue(position, .cgPoint, &point),
          AXValueGetValue(size, .cgSize, &dimensions) else { return nil }
    return CGRect(origin: point, size: dimensions)
}

func selectedWindow(named name: String) -> (NSRunningApplication, AXUIElement, CGRect, CGWindowID) {
    guard AXIsProcessTrusted() else {
        fail("grant Accessibility access to the ARTEMIS terminal/host in System Settings")
    }
    let apps = NSRunningApplication.runningApplications(withBundleIdentifier: "com.apple.dt.Devices")
    guard apps.count == 1, let app = apps.first else { fail("open Xcode Device Hub first") }
    let element = AXUIElementCreateApplication(app.processIdentifier)
    guard let windows = attribute(element, kAXWindowsAttribute as CFString) as? [AXUIElement] else {
        fail("cannot read Device Hub windows")
    }
    let candidates = windows.compactMap { window -> (AXUIElement, CGRect)? in
        guard let title = attribute(window, kAXTitleAttribute as CFString) as? String,
              title.contains(name), let bounds = frame(of: window) else { return nil }
        return (window, bounds)
    }
    guard candidates.count == 1, let selected = candidates.first else {
        fail("expected exactly one visible Device Hub window for the selected device")
    }
    guard let windowInfo = CGWindowListCopyWindowInfo(.optionOnScreenOnly, kCGNullWindowID)
            as? [[String: Any]] else { fail("cannot list macOS windows") }
    let matches = windowInfo.compactMap { info -> CGWindowID? in
        guard let pid = info[kCGWindowOwnerPID as String] as? Int32,
              pid == app.processIdentifier,
              let bounds = info[kCGWindowBounds as String] as? [String: NSNumber],
              let x = bounds["X"]?.doubleValue, let y = bounds["Y"]?.doubleValue,
              let width = bounds["Width"]?.doubleValue,
              let height = bounds["Height"]?.doubleValue,
              abs(x - selected.1.minX) < 3, abs(y - selected.1.minY) < 3,
              abs(width - selected.1.width) < 3, abs(height - selected.1.height) < 3,
              let id = info[kCGWindowNumber as String] as? NSNumber else { return nil }
        return id.uint32Value
    }
    guard matches.count == 1, let id = matches.first else {
        fail("selected device window is hidden or cannot be uniquely matched")
    }
    return (app, selected.0, selected.1, id)
}

func number(_ value: String) -> Double {
    guard let parsed = Double(value), parsed.isFinite else { fail("invalid coordinate") }
    return parsed
}

func mouse(_ type: CGEventType, _ point: CGPoint) {
    guard let event = CGEvent(mouseEventSource: nil, mouseType: type,
                              mouseCursorPosition: point, mouseButton: .left) else {
        fail("cannot create macOS pointer event")
    }
    event.post(tap: .cghidEventTap)
}

let args = CommandLine.arguments
guard args.count >= 3 else { fail("usage: bridge window|tap|drag <device-name> ...") }
let command = args[1]
let (app, window, bounds, windowID) = selectedWindow(named: args[2])

switch command {
case "window":
    guard args.count == 3 else { fail("window takes no extra arguments") }
    guard CGPreflightScreenCaptureAccess() else {
        fail("grant Screen Recording access to the ARTEMIS terminal/host in System Settings")
    }
    let metadata = HubWindow(x: bounds.minX, y: bounds.minY,
                             width: bounds.width, height: bounds.height,
                             windowID: windowID)
    let json = try JSONEncoder().encode(metadata)
    print(String(decoding: json, as: UTF8.self))
case "tap", "drag":
    let expected = command == "tap" ? 11 : 13
    guard args.count == expected else { fail("invalid pointer arguments") }
    let geometryIndex = command == "tap" ? 6 : 8
    guard let expectedID = CGWindowID(args[geometryIndex]), expectedID == windowID,
          abs(number(args[geometryIndex + 1]) - bounds.minX) < 2,
          abs(number(args[geometryIndex + 2]) - bounds.minY) < 2,
          abs(number(args[geometryIndex + 3]) - bounds.width) < 2,
          abs(number(args[geometryIndex + 4]) - bounds.height) < 2 else {
        fail("Device Hub window changed since screen calibration")
    }
    let start = CGPoint(x: number(args[3]), y: number(args[4]))
    guard bounds.insetBy(dx: 1, dy: 1).contains(start) else {
        fail("pointer target lies outside the selected Device Hub window")
    }
    let end = command == "drag" ? CGPoint(x: number(args[5]), y: number(args[6])) : start
    guard bounds.insetBy(dx: 1, dy: 1).contains(end) else {
        fail("drag target lies outside the selected Device Hub window")
    }
    let duration = max(0, number(args[command == "tap" ? 5 : 7]))
    guard AXUIElementPerformAction(window, kAXRaiseAction as CFString) == .success,
          app.activate(options: []) else {
        fail("cannot raise the selected Device Hub window")
    }
    Thread.sleep(forTimeInterval: 0.05)
    guard NSWorkspace.shared.frontmostApplication?.processIdentifier == app.processIdentifier else {
        fail("Device Hub is not the frontmost application")
    }
    mouse(.mouseMoved, start)
    mouse(.leftMouseDown, start)
    let steps = command == "drag" ? max(2, Int(duration * 30)) : 1
    for step in 1...steps {
        if command == "drag" {
            let fraction = CGFloat(step) / CGFloat(steps)
            mouse(.leftMouseDragged, CGPoint(x: start.x + (end.x - start.x) * fraction,
                                             y: start.y + (end.y - start.y) * fraction))
        }
        Thread.sleep(forTimeInterval: duration / Double(steps))
    }
    mouse(.leftMouseUp, end)
    print("ok")
default:
    fail("unsupported command")
}
