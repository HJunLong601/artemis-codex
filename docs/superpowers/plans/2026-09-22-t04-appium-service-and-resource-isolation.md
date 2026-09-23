# T04 Appium 服务与资源隔离任务包

## 当前流程状态

| 字段 | 值 |
| --- | --- |
| 当前阶段 | 验收 |
| 当前门禁 | 无 |
| 门禁状态 | 架构门禁已确认通过 |
| 当前允许动作 | 固化验证证据并进入 iOS Driver |
| 下一步 | 实现 Appium session 与 iOS Driver |

## 目标

实现 iOS 路径所需的资源隔离基础：平台命名空间设备锁入口、跨进程 Appium 端口租约、受管 Appium 启停/健康检查，以及外部 Appium URL 兼容模式。

## 输入物

- 架构确认版中的设备身份、Appium 生命周期和错误处理约束。
- T03 `DeviceDescriptor.canonical_id` 与现有 `DeviceExecutionLock`、`ProcessSupervisor`。
- 已安装 Appium 3.7.0 与 XCUITest Driver 12.13.1。

## 可改范围

- `artemis/runtime/device_lock.py`
- `artemis/runtime/port_lease.py`
- `artemis/runtime/appium_service.py`
- `artemis/runtime/__init__.py`
- 对应单元测试和任务文档。

## 非目标

- 不创建 XCUITest session，不实现 WDA capabilities。
- 不实现 iOS UI 动作或坐标转换。
- 不改变现有 Android 裸 serial 锁行为。
- 不自动管理外部 Appium 进程。

## 依赖

- T02、T03 已完成。
- Appium 可执行文件位于 PATH。

## 实现步骤

1. 编写平台锁入口、端口互斥、受管/外部服务测试并确认 RED。
2. 为 `DeviceExecutionLock` 增加 `DeviceDescriptor` 构造入口。
3. 实现 PID 感知的跨进程端口租约与陈旧租约清理。
4. 实现 `AppiumServiceManager`：外部健康检查、受管启动、日志、ready 等待、幂等停止和资源释放。
5. 运行单元测试、锁回归、Ruff，并启动真实 Appium 验证 `/status`。

## 验收 Profile

- 适用 Profile：通用 Profile
- 选择原因：Python 服务生命周期与资源隔离，不是 iOS App 代码。
- Profile 规则来源：`references/validation-profiles.md`

## Figma 设计还原检查候选

不适用：无 UI 实现。

## XCTest/UI Test 自动化候选（iOS Profile）

不适用：使用通用 Profile，未提前要求 XCTest/UI Test。

## TDD 要求

| 行为 | 测试文件/方式 | RED 证据 | GREEN 证据 | 不适用原因 |
| --- | --- | --- | --- | --- |
| Descriptor 生成平台隔离锁 | `test_device_lock.py` | 实现前缺少 `for_device` | 相关回归 72 项通过 |  |
| 同一端口不可重复租用且可释放 | `test_port_lease.py` | 实现前模块不存在 | 相关回归 72 项通过 |  |
| 外部 Appium 只校验不终止 | `test_appium_service.py` | 实现前模块不存在 | 相关回归 72 项通过 |  |
| 受管 Appium 启停释放资源 | `test_appium_service.py` | 实现前模块不存在 | 单元测试与真实 `/status` 均通过 |  |

## 验收契约

### AI 必须验证

| 类型 | 命令/方式 | 期望结果 | 证据 |
| --- | --- | --- | --- |
| 单元测试 | focused pytest | 全部通过 | pytest 输出 |
| 锁回归 | 现有 device_lock/device_pool 测试 | 全部通过 | pytest 输出 |
| 静态检查 | Ruff check/format | 退出码 0 | Ruff 输出 |
| 真实服务 | 启动受管 Appium 并请求 `/status` | ready，停止后端口释放 | 命令/日志 |

### 人工必须验证

无。本任务无 UI 或真实设备交互。

### 不可验证声明

无。

## 完成前验证

| 检查项 | 命令/方式 | 期望证据 | 实际证据 |
| --- | --- | --- | --- |
| 新行为 | focused pytest | 全绿 | 72 passed |
| 回归 | device lock/pool/provider/context/SDK pytest | 全绿 | 72 passed |
| 代码质量 | Ruff | 全绿 | All checks passed；29 files formatted |
| 真实 Appium | `/status` | ready | Appium 3.7.0 ready，XCUITest 12.13.1 已加载 |

## 完成禁止条件

- 外部 Appium 被 ARTEMIS 终止。
- 启动失败后端口租约或进程残留。
- 同一端口可被两个 ARTEMIS 进程同时租用。
- Android 现有锁文件命名或选择行为回归。

## 门禁条件

若需要改变已确认的服务所有权、端口归属或设备锁模型，停止并重新进入实施门禁。

## 完成证据

- RED：首次测试收集因 `port_lease` 与 `appium_service` 模块不存在而失败。
- GREEN：72 项相关测试通过；Ruff check 与 format check 通过。
- 真实服务：`READY http://127.0.0.1:4723 managed=True`，随后 `STOPPED`。
- 清理证据：停止后 4723 无监听进程，端口租约目录为空。
- 日志：`<tmpdir>/artemis/appium/appium-4723.log`。
