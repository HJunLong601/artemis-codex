# T07：iOS 录屏与平台诊断

## 目标

补齐 iOS 在 ARTEMIS 平台配套层的录屏生命周期，并让诊断/展示入口逐步从 Android-only
假设迁移到平台感知实现。

## 本轮范围

- `UnifiedMobileController` 在 iOS 下调用 XCUITest Driver 的 Appium 录屏接口，不启动 scrcpy。
- 复用全局 RecordingSession、DataEngine 记录和单段 manifest，使 Video Analyzer 可读取产物。
- 启停失败时回收 session，重复启动与无会话停止返回结构化失败。
- Runtime 安装完成后执行真实 Simulator 录屏烟测。

## 已完成的平台入口

- `mobile_diagnose` 支持 Registry 驱动的 Android/iOS 双平台报告；iOS 探针检查
  Xcode、Appium、XCUITest Driver、Simulator，并可执行截图与层级端到端验证。
- CLI、MCP、Admin API/队列统一传播 `device_platform`，并用
  `platform:device_id` 隔离 iOS 锁；Android 默认行为保持兼容。
- `mobile_get_device_state` 可通过 XCUITest Driver 读取 iOS 截图和层级。
- Agent 初始化、前台 App 查询、Driver 连接和清理均按平台分流；iOS 不再依赖 ADB。

## 已完成证据

- RED：原 Unified Controller 对 iOS 错误启动 scrcpy，聚焦测试稳定复现失败。
- GREEN：iOS 录屏进入平台分支；Simulator 使用 `simctl io recordVideo`，真机保留
  Appium fallback；session、manifest、DataEngine 与清理语义保持统一。
- LIVE：专用 Simulator 上生成 1,093,248 字节 H.264 MP4，分辨率 1206×2622。
- 发现并修复 WebDriverAgent 首次构建超过 60 秒导致建会话超时的问题；建会话独立使用
  180 秒超时，普通指令仍保持 60 秒。
- 真实 `mobile_diagnose(device_platform="ios", probe_device=true)` 返回 `ready`；
  截图 3,252,476 字节，识别 296 个元素，探针耗时 12.63 秒。
- 重复录屏验收发现并修复固定文件名无法覆盖的问题；修复后生成 1,785,739 字节
  H.264 MP4，分辨率 1206×2622。

## 测试顺序

1. RED：iOS start/stop 必须只调用 Driver，不调用 Android display/scrcpy 路径。
2. GREEN：增加平台分支与 session/manifest 清理。
3. 回归：Android 录屏既有测试保持通过。
4. LIVE：Simulator 上生成非空 MP4 并验证可读取。
