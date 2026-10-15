<p align="center">
  <img src="./docs/assets/artemis-banner-cn.png?v=7" alt="ARTEMIS Banner" width="100%" />
</p>

<p align="center">
  <strong>让 AI 助手与测试套件像人一样直接操作真机。</strong>
</p>

<p align="center">
  <a href="./README.md">English</a> •
  <a href="./README_CN.md"><b>中文文档</b></a> •
  <a href="#workflow-showcase">全流程演示</a> •
  <a href="#fork-features">Fork 特性</a> •
  <a href="#model-configuration">模型配置</a> •
  <a href="#visual-location-cache">缓存后台</a> •
  <a href="#quick-start">快速上手</a> •
  <a href="#device-selection">设备选择</a> •
  <a href="#ios-simulator">iOS 支持</a> •
  <a href="#mcp-setup">MCP 接入 IDE</a> •
  <a href="#benchmarks">基准评测</a> •
  <a href="https://discord.gg/wF2FN4WHGY">Discord 社区</a>
</p>

<p align="center">
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/Python-3.12+-3776AB.svg?logo=python&logoColor=white" alt="Python 3.12+"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-Apache%202.0-blue.svg" alt="License: Apache-2.0"></a>
  <a href="https://modelcontextprotocol.io/"><img src="https://img.shields.io/badge/MCP-Native%20Server-8A2BE2.svg" alt="MCP Native"></a>
  <a href="./docs/codex-client-provider.md"><img src="https://img.shields.io/badge/Default-Codex%20Client%20%7C%20No%20API%20Key-111827.svg" alt="无需 API Key 的 Codex 客户端"></a>
  <a href="https://ai.google.dev/"><img src="https://img.shields.io/badge/Multimodal-Gemini%20%7C%20Claude%20%7C%20GPT--4o%20%7C%20Qwen--VL-4285F4.svg" alt="Multi-Model"></a>
  <a href="https://github.com/google-research/android_world"><img src="https://img.shields.io/badge/AndroidWorld-99%25%2B%20SOTA-success.svg" alt="AndroidWorld SOTA"></a>
</p>

<a id="fork-features"></a>
<a id="codex-client-integration"></a>
## 本 Fork 的新增与增强

