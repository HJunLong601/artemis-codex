# T05 iOS XCUITest Driver 任务包

## 当前流程状态

| 字段 | 值 |
| --- | --- |
| 当前阶段 | 实施 |
| 当前门禁 | 无 |
| 门禁状态 | 架构门禁已确认通过 |
| 当前允许动作 | 实现 Appium session、坐标、层级与 iOS Driver |
| 下一步 | Runtime 完成后运行真实 Simulator session 验收 |

## 目标

实现 `IosXcuiTestDriver` 的最小闭环：受管/外部 Appium session、截图、WDA XML、物理像素与逻辑坐标转换、点击/长按/滑动/输入、应用生命周期、前台应用、有限系统键与 Appium 录屏。

## 输入物

- T03 平台与 Provider 骨架。
- T04 Appium 服务与端口生命周期。
- `BaseDeviceDriver` 当前接口。
- Appium 3.7.0、XCUITest Driver 12.13.1。

## 可改范围

- `artemis/drivers/ios/`
- `artemis/drivers/factory.py`
- `artemis/drivers/base.py`（仅新增类型化错误/元数据，不破坏 Android）
- 对应单元测试、计划文档。

## 非目标

- 不实现 Actuator capability/参数约束；属于 T06。
- 不修改 Planner/Operator/Checker。
- 不实现真机签名或旧 iOS 矩阵。
- 不新增 XCTest/UI Test。

## 依赖

- T02、T03、T04 已完成。
- 真实验收依赖 iOS 27 Runtime 下载完成。

## 实现步骤

1. 为坐标转换、XML 解析、HTTP session 与 Driver 动作编写失败测试。
2. 实现异步 Appium WebDriver Client 与 Session Manager。
3. 实现 `CoordinateSpace` 与 WDA XML 适配。
4. 实现 `IosXcuiTestDriver` 的 `BaseDeviceDriver` 契约。
5. Driver Factory 按 iOS 平台创建专用 Driver，不触碰 ADB。
6. 单元/回归/Ruff 验证。
7. Runtime 完成后创建 Simulator session，验证截图、source、动作和清理。

## 验收 Profile

- 适用 Profile：通用 Profile + iOS Profile（仅真实 Simulator 系统行为部分）
- 选择原因：核心为 Python Driver；最终行为依赖 iOS Simulator。
- Profile 规则来源：`references/validation-profiles.md`

## Figma 设计还原检查候选

不适用：无 UI 设计实现。

## XCTest/UI Test 自动化候选（iOS Profile）

不启用：未提前明确要求使用 XCTest/UI Test 自动化。

## TDD 要求

| 行为 | 测试文件/方式 | RED 证据 | GREEN 证据 | 不适用原因 |
| --- | --- | --- | --- | --- |
| 像素与逻辑坐标双向转换 | `test_coordinate_space.py` | 待执行 | 待执行 |  |
| WDA XML 解析为统一元素 | `test_wda_xml.py` | 待执行 | 待执行 |  |
| W3C session/错误解析 | `test_appium_client.py` | 待执行 | 待执行 |  |
| Driver 截图/动作/不支持能力 | `test_xcuitest_driver.py` | 待执行 | 待执行 |  |
| Factory iOS 路由 | `test_driver_factory.py` | 现阶段抛出尚未安装错误 | 待执行 |  |

## 验收契约

### AI 必须验证

| 类型 | 命令/方式 | 期望结果 | 证据 |
| --- | --- | --- | --- |
| 单元测试 | focused pytest | 全绿 | pytest 输出 |
| Android 回归 | Driver/Context/Runtime 相关测试 | 全绿 | pytest 输出 |
| 静态检查 | Ruff check/format | 退出码 0 | Ruff 输出 |
| Simulator session | Appium session + screenshot/source/action/cleanup | 全链路通过 | 日志、截图、命令输出 |

### 人工必须验证

无。本任务的 Simulator 行为可由 AI 直接观测；真机不在范围内。

### 不可验证声明

| 项目 | 原因 | 替代证据 | 后续验收人 |
| --- | --- | --- | --- |
| 真机 WDA | 未授权且缺少设备/Team | Simulator 证据 | T09 用户与 AI |

## 完成前验证

| 检查项 | 命令/方式 | 期望证据 | 实际证据 |
| --- | --- | --- | --- |
| 新行为 | focused pytest | 全绿 | 待执行 |
| Android 兼容 | regression pytest | 全绿 | 待执行 |
| 质量 | Ruff | 全绿 | 待执行 |
| iOS 实机环境 | Simulator session | 截图/source/action/清理成功 | 等 Runtime |

## 完成禁止条件

- iOS 仍创建 ADB client。
- 截图像素坐标直接当作 WDA 逻辑坐标。
- 不支持的 shell/系统键静默成功。
- session 失败或 disconnect 后残留 Appium 服务。
- 只有 mock 单元测试却宣称真实 Simulator 已通过。

## 门禁条件

若 Appium 12.13.1 实际 API 与确认架构冲突，或需要扩大到真机签名，停止并进入实施门禁。

## 完成证据

待实现与验证后补充。
