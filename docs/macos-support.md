# macOS 运行说明

Artemis 的标准运行路径支持 Apple Silicon（arm64）和 Intel（x86_64）Mac。Web 控制台、
Python 服务、Codex App Server、ADB 和 scrcpy 都没有依赖 Windows 专属组件；正常使用
不需要完整的 Xcode 或 Android Studio。Homebrew 首次安装时可能会触发 Apple Command
Line Tools 的系统安装提示。

控制 iOS 设备需要完整 Xcode。模拟器与真机的准备步骤见下方的 [iOS 设备配置](#ios-devices)。

## 首次运行

```bash
git clone https://github.com/HJunLong601/artemis-codex.git
cd artemis-codex
./start.sh
```

首次启动统一调用 `scripts/install_deps.sh`，依次准备：

1. Homebrew（仅在需要安装系统工具且本机尚未安装时）；
2. Android platform-tools（ADB）、FFmpeg 和 scrcpy；
3. `uv` 与 Python 3.12+ 项目环境；
4. OpenAI 官方 Codex CLI；
5. Node.js 22.22.3+ 与 Angular 前端依赖。

Codex CLI 安装后仍需要用户完成一次账号登录：

```bash
codex login
codex login status
```

如果浏览器登录回调受限，使用 `codex login --device-auth`。登录态由 Codex 客户端维护，
Artemis 不读取或复制认证令牌。

## 设备准备

- 在 Android 手机的开发者选项中开启 USB 调试，首次连接时在手机上接受电脑的 RSA 指纹。
- macOS 不需要安装手机厂商的 Windows USB 驱动。
- 无线调试要求 Mac 和手机网络可达；有线 USB 是首次验证最直接的方式。
- Artemis 会在首次需要时安装随项目提供的 Accessibility Helper APK，用户仍需在手机设置中启用对应无障碍服务。

<a id="ios-devices"></a>

## iOS 设备配置

### iOS 模拟器

在 Xcode 安装目标 iOS Simulator Runtime，然后运行 `./start.sh --with-ios` 准备 Appium 3 和兼容的 XCUITest Driver。用 `bash scripts/setup_ios.sh --check` 检查依赖。启动模拟器后，可按它的 UDID 运行任务。

```bash
xcrun simctl list devices available
uv run artemis run "打开设置，查看 iOS 版本" --profile flash --platform ios --device-serial ios:YOUR_SIMULATOR_UDID
```

将示例中的 UDID 换成列表里的目标设备。使用 MCP 时指定 `device_platform="ios"` 与 `device_serial="ios:YOUR_SIMULATOR_UDID"`。Python SDK 同样可用这个序列号选择模拟器。

### iPhone 真机

在 Xcode 27 及以上，ARTEMIS 默认通过 Device Hub 获取画面、启动 App、录屏和执行坐标手势。这条路径需要保持投屏窗口可见，真机自动点击仍待验证。

1. 在 iPhone 上信任并配对这台 Mac，开启开发者模式。
2. 在 Xcode 的 Device Hub 中选中 iPhone 并点击 **View Screen**。在 macOS 系统设置中，给运行 ARTEMIS 的终端或 IDE 授予辅助功能和屏幕录制权限。[Apple 窗口操作说明](https://developer.apple.com/documentation/xcode/interacting-with-your-app-in-the-ios-or-ipados-simulator)
3. 运行 `bash scripts/setup_ios.sh --device-hub` 检查依赖。用 `mobile_diagnose(device_platform="ios", device_serial="ios:YOUR_PHYSICAL_UDID", probe_device=true)` 检查截图与窗口匹配。

需要控件层级、文本输入或无界面运行时，可切换到 Appium/XCUITest。先为 WebDriverAgent 准备开发签名和匹配的 Provisioning Profile，再将以下配置写入本机 `.env`。[Appium 真机准备](https://appium.github.io/appium-xcuitest-driver/latest/getting-started/device-setup/) · [签名配置](https://appium.github.io/appium-xcuitest-driver/latest/getting-started/provisioning-profile/)

```dotenv
ARTEMIS_IOS_PHYSICAL_DRIVER=appium
ARTEMIS_IOS_XCODE_ORG_ID=YOUR_TEAM_ID
ARTEMIS_IOS_XCODE_SIGNING_ID=Apple Development
ARTEMIS_IOS_WDA_BUNDLE_ID=com.example.WebDriverAgentRunner
```

## 已知阻碍

| 情况 | 影响与处理 |
|---|---|
| Homebrew 或 Codex 下载地址被代理拦截 | 安装器会显示失败项；配置网络/代理后重跑 `bash scripts/install_deps.sh`。企业自签 CA 可按 Codex 文档设置 `CODEX_CA_CERTIFICATE`。 |
| Apple Command Line Tools 未安装 | Homebrew 通常会触发系统安装流程；按 macOS 提示完成后重跑安装脚本。 |
| Codex 浏览器登录无法回调 localhost | 使用 `codex login --device-auth`。 |
| 较旧的 Intel macOS | Homebrew 的当前 scrcpy bottle 可能不覆盖旧系统。优先升级系统；否则使用 scrcpy 官方提供的 macOS x86_64 静态包。 |
| 手机未授权或 USB 线仅支持充电 | `adb devices -l` 会显示 `unauthorized` 或没有设备；更换数据线并重新接受 RSA 授权。 |
| 端口 8000 已占用 | 使用 `./start.sh --port 8001`，或先结束占用端口的旧实例。 |

## 验证

```bash
codex login status
adb devices -l
uv run artemis doctor
./start.sh --port 8001
```

`doctor` 中 ADB 和 Codex 是默认运行路径的关键检查；FFmpeg/scrcpy 缺失会影响视频处理或
投屏相关功能，但不妨碍所有纯 ADB 操作。
