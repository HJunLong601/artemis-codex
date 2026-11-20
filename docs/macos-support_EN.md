# Running Artemis on macOS

[中文](./macos-support.md)

The standard Artemis workflow supports Apple Silicon (arm64) and Intel (x86_64) Macs. The Web console, Python service, Codex App Server, ADB, and scrcpy work on macOS. Android operation requires neither full Xcode nor Android Studio. Homebrew may prompt you to install Apple Command Line Tools.

Controlling iOS devices requires full Xcode. See [iOS device setup](#ios-devices) below.

## First launch

```bash
git clone https://github.com/HJunLong601/artemis-codex.git
cd artemis-codex
./start.sh
```

The startup script runs `scripts/install_deps.sh` to prepare these dependencies.

1. Homebrew, if a required system tool needs installation and Homebrew is absent.
2. Android platform-tools (ADB), FFmpeg, and scrcpy.
3. `uv` and the Python 3.12+ project environment.
4. The official OpenAI Codex CLI.
5. Node.js 22.22.3+ and the Angular frontend dependencies.

Sign in to Codex after installation.

```bash
codex login
codex login status
```

If browser login cannot reach the local callback, use `codex login --device-auth`. Codex manages the credentials. Artemis does not read or copy authentication tokens.

## Android device setup

- Enable USB debugging in Developer options. Accept the computer's RSA fingerprint when connecting for the first time.
- macOS does not require the phone manufacturer's Windows USB driver.
- Wireless debugging requires a reachable network connection between the Mac and phone. USB is the simplest way to verify the first connection.
- Artemis installs the bundled Accessibility Helper APK when needed. Enable its accessibility service in the phone's settings.

<a id="ios-devices"></a>

## iOS device setup

### iOS Simulator

Install the target iOS Simulator runtime in Xcode. Run `./start.sh --with-ios` to prepare Appium 3 and a compatible XCUITest driver, then check dependencies with `bash scripts/setup_ios.sh --check`. Start the simulator and select it by UDID.

```bash
xcrun simctl list devices available
uv run artemis run "Open Settings and report the iOS version" --profile flash --platform ios --device-serial ios:YOUR_SIMULATOR_UDID
```

Replace the example UDID with a device from the list. MCP calls use `device_platform="ios"` and `device_serial="ios:YOUR_SIMULATOR_UDID"`. The Python SDK accepts the same serial.

### Physical iPhone

With Xcode 27 or newer, Artemis defaults to Device Hub for screen capture, app launch, recording, and coordinate gestures. Keep the screen-mirroring window visible. Automated taps on physical iPhones still await device verification.

1. Pair the iPhone with the Mac, trust the computer, and enable Developer Mode.
2. Select the iPhone in Xcode Device Hub and click **View Screen**. Grant macOS Accessibility and Screen Recording permissions to the terminal or IDE running Artemis. [Apple's window instructions](https://developer.apple.com/documentation/xcode/interacting-with-your-app-in-the-ios-or-ipados-simulator)
3. Run `bash scripts/setup_ios.sh --device-hub` to check dependencies. Check screenshot and window matching with `mobile_diagnose(device_platform="ios", device_serial="ios:YOUR_PHYSICAL_UDID", probe_device=true)`.

For UI hierarchy, text entry, or headless operation, select the Appium/XCUITest driver. Prepare WebDriverAgent development signing and a matching provisioning profile, then put the following settings in the local `.env`. [Appium device preparation](https://appium.github.io/appium-xcuitest-driver/latest/getting-started/device-setup/) · [Provisioning setup](https://appium.github.io/appium-xcuitest-driver/latest/getting-started/provisioning-profile/)

```dotenv
ARTEMIS_IOS_PHYSICAL_DRIVER=appium
ARTEMIS_IOS_XCODE_ORG_ID=YOUR_TEAM_ID
ARTEMIS_IOS_XCODE_SIGNING_ID=Apple Development
ARTEMIS_IOS_WDA_BUNDLE_ID=com.example.WebDriverAgentRunner
```

## Troubleshooting

| Situation | Action |
|---|---|
| A proxy blocks Homebrew or Codex downloads | Configure the network or proxy, then rerun `bash scripts/install_deps.sh`. For an enterprise CA, follow Codex documentation for `CODEX_CA_CERTIFICATE`. |
| Apple Command Line Tools are missing | Complete the system installation prompted by Homebrew, then rerun setup. |
| Browser login cannot reach localhost | Use `codex login --device-auth`. |
| Older Intel macOS | Current Homebrew scrcpy bottles may not support the OS. Upgrade macOS or use scrcpy's official macOS x86_64 static package. |
| Phone is unauthorized or cable is charge-only | Check `adb devices -l`, replace the cable if necessary, and accept RSA authorization. |
| Port 8000 is occupied | Use `./start.sh --port 8001` or stop the existing instance first. |

## Verification

```bash
codex login status
adb devices -l
uv run artemis doctor
./start.sh --port 8001
```

ADB and Codex are required by the default workflow. Missing FFmpeg or scrcpy affects video processing or mirroring, while some ADB-only operations can still run.
