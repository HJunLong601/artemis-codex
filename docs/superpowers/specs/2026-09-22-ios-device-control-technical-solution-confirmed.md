# iOS 设备控制技术方案确认版

## 当前流程状态

| 字段 | 值 |
| --- | --- |
| 当前阶段 | 技术方案 |
| 当前门禁 | 技术方案门禁 |
| 门禁状态 | 已确认通过 |
| 当前允许动作 | 进入架构设计方案阶段 |
| 下一步 | 输出架构设计方案评审版并等待确认 |

## 文档状态

- 状态：确认版
- 来源评审版：`2026-09-22-ios-device-control-technical-solution.md`
- 来源输入物：`2026-09-22-ios-device-control-input-normalization.md`
- 确认人：当前用户
- 确认日期：2026-09-22
- 确认原文：确认

## 已确认技术基线

1. Host 使用当前 Apple Silicon Mac，安装稳定版完整 Xcode，不使用 beta 工具链。
2. 执行时实际稳定基线采用 Xcode 27.0 与其匹配的稳定 iOS 27.0 Simulator Runtime。
3. iOS 控制后端采用 Appium 3.7.0 + XCUITest Driver 12.13.1 + WebDriverAgent。
4. 交付顺序为 Simulator-first；真机签名、Developer Mode 与 provisioning 单独分阶段处理。
5. ARTEMIS 上层 Planner、Operator、Checker、Explorer 与历史能力保持平台无关。
6. screenshot、page source、tap、long press、swipe、input、app lifecycle 和 recording 通过 iOS Driver/Actuator 接入。
7. iOS 不具备 Android 任意 shell 和完整系统按键等价能力；必须使用 capability 协商与明确的 `UnsupportedOperation`，不得静默成功。
8. 截图物理像素和 WDA 交互逻辑坐标必须通过明确的坐标空间模型转换。
9. 首步仅安装并验证 Xcode、Simulator Runtime 与模拟器，不在项目中自动启用 XCTest/UI Test。

## 评审意见处理

- 用户未提出修改项，评审版全部内容原样进入确认基线。
- 架构阶段必须确定设备发现、锁键命名、Appium 生命周期、坐标空间、能力约束、录屏与诊断的模块归属。

## 剩余风险

- Xcode 与 Simulator Runtime 下载体积较大，系统认证步骤可能需要用户在 App Store 或系统弹窗中操作。
- 真机阶段仍需明确 Apple Developer Team、WDA bundle id 和首批设备/系统版本。
- Appium/XCUITest Driver 的精确锁定版本在其安装任务执行时，依据当前 Xcode 兼容矩阵再次验证。

## 变更控制

以下变化需要重新打开技术方案门禁：

- 更换 Appium/XCUITest/WDA 主后端；
- 改用 Xcode beta 或改变最低 Xcode/iOS 版本策略；
- 支持非 macOS Host；
- 改变签名与凭据安全边界；
- 要求 Android 与 iOS 所有能力完全等价；
- 在当前环境首步中新增 XCTest/UI Test 项目改造。

## 门禁结论

- 结论：通过
- 负责人：当前用户
- 日期：2026-09-22
- 后续阶段：架构设计方案