> [!NOTE]
> 本仓库是 [google/artemis](https://github.com/google/artemis) 的社区 Fork，保留上游作者署名与 Apache 2.0 许可证。下表集中介绍本 Fork 的扩展；后续核心亮点、Flash/Pro 与 AndroidWorld 介绍说明 ARTEMIS 的通用能力。

| 方向 | 主要能力 |
|---|---|
| **Codex 接入** | 复用本机 ChatGPT 登录态，核心流程无需额外模型 API Key；优先使用桌面客户端内置 Codex，支持按角色配置模型。[接入与配置](#model-configuration) |
| **缓存与运行优化** | 视觉位置缓存减少重复定位，后台提供查询、失败案例和节省时间估算；结合图片压缩、Flash 延迟摘要及截图与历史加载优化。[缓存说明](#visual-location-cache) · [耗时记录](./docs/model-call-performance.md) |
| **跨平台与设备支持** | 统一 Windows/macOS/Linux 安装与诊断，完善真机优先选择、AVD 发现与复用；扩展 iOS 模拟器和真机驱动，真机自动点击仍待验收。[安装](#quick-start) · [设备](#device-selection) · [iOS](#ios-simulator) |

Codex 需要已登录且可访问所选模型的客户端；云 OCR 和其他 API Provider 仍需各自的 Key。缓存节省时间为历史耗时估算。详细配置与验收边界见下文及 [Provider 文档](./docs/codex-client-provider.md)。

<!-- 演示效果图 -->
<p align="center">
  <img src="./docs/assets/demo.gif" alt="Artemis 演示效果" width="100%" />
  <br>
  <em>实机演示：在 Google Maps 中设置驾车路线并计算总耗时，随后打开 YouTube 播放 Coldplay 的歌曲。</em>
</p>

## 核心亮点

* **跨 App 自动化**：根据自然语言指令，在 Android 上执行测试流程和日常任务。
* **多模态定位**：优先使用元素索引，对自绘界面提供坐标和视觉定位兜底。
* **IDE 内诊断**：通过 **MCP 协议**，让 **Antigravity、Claude Code、Windsurf** 操作测试设备，收集 **Logcat** 输出和截图。
* **Flash 执行**：使用观察、执行循环和异步历史摘要，单步通常约 **3–5 秒**。
* **Pro 探索**：单个动作执行前校验目标，被拦截的动作交回 Operator 处理。支持长时间探索和稳定性测试。
* **AndroidWorld 结果**：在 Google Research **AndroidWorld** 基准评测（100+ 多步任务）中取得 **99%+ 任务完成率**。

<a id="workflow-showcase"></a>
<a id="全流程演示"></a>
## Antigravity × ARTEMIS：全流程自主测试演示

**Antigravity** 通过 MCP 调用 **ARTEMIS**，将测试需求转为计划、真机操作和诊断报告：

<table width="100%">
  <tr>
    <td width="50%" align="center">
      <b>1. 输入测试提示词 (Task Dispatch)</b><br>
      <sub>在 Antigravity 中用自然语言描述测试需求与目标指标</sub><br><br>
      <img src="./docs/assets/workflow-1-prompt.png" width="100%" alt="步骤一：输入测试提示词" />
    </td>
    <td width="50%" align="center">
      <b>2. 生成测试方案 (Test Plan Generation)</b><br>
      <sub>自动拆解任务，生成详细测试步骤与架构图供确认</sub><br><br>
      <img src="./docs/assets/workflow-2-plan.png" width="100%" alt="步骤二：生成测试方案" />
    </td>
  </tr>
  <tr>
    <td width="50%" align="center">
      <b>3. 自主执行测试 (Autonomous Test Execution)</b><br>
      <sub>驱动真机操作、界面导航并实时分析性能指标</sub><br><br>
      <img src="./docs/assets/workflow-3-exec.png" width="100%" alt="步骤三：自主执行测试" />
    </td>
    <td width="50%" align="center">
      <b>4. 交付最终报告 (Comprehensive Final Report)</b><br>
      <sub>生成结构化测试报告，交付性能图表、结论与原始数据</sub><br><br>
      <img src="./docs/assets/workflow-4-report.png" width="100%" alt="步骤四：交付最终报告" />
    </td>
  </tr>
</table>

<a id="quick-start"></a>
<a id="快速上手"></a>
## 快速上手

确保电脑已连接 Android 实体机（已开启 **USB 调试**）或 Android 模拟器。一键启动脚本将会自动完成以下配置：
- **安装系统环境依赖**：自动检测并安装 ADB、scrcpy、FFmpeg、Codex CLI、Python（`uv`）运行时、Node.js 及项目依赖。
- **全局挂载 MCP 服务与测试准则 (Rules)**：主动引导并自动将全局 MCP 服务与 **Artemis 移动端测试思维准则 (`rules.md`)** 挂载至你使用的 AI IDE（支持 **Antigravity**、**Cursor**、**Claude Code**、**Codex**、**Windsurf**、**VS Code**、**Cline/Roo**、**OpenClaw**）。

当前分支默认复用本机已登录的 **Codex 客户端**，模型调用优先使用桌面客户端内置 Codex，再回退到 PATH 中的 CLI。首次使用按提示执行 `codex login`；Artemis 通过 Codex App Server 调用模型，无需配置 OpenAI API Key。配置方式和限制见 [Codex 客户端 Provider](./docs/codex-client-provider.md)。macOS 的兼容性、权限和旧系统限制见 [macOS 运行说明](./docs/macos-support.md)。

### macOS 与 Linux

```bash
# 1. 克隆代码仓库并进入目录
git clone https://github.com/HJunLong601/artemis-codex.git && cd artemis-codex

# 2. 一键启动
./start.sh
```

### Windows PowerShell

```powershell
# 1. 克隆代码仓库并进入目录
git clone https://github.com/HJunLong601/artemis-codex.git
cd artemis-codex

# 2. 一键启动
.\start.bat
```

> PowerShell 默认不会从当前目录查找可执行脚本，因此必须使用 `.\start.bat`，且命令末尾不要添加 `\`。如果使用传统命令提示符（CMD），则运行 `start.bat`。

> **提示**：启动后将自动在默认浏览器中打开 Web 控制台（`http://localhost:8000`），提供设备连接向导、实时投屏、任务演练与状态回放面板。你也可以通过命令行直接运行：`uv run artemis run "打开系统设置，找到电池选项并告诉我当前电量" --profile flash`。

### 刷新已有的全局安装

在已更新到最新提交的仓库中，先移除旧的全局工具环境，再安装当前分支：

```bash
git pull --ff-only origin main
uv tool uninstall artemis
uv tool install -e .
artemis doctor
```

安装过程会从 PyPI 获取 `codex-client-provider==0.1.0`。可通过 `uv tool list`
确认全局 Artemis 已正确安装。

<a id="device-selection"></a>
## 设备选择与 Android 模拟器

自动 Android 选择优先使用空闲真机，其次是空闲模拟器；全部繁忙时在就绪设备上排队。跨平台注册表未指定平台时优先选择可用真机，再考虑 Android 模拟器和 iOS 模拟器；CLI/MCP 任务未指定平台时仍默认 Android。多设备场景应显式传入序列号。

```dotenv
# .env：是否自动发现并选择虚拟设备，默认开启
ARTEMIS_EMULATOR_DISCOVERY_ENABLED=true
# 无已连接 Android 设备时，独立 MCP 任务使用的 AVD 名称（可选）
# ARTEMIS_AVD_NAME=YOUR_INSTALLED_AVD
```

- 关闭自动发现后，已连接模拟器仍在设备清单中可见，仍可显式指定序列号或通过诊断手动启动。
- SDK/AVD 从显式路径、Android 环境变量、PATH、已解析 ADB 所在 SDK 及常见目录发现，支持迁移后的 SDK 和自定义 AVD 目录。需要时配置 `ARTEMIS_EMULATOR_PATH`、`ANDROID_HOME` 或 `ANDROID_AVD_HOME`。
- 无已连接设备的独立 MCP 任务可复用或启动指定 AVD；未配置名称时仅在已安装 AVD 唯一时自动启动。多个 AVD 需要选择，不能假定名称或 `emulator-5554`。
- 通过 `mobile_diagnose(launch_avd="YOUR_INSTALLED_AVD")` 可在后台启动或复用同名运行实例；随后不带 `launch_avd` 轮询诊断，待 `emulator.status` 为 `ready`，使用实际序列号提交任务。使用 daemon 调度器时，先通过诊断启动，再提交任务。
- 启动就绪同时要求系统启动完成和 Android 包服务可用；再用 `mobile_diagnose(device_serial="SERIAL", probe_device=true)` 检查截图与 UI 层级。AVD 清单短时缓存并合并并发扫描，显式就绪刷新会清除缓存。

修改 `.env` 后重启 ARTEMIS/MCP。详细选择规则、路径发现和验收步骤见 [设备与模拟器说明](./docs/testing.md#android-emulator-without-a-physical-device)。

<a id="model-configuration"></a>
## 模型与图片配置

编辑 [`config/artemis.jsonc`](./config/artemis.jsonc)，修改后重启 ARTEMIS/MCP。配置文件只放模型与运行设置，其他 Provider 的密钥放在被 Git 忽略的 `.env`。

| 配置项 | 用途 |
|---|---|
| `default` | 主模型、Provider、推理强度与回退；当前仓库为 Codex `gpt-6.1-sol` / `low`，回退 `gpt-6-luna`。 |
| `nodes` | 单独覆盖 Planner、Operator、Explorer、Checker 等角色，未指定字段继承默认值；轻量摘要和历史压缩保留 Luna 档位。 |
| `presets` | 预设的 Provider/模型组合。 |
| `agent.flash` / `agent.pro` | Flash/Pro 行为，包括 Explorer 档位与步骤摘要设置。 |

源码优先读取上述配置，打包模板位于 [`artemis/resources/config/artemis.jsonc`](./artemis/resources/config/artemis.jsonc)。可用 `ARTEMIS_ARTEMIS_JSONC` 指定另一份已存在的配置；主模型、回退和各角色必须与所选 Provider 兼容。

图片默认等比缩至短边最多 720、长边最多 1600 像素，以 WebP 质量 70 编码，字节预算 768 KiB；超出预算时继续缩小，不放大小图。原始截图和坐标尺寸保持不变。环境变量、客户端路径优先级与适配层说明集中在 [Codex Provider 文档](./docs/codex-client-provider.md)。

检查本地环境：

```bash
codex login status
adb devices -l
uv run artemis doctor
```

<details>
<summary>验证记录：2026-09-19 无 API Key Android 真机流程</summary>

2026-09-19 已在连接的小米 Android 真机上重新验证零 Key Codex 路径。Pro 模式成功
打开小红书、搜索“秋日穿搭”、确认多条相关结果并停留在结果页，全程未点赞、收藏、
关注、评论或发布内容。

| 能力 | 验证结果 |
|---|---|
| Planner 与 Operator | 通过，复用已登录的 Codex 客户端 |
| 截图理解与步骤摘要 | 通过，使用 Codex 图片输入 |
| Checkpoint 与最终 Checker | 4/4 检查通过 |
| Outputter 报告 | 通过 |
| OpenAI、Google、Gemini、Anthropic、OpenRouter、xAI Key | 均未配置 |
| 云 OCR Key | 未配置；本次由 Accessibility Helper、UI 层级和 Codex 视觉完成 |

云 OCR 仍是可选的外部服务，显式启用时依然需要 `OCR_API_KEY`。默认的 UI XML
加 Codex 视觉路径不依赖该 Key。

</details>

<a id="visual-location-cache"></a>
## 视觉定位缓存与后台

定位顺序为 **ADB/UI 树 → 本地视觉位置缓存 → OCR/模型**。每台设备默认保留 10,000 条位置；一次视觉请求可额外学习最多 20 个可见控件，多目标查询只将未命中部分交给模型。

缓存支持整页精确匹配、局部控件校验与固定区域复用。设备、应用、Activity、尺寸和目标证据仍需匹配；遮挡、目标移动或歧义会阻止复用。复用的是位置，点击与结果验证仍由正常执行流程完成。

Web 控制台的 **页面缓存**（`/cache`）提供页面/元素查询、学习状态、命中率、耗时、失败案例与单页清理。新增的节省时间按同设备、同应用的平均视觉定位耗时折算命中收益，再扣除全部缓存查询开销；缺少样本时显示“暂无估算”，不代表任务总耗时实测差值。

缓存仅在本地保存位置元数据和指纹，不保存截图或完整模型回复。配置统一放在 `config/artemis.jsonc`：

```jsonc
{
  "agent": {
    "visual_location_cache": {
      "enabled": true,
      "batch_prefetch": true,
      "capacity_per_device": 10000
    }
  }
}
```

配置范围、旧变量迁移、数据库路径、复用保护和统计口径见 [缓存专题文档](./docs/visual-location-cache_CN.md)。

<details>
<summary>缓存后台示例</summary>

<p align="center">
  <img src="./docs/assets/visual-cache-dashboard.jpg" alt="ARTEMIS 页面缓存后台：总览、统计与学习状态" width="640" />
</p>

图中已裁去设备标识、业务应用名称和本地路径，数值仅展示界面，不作为性能评测结果。

</details>

<a id="ios-simulator"></a>
## iOS 支持（macOS）

目前通过 ARTEMIS 完整验收的 iOS 目标是 **iOS 模拟器**。需要 macOS、完整 Xcode、已安装的 iOS Simulator Runtime、Node.js/npm、Appium 3 及兼容的 XCUITest Driver。Android 默认启动流程不变；显式运行 `./start.sh --with-ios` 或 `bash scripts/install_deps.sh --with-ios`，才会安装缺失的 Node.js/Appium/XCUITest 组件。Xcode 与 iOS Runtime 仍需自行安装。只读检查可运行 `bash scripts/setup_ios.sh --check`。请按目标 iOS Runtime 选择兼容组合，参见 [XCUITest Driver 兼容要求](https://appium.github.io/appium-xcuitest-driver/latest/getting-started/system-requirements/)。

```bash
xcodebuild -version
xcrun simctl list runtimes
npm install -g appium@3
appium driver install xcuitest
appium driver doctor xcuitest
xcrun simctl list devices available

# 将 YOUR_SIMULATOR_UDID 替换为上方列表中的模拟器 ID。
# 仅在模拟器尚未启动时执行 boot。
xcrun simctl boot YOUR_SIMULATOR_UDID
xcrun simctl bootstatus YOUR_SIMULATOR_UDID -b
uv run artemis run "打开设置 > 通用 > 关于本机，报告 iOS 版本" --profile flash --platform ios --device-serial ios:YOUR_SIMULATOR_UDID
```

指定 `--platform ios` 后，CLI 也接受不带 `ios:` 前缀的模拟器 ID。MCP 调用 `mobile_run_task`、`mobile_get_device_state` 或 `mobile_diagnose` 时，可传 `device_platform="ios"` 与 `device_serial="ios:YOUR_SIMULATOR_UDID"`；其中 `mobile_diagnose` 设置 `probe_device=true` 可实际检查 XCUITest 截图和 UI 层级。ARTEMIS 会为会话管理本机 Appium 服务。未指定平台时仍默认 Android；下文的 ADB 与 Accessibility Helper 安装说明不适用于 iOS。更新旧版 MCP 服务后应重新安装或重载，否则旧进程可能仍只暴露 Android 参数。

### iPhone 真机：优先 Device Hub

ARTEMIS 通过 `devicectl` 发现已配对的 iPhone，在 CLI/MCP 使用 `ios:YOUR_PHYSICAL_UDID` 选中。Xcode 27 及以上的真机默认使用 **Device Hub** 驱动：CoreDevice 负责截图、启动应用和录屏；仓库内的 Swift 桥接程序将实时真机截图与可见的 Device Hub 画面匹配，再执行鼠标手势。**不需要**在手机安装 WebDriverAgent，也不需要 Apple Developer Team 或签名证书。手机须信任并配对 Mac、开启开发者模式；[打开 Device Hub](https://developer.apple.com/documentation/xcode/interacting-with-your-app-in-the-ios-or-ipados-simulator)，选中真机并点 **View Screen**。还须在 macOS 系统设置中为运行 ARTEMIS 的终端/IDE 授予 **辅助功能**和**屏幕录制**权限；脚本不会自行修改这些权限。

运行 `bash scripts/setup_ios.sh --device-hub` 只读检查真机依赖，再用 `mobile_diagnose(device_platform="ios", device_serial="ios:YOUR_PHYSICAL_UDID", probe_device=true)` 做实时截图/窗口校准探测。找不到指定窗口或画面匹配不可靠时，驱动会安全失败。Device Hub 路线支持视觉和坐标动作，**不提供 iOS 控件层级**，目前不支持控件定位、文本输入及部分系统动作，也不能无界面运行。Swift 桥接程序在首次使用时由本机 Xcode 编译，无需用户编译或安装 iPhone 项目。

```bash
uv run artemis run "打开设置并报告 iOS 版本" --profile flash --platform ios --device-serial ios:YOUR_PHYSICAL_UDID
```

仅当需要控件层级、文本输入或无界面运行时，才显式切换真机的 Appium/XCUITest 备选方案。它仍需要 WDA 签名、Apple Developer Team、有效的 **Apple Development** 身份和匹配的 Provisioning Profile。仅在有权使用对应 Team/Profile 时，把下列值写入被 Git 忽略的 `.env`（WDA Bundle ID 须由 Profile 覆盖）：

```dotenv
ARTEMIS_IOS_PHYSICAL_DRIVER=appium
ARTEMIS_IOS_XCODE_ORG_ID=YOUR_TEAM_ID
ARTEMIS_IOS_XCODE_SIGNING_ID=Apple Development
ARTEMIS_IOS_WDA_BUNDLE_ID=com.example.WebDriverAgentRunner
# ARTEMIS_IOS_SHOW_XCODE_LOG=true  # 仅供本地排错
```

默认值也可显式设为 `ARTEMIS_IOS_PHYSICAL_DRIVER=device-hub`。`--with-ios` 安装器和 `setup_ios.sh --check` 仍用于模拟器/Appium 路线；Device Hub 不需要 WDA。Appium 备选方案可参考官方[真机准备](https://appium.github.io/appium-xcuitest-driver/latest/getting-started/device-setup/)和 [Provisioning 配置](https://appium.github.io/appium-xcuitest-driver/latest/getting-started/provisioning-profile/)。此前已在真机上验证 Device Hub 手动交互；Appium/WDA 路线在缺少有效签名身份的主机上失败。新的 Device Hub 自动化桥接已有单元与编译检查，但自动点击尚未经过真机验收。个人截图请放在 Git 忽略的 `artifacts/ios/`，不要提交到开源仓库。

<a id="mcp-setup"></a>
<a id="mcp"></a>
<details>
<summary><b>Codex / Antigravity / Claude Code / Windsurf MCP 配置（点击展开）</b></summary>

<br>

ARTEMIS 内置原生 **Model Context Protocol (MCP)** 服务。只需将以下配置加入你的 IDE 配置文件中，即可在编写代码时直接驱动真机：

### 1. 一键自动安装到 IDE（推荐）

运行 `./start.sh`（macOS/Linux）或 `.\start.bat`（Windows PowerShell）启动脚本时，会主动询问是否自动挂载全局 MCP 与测试行为准则（支持跳过并在之后随时手动执行以下命令挂载）：

```bash
# 一键安装全局 MCP 服务与 Rules 到 Antigravity / Jetski：
uv run artemis mcp --install antigravity

# 或一键安装到所有支持的 AI IDE（包括 Codex）：
uv run artemis mcp --install all
```

> **提示**：你也可以在首次运行 `uv run artemis init` 配置向导时，交互式完成 IDE 的 MCP 自动挂载。
> **进阶提示**：如果希望在任意目录下都不需要加 `uv run` 就能全局直接使用 `artemis` 命令，可在项目根目录下执行一次 `uv tool install -e .`。

### 2. 手动配置（可选）

如果你习惯手动复制配置，可运行 `uv run artemis mcp --generate-config <client>`（例如 `codex` 或 `antigravity`）获取对应的 TOML 或 JSON 配置。请将 `command` 填写为项目下 `.venv` 虚拟环境中的 Python 绝对路径，并将 `/path/to/artemis` 替换为项目实际路径：

* **Codex** (`~/.codex/config.toml`)：
```toml
[mcp_servers.artemis]
command = "/path/to/artemis/.venv/bin/python"
args = ["-m", "mcp_server"]
cwd = "/path/to/artemis"

[mcp_servers.artemis.env]
PYTHONUNBUFFERED = "1"
PYTHONPATH = "/path/to/artemis"
```

* **Antigravity** (`~/.gemini/jetski/mcp_config.json`)：
```json
{
  "mcpServers": {
    "artemis": {
      "command": "/path/to/artemis/.venv/bin/python",
      "args": ["-m", "mcp_server"],
      "cwd": "/path/to/artemis",
      "env": {
        "PYTHONUNBUFFERED": "1"
      },
      "tools": {
        "mobile_run_task": { "eager": true },
        "mobile_manage_task": { "eager": true },
        "mobile_get_device_state": { "eager": true },
        "mobile_inspect_trace": { "eager": true },
        "mobile_diagnose": { "eager": true }
      }
    }
  }
}
```

* **Claude Desktop** (`claude_desktop_config.json`)：
```json
{
  "mcpServers": {
    "artemis": {
      "command": "/path/to/artemis/.venv/bin/python",
      "args": ["-m", "mcp_server"],
      "cwd": "/path/to/artemis"
    }
  }
}
```

### 3. 挂载智能体行为规范 Rules（强烈推荐）

为使 AI 编程助手具备资深移动端测试工程师的严谨思维，避免凭空臆测 UI 交互，我们提供了专属的测试思维行为规范文件 [`mcp_server/rules.md`](./mcp_server/rules.md)（涵盖**可运行代码原则与真机探索**、**Flash 与 Pro 任务路由策略**、**延迟与时间补偿机制**以及**“动态优先、坐标兜底”定位模式**）。

你可以将 [`mcp_server/rules.md`](./mcp_server/rules.md) 挂载或复制到你的 AI IDE 规则配置中：
* **Antigravity**：将 `rules.md` 内容添加至工作区规则（Workspace Rules）或全局规则设置中。
* **Claude Code**：运行 `artemis mcp --install claude` 自动安装规则至 `~/.claude/rules/artemis.md`（只安装到单一位置——Claude Code 会同时加载 `~/.claude/CLAUDE.md` 与 `~/.claude/rules/*.md`，重复安装会浪费上下文）。
* **Cursor**：将内容复制到 `.cursorrules` 文件或在 `.cursor/rules/artemis.mdc` 中创建新规则。
* **Codex**：将内容添加至 `~/.codex/AGENTS.md`（或当前生效的 `AGENTS.override.md`）。
* **Windsurf / OpenClaw**：将内容添加到工作区规则或全局 System Prompt 中。

> 更多规范设计细节与 MCP 架构说明，请参阅 [MCP Server 文档](./mcp_server/README.md)。

### 4. 在 IDE 中体验真机协同
在 Codex / Antigravity / Claude Code 对话框中直接输入：
> *"请帮我把刚刚修改的代码编译成 APK 并安装到手机上，打开登录页面输入测试账号，验证登录后是否有异常弹窗，并把最终页面截图回传。"*

</details>

<a id="python-sdk"></a>
<details>
<summary><b>Python SDK 集成（点击展开）</b></summary>

<br>

开发电脑只需安装零运行时依赖的薄客户端；ADB、Agent、模型与图像处理全部留在设备主机：

```powershell
uv add "artemis-client @ git+https://github.com/HJunLong601/artemis-codex.git#subdirectory=packages/artemis-client"
```

```python
import asyncio
from artemis_client import ArtemisClient


async def main():
    client = ArtemisClient(
        "http://artemis-host:8000",
        device_serial="emulator-5554",  # 可选：指定目标设备序列号（不传则自动选择空闲设备）
        default_profile="flash",  # "flash" 极速校验 或 "pro" 深度推理自愈
    )

    result = await client.run(
        "打开系统设置，进入『电池』页面，验证是否正常显示电量百分比，确认页面无异常报错弹窗。",
    )

    assert result.succeeded, f"测试执行失败: {result.error or result.status}"
    print(f"✅ 测试通过！设备: {result.device_serial} | Trace ID: {result.trace_id}")


if __name__ == "__main__":
    asyncio.run(main())
```

使用 iOS 模拟器时，把 SDK 客户端的 `device_serial` 设为 `"ios:YOUR_SIMULATOR_UDID"`；设备主机仍需满足[上述 iOS 环境要求](#ios-simulator)。

</details>

## 使用方式

<p align="center">
  <img src="./docs/assets/artemis-ui-showcase.png" alt="Artemis 可视化控制台" width="100%" />
  <br />
  <sub><b>控制台功能概览</b>：<b>① 视图切换</b>（主页与工作区） · <b>② 运行模式与回放</b>（Flash/Pro 状态与视频回放） · <b>③ 实时感知推理流</b>（动作感知、目标坐标与结构化总结） · <b>④ 提示词输入坞 (Prompt Dock)</b>（自然语言下发） · <b>⑤ 任务队列看板</b>（生命周期与历史回溯）</sub>
</p>

* **Web 可视化测试控制台 (`uv run artemis ui`)**：集成设备实时投屏与交互面板，支持通过自然语言下发测试用例，实时观测推理步骤、操作轨迹、截图留存与异常状态回放；支持在任意终端使用 `uv run artemis restart`、`uv run artemis stop`、`uv run artemis status` 一键重启、关停或查看服务状态；
* **原生 MCP 协议 (IDE 协同)**：作为标准 MCP 服务器接入 **Antigravity、Claude Code、Windsurf** 等开发环境，在 IDE 中直接驱动真机完成自动化测试与 Bug 复现验证；
* **开发者命令行 CLI (`uv run artemis run`)**：支持通过终端直接执行自动化测试用例、探索性稳定性巡检或 AndroidWorld 基准评测，提供高保真结构化终端输出；
* **Python SDK**：作为标准 Python 库集成至现有自动化测试框架（如 pytest）或 CI/CD 流水线，提供基于 Pydantic 的强类型结构化结果与断言支持。

<a id="on-device-helper"></a>
## ARTEMIS 会在手机上安装什么

首次在某台设备上执行任务时，ARTEMIS 会安装 **Artemis Accessibility Helper**：一个用于读取屏幕布局的小型无障碍服务，
不占用 UiAutomation 连接。使用 UiAutomation 的工具需要启用 `FLAG_DONT_SUPPRESS_ACCESSIBILITY_SERVICES`，否则可能使该服务暂停运行。
手机上会出现一条折叠的"Artemis test helper is running"通知，以及"设置 > 无障碍"里的一个新条目，都是它。
它只监听手机本机回环地址，不向外发送任何数据。

* 预先安装（避免首个任务多等约 3 秒）：`uv run artemis helper install`
* 查看状态：`uv run artemis helper status` / `uv run artemis doctor`
* 随时移除：`uv run artemis helper uninstall`
* 改用 UIAutomator2：在 `.env` 中设置 `ARTEMIS_HIERARCHY_BACKEND=uiautomator`
* 禁止自动安装：在 `.env` 中设置 `ARTEMIS_HELPER_AUTO_INSTALL=false`

任务中途 helper 失效时，ARTEMIS 会回退到 UIAutomator2，并在任务时间线、`mobile_manage_task` 状态和最终报告里明确说明。

<a id="benchmarks"></a>
<a id="基准评测"></a>
## 基准评测：AndroidWorld (SOTA 99%+)

Artemis 在 Google Research 发布的 [AndroidWorld](https://github.com/google-research/android_world) 基准评测中取得 **99%+ 任务完成率**。该评测涵盖 20+ 款应用与 100+ 项多步任务。

<p align="center">
  <img src="./docs/assets/androidworld_leaderboard.png?v=2" alt="AndroidWorld 评测基准对比" width="100%" />
</p>

## ARTEMIS 是如何建构的

* **执行前校验与动作序列**：Pro 在下发单个动作前，通过实时 UI 树和像素校验目标。快速动作序列用于处理瞬态控件，无需等待下一轮模型响应。
* **元素定位**：结合无障碍树、OCR 和视觉模型，支持 Canvas、Compose、Flutter 等自绘界面。
* **共享历史压缩**：Flash 和 Pro 将较早的截图替换为视觉摘要，并把已完成步骤压缩为可检索的历史分块。上下文阈值决定何时替换原始记录。

<p align="center">
  <img src="./docs/assets/artemis_architecture_diagram.png" alt="ARTEMIS 架构系统示意图" width="100%" />
</p>

## 运行模式：Flash vs. Pro

ARTEMIS 提供两种运行模式以适应不同的自动化需求：

* **Flash 模式 (`--profile flash`)**：使用历史压缩的响应式循环（单步约 3–5 秒）：单个模型观察实时屏幕、思考、执行，不经过图编排，适合常规确定性 UI 操作。默认不限步数（`agent.flash.max_turns`，0 表示不限），因为历史是被压缩而不是被截断：Flash 与 Pro 共用同一套会话记录账本（相对测试时间 `T+mm:ss`、截图折叠为视觉摘要、更早的步骤分块归档并可通过 `search_history` / `replay_steps` 按需召回），并可调用 `video_analyzer` 分析整段会话录屏；对自动消失的控制栏、toast 等瞬态控件，用 `click_sequence` 把多次点击串成一个原子序列。*局限性*：没有任务计划与笔记、没有执行前安全网、没有检查点校验与最终报告、不能执行 ADB 命令。
* **Pro 模式 (`--profile pro`)**：包含计划和校验的执行流程（单步约 15–40 秒），由多智能体图编排：**Planner** 维护一份带里程碑和 `verify` / `assert` 检查项的 Markdown 任务计划；**Operator** 按计划执行，拥有全套工具（Explorer 元素定位，其 `flash` / `pro` / `ultra` 三档感知深度是按运行模式设置的用户配置——`config/artemis.jsonc` 里的 `pro.explorer.mode` / `flash.explorer_mode` 或 `--explorer-pro-mode`——智能体自身不会选择档位；笔记、历史召回、视频分析、ADB 诊断）。单个动作执行前都经过 **Safety Net** 校验（XML 优先、像素兜底），而一轮里的多个动作则作为**快速动作序列 (fast-action burst)** 连发，赶在瞬态控件消失前完成操作。动作被拦截或失败时会打开一条**执行事故 (execution incident)**，持续留在 Operator 的上下文里直到后续动作执行成功，恢复由 Operator 自己完成，不再有独立的修复智能体。只读的 **Checker** 校验计划中的检查点，并在退出前对照原始目标做终审（`--verification-level`：`off` / `final`（默认）/ `checkpoints` / `strict`），计划里程碑的改动会得到建议式复核。支持 100+ 步的长程复杂业务流、`[Loop:continuous]` 持续监控以及可选的书面报告。

## 路线图

- [ ] **Android Studio 深度集成**：推出官方 IDE 插件与协同工作流，支持在 Android Studio 内直接进行自动化测试、设备交互与断点调试。
- [x] **iOS 模拟器支持**：已实现并在本机验证模拟器发现与 Appium/XCUITest 控制。
- [ ] **iOS 真机支持**：Device Hub 已作为默认实现，具备可视手势、CoreDevice 截图/启动/录屏和安全校准；自动点击的真机验收仍待完成。Appium/WDA 为需签名的显式备选。
- [ ] **端侧轻量化模型**：支持离线运行的轻量级 Edge VLM，实现低延迟与隐私安全的本地自动化。
- [ ] **实时语音双工交互**：支持自然语音下发任务与实时打断（Barge-in）控制。

## 社区与贡献

欢迎通过以下方式参与项目建设：
* **Star 本项目**以关注最新进展与更新
* 加入 [Discord 社区](https://discord.gg/wF2FN4WHGY) 参与技术探讨与功能建议
* 在本 Fork 提交 [Issue](https://github.com/HJunLong601/artemis-codex/issues) 或发起 [Pull Request](https://github.com/HJunLong601/artemis-codex/pulls)
* 关注[上游 google/artemis 项目](https://github.com/google/artemis)，了解原项目路线图和版本发布

## 开源许可证

本项目基于 [Apache License 2.0](LICENSE) 协议开源。

本仓库基于 [google/artemis](https://github.com/google/artemis)，并包含由 [Minitap, Inc.](https://github.com/minitap-ai/mobile-use) 开发的源代码；原始版权和许可证声明均予以保留。

### 第三方代码

非 Google 所有的代码位于 [`third_party/`](third_party/) 目录下，每个上游项目一个子目录，并各自附带 `LICENSE` 与 `METADATA`。

- [`third_party/mobile_use`](third_party/mobile_use/) – 来自 Minitap, Inc. 的 [mobile-use](https://github.com/minitap-ai/mobile-use) 的部分代码，Apache License 2.0。
