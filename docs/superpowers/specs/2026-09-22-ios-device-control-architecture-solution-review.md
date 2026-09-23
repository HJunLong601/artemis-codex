# iOS 设备控制架构设计方案

## 当前流程状态

| 字段 | 值 |
| --- | --- |
| 当前阶段 | 架构设计方案 |
| 当前门禁 | 架构门禁 |
| 门禁状态 | 已获执行授权，视为确认通过 |
| 当前允许动作 | 固化确认版、生成项目计划表并执行环境安装 |
| 下一步 | 安装 Xcode 与 iOS Simulator 并完成验收 |

## 1. 背景

ARTEMIS 当前已经具备通用设备驱动抽象、统一控制器、Planner/Operator/Checker、视觉定位、动作清单、Actuator 能力过滤、多设备池、跨进程锁、诊断、录屏和任务追踪；但设备发现、默认平台、Shell、系统按键、录屏、诊断和管理台设备展示仍以 Android ADB/UIAutomator2/scrcpy 为中心。

本架构在不破坏 Android 默认行为的前提下，为 iOS Simulator 和后续真机建立明确的平台边界。iOS 使用当前稳定版 Appium + XCUITest Driver + WebDriverAgent，复用 ARTEMIS 的上层智能能力，不将 Appium 变成新的任务编排层。

## 2. 文档状态

- 状态：评审版
- 来源技术方案确认版：`2026-09-22-ios-device-control-technical-solution-confirmed.md`
- 来源输入物：`2026-09-22-ios-device-control-input-normalization.md`
- 确认人：待用户确认
- 确认日期：待确认
- 后续变更是否需要重新进入架构门禁：是。模块职责、状态所有权、设备身份、会话生命周期或核心数据流发生实质变化时必须重新评审。

## 3. 理解完整性审计

| 检查项 | 状态 | 依据/说明 |
| --- | --- | --- |
| 当前系统边界 | 已明确 | Driver、Unified Controller、Actuator、DevicePool、DeviceExecutionLock、诊断与任务运行器均已定位 |
| 已确认技术基线 | 已明确 | 稳定版 Xcode、Simulator-first、当前稳定版 Appium + XCUITest/WDA、显式能力降级 |
| 模块职责 | 已明确 | 平台描述、设备发现、Appium 服务/会话、iOS Driver/Actuator、坐标、录屏、诊断分别归属 |
| 数据流 | 已明确 | 发现、选择、加锁、服务就绪、建会话、观察/动作、追踪、清理形成闭环 |
| 事件流 | 已明确 | 设备状态、服务状态、会话状态、动作结果、失败与清理事件有唯一生产者 |
| 状态归属 | 已明确 | 设备选择、锁、服务、会话、UI 观察、管理台展示状态均有明确所有者 |
| 持久化与迁移 | 已明确 | 配置向后兼容；锁键平台命名空间化；追踪记录扩充平台元数据 |
| 错误边界 | 已明确 | 不支持能力、工具链缺失、服务失败、会话失败、坐标失败分别建模，不静默吞错 |
| 兼容性 | 已明确 | Android 默认路径保持；iOS 为新增 provider/driver/actuator/backend |
| 测试策略 | 已明确 | 单元、契约、Simulator 集成、回归、人工/ARTEMIS 证据分层 |
| 开放问题 | 已列出 | 真机 Team、旧 iOS 矩阵、首版并发规模不阻塞 Simulator MVP |

### 审计结论

- 架构信息足以进入门禁评审。
- 当前开放问题不会影响 Xcode 与单台 iOS Simulator 环境安装，也不会迫使后续核心接口返工。

## 4. 目标

1. 在 `DevicePlatform` 中正式引入 iOS，并让平台选择贯穿 CLI、MCP 后台任务、SDK、设备池、锁和追踪。
2. 让 iOS Simulator 与后续真机共享同一套设备描述、Appium 服务管理、会话管理、Driver 与 Actuator 边界。
3. 复用现有动作清单与能力过滤，避免在 Planner/Operator 中复制一套 iOS 专用智能链路。
4. 显式处理 iOS 与 Android 的能力差异、坐标差异、录屏差异和应用标识差异。
5. 保持 Android 当前默认行为与现有调用方兼容。
6. 支持可诊断、可清理、可测试的 Appium/WDA 生命周期，避免遗留端口、会话与设备锁。

## 5. 非目标

