# T08：iOS Simulator 验收报告

## 结论

iOS Simulator 控制链路已通过验收。设备发现、Appium/XCUITest 会话、截图、UI 层级、
HOME 动作、原生录屏、平台诊断，以及 CLI/MCP/Admin 任务入口可在同一环境中重复执行。

## 验收矩阵

| 能力 | 结果 | 证据 |
| --- | --- | --- |
| Registry 发现 | 通过 | 唯一目标 Simulator 状态为 `ready`，平台为 `ios` |
| iOS 环境诊断 | 通过 | `mobile_diagnose` 返回 `ready`，Xcode/Appium/XCUITest 检查通过 |
| Driver 建连 | 通过 | Appium session 创建和删除均返回 HTTP 200 |
| 截图 | 通过 | PNG 为 1206×2622，3,252,476 字节 |
| UI 层级 | 通过 | XCUITest source 成功解析 296 个元素 |
| 系统动作 | 通过 | HOME 动作返回成功 |
| 录屏 | 通过 | H.264 MP4，1206×2622，1,785,739 字节 |
| 重复执行 | 通过 | 第二次录屏可安全替换同名旧产物并正常完成 |
| 自动化回归 | 通过 | Runtime、iOS Driver、MCP、SDK、Admin、CLI 相关测试通过 |

## 本轮发现并关闭的问题

重复运行录屏脚本时，`simctl recordVideo` 会拒绝覆盖固定文件名，旧逻辑因此误入真机
Appium fallback。Driver 现在只在启动新会话前删除自己管理的目标录屏文件，并新增回归
测试，确保录屏可重复执行且不会扩大删除范围。

## 边界

- 本报告覆盖 iOS Simulator。
- iOS 真机仍属于 T09，需要用户指定设备、Apple Developer Team、签名和 Developer Mode
  授权后单独验收。
- 本地截图和录屏只保存在已忽略的 `artifacts/ios/`，不进入开源提交。
