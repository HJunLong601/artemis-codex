# iOS 设备控制技术方案

## 当前流程状态

| 字段 | 值 |
| --- | --- |
| 当前阶段 | 技术方案 |
| 当前门禁 | 技术方案门禁 |
| 门禁状态 | 已确认通过 |
| 当前允许动作 | 固化确认版并进入架构设计方案阶段 |
| 下一步 | 输出架构设计方案评审版 |

## 1. 背景

ARTEMIS 当前拥有通用 `BaseDeviceDriver`、统一控制器、Planner、Operator、Checker、视觉定位、历史压缩和多设备锁，但实际设备执行、发现、诊断与录屏均以 Android ADB/UIAutomator2/scrcpy 为基础。项目路线图已列出 iOS Simulator 与真机支持。

本方案选择当前稳定版 Appium + XCUITest Driver + WebDriverAgent 作为 iOS 执行后端。ARTEMIS 继续负责 AI 规划、感知、校验、历史和任务编排；Appium/WDA 负责 Apple 平台的设备会话、UI 层级和输入动作。

## 2. 文档状态

- 状态：评审版
- 来源评审版：无
- 来源输入物：`2026-09-22-ios-device-control-input-normalization.md`
- 确认人：待用户确认
- 确认日期：待确认
- 后续变更是否需要重新进入技术方案门禁：是。更换 iOS 执行后端、改变最低版本、扩大到非 macOS Host 或要求绕过 WDA 时必须重新评审。

## 3. 理解完整性审计

| 检查项 | 状态 | 依据/说明 |
| --- | --- | --- |
| 技术目标 | 已明确 | 先建立 Xcode/Simulator 环境，后续让 ARTEMIS 支持 iOS Simulator 与真机 |
| 技术约束 | 已明确 | Apple Silicon Mac；真机需 Xcode/WDA 签名；iOS 无任意 shell；当前代码存在 Android 假设 |
| 引擎与外部能力 | 已明确 | Appium、XCUITest Driver、WebDriverAgent、完整 Xcode、Simulator Runtime |
| API 与资源影响 | 已明确 | 使用 WebDriver/Appium HTTP API；需要 Simulator Runtime、WDA 构建产物与可选 ffmpeg |
| 包管理影响 | 已明确 | Xcode 由 Apple 提供；Appium/XCUITest 后续使用 Node/npm；Python 主包不在首步新增依赖 |
| 兼容性策略 | 已明确 | 首个 MVP 使用当前稳定 Xcode 与其自带最新 iOS Runtime；旧版本矩阵后续按需求扩展 |
| 推荐路线 | 已明确 | 当前稳定版 Appium + XCUITest Driver + WDA，Simulator-first |
| 备选路线 | 已明确 | 直接 WDA、原生 XCUIAutomation、Maestro、idb |
| 风险与缓解 | 已明确 | 下载体积、签名、坐标空间、能力差异、版本耦合、启动延迟均有缓解方案 |
| 验证策略 | 已明确 | 工具链命令、Runtime、模拟器 bootstatus、截图与后续动作闭环 |
| 开放问题 | 已列出 | 旧 iOS 支持矩阵、真机 Team、首版并发规模不阻塞 Simulator-first |

### 审计结论

- 可以产出技术方案门禁产物。
- 当前开放问题不阻塞完整 Xcode 与最新稳定 iOS Simulator Runtime 的首步安装。

## 4. 技术目标

1. 在当前 Apple Silicon Mac 上安装完整、稳定版 Xcode，并切换 active developer directory。
2. 安装一个与 Xcode 匹配的稳定 iOS Simulator Runtime。
3. 创建或选择一个兼容的 iPhone Simulator，完成启动、bootstatus、设备列表与截图验证。
4. 后续通过 Appium/WDA 为 ARTEMIS 提供 screenshot、page source、tap、long press、swipe、text input、app lifecycle、active app 和 screen recording。
5. 保持 Planner、Operator、Checker、Explorer、历史和坐标归一化的上层能力尽量平台无关。
6. 对 iOS 不支持的能力进行显式能力协商，不使用静默空实现伪装成功。

## 5. 技术约束