- 首批不实现 Windows/Linux Host 上的 iOS 控制。
- 首批不支持越狱设备、任意 iOS shell 或 Android 全量系统键语义。
- 首批不承诺全部历史 iOS/Xcode 组合。
- 环境安装任务不改造业务 App，也不自动新增 XCTest/UI Test target。
- 首批不重写 Planner、Operator、Checker、Explorer 或历史压缩机制。
- 首批不引入 idb 作为必需依赖；仅在 Appium/WDA 无法覆盖具体设备管理需求时再评估。

## 6. 当前系统

### 6.1 已有可复用能力

| 现有区域 | 当前价值 | iOS 接入方式 |
| --- | --- | --- |
| `artemis/context.py` | 保存设备上下文与平台枚举 | 增加 `IOS`，平台随任务显式传播 |
| `artemis/drivers/base.py` | 异步设备操作抽象 | 保留核心动作，补充能力和坐标元数据 |
| `artemis/drivers/factory.py` | 统一创建 Driver | 按平台路由 Android、iOS、Cloud、Mock |
| `artemis/controllers/unified_controller.py` | 上层统一操作入口 | 移除内部 Android 假设，录屏委托 backend |
| `artemis/mcp/action_manifest.py` | 动作集合与契约校验 | 支持平台核心动作及参数级能力约束 |
| `artemis/mcp/actuators/` | 把动作映射到设备执行 | 新增 `IosActuator`，继续参与 capability 过滤 |
| `artemis/runtime/device_pool.py` | 设备发现、状态和调度 | 演进为多 Provider 聚合池 |
| `artemis/runtime/device_lock.py` | 跨进程设备互斥 | 锁键改为平台命名空间化设备身份 |
| `artemis/mcp_server/tools/diagnose.py` | 环境与设备自诊断 | 按平台加载检查项并生成平台分区结果 |
| trace/data engine | 保存任务、步骤、图像和视频 | 增加平台、设备类型、Runtime 与坐标元数据 |

### 6.2 当前耦合点

- `DevicePlatform` 仅定义 Android，多个入口直接写死 `ANDROID`。
- Driver Factory 在常规分支无条件初始化 ADB 客户端并创建 Android Driver。
- DevicePool 依赖 `adb devices`，管理台和平台命令控制器只认识 Android serial。
- `execute_shell`、Back/App Switch 等能力被通用接口视作默认能力。
- 统一控制器的录屏路径直接依赖 scrcpy 与 Android display state。
- 诊断、设备探测与清锁逻辑使用裸 serial，尚无平台命名空间。

## 7. 技术方案基线

- 技术方案确认版：`docs/superpowers/specs/2026-09-22-ios-device-control-technical-solution-confirmed.md`
- 关键技术路线：稳定版完整 Xcode + iOS Simulator Runtime；当前稳定版 Appium + XCUITest Driver + WDA；Simulator-first。
- 核心约束：macOS Host；真机需要签名；iOS 无任意 shell；坐标空间需转换；平台不支持能力必须显式暴露。
- 兼容原则：Android 继续作为未显式指定平台时的默认值；新增配置不改变现有 Android 用户的启动方式。

## 8. 推荐架构

### 8.1 总览

```text
CLI / MCP / SDK / Admin Console
              |
      Platform + DeviceSelector
              |
       DeviceRegistry / Pool
        /                 \
AndroidDeviceProvider   IosDeviceProvider
        |                 |-- simctl (Simulator discovery/lifecycle)
       ADB                |-- devicectl (real-device discovery)
                          `-- Appium health/session readiness
              |
      namespaced DeviceExecutionLock
              |
         DriverFactory
        /            \
AndroidAdbDriver   IosXcuiTestDriver
        |            |-- AppiumServiceManager
        |            `-- AppiumSessionManager / WDA
        |                    |
   AdbActuator          IosActuator
        \                  /
       ActionManifest + constraints
                  |
     Unified Controller / Agent Loop
                  |
       Trace + platform metadata
```

### 8.2 模块边界

