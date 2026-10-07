# Local tests and CI

The default Python suite covers `tests/unit`, `tests/tools`, and
`packages/artemis-client/tests`. It must run without provider API keys, a Codex
login, or a connected mobile device. Tests marked `integration`, `e2e`, `cloud`,
`manual`, or `android` are excluded by the default pytest configuration.

Install the locked development environment and run the same Python checks as CI:

```sh
uv sync --dev --locked
uv run ruff format --check .
uv run ruff check .
uv run python scripts/quality_ratchet.py
uv run pyright --project pyright-core.json
uv run pytest -q --cov=artemis --cov=mcp_server --cov=apps/admin_console --cov-fail-under=60
```

CI runs these checks on Windows and Linux for pushes, pull requests, and manual
runs. The quality baseline is a ceiling, not a target to increase when a check
fails. Fix unexpected exceptions and swallowed errors before updating it.

## Keeping tests independent of the host

- Use `tmp_path` for files written by a test. Do not write files at module import
  time or assume `/tmp` exists on every platform.
- Pass mock model clients through the same context used by the execution engine.
  Mocking the SDK constructor alone does not select the native model path when
  no provider key is present.
- For configuration-only tests, use `build(validate_profiles=False)`. Tests of
  authentication itself should mock login/key checks and verify both outcomes;
  production validation remains enabled by default.
- Supply explicit daemon host/port values or patch the defaults in the test
  fixture. Local `.env` settings must not determine expected test results.
- Restore both settings fields and environment variables after testing
  `set_api_key`, even when `persist_to_env=False`: that option prevents disk
  writes, but the method still updates process state. `monkeypatch.setenv` records
  an absent variable for restoration; deleting an already absent variable does
  not.

Device acceptance tests are a separate activity. Select the target device,
explore the application with ARTEMIS, and preserve screenshots and traces before
authoring or updating device interaction tests. Passing the default suite does
not establish real-device acceptance or live model availability.

## 可重复的 MIUI 真机回归

当前已探索并验证的基线是 Xiaomi M2002J9E、Android 12、MIUI 13.0.7、中文、
1080×2400。源路径见 ARTEMIS Pro trace
`6748c371-729e-43f6-b605-a10b71fb9dc3`（2026-10-07）。其他系统/语言需先用
ARTEMIS 探索，再适配断言；不要把当前脚本当作通用 Android 回归。

1. 执行 `adb devices -l`；多个设备时先确认目标序列号。
2. 调用 `mobile_diagnose(attempt_fix=true, device_serial="SERIAL", verify_models=true)`。
   该参数检查 Codex 模型目录和最小推理；目录存在不代表账号有推理权限。
   配置变化后重启 MCP/daemon 再诊断。日常轮询不必重复发送推理请求。
3. 运行固定导航回归：

   ```powershell
   .venv\Scripts\python.exe scripts/real_device_settings_regression.py --device-id SERIAL
   ```

4. 检查退出码和 `artifacts/android/<日期>/settings-<时间>/report.json`：三项
   `settings_home`、`display_page`、`return_home` 都应通过。文件记录独立阶段耗时、
   文本定位方式、实际层级后端，并保存三张截图。返回首页还通过 ADB 前台 Activity
   独立核对。截图可能含设备账号等信息，分享前检查；不要提交到仓库。
5. 模型接入、提示词、工具契约有改动时，另跑 Flash 自主导航验收；Planner/Checker
   有改动时再跑 Pro。固定脚本不调用模型，不能替代自主任务验收。

脚本使用 ARTEMIS 的设备互斥锁、屏幕客户端和控制器，先通过“显示”文本定位，失败
后才使用新鲜层级中已确认的元素坐标。页面状态通过 250 ms 轮询等待，并要求匹配的
层级稳定 500 ms，避免在 MIUI 转场中截图或继续点击。单次断言默认
15 秒截止；失败退出码为 1，仍保留报告并释放连接/设备锁。不会点击显示设置开关。
设备驱动自身的连接、ADB 超时独立于页面断言截止时间。

MCP 任务状态若为 `paused`，读取 `pause.reason`、`pause.category`、`pause.since`
和 `next_steps`。这是 LLM 重试耗尽后正在等待恢复的执行状态，任务进程和设备锁仍然
存在；修复后在控制台恢复，或用 `mobile_manage_task(action="stop", trace_id=...)`
停止。恢复/取消/暂停超时会清理暂停元数据，完成或失败状态优先于旧暂停记录。
