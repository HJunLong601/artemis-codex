# iOS 设备控制架构设计方案确认版

## 当前流程状态

| 字段 | 值 |
| --- | --- |
| 当前阶段 | 架构设计方案 |
| 当前门禁 | 架构门禁 |
| 门禁状态 | 已确认通过 |
| 当前允许动作 | 生成项目计划与任务包、执行环境安装 |
| 下一步 | 安装并验收 Xcode 与 iOS Simulator |

## 文档状态

- 状态：确认版
- 来源评审版：`2026-09-22-ios-device-control-architecture-solution-review.md`
- 来源技术方案：`2026-09-22-ios-device-control-technical-solution-confirmed.md`
- 确认人：当前用户
- 确认日期：2026-09-22
- 授权原文：先帮我安装完这些环境

## 确认基线

- 使用 `DeviceProvider` 聚合 Android ADB 与 iOS simctl/devicectl 设备来源。
- 使用平台独立 `DeviceDescriptor`，内部锁键采用 `platform:device_id`。
- iOS 执行层采用 `IosXcuiTestDriver` + `IosActuator` + Appium/XCUITest/WDA。
- Appium 默认由 ARTEMIS 管理，允许配置外部服务 URL。
- 截图像素与交互逻辑坐标由 `CoordinateSpace` 显式转换。
- 录屏从 Unified Controller 抽为 Android/iOS `RecordingBackend`。
- capability 同时约束动作名与参数；不支持能力返回类型化错误。
- Android 保持默认路径和向后兼容。

## 本次评审意见处理

- 用户未要求修改架构，直接要求开始安装环境。
- 安装是确认架构后的首个任务，不涉及业务代码或真机签名。

## 仍待后续决策

- 真机 Apple Developer Team、WDA bundle id 和签名策略。
- 首批旧 iOS/Xcode 兼容矩阵。
- iOS 多设备并发规模。

## 重新打开门禁条件

更换设备身份模型、Provider 分层、Appium 生命周期所有者、Driver/Actuator 边界、session/锁/端口状态归属，或改变签名凭据安全边界时重新评审。

## 门禁结论

- 结论：通过
- 日期：2026-09-22
- 下一阶段：项目计划与任务执行