| 模块 | 职责 | 不应承担 |
| --- | --- | --- |
| `DevicePlatform` / `DeviceDescriptor` | 表达平台、设备 ID、名称、OS、真机/模拟器、状态、Provider 与屏幕元数据 | 不启动服务、不持有 Appium session |
| `DeviceProvider` | 发现、刷新、探测、可选启动/关闭平台设备 | 不执行业务 UI 动作、不持有任务锁 |
| `AndroidDeviceProvider` | 封装现有 ADB 设备池行为 | 不感知 Appium/WDA |
| `IosDeviceProvider` | 解析 `simctl --json`、`devicectl --json-output`，管理 Simulator boot/shutdown | 不替代 Appium 执行 UI 动作，不保存凭据 |
| `DeviceRegistry` / 聚合池 | 汇总多个 Provider、执行筛选与自动选择、返回统一描述 | 不隐式挑选多个候选中的任意设备；歧义时交给调用方请求用户选择 |
| `AppiumServiceManager` | 默认启动/监控/停止本地 Appium 子进程，分配端口、记录日志、健康检查；也支持连接外部 URL | 不持有具体设备操作语义，不管理用户证书 |
| `AppiumSessionManager` | 按设备创建、复用、释放 XCUITest session，管理 WDA 相关端口和 capabilities | 不做 AI 规划，不跨任务永久复用脏 session |
| `IosXcuiTestDriver` | 实现截图、层级、点按、长按、滑动、输入、应用生命周期、前台应用与基础录屏 | 不暴露任意 shell；不在内部自动选择设备 |
| `CoordinateSpace` | 维护截图像素、WDA 逻辑 viewport、scale、安全区域与方向，提供双向转换 | 不猜测未知比例；元数据不完整时不得静默点击 |
| `IosActuator` | 将 canonical actions 转换为 Driver/Appium 调用，声明动作与参数约束 | 不复制 Agent 逻辑，不伪造不支持动作成功 |
| `ActionManifest` / constraints | 定义跨平台核心动作、可选动作、扩展动作，以及 `system_button` 等参数允许值 | 不直接访问设备 |
| `RecordingBackend` | 统一录屏生命周期与时间元数据；Android 用 scrcpy，iOS 用 Appium/XCUITest 录屏 | 不把平台录屏细节留在 Unified Controller |
| `PlatformDiagnostics` | 检查 Xcode、Runtime、simctl/devicectl、Node/Appium、XCUITest、WDA 和设备探测 | 不修改签名、Apple ID、证书或系统安全设置 |

### 8.3 核心模型

```text
DeviceDescriptor
  platform: android | ios
  device_id: string               # Android serial 或 Apple UDID
  canonical_id: "<platform>:<device_id>"
  name: string
  os_version: string | null
  kind: physical | simulator | emulator
  state: ready | booting | offline | unauthorized | unavailable | busy
  provider: adb | simctl | devicectl
  screen: CoordinateSpace | null

DriverCapabilities
  actions: set[action_name]
  constraints: map[action_name, allowed_parameters]
  shell: none | restricted | full
  recording: supported | unsupported
  app_identifier: package | bundle_id
```

- 对外仍可显示原始 serial/UDID；内部锁、队列和任务归属使用 `canonical_id`，防止不同平台 ID 碰撞。
- `BaseDeviceDriver.execute_shell()` 在过渡期保留以兼容现有实现，但必须由 capability 守卫；iOS 返回类型化 `UnsupportedOperationError`。后续再把 Shell/Recording 拆成可选协议，避免一次性破坏全部调用方。
- `system_button` 采用参数级约束：iOS 允许 Home 等已验证动作，不宣称支持 Android Back/App Switch。

### 8.4 Appium 生命周期决策

- 默认模式：ARTEMIS 受管本地 Appium。`AppiumServiceManager` 负责端口、启动参数、日志、健康检查和退出清理。
- 兼容模式：配置 `appium_server_url` 时连接外部服务，不管理其进程，只做状态检查。
- 一个 Host 可共享 Appium 服务；每个设备/任务拥有独立 Appium session 和 WDA 端口集合。
- session 创建失败必须保存 Appium/WDA 日志路径，并释放已取得的任务锁与端口租约。
- 任务正常结束、取消、超时或进程恢复时均执行幂等清理。

### 8.5 数据流

1. CLI/MCP/SDK 接收 `platform` 与可选 `device_id`。
2. `DeviceRegistry` 调用对应 Provider 刷新设备，生成 `DeviceDescriptor`。
3. 若未指定设备且只有一个可用候选，自动选择；若多个候选或平台不明确，返回候选列表并要求用户确认。
4. 任务以 `canonical_id` 获取 `DeviceExecutionLock`，并把平台与设备描述写入 trace metadata。
5. `DriverFactory` 根据平台创建 Driver；iOS 路径先确保 Appium 服务 ready，再由 Session Manager 创建 XCUITest session。
6. Driver 获取 screenshot、page source 与 viewport，`CoordinateSpace` 生成可审计的坐标映射。
7. Agent 根据 `IosActuator` 的能力和参数约束生成动作；Action Executor 校验后调用 Driver。
8. 动作结果、观察、截图、坐标元数据和错误进入现有 trace/data engine。
9. 任务退出时依次停止录屏、删除 Appium session、释放端口租约、释放设备锁；受管服务按引用计数或 Server 生命周期策略退出。

