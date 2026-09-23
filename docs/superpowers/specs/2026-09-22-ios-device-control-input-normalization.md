# iOS 设备控制输入物归一化

## 当前流程状态

| 字段 | 值 |
| --- | --- |
| 当前阶段 | 输入物归一化 |
| 当前门禁 | 无 |
| 门禁状态 | 已确认通过 |
| 当前允许动作 | 进入技术方案门禁 |
| 下一步 | 输出当前稳定版 Appium + XCUITest Driver + WebDriverAgent 技术方案评审版 |

## 范围摘要

- 最终目标：让 ARTEMIS 在保留现有 Planner、Operator、Checker、视觉理解和历史能力的前提下，支持控制 iOS Simulator 与 iOS 真机。
- 推荐执行后端：当前稳定版 Appium + XCUITest Driver + WebDriverAgent。
- 当前用户授权的首个执行范围：安装完整 Xcode、安装至少一个兼容的 iOS Simulator Runtime、启动一个 iPhone Simulator，并验证 `xcodebuild`、`simctl`、`devicectl` 和模拟器启动链路。
- 后续预期范围：iOS Driver、设备发现、页面层级转换、动作映射、录屏、诊断、自愈、多设备互斥、CLI/MCP/Admin Console 接入与验收。

## 当前阶段非目标

- 本阶段不修改 ARTEMIS 业务代码。
- 本阶段不安装或签名 WebDriverAgent 到真机。
- 本阶段不要求 Apple Developer Team、证书或 provisioning profile。
- 本阶段不承诺 Android 与 iOS 全能力等价；iOS 不支持任意设备 shell、Android BACK 键语义及部分系统设置操作。
- 本阶段不进行发布、提交、推送或创建合并请求。

## 输入物索引

| 输入物 | 来源 | 已确认事实 |
| --- | --- | --- |
| 用户目标 | 当前会话 | 采用推荐路线，先形成项目计划，再执行 iOS 开发环境与模拟器安装 |
| iOS 路线图 | `README.md`、`README_CN.md` | 项目明确把 iOS Simulator 与真机支持列为未完成路线图 |
| 平台模型 | `artemis/context.py` | `DevicePlatform` 当前只有 `ANDROID` |
| Driver 抽象 | `artemis/drivers/base.py` | 已存在通用异步 `BaseDeviceDriver`，可承载 iOS Driver |
| Driver 创建 | `artemis/drivers/factory.py` | 非 Mock 路径当前默认创建 `AndroidAdbDriver` |
| CLI/MCP 路径 | `artemis/interfaces/cli/commands/run.py`、`mcp_server/background/task_runner.py` | 创建设备上下文时写死 Android |
| 录屏路径 | `artemis/controllers/unified_controller.py` | 当前实现绑定 ADB、scrcpy 与 Android display state |
| Apple 官方工具链 | Apple Xcode command-line tools 文档 | `xcodebuild`、`simctl`、`devicectl` 随完整 Xcode 提供 |
| iOS 自动化后端 | Appium XCUITest Driver 官方文档 | 支持 iOS Simulator 与真机，依赖 macOS、Xcode、Appium 和 WDA |

## 当前机器基线

| 检查项 | 当前结果 | 影响 |
| --- | --- | --- |
| 主机 | Apple Silicon (`arm64`)，macOS 26.6.2 | 满足 iOS 开发主机方向 |
| 可用磁盘 | 约 756 GiB | 满足 Xcode 与 Simulator Runtime 安装空间要求 |
| Developer Directory | `/Library/Developer/CommandLineTools` | 只有 Command Line Tools，不是完整 Xcode |
| Xcode | 未发现 `Xcode.app` | `xcodebuild` 当前不可用 |
| Simulator 工具 | `simctl` 不可用 | 尚不能发现、创建或启动模拟器 |
| 真机工具 | `devicectl` 不可用 | 尚不能发现或管理 iOS 真机 |
| Appium / idb / Maestro | 均未安装 | 自动化后端尚未建立；不阻塞先安装 Xcode |
| Git | `main` 与 `origin/main` 一致 | 可安全增加文档产物 |

## 冲突列表

| 编号 | 冲突 | 处理方式 |
| --- | --- | --- |
| C1 | 用户希望立即安装，但当前交付工作流要求先完成输入物归一化并经过技术方案门禁 | 本轮只固化输入；确认后进入技术方案门禁，不在未确认门禁时安装 |
| C2 | `BaseDeviceDriver` 名义通用，但包含 `execute_shell`、Android 键值和物理像素坐标假设 | 技术方案中引入平台能力协商与独立坐标空间，避免用空实现掩盖不支持能力 |

## 缺失信息

| 编号 | 信息 | 是否阻塞当前阶段 | 最迟解决阶段 |
| --- | --- | --- | --- |
| M1 | 首批要支持的最低 iOS/Xcode 版本矩阵 | 否 | 技术方案门禁 |
| M2 | 后续真机使用的 Apple Developer Team 与 WDA 签名方式 | 否 | 真机任务实施前 |
| M3 | 真机首批设备型号、系统版本和 UDID | 否 | 真机任务实施前 |
| M4 | 是否要求首版支持多台 iOS 设备并发 | 否 | 架构门禁 |
| M5 | iOS 与 Android 的能力差异是否允许在工具层显式降级 | 否 | 技术方案门禁 |

