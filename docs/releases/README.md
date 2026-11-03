# 自 1.0.5 起的更新 / Changes since 1.0.5

## 中文

- 新增 USB 一键转 Wi-Fi ADB：自动获取手机 Wi-Fi 地址、连接并核对设备身份，支持多设备选择与已连接设备复用。
- 新增 Android 无线调试配对，区分配对端口与连接端口，配对码不写入日志。
- 改进控件选中、禁用状态识别，修复系统权限弹窗被误过滤的问题。
- 为 Flash / Pro 增加连续空转保护，减少无效模型调用。
- 修复 Pro 修复后的最终验证，以及 Windows 设备锁交接的偶发访问拒绝。
- 完善中英文提示与回归测试。OpenCode 接入继续留在独立开发分支，不包含在本次更新中。

## English

- Added one-click USB-to-Wi-Fi ADB with automatic phone IP discovery, device identity verification, multi-device selection and connection reuse.
- Added Android wireless debugging pairing with separate pairing/connection ports and pairing codes excluded from logs.
- Improved checked/disabled control-state recognition and fixed incorrectly filtered system permission dialogs.
- Added consecutive no-progress guards for Flash and Pro to reduce wasted model calls.
- Fixed Pro final verification after repair and intermittent access-denied errors during Windows device-lock handoff.
- Expanded English/Chinese feedback and regression coverage. OpenCode integration remains on a separate development branch and is excluded from this update.