### 8.6 事件流

| 事件 | 生产者 | 消费者 | 失败处理 |
| --- | --- | --- | --- |
| `device.discovered/updated` | Provider | Registry、Admin、Selector | 标记不可用，不创建 session |
| `device.selected` | Selector | Lock、Trace、DriverFactory | 歧义时停止并请求用户选择 |
| `service.ready/failed` | AppiumServiceManager | SessionManager、Diagnostics | 输出日志路径，释放租约 |
| `session.created/deleted` | AppiumSessionManager | Driver、Trace、Cleanup | 删除失败进入清理队列并报告 |
| `observation.captured` | Driver | Agent、Trace | XML 慢可降级到视觉；截图失败不可继续点击 |
| `action.executed/failed` | Actuator/Driver | Agent、Checker、Trace | 类型化错误决定重试、降级或终止 |
| `recording.started/stopped` | RecordingBackend | Trace/Outputter | 录屏可选失败不伪装为成功 |
| `device.released` | Task cleanup | Lock、Queue、Registry | 幂等重试并保留诊断证据 |

### 8.7 状态归属

| 状态 | 唯一所有者 | 生命周期 |
| --- | --- | --- |
| 已发现设备清单 | `DeviceRegistry` | 短期缓存，按请求/TTL 刷新 |
| Simulator boot 状态 | `IosDeviceProvider` 读取，CoreSimulator 为事实源 | 外部状态，不永久缓存 |
| 设备互斥与队列 | `DeviceExecutionLock` | 任务级、跨进程 |
| Appium 服务状态与端口 | `AppiumServiceManager` | Host/server 级 |
| Appium session 与 WDA 端口 | `AppiumSessionManager` | 单设备单任务 |
| 当前 UI 截图和层级 | Driver/Agent step | 单步骤，不作为全局事实源 |
| 坐标映射 | `CoordinateSpace` | 随 session、方向或 viewport 变化而更新 |
| 管理台选择/展示状态 | Admin 前端 | 仅 UI 状态；后端 Registry/Lock 仍为事实源 |
| 任务历史与证据 | 现有 trace/data engine | 按当前持久化策略 |

### 8.8 持久化、缓存与迁移

- 配置新增 `platform`、`ios.appium_server_url`、受管服务开关、端口范围和默认 capabilities；全部提供 Android 兼容默认值。
- 设备清单只做短 TTL 缓存，`simctl`/`devicectl` 输出是事实源；不得把设备在线状态长期持久化。
- 锁键从裸 device id 演进为 `platform:device_id`。旧 Android 调用在过渡期规范化为 `android:<serial>`；旧锁文件属于临时运行态，可由既有 stale-lock 清理机制安全回收。
- trace/session metadata 增加 `platform`、`device_kind`、`os_version`、`runtime`、`driver_backend`、`screenshot_size`、`viewport_size`、`scale` 和 `orientation`。旧记录读取时字段缺失按 Android legacy 处理。
- Appium session id、WDA 派生数据、临时端口和日志路径只属于运行态，不写入版本库；日志可作为 trace 附件保留。
- 不持久化 Apple ID、Team 私钥、密码、证书口令或 provisioning secret。

### 8.9 错误处理

| 错误类别 | 示例 | 策略 |
| --- | --- | --- |
| `ToolchainUnavailable` | Xcode、Runtime、Appium 或 Driver 缺失 | diagnose 给出有序修复步骤，禁止盲目重试 |
| `DeviceUnavailable` | Simulator 未 boot、真机未信任/离线 | 刷新 Provider；有多个设备时请求用户选择 |
| `ServiceStartError` | Appium 端口占用、启动失败 | 保留 stderr/log，释放端口并返回明确原因 |
| `SessionCreateError` | WDA 构建、签名或 capability 失败 | 分类 Simulator/真机问题，幂等删除半成品 session |
| `UnsupportedOperation` | shell、Back、App Switch 等无 iOS 语义 | capability 阶段过滤；运行时再次防御并明确失败 |
| `CoordinateMappingError` | scale/viewport/方向不一致 | 重新采集屏幕元数据；无法确认时禁止执行坐标点击 |
| `ObservationError` | screenshot/source 超时 | 有截图时允许视觉降级；无可靠观察时停止 |
| `CleanupError` | session/录屏/锁释放失败 | 幂等重试，登记 stale resource，交给诊断清理 |

