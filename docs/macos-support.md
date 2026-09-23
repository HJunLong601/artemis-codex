# macOS 运行说明

Artemis 的标准运行路径支持 Apple Silicon（arm64）和 Intel（x86_64）Mac。Web 控制台、
Python 服务、Codex App Server、ADB 和 scrcpy 都没有依赖 Windows 专属组件；正常使用
不需要完整的 Xcode 或 Android Studio。Homebrew 首次安装时可能会触发 Apple Command
Line Tools 的系统安装提示。

iOS 是例外：模拟器和真机 XCUITest 自动化需要完整 Xcode、匹配的 iOS Runtime、
Appium 3 与 XCUITest Driver。可显式运行 `./start.sh --with-ios` 安装缺失的
Node.js/Appium/XCUITest，或运行 `bash scripts/setup_ios.sh --check` 只读检查；
Xcode/Runtime 和真机签名仍需用户自行配置。详细步骤与真机验收边界见
[README 中文版的 iOS 章节](../README_CN.md#ios-simulator)。

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
