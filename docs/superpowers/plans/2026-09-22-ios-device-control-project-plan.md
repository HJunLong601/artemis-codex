# iOS 设备控制项目计划表

## 当前流程状态

| 字段 | 值 |
| --- | --- |
| 当前阶段 | 验收完成 |
| 当前门禁 | 架构门禁已通过 |
| 当前允许动作 | 执行已确认范围内的任务包 |
| 当前执行任务 | T08 Simulator 验收完成 |
| 下一步 | T09 真机扩展（需用户提供设备与签名授权） |

## 计划总览

| ID | 阶段 | 任务 | 主要产物 | 验收标准 | 依赖 | 状态 |
| --- | --- | --- | --- | --- | --- | --- |
| T01 | 环境基线 | 安装稳定版 Xcode、iOS Runtime、创建专用 Simulator | 工具链、模拟器、版本与截图证据 | `xcodebuild`、`simctl`、bootstatus、截图全部通过 | 无 | 已完成 |
| T02 | 环境基线 | 安装 Node/Appium/XCUITest Driver 并运行 doctor | Appium 3.7.0、XCUITest Driver 12.13.1 | `appium driver doctor xcuitest` 0 个必需修复 | T01 | 已完成 |
| T03 | 平台骨架 | 增加 iOS 平台、DeviceDescriptor、Provider 与配置传播 | 平台发现与选择代码 | 49 个相关测试通过，Android 默认不变，Ruff 通过 | T01 | 已完成 |
| T04 | 资源隔离 | 平台命名空间锁键、端口租约和 Appium 生命周期 | Lock/Service Manager | 72 项回归、Ruff、真实 `/status` 与清理通过 | T02,T03 | 已完成 |
| T05 | iOS Driver | Appium session、截图、XML、坐标空间、App 生命周期 | `IosXcuiTestDriver` | 真实会话、1206×2622 截图、296 元素与 HOME 动作通过 | T04 | 已完成 |
| T06 | 动作闭环 | IosActuator、动作/参数能力约束 | 可执行 core actions | 64 项聚焦测试、459 项相关回归与 Ruff 通过 | T05 | 已完成 |
| T07 | 平台配套 | Recording Backend、diagnose、CLI/MCP/Admin 展示 | 平台化诊断与录屏 | iOS 诊断、设备状态、任务入口、Admin 队列和重复录屏均通过 | T06 | 已完成 |
| T08 | Simulator 验收 | ARTEMIS 探索、动作闭环、稳定性和证据报告 | trace、截图、日志、验收报告 | 目标场景可重复执行 | T07 | 已完成 |
| T09 | 真机扩展 | Developer Mode、签名、WDA 与真机矩阵 | 真机配置与验证报告 | 用户指定真机闭环通过 | T08、用户 Team/设备 | 未授权 |

## T01 可执行任务包

### 目标

在当前 Apple Silicon Mac 上建立可用的稳定版 iOS 开发环境，并证明模拟器可启动和截图。

### 输入与约束

- 当前系统：macOS 26.6.2，arm64。
- 当前仅安装 Command Line Tools，尚无完整 Xcode。
- 可用磁盘约 756 GiB。
- 安装 Mac App Store 当前稳定版 Xcode 27.0，不安装 beta。
- 安装匹配的稳定 iOS 27 Simulator Runtime。
- 不保存或代填 Apple ID、密码、Touch ID、管理员密码。
- 不修改业务代码，不启用 XCTest/UI Test。

### 执行步骤

1. 从 Mac App Store 安装稳定版 Xcode。
2. 启动 Xcode 完成首次组件安装；如需用户认证，暂停交接。
3. 将 active developer directory 切换到 `/Applications/Xcode.app/Contents/Developer`。
4. 接受 Xcode license，并运行首次启动组件安装。
5. 检查并安装稳定 iOS Simulator Runtime。
6. 创建或选择一个专用 iPhone Simulator。
7. 启动模拟器，等待 `bootstatus -b` 完成。
8. 获取设备/Runtime 清单并保存模拟器截图。
9. 运行 `devicectl list devices` 验证真机工具可执行。

### 验收证据

| 检查 | 通过条件 |
| --- | --- |
| `xcode-select -p` | 指向完整 Xcode Developer 目录 |
| `xcodebuild -version` | 返回稳定版 Xcode 与 Build version |
| `xcrun simctl list runtimes` | 稳定 iOS Runtime 为 available |
| `xcrun simctl list devices` | 专用 iPhone Simulator 存在 |
| `xcrun simctl bootstatus <udid> -b` | 退出码 0 |
| `xcrun simctl io <udid> screenshot ...` | PNG 存在且可查看 |
| `xcrun devicectl list devices` | 命令可执行；无真机允许空列表 |

### 实际验收结果

- Xcode：27.0（Build 27A266a），Developer Directory 为完整 Xcode。
- Runtime：iOS 27.0（Build 24A434），arm64，available。
- 专用模拟器：`ARTEMIS iPhone 18 Pro`，已验证可正常启动。
- Appium：3.7.0；XCUITest Driver：12.13.1；doctor 为 0 个 required fix。
- `devicectl` 可执行并将专用模拟器报告为 connected/simulated。
- 截图证据：`artifacts/ios/artemis-iphone-18-pro-home.png`。
- Appium/XCUITest 截图证据：`artifacts/ios/appium-xcuitest-home.png`。
- 录屏证据：本地 `artifacts/ios/` 目录中的 Simulator 录屏，
  H.264、1206×2622、非空且可由 ffprobe 解析。
- 平台入口证据：iOS diagnose 返回 `ready`，截图与 296 个 UI 元素可读取；CLI、
  MCP、Admin 队列均携带平台和规范化设备标识。
- 重复性证据：同一 Simulator 再次执行截图、HOME 动作和录屏均成功；旧录屏产物会在
  新录屏启动前安全替换。

### 回滚与恢复

- 下载中断时使用 App Store 继续下载，不删除已有系统组件。
- 首次启动组件失败时保留 Xcode 与日志，重新执行 `xcodebuild -runFirstLaunch`。
- Runtime 下载失败时保留 Xcode，通过 Xcode Settings > Components 或受支持 CLI 重试。
- 不执行破坏性卸载或清理用户已有模拟器。