### 8.10 平台能力策略

- 跨平台核心动作：click、long_press、input_text、swipe、app_control、wait；只有通过 Driver 契约测试后才对 iOS 宣告。
- 可选动作：system_button、shell、recording 等由 capabilities 暴露。
- 参数级约束进入 manifest/actuator contract，例如 `system_button` 的允许值、`app_control` 使用 bundle id、iOS 文本清除策略。
- Prompt 只获得当前设备真正可用的动作与参数；Executor 仍执行服务端校验，防止模型绕过约束。

## 9. 备选方案

| 方案 | 优点 | 缺点 | 结论 |
| --- | --- | --- | --- |
| 在现有 Android Driver 中堆叠 iOS 条件分支 | 文件少、短期看似快 | 平台语义混杂，Shell/录屏/坐标分支扩散，难测试 | 不采用 |
| 直接调用 WDA，不使用 Appium | 链路短、可深度定制 | 自行维护协议、兼容矩阵、会话和真机配置成本高 | 保留为未来性能优化备选 |
| 所有设备都统一迁移到 Appium | API 表面统一 | Android 成熟路径回归面巨大，引入无必要依赖 | 不采用 |
| iOS 单独复制一套 Planner/Operator | 平台隔离彻底 | 智能链路重复，长期行为漂移 | 不采用 |
| 外部 Appium 服务作为唯一模式 | 实现简单 | 用户需手工维护进程、端口和日志，诊断闭环差 | 仅作为兼容模式 |
| 引入 DeviceProvider + 平台 Driver/Actuator | 复用上层能力，边界清晰，Android 风险可控 | 初期需要梳理设备池、锁和能力契约 | 推荐 |

## 10. 风险

| 风险 | 缓解措施 |
| --- | --- |
| `BaseDeviceDriver` 中 Android 语义过强 | 先增加 capabilities 和类型化不支持错误，再逐步拆可选协议，避免大爆炸重构 |
| 现有 REQUIRED_ACTIONS 对 iOS 过严 | 区分跨平台 core required 与平台 optional，并增加参数约束契约测试 |
| 多设备 ID 或端口冲突 | 使用 `platform:device_id` 锁键；集中端口租约；每 session 记录端口 |
| 坐标偏移造成错误点击 | 每次方向/viewport 变化重建 `CoordinateSpace`；保存转换证据；失败时停止 |
| WDA 构建与签名故障难定位 | Appium/WDA 日志作为 trace 附件；诊断分 Simulator 与真机检查 |
| page source 性能影响 Agent 延迟 | 可配置 snapshot timeout；视觉优先时按需取 source；禁止使用陈旧树执行危险动作 |
| 受管 Appium 泄漏进程 | ServerLifecycle 注册、引用计数、退出钩子和 stale resource 诊断 |
| Android 回归 | 默认平台保持 Android；现有测试先绿；新功能用平台参数化和契约测试隔离 |
| Xcode/Appium 版本升级漂移 | 锁定已验证版本；升级时运行 doctor、Driver 契约与 Simulator 冒烟矩阵 |

## 11. 测试策略

### 11.1 单元测试

- `simctl`/`devicectl` JSON fixture 解析、状态映射与无设备/多设备场景。
- canonical device id、旧 Android serial 兼容与锁键迁移。
- Appium HTTP client、服务健康检查、端口租约和 session 幂等清理。
- WDA XML 到现有 UI 元素模型的解析。
- 不同 scale、方向、安全区域与 viewport 的坐标转换。
- capabilities、参数约束和 `UnsupportedOperation`。

### 11.2 契约测试

- 为 Android 与 iOS Actuator 运行同一套 core action contract。
- 对不同平台分别断言可选动作和参数集合。
- Driver Factory 必须在平台选择错误、依赖缺失和外部 Appium 不可达时给出类型化错误。
- Recording Backend 必须满足开始、停止、重复停止和失败清理契约。

### 11.3 Simulator 集成测试