- Host 必须是安装完整 Xcode 的 macOS；当前机器满足 Apple Silicon 和磁盘要求。
- 安装执行时 Mac App Store 当前稳定基线为 Xcode 27.0；当前主机为 macOS 26.6.2，已完成安装和首次启动验证。
- 首个 Simulator Runtime 采用 Xcode 27.0 对应的稳定 iOS 27.0 Runtime；不在首步额外下载旧 Runtime。
- 不安装 Xcode beta，不将 beta 工具链作为项目基线。
- 真机阶段必须经过设备信任、Developer Mode、Enable UI Automation、WDA 签名与 provisioning profile 配置。
- 非越狱 iOS 不提供 Android 等价的任意设备 shell。
- XCTest 动作坐标与截图物理像素可能存在 `pixelRatio`、安全区域和方向差异，后续 Driver 必须显式转换坐标空间。
- 不把 Apple ID、Team ID、证书私钥、keychain 密码或 provisioning profile secret 写入仓库。
- 首个环境安装任务不新增 XCTest/UI Test 自动化：当前目标是工具链可用性验证，WDA 自身使用 XCTest 不等于项目启用 XCTest/UI Test 测试任务。

## 6. 推荐技术路线

### 6.1 总体路线

```text
ARTEMIS Planner / Operator / Checker / Explorer
                    |
          platform-neutral actions
                    |
       IosXcuiTestDriver (future)
                    |
          Appium WebDriver HTTP API
                    |
       XCUITest Driver -> WebDriverAgent
                    |
         iOS Simulator / iOS device
```

### 6.2 环境安装路线

1. 从 Mac App Store 安装 Apple 当前稳定版 Xcode 27.0。
2. 启动 Xcode 完成首次组件安装；必要时由用户完成 Apple ID、Touch ID、系统管理员密码或许可确认。
3. 设置 `/Applications/Xcode.app/Contents/Developer` 为 active developer directory。
4. 执行 Xcode first-launch 初始化并验证 `xcodebuild -version`。
5. 安装或确认 iOS 27.0 Simulator Runtime。
6. 动态读取可用 Runtime 与 iPhone device type，不硬编码机型；优先选择最新可用的标准尺寸 iPhone Pro 模拟器。
7. 创建或复用专用模拟器，命名使用 `ARTEMIS iPhone <runtime>`，避免污染用户已有设备。
8. 启动并等待 `xcrun simctl bootstatus <udid> -b` 完成。
9. 验证 `simctl list`、`devicectl list devices`、截图输出和 Simulator UI。

### 6.3 后续自动化后端路线

1. 安装 Appium 3.7.0 与匹配的 XCUITest Driver 12.13.1。
2. 使用 `appium driver doctor xcuitest` 检查依赖。
3. Simulator 上先验证 WDA session、screenshot、page source、tap、swipe、input、activate/terminate app。
4. 再实现 ARTEMIS `IosXcuiTestDriver`，优先使用项目已有异步 `httpx` 调用 Appium HTTP API。
5. Simulator 闭环稳定后再进入真机签名与端口转发，不让真机配置阻塞平台抽象开发。

## 7. 依赖能力

| 类型 | 选择 | 职责 | 风险 |
| --- | --- | --- | --- |
| Apple 工具链 | Xcode 27.0 stable | SDK、Simulator、`xcodebuild`、`simctl`、`devicectl` | 下载体积大；首次启动可能需要交互 |
| Simulator Runtime | iOS 27.0 stable | 首个 iOS 虚拟设备运行环境 | 下载耗时；旧系统行为不能覆盖 |
| 自动化引擎 | Appium 3.7.0 | 会话生命周期与 WebDriver API | Node 版本和插件版本需锁定 |
| iOS Driver | Appium XCUITest Driver | 把 WebDriver 命令转换为 XCTest/WDA 操作 | 与 Xcode/iOS 版本存在兼容矩阵 |
| 设备代理 | WebDriverAgent | Simulator/真机 UI 层级与输入动作 | 真机需签名；启动时间与稳定性需治理 |
| HTTP 客户端 | 项目已有 `httpx` | ARTEMIS 异步调用 Appium | 需要处理 session、timeout、retry 和错误分类 |
| 录屏 | Appium screen recording / XCTest recording | iOS 视频证据 | 真机能力受版本和安全参数限制；无音频 |
| 辅助设备管理 | `simctl`、`devicectl`；后续可选 idb | 发现、启动、安装、日志与设备池辅助 | idb 不作为真机 UI 主驱动 |
| 可选视频工具 | ffmpeg | Appium MJPEG 录屏与视频处理 | PATH 与版本差异 |
| 存储 | 复用 ARTEMIS trace/session 目录 | 截图、层级、视频、日志证据 | 需增加 platform 元数据和坐标信息 |

