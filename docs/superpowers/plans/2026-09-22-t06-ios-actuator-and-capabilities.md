# T06：iOS 动作执行器与能力约束

## 目标

将 T05 的 `IosXcuiTestDriver` 接入 ARTEMIS 现有动作层，让 Flash、Pro Operator 和
Validator 继续使用统一的规范化坐标协议，同时只向模型暴露 iOS 实际支持的能力。

## 范围

- 新增 `IosActuator`，实现点击、长按、滑动、文本输入、按键、应用管理、等待和观察。
- iOS 坐标仍使用 `0..1000` 规范化空间，执行前映射到当前截图物理像素。
- `press_key` 仅允许 `home`、`enter`、`delete`；其他按键返回结构化
  `UNSUPPORTED`，不得静默退化成 Android 行为。
- `manage_app.app_name` 在 iOS 下解释为 bundle identifier，不执行 Android 包名搜索。
- 暂不暴露 `open_link`，因为当前 iOS Driver 尚未提供已验证的 deep-link 实现。
- 根据 `DevicePlatform` 自动选择 Android 或 iOS Actuator，并覆盖 Action Session 与
  Flash Executor 两条默认构造路径。

## 非目标

- 不在本任务中实现 XCTest/UI Test 测试工程。
- 不实现真机签名、WebDriverAgent 自动签名或 USB 真机配对。
- 不伪造 Android-only 的 Back、App Switch、ADB shell 等语义。

## 测试顺序

1. RED：先添加 iOS 能力集合、坐标转换、按键约束、文本清理、应用管理和工厂选择测试。
2. GREEN：实现最小 `IosActuator` 与平台工厂。
3. 回归：运行 MCP/Driver/Runtime 相关单测与 Ruff。
4. 环境完成后：在真实 iOS Simulator 上通过 Appium 创建会话并验证截图、层级和 HOME。

## 验收标准

- iOS 不再默认实例化 `AdbActuator`。
- iOS 对不支持按键返回 `ActionCode.UNSUPPORTED`，且不会调用底层 Driver。
- `input_text(clear_exist=True)` 直接使用 XCUITest 的清理语义，不进入 Android shell。
- 能力清单包含 required/internal 动作，排除 `open_link`。
- 新增测试及相关回归全部通过。

## 执行证据

- RED：`tests/unit/mcp/test_ios_actuator.py` 首次收集因 `IosActuator` 不存在失败。
- GREEN：iOS Actuator 聚焦测试、Manifest、ActionSpec、ActionSession 合计 64 项通过。
- 回归：MCP、iOS Driver、Runtime、Operator 与 Flash 相关范围 459 项通过。
- 静态检查：修改范围 `ruff check` 通过，`git diff --check` 通过。
- 已知非本任务失败：`tests/unit/mcp/test_device_utils.py::test_ensure_emulator_uses_windows_creation_flags`
  在当前 macOS 环境单独运行也失败，与 iOS 变更无调用链关系。