- 使用专用 iPhone Simulator：发现、启动、bootstatus、截图、关闭。
- 安装/启动可控测试 App，创建 Appium session，验证 screenshot、page source、tap、swipe、input、activate/terminate 和清理。
- 验证方向变化、Retina scale、录屏、任务取消和 Appium 重启恢复。
- 测试代码必须在实际 ARTEMIS 探索并确认界面与交互后编写，使用显式等待；动态定位优先，坐标作为已验证 fallback。

### 11.4 回归与验收

- 现有 Android 单元/集成测试保持通过；未指定平台时行为不变。
- `mobile_diagnose` 对 Android、iOS Simulator 和真机返回平台化报告。
- 多设备场景不得擅自选择；候选不唯一时必须请求用户确认。
- 最终保留环境版本、模拟器 UDID、命令输出、截图、Appium/WDA 日志与 ARTEMIS trace 作为验收证据。
- 当前环境安装阶段不新增 XCTest/UI Test；需要项目级 UI Test 时另开任务并经过对应门禁。

## 12. 预计变更区域

| 区域 | 预计动作 |
| --- | --- |
| `artemis/context.py` | 增加 iOS 平台与设备上下文字段 |
| `artemis/drivers/base.py` | 增加 capabilities、坐标元数据和类型化不支持错误 |
| `artemis/drivers/factory.py` | 按平台路由 Driver |
| `artemis/drivers/ios/` | 新增 Appium client、session manager、iOS Driver、XML/坐标适配 |
| `artemis/runtime/device_provider.py` | 新增统一 Provider 契约与 DeviceDescriptor |
| `artemis/runtime/ios_device_provider.py` | 新增 simctl/devicectl 发现与 Simulator 生命周期 |
| `artemis/runtime/device_pool.py` | 聚合 Provider，兼容原 Android API |
| `artemis/runtime/device_lock.py` | 平台命名空间锁键与兼容规范化 |
| `artemis/runtime/appium_service.py` | 受管/外部 Appium 生命周期与端口租约 |
| `artemis/mcp/actuators/ios.py` | 新增 iOS 动作适配 |
| `artemis/mcp/action_manifest.py` | core/optional 动作与参数约束 |
| `artemis/controllers/unified_controller.py` | 删除直接 Android 录屏/Shell 假设 |
| `artemis/recording/` | 抽取 Android/iOS Recording Backend |
| CLI/MCP/SDK task runner | 接收并传播平台与统一设备描述 |
| `mcp_server/tools/diagnose.py` | 新增 iOS 工具链、Runtime、Appium/WDA 与设备探测 |
| Admin Console | 显示平台、设备类型、OS 与状态，选择时携带 canonical id |
| tests/ | 增加 Provider、Driver、Actuator、坐标、服务、契约与 Simulator 集成测试 |
| docs/config/README | 增加环境、配置、能力矩阵与排障文档 |

## 13. 分阶段落地边界

1. 环境基线：安装并验证 Xcode、Runtime、专用 Simulator；不改业务代码。
2. 平台骨架：DevicePlatform、Descriptor、Provider、锁键和配置传播。
3. Appium 执行层：受管服务、session、iOS Driver、XML/坐标适配。
4. 动作闭环：IosActuator、能力/参数约束、核心动作与应用生命周期。
5. 录屏/诊断/管理台：平台化 backend、doctor、设备展示与清理。
6. Simulator 验收：用实际 App 走 ARTEMIS 闭环并保存证据。
7. 真机扩展：Team、Developer Mode、WDA 签名、端口与真机矩阵；单独门禁。

## 14. 确认版固化

- 评审意见处理：用户要求“先帮我安装完这些环境”，未提出架构修改意见，作为对唯一待确认产物的执行授权。
- 确认版基线：DeviceProvider + 平台 Driver/Actuator、平台命名空间锁、受管 Appium 默认模式、显式坐标空间、Recording Backend 与平台诊断。
- 重新打开架构门禁的条件：更换设备身份模型、取消 Provider 分层、更换 Appium 生命周期所有者、改变 Driver/Actuator 边界、改变 session/锁/端口状态归属，或把真机签名凭据纳入仓库管理。

## 15. 门禁结论

- 结论：已获用户执行授权，架构门禁通过。
- 负责人：当前用户。
- 日期：2026-09-22。
- 通过条件：确认 DeviceProvider + 平台 Driver/Actuator、平台命名空间锁、受管 Appium 默认模式、显式坐标空间、录屏 backend 与平台诊断方案。

## 确认记录

- 确认人：当前用户
- 确认日期：2026-09-22
- 授权原文：先帮我安装完这些环境
- 下一阶段：任务拆分与环境安装执行
