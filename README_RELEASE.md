# 版本更新说明 / Release notes

状态 / Status：待发布 / Unreleased（2026-11-03）。版本号与标签保持不变。

范围 / Scope：自 `1.0.5`（`a69c1d1`）起的 main 更新，包含此前 5 个提交与本次自动连接、设备锁修复。可直接用于更新目录的简短摘要见 [docs/releases/README.md](docs/releases/README.md)。

## 中文

### 新增

- **一键连接 Wi-Fi ADB**：USB 连接并授权手机后，点击“自动连接 Wi-Fi ADB”，自动读取真实 Wi-Fi 地址、启用 TCP/IP 调试并连接，核对设备身份后提示成功。多台 USB 手机需选择目标，忙碌设备不重启，已有无线连接验证后复用。支持中英文提示，保留手动连接入口。
- **Android 无线调试配对**：支持 Android 11+ 的 IP、配对端口与六位配对码流程；配对和连接使用不同端口，配对码提交后清空，不写入命令参数、接口响应或日志。
- **控件状态观察**：开关、复选框等控件显示选中和可用状态，禁用元素明确标注；OCR 合并后保留 XML 状态信息。

### 改进与修复

- **任务空转保护**：Flash 连续无工具调用默认 5 轮后失败退出，Pro 连续无可执行决策默认 4 轮后失败退出；保留模型反馈用于排查，不会误报成功。
- **系统弹窗识别**：避免权限弹窗和全屏系统界面被误当作状态栏或导航栏过滤。
- **修复后最终验证**：Pro 根据新证据重新评估修复后的最终 `verify` 项，保留历史记录；原有检查点与失败的 `assert` 约束继续有效。
- **Windows 设备锁交接**：对创建锁文件时的短暂访问拒绝最多尝试 3 次，间隔 20 毫秒；保持排他创建，持续失败仍抛错。
- **连接反馈**：自动连接成功后立即响应，不等待无关的模型凭据或工具链诊断；设备列表由已有轮询刷新。
- 补充可复用的后端和前端回归测试，修正无线配对后端格式检查问题。

### 使用与配置

- 入口：系统设置 → Android 设备与模拟器连接 → 更改连接 → 无线 ADB。
- USB 自动连接要求手机已授权 USB 调试，电脑与手机处于可互通的可信 Wi-Fi 网络。它启用 5555 TCP/IP 调试，区别于 Android 11+ 的配对码方式。
- 恢复 USB 调试：通过 USB 连接后执行 `adb -s <USB序列号> usb`。无线连接失败时 TCP/IP 调试可能仍开启。
- 手动配对成功只代表建立信任，还需填写无线调试主页面的连接端口；配对端口与连接端口不可混用。
- `agent.flash.max_silent_turns` 默认 `5`，`0` 关闭此保护；与总轮数限制独立。
- `agent.pro.execution.max_no_action_turns` 默认 `4`，范围 `1–20`。配置修改后需重启相关服务。

## English

### Added

- **One-click Wi-Fi ADB**: connect and authorize a phone over USB, then select **Auto-connect Wi-Fi ADB**. Artemis discovers its Wi-Fi address, enables TCP/IP debugging, connects and verifies the phone identity. Multiple USB phones require selection; busy transports are protected and existing verified connections are reused. Includes English/Chinese feedback and keeps manual setup available.
- **Android wireless pairing**: supports Android 11+ pairing through the phone IP, pairing port and six-digit code. Pairing and connection ports remain separate. Codes are cleared on submission and excluded from command arguments, responses and logs.
- **Control-state observations**: exposes checked/enabled states and disabled elements, preserving XML state when labels come from OCR.

### Improved and fixed

- **No-progress protection**: Flash fails after 5 consecutive tool-less turns by default; Pro fails after 4 consecutive turns without executable decisions. Model feedback remains available for diagnosis; termination never counts as successful completion.
- **System dialogs**: preserves permission dialogs and full-screen system UI that could previously be filtered as fixed system bars.
- **Final verification after repair**: re-evaluates repaired final `verify` findings against new evidence while preserving review history, checkpoint authority and failed `assert` constraints.
- **Windows device-lock handoff**: retries transient permission failures up to 3 times at 20 ms intervals, retaining exclusive creation and propagating persistent failures.
- **Connection feedback**: verified automatic connections return immediately without waiting for unrelated credential/toolchain diagnostics. Existing polling refreshes device inventory.
- Adds reusable backend/frontend regressions and fixes formatting in the wireless-pairing backend.

### Usage and configuration

- Open **System Setup → Android Device & Emulator Connection → Change Connection → Wireless ADB**.
- USB-assisted setup requires an authorized USB phone and a reachable trusted Wi-Fi network. It enables TCP/IP debugging on port 5555, separate from Android 11+ code pairing.
- To restore USB debugging, reconnect USB and run `adb -s USB_SERIAL usb`. TCP/IP debugging may remain enabled after a failed wireless connection.
- Pairing establishes trust only; connect using the separate port shown on the Wireless debugging main screen.
- `agent.flash.max_silent_turns`: default `5`; `0` disables this guard, independently of the total-turn limit.
- `agent.pro.execution.max_no_action_turns`: default `4`, range `1–20`. Restart affected services after configuration changes.

## 验证 / Validation

- Python 基线整套 / full baseline suite including the lock fix: **3112 passed, 4 skipped, 8 deselected**, coverage **69.62%**. This run preceded the automatic Wi-Fi feature; its incremental checks are listed separately below.
- 自动 Wi-Fi 与既有配对后端 / automatic Wi-Fi and existing pairing backend: **34 passed**.
- 提交前自动连接、配对与设备锁联合回归 / pre-commit automatic connection, pairing and device-lock regression: **55 passed**.
- 前端整套 / full frontend suite: **246 passed**; production build passed.
- Ruff、格式、质量基线与受保护核心 Pyright 检查通过；自动连接模块独立类型检查通过。 / Ruff, formatting, quality ratchet, protected-core Pyright and standalone auto-connection module type checks passed.
- 当前手机验证了 USB 转 Wi-Fi、设备身份匹配、重复连接复用，以及无线截图与 UI 层级探测。数据线未实际拔除，未覆盖重启或网络切换。 / Current-phone checks covered USB-to-Wi-Fi setup, identity matching, reuse and screenshots/UI hierarchy over Wi-Fi. Physical unplug, reboot and network-change acceptance remain unverified.

## 范围说明 / Scope notes

OpenCode 仍保存在独立分支 `codex/opencode-integration-wip`，未合入 main。本机日志、截图、打包产物及临时脚本不属于提交内容。

OpenCode remains on `codex/opencode-integration-wip` and is excluded from main. Local logs, screenshots, build artifacts and temporary scripts are excluded from this change.