## 8. API、资源、打包与迁移影响

### 8.1 API

- 后续增加 `DevicePlatform.IOS`。
- 把 Android 的 `package_name` 语义提升为跨平台 `app_id`，iOS 使用 bundle identifier。
- 为 Driver 增加 capability 描述，至少覆盖 shell、back key、system settings、screen recording、app install、biometric、location。
- `ScreenData` 后续增加或派生 `screenshot_size`、`interaction_viewport`、`pixel_ratio`、`orientation`、`safe_area`。

### 8.2 资源

- Xcode 与 Runtime 属于主机级资源，不进入仓库。
- WDA 源码/构建产物由 Appium XCUITest Driver 管理；不复制到主仓库。
- 真机证书和密钥只进入用户 keychain 或 CI secret store。

### 8.3 包管理

- 首步不修改 `pyproject.toml`。
- Appium/XCUITest 应使用独立 Node 锁定策略或安装脚本，避免依赖用户全局漂移；具体边界在架构门禁确定。
- 若未来加入 Python 侧 iOS 专用依赖，应放入可选 `ios` extra，而不是扩大所有 Android 用户的默认安装。

### 8.4 迁移

- Android 默认行为保持不变。
- iOS 支持以新增平台分支接入，不替换现有 Android Driver。
- 原有 Android-only 工具按 capability 过滤，不在 iOS 会话中暴露不可执行动作。

## 9. 决策矩阵与备选方案

| 方案 | Simulator | 真机 | 动态层级与动作 | ARTEMIS 接入成本 | 维护成本 | 结论 |
| --- | --- | --- | --- | --- | --- | --- |
| Appium + XCUITest + WDA | 支持 | 支持 | 完整 | 中 | 中 | 推荐主方案 |
| 直接调用 WDA | 支持 | 支持 | 完整 | 中 | 高 | 暂不选；需自行维护 WDA 生命周期与兼容性 |
| 原生 XCTest/XCUIAutomation | 支持 | 支持 | 完整 | 高 | 中 | 适合固定测试，不适合 Python AI 动态控制主路径 |
| Maestro | 支持 | 当前本地真机不支持 | 高层声明式 | 低 | 低 | 可作旁路测试，不作为主后端 |
| idb | 支持 | 管理能力为主 | 真机 UI 控制有限 | 中 | 中 | 只作为设备管理补充 |

## 10. 兼容性策略

- 环境 MVP：Xcode 27.0 + iOS 27.0 Simulator Runtime + Apple Silicon macOS 26.6.2。
- Appium/XCUITest 阶段：选择支持当前 Xcode/iOS 的稳定 Driver 版本并锁定；升级 Xcode 时先运行 doctor 与 Simulator 冒烟测试。
- 真机阶段：首批支持范围以实际设备 OS 与 Xcode/WDA 兼容交集为准；在设备信息明确前不承诺旧 iOS 范围。
- 后续最低 iOS 版本矩阵属于兼容性扩展，不阻塞最新稳定 Simulator MVP。

## 11. 风险与缓解

| 风险 | 影响 | 缓解 |
| --- | --- | --- |
| Xcode/Runtime 下载体积大、耗时长 | 安装可能中断或占用大量磁盘 | 安装稳定版；下载前检查磁盘；使用系统/App Store 原生恢复机制 |
| 首次安装需要 Apple ID、Touch ID 或管理员密码 | 自动执行可能暂停 | 遇到系统认证立即交给用户，不读取或代填凭据 |
| Xcode 与 XCUITest Driver 版本耦合 | 升级后 WDA 可能失败 | 锁定稳定版本；升级前 doctor + Simulator 冒烟 |
| 真机 WDA 签名复杂 | 无法启动真机会话 | Simulator-first；真机单独任务处理 Team、bundle id、profile 和 Developer Mode |
| 截图像素与交互坐标不一致 | AI 点按偏移 | 独立维护 screenshot 与 interaction 坐标空间，使用 pixelRatio/viewport 转换 |
| iOS 不支持任意 shell/部分系统动作 | 工具调用失败或误报成功 | capability 过滤；明确 `UnsupportedOperation`；Prompt 注入平台约束 |
| WDA page source 较慢 | 每步延迟增大 | 可配置 snapshot timeout、按需层级、视觉 fallback 和缓存策略 |
| 录屏能力与 Android 不同 | 视频时间轴或格式不一致 | 抽象 recording backend；记录平台、开始时刻、首帧偏移和能力限制 |
| 多设备端口冲突 | 并发 session 启动失败 | 架构阶段设计 per-device Appium/WDA port allocation 和锁 |