## 初始门禁清单

| 门禁 | 是否需要 | 原因 |
| --- | --- | --- |
| 产品门禁 | 暂不单独触发 | 当前目标、首步范围和非目标足够明确 |
| 设计门禁 | 不需要 | 当前任务没有 UI 设计交付 |
| 交互门禁 | 暂不单独触发 | 本轮是工具链安装；设备动作语义在技术方案中处理 |
| 技术方案门禁 | 需要 | 需要固定 Appium/WDA、Xcode/Runtime 安装方法、版本策略与验证标准 |
| 架构门禁 | 需要 | 后续要决定 Driver、能力协商、设备发现、录屏和诊断边界 |
| 实施门禁 | 需要 | 安装完整 Xcode 属于大体积外部环境变更；代码阶段也会修改核心设备路径 |
| 验收门禁 | 需要 | 需提供模拟器启动、工具链版本和后续动作闭环证据 |
| 发布门禁 | 后续判断 | 只有涉及包发布、安装脚本或公共接口变更时触发 |

## 理解完整性审计

| 维度 | 状态 | 说明 |
| --- | --- | --- |
| 用户目标 | 完整 | 先规划，再安装 iOS 开发环境和模拟器，最终接入 ARTEMIS |
| 当前代码基线 | 完整 | 已确认平台枚举、Driver Factory、CLI/MCP 与录屏的 Android 绑定点 |
| 当前机器基线 | 完整 | 已确认系统、架构、磁盘、Xcode 与工具缺口 |
| 首步安装范围 | 基本完整 | 完整 Xcode + Simulator Runtime + 启动验证；具体版本在技术方案固化 |
| 真机范围 | 部分完整 | 已知后续需要真机，但签名与设备矩阵尚未提供 |
| 架构范围 | 部分完整 | 推荐路线明确，能力协商与坐标模型需在架构门禁确认 |
| 验收标准 | 部分完整 | 首步可验证，完整 iOS 控制验收需后续固定 |

结论：输入物归一化已达到进入技术方案门禁的条件；当前没有必须在本阶段补充的阻塞性业务信息。

## 问题队列

| 优先级 | 问题归属 | 建议回答角色 | 问题 | 阻塞阶段 | 状态 |
| --- | --- | --- | --- | --- | --- |
| P0 | 技术方案 | 技术负责人、当前任务确认人 | 是否确认本输入物归一化，并进入技术方案门禁？ | 技术方案门禁 | 已回答：2026-09-22 用户确认 |
| P1 | 技术方案 | iOS 研发负责人 | 首批最低 iOS/Xcode 支持矩阵是什么？ | 技术方案门禁 | 待回答 |
| P1 | 技术方案 | iOS 研发负责人、账号管理员 | 后续真机采用哪个 Apple Developer Team 和 WDA 签名方式？ | 真机实施 | 待回答 |
| P1 | 架构设计 | 技术负责人 | 首版是否要求多台 iOS 设备并发？ | 架构门禁 | 待回答 |
| P2 | 验收标准 | 测试负责人 | 完整 iOS 接入是否要求长期真机稳定性与多版本矩阵？ | 验收门禁 | 待回答 |

## 项目上下文缺口

- 当前仓库未发现 iOS 设备控制专用的项目上下文文档。
- 后续需在技术方案中固化版本策略、Apple 签名边界、平台能力差异、坐标语义和诊断责任。
- 后续需在架构确认后补充项目上下文，避免把 iOS 假设写入通用工作流技能。

## 产物路径计划

| 产物 | 路径 | 状态 |
| --- | --- | --- |
| 输入物归一化 | `docs/superpowers/specs/2026-09-22-ios-device-control-input-normalization.md` | 已生成 |
| 技术方案 | `docs/superpowers/specs/2026-09-22-ios-device-control-technical-solution.md` | 待生成 |
| 技术方案确认版 | `docs/superpowers/specs/2026-09-22-ios-device-control-technical-solution-confirmed.md` | 待确认后生成 |
| 架构设计方案评审版 | `docs/superpowers/specs/2026-09-22-ios-device-control-architecture-solution-review.md` | 待生成 |
| 架构设计方案确认版 | `docs/superpowers/specs/2026-09-22-ios-device-control-architecture-solution-confirmed.md` | 待确认后生成 |
| 项目实施计划 | `docs/superpowers/plans/2026-09-22-ios-device-control-implementation-plan.md` | 待架构确认后生成 |
| 验收报告 | `docs/superpowers/reports/2026-09-22-ios-device-control-acceptance-report.md` | 待实施后生成 |

## 确认记录

- 确认结果：输入物归一化通过。
- 确认日期：2026-09-22。
- 确认依据：用户在当前会话回复“确认”。
- 后续动作：进入技术方案门禁，输出技术方案评审版。
