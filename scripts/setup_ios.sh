#!/usr/bin/env bash
# Optional macOS iOS toolchain setup. It never changes Apple account, signing,
# provisioning, device trust, or Developer Mode settings.

set -euo pipefail

MODE="check"
case "${1:-}" in
    ""|--check) ;;
    --install) MODE="install" ;;
    --device-hub) MODE="device-hub" ;;
    *) echo "Usage: bash scripts/setup_ios.sh [--check|--install|--device-hub]" >&2; exit 2 ;;
esac

if [ "$(uname -s)" != "Darwin" ]; then
    echo "iOS Simulator and local XCUITest setup require macOS with full Xcode." >&2
    exit 2
fi

failed=false
developer_dir="$(xcode-select -p 2>/dev/null || true)"
if [[ "${developer_dir}" != */Contents/Developer ]] || ! xcodebuild -version >/dev/null 2>&1; then
    echo "[iOS] Full Xcode is not selected. Install Xcode, open it once, then select its Developer directory."
    failed=true
else
    echo "[iOS] Full Xcode: ${developer_dir}"
fi

if [ "${MODE}" = "device-hub" ]; then
    xcode_version="$(xcodebuild -version 2>/dev/null | sed -n '1s/^Xcode //p')"
    if ! [[ "${xcode_version}" =~ ^(2[7-9]|[3-9][0-9])\. ]]; then
        echo "[iOS] Device Hub physical-device control requires Xcode 27 or newer (found: ${xcode_version:-missing})."
        failed=true
    fi
    if ! xcrun --find swiftc >/dev/null 2>&1; then
        echo "[iOS] Swift compiler is unavailable; select full Xcode."
        failed=true
    fi
    if ! xcrun devicectl list devices --json-output - >/dev/null 2>&1; then
        echo "[iOS] CoreDevice discovery is unavailable; pair/trust the iPhone and enable Developer Mode."
        failed=true
    else
        echo "[iOS] CoreDevice discovery is available."
    fi
    echo "[iOS] Open Device Hub > select the iPhone > View Screen. Grant the ARTEMIS host Accessibility and Screen Recording in macOS System Settings."
    echo "[iOS] Device Hub does not require Appium, WDA, Apple Developer Team, or signing."
    if [ "${failed}" = true ]; then exit 1; fi
    exit 0
fi

if ! runtime_list="$(xcrun simctl list runtimes 2>/dev/null)" \
    || ! printf '%s' "${runtime_list}" | grep -Eq 'iOS [0-9]'; then
    echo "[iOS] No usable Simulator runtime. Install an iOS runtime in Xcode."
    failed=true
else
    echo "[iOS] Simulator runtime query passed."
fi

if ! command -v npm >/dev/null 2>&1 || ! command -v node >/dev/null 2>&1; then
    if [ "${MODE}" = "install" ] && command -v brew >/dev/null 2>&1; then
        echo "[iOS] Installing Node.js with Homebrew..."
        brew install node
        hash -r 2>/dev/null || true
    fi
fi
if ! command -v npm >/dev/null 2>&1 || ! command -v node >/dev/null 2>&1; then
    echo "[iOS] Node.js and npm are required for Appium. Install them, then rerun this check."
    failed=true
else
    echo "[iOS] Node.js $(node --version) and npm $(npm --version) are available."
fi

appium_version="$(appium --version 2>/dev/null || true)"
if ! [[ "${appium_version}" =~ ^3\. ]]; then
    if [ "${MODE}" = "install" ] && command -v npm >/dev/null 2>&1; then
        echo "[iOS] Installing Appium 3..."
        npm install -g appium@3
        hash -r 2>/dev/null || true
        appium_version="$(appium --version 2>/dev/null || true)"
    fi
fi
if ! [[ "${appium_version}" =~ ^3\. ]]; then
    echo "[iOS] Appium 3 is required (found: ${appium_version:-missing}). Run: npm install -g appium@3"
    failed=true
else
    echo "[iOS] Appium ${appium_version} is available."
    if ! installed_drivers="$(appium driver list --installed --json 2>/dev/null)"; then
        echo "[iOS] Could not query Appium drivers. Check the Appium home directory access."
        failed=true
    elif ! printf '%s' "${installed_drivers}" | grep -qi 'xcuitest'; then
        if [ "${MODE}" = "install" ]; then
            echo "[iOS] Installing the XCUITest driver..."
            appium driver install xcuitest
        else
            echo "[iOS] XCUITest driver is missing. Run: appium driver install xcuitest"
            failed=true
        fi
    fi
    if [ "${failed}" = false ]; then
        if appium driver doctor xcuitest; then
            echo "[iOS] XCUITest doctor completed."
        else
            echo "[iOS] XCUITest doctor found required setup issues."
            failed=true
        fi
    fi
fi

if xcrun devicectl list devices --json-output - >/dev/null 2>&1; then
    echo "[iOS] CoreDevice real-device discovery is available."
else
    echo "[iOS] CoreDevice real-device discovery is unavailable; check Xcode and device trust."
fi

if command -v security >/dev/null 2>&1; then
    identities="$(security find-identity -v -p codesigning 2>/dev/null || true)"
    if printf '%s' "${identities}" | grep -Eq 'Apple Development:|iPhone Developer:'; then
        echo "[iOS] An Apple Development signing identity is present."
    else
        echo "[iOS] No Apple Development signing identity found: Simulator use may work, but physical-device WebDriverAgent cannot be verified yet."
    fi
fi

echo "[iOS] Physical-device automation also requires a trusted/paired iPhone, Developer Mode, UI Automation, and a matching WebDriverAgent provisioning profile."
echo "[iOS] Set ARTEMIS_IOS_XCODE_ORG_ID and, when needed, ARTEMIS_IOS_WDA_BUNDLE_ID in your local .env; this script never creates certificates or registers devices."

if [ "${failed}" = true ]; then
    exit 1
fi