## 12. 验证策略

### 12.1 首步环境安装验收

| 检查 | 方法 | 通过条件 | 证据 |
| --- | --- | --- | --- |
| Xcode 安装 | 检查 `/Applications/Xcode.app` | App 存在且可启动 | 路径与版本输出 |
| Developer Directory | `xcode-select -p` | 指向 Xcode Developer 目录 | 命令输出 |
| Xcode CLI | `xcodebuild -version` | 返回 Xcode 27.0 与 Build version | 命令输出 |
| iOS Runtime | `xcrun simctl list runtimes` | 至少一个稳定 iOS Runtime 为 available | 命令输出 |
| Simulator | `xcrun simctl list devices` | 专用 iPhone Simulator 存在 | UDID、机型、Runtime |
| 启动 | `xcrun simctl bootstatus <udid> -b` | 退出码 0 | 启动日志 |
| 截图 | `xcrun simctl io <udid> screenshot <path>` | PNG 存在且可查看 | 截图文件 |
| 真机工具 | `xcrun devicectl list devices` | 命令可执行；无真机也允许空列表 | 命令输出 |

### 12.2 后续 Appium/WDA 验收

- `appium driver doctor xcuitest` 必须通过必需项。
- Simulator session 必须能创建和释放。
- screenshot、page source、tap、swipe、input、activate/terminate app 必须形成闭环。
- 不配置任何 LLM/API Key 也应能完成驱动层测试。
- XCTest/UI Test 自动化不在当前环境安装任务中启用；后续任务拆分前若需要，必须由用户明确要求。

## 13. 开放技术问题

| 优先级 | 问题 | 阻塞范围 | 处理阶段 |
| --- | --- | --- | --- |
| P1 | 首批最低 iOS/Xcode 支持矩阵 | 旧版本兼容 | 架构/任务拆分前 |
| P1 | 真机 Apple Developer Team 与 WDA bundle id | 真机接入 | 真机任务前 |
| P1 | 首版是否要求多台 iOS 并发 | 端口、锁与设备池 | 架构门禁 |
| P2 | Appium 作为受管子进程还是外部服务 | 生命周期、日志与升级 | 架构门禁 |
| P2 | 是否引入 idb 辅助设备池 | 设备管理 | Simulator MVP 后评估 |

## 14. 确认版固化

- 评审意见处理：用户无新增修改意见，于 2026-09-22 回复“确认”。
- 确认版基线：稳定版 Xcode、Simulator-first、当前稳定版 Appium + XCUITest Driver + WDA、平台能力显式降级。
- 重新打开技术方案门禁的条件：切换主后端、改变稳定版策略、支持非 macOS Host、改变签名安全边界或要求 Android/iOS 完全能力等价。

## 15. 门禁结论

- 结论：技术方案门禁已确认通过。
- 负责人：当前用户。
- 日期：2026-09-22。
- 通过条件：确认使用稳定版 Xcode、Simulator-first、当前稳定版 Appium + XCUITest Driver + WDA，并接受平台能力显式降级策略。

## 16. 参考依据

- Apple Xcode：<https://developer.apple.com/xcode/>
- Apple Xcode SDK and system requirements：<https://developer.apple.com/xcode/system-requirements>
- Appium XCUITest Driver Overview：<https://appium.github.io/appium-xcuitest-driver/latest/overview/>
- Appium XCUITest Driver Requirements：<https://appium.github.io/appium-xcuitest-driver/latest/installation/requirements/>
- Appium Real Device Configuration：<https://appium.github.io/appium-xcuitest-driver/latest/preparation/real-device-config/>

## 确认记录

- 确认人：当前用户
- 确认日期：2026-09-22
- 确认原文：确认
- 下一阶段：架构设计方案
