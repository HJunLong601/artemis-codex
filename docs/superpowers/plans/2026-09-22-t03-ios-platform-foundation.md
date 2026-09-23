# T03 iOS 平台骨架任务包

## 当前流程状态

| 字段 | 值 |
| --- | --- |
| 当前阶段 | 验收 |
| 当前门禁 | 无 |
| 门禁状态 | 架构门禁已确认通过 |
| 当前允许动作 | 固化 T03 验证证据并进入下一任务 |
| 下一步 | 进入 Appium 服务与资源隔离任务 |

## 目标

为 ARTEMIS 增加可安全扩展的 iOS 平台入口：`DevicePlatform.IOS`、统一 `DeviceDescriptor`、设备 Provider 契约、Android Provider 适配、iOS Simulator Provider 与多 Provider Registry。任何 iOS 上下文在专用 Driver 尚未完成前必须明确失败，不得误落到 Android ADB Driver。

## 输入物

- `2026-09-22-ios-device-control-technical-solution-confirmed.md`
- `2026-09-22-ios-device-control-architecture-solution-confirmed.md`
- `2026-09-22-ios-device-control-project-plan.md`
- 当前 `DeviceContext`、`DevicePool`、`DriverFactory` 与 Runtime 公共导出。

## 可改范围

- `artemis/context.py`
- `artemis/drivers/factory.py`
- `artemis/runtime/device_provider.py`
- `artemis/runtime/android_device_provider.py`
- `artemis/runtime/ios_device_provider.py`
- `artemis/runtime/device_registry.py`
- `artemis/runtime/__init__.py`
- 对应 `tests/unit/` 测试与本任务文档。

## 非目标

- 不实现 Appium 服务、WDA session 或 `IosXcuiTestDriver`。
- 不改动现有 Android `DevicePool` 的选择与锁行为。
- 不接入真机 `devicectl`；真机属于 T09。
- 不改变 CLI/MCP/Admin 默认 Android 入口。
- 不编写 XCTest/UI Test 或移动 UI 自动化测试。

## 依赖

- Python 3.12+ 与项目现有 Pydantic/pytest 工具链。
- iOS Simulator 实际发现依赖完整 Xcode；解析逻辑使用 fixture 单元测试，不阻塞代码实现。

## 实现步骤

1. 先编写平台枚举、描述模型、Registry 选择与 simctl JSON 解析测试，确认 RED。
2. 增加统一设备模型与 Provider 协议。
3. 将现有 Android `DevicePool` 适配为 Android Provider。
4. 增加 iOS Simulator Provider，使用 `xcrun simctl list devices available --json` 与 Runtime JSON。
5. 增加 Registry 聚合、平台过滤、显式设备选择与歧义错误。
6. Driver Factory 对 iOS 在 Driver 尚未实现时抛出明确错误。
7. 运行 focused tests、相关回归与 Ruff。

## 验收 Profile

- 适用 Profile：通用 Profile
- 选择原因：本任务修改 Python 核心运行时与设备发现模型，不是 iOS App/Extension 代码。
- Profile 规则来源：`references/validation-profiles.md`

## Figma 设计还原检查候选

不适用：没有 UI 实现或设计来源。

## XCTest/UI Test 自动化候选（iOS Profile）

不适用：本任务使用通用 Profile，且未提前明确要求使用 XCTest/UI Test 自动化。

## TDD 要求

| 行为 | 测试文件/方式 | RED 证据 | GREEN 证据 | 不适用原因 |
| --- | --- | --- | --- | --- |
| iOS 平台与 canonical id | `tests/unit/runtime/test_device_provider.py` | 实现前 `ModuleNotFoundError` | 49 项相关回归的一部分，已通过 |  |
| Registry 聚合与歧义保护 | `tests/unit/runtime/test_device_registry.py` | 实现前 `ModuleNotFoundError` | 49 项相关回归的一部分，已通过 |  |
| simctl JSON 解析 | `tests/unit/runtime/test_ios_device_provider.py` | 实现前 `ModuleNotFoundError` | 49 项相关回归的一部分，已通过 |  |
| iOS 不得回落 Android Driver | `tests/unit/drivers/test_driver_factory.py` | 实现前缺少错误类型且会走 Android 分支 | 49 项相关回归的一部分，已通过 |  |

## 验收契约

### AI 必须验证

| 类型 | 命令/方式 | 期望结果 | 证据 |
| --- | --- | --- | --- |
| focused unit tests | `uv run pytest` 指定新增测试 | 全部通过 | pytest 输出 |
| Android 回归 | context、device_pool、SDK builder 相关测试 | 全部通过 | pytest 输出 |
| 静态检查 | `uv run ruff check` 指定变更文件 | 退出码 0 | Ruff 输出 |
| 格式检查 | `uv run ruff format --check` 指定变更文件 | 退出码 0 | Ruff 输出 |

### 人工必须验证

无。本任务不改变用户界面或真实设备行为；真实 Simulator 发现将在环境安装完成后由 T01/T08 验收。

### 不可验证声明

| 项目 | 原因 | 替代证据 | 后续验收人 |
| --- | --- | --- | --- |
| 当前机器真实 simctl 发现 | Xcode 正在首次安装和初始化 | fixture 解析测试；环境完成后执行真实命令 | AI 在 T01/T08 验收 |

## 完成前验证

| 检查项 | 命令/方式 | 期望证据 | 实际证据 |
| --- | --- | --- | --- |
| 新行为 | focused pytest | 全绿 | 49 passed |
| 兼容性 | DevicePool、Context、SDK 相关 pytest | 全绿 | 49 passed |
| 代码质量 | Ruff check/format | 全绿 | All checks passed；12 files formatted |
| 真实环境 | simctl 列表 | 至少可执行 | Xcode 27.0 已安装；iOS 27 Runtime 下载中，转 T01/T08 验收 |

## 完成禁止条件

- iOS 上下文仍可能实例化 Android Driver。
- Registry 在多个可用设备时静默选择任意设备。
- simctl 失败被伪装成“无设备”。
- 没有 RED/GREEN 测试证据。
- Android 默认行为回归。

## 门禁条件

如果实现要求改变已确认的 Provider/Driver/Actuator 边界、设备身份模型或锁状态归属，停止执行并重新进入实施门禁。

## 完成证据

- RED：新增测试首次收集时出现 4 个预期错误，缺少 Provider/Registry/Driver 错误类型。
- GREEN：相关测试共 49 项通过；Ruff check 和 format check 通过。
- 环境：Appium 3.7.0、XCUITest Driver 12.13.1，doctor 0 个必需修复。
- 遗留：真实 simctl 发现、启动与截图等待 iOS 27 Runtime 下载完成后在 T01/T08 验收。
