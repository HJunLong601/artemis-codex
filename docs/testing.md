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

## Android emulator without a physical device

`ARTEMIS_EMULATOR_DISCOVERY_ENABLED=true` is the default. Automatic Android
selection prefers an idle physical device, falls back to an idle emulator, then
queues on a ready device (physical first) when all are busy. Set it to `false`
in `.env` to disable automatic emulator selection, installed-AVD discovery and
standalone emulator startup. Restart MCP/daemon after changing the setting.
Connected emulators remain visible in inventory; an explicitly selected serial
or a manual diagnostics launch still works. Offline or locked physical devices
do not displace a usable emulator, and multiple AVDs still require a choice.
When selecting across platforms without an explicit platform, eligible physical
devices take precedence, followed by Android emulators, then iOS Simulators.
An explicit platform or device remains authoritative.

Use an existing Android SDK emulator, system image and AVD. Tool discovery checks
`ARTEMIS_EMULATOR_PATH`, Android SDK environment variables, tools on `PATH`, the SDK
containing the resolved `platform-tools/adb`, then common SDK directories. A custom
SDK does not need `ANDROID_HOME` when its SDK ADB is already discoverable.
Installed AVDs come from that emulator's `-list-avds` output. If the tool is
unavailable, directory fallback uses `ANDROID_AVD_HOME`, `ANDROID_EMULATOR_HOME`,
`ANDROID_USER_HOME`, legacy `ANDROID_SDK_HOME`, and the current user's home;
it does not embed a developer's SDK drive or username. See the
[Android environment-variable reference](https://developer.android.com/tools/variables).
iOS inventory uses `xcrun simctl` from the host toolchain, inheriting its Xcode
selection and `DEVELOPER_DIR`; it does not assume a particular Xcode app path.

1. Run `adb devices -l` and list AVDs using the resolved emulator executable's
   `-list-avds` option. Confirm the target when several AVDs or devices exist.
2. Call `mobile_diagnose(launch_avd="CHOSEN_AVD")`. It launches in the background;
   poll diagnostics without `launch_avd` until `emulator.status` is `ready`.
   Read failures and logs before retrying a launch.
3. Use the returned serial, rather than assuming `emulator-5554`, in
   `mobile_diagnose(device_serial="SERIAL", attempt_fix=true, probe_device=true)`.
   Wait for a passing screenshot and hierarchy probe before starting UI tests.
4. Run ARTEMIS tasks with that explicit `device_serial`. Explore the target app
   before authoring navigation tests; AOSP Settings labels differ from MIUI.

For standalone MCP tasks started with no connected Android device, `ARTEMIS_AVD_NAME`
selects the AVD to boot. If unset, startup requires exactly one installed AVD;
multiple choices produce an actionable selection error. Restart MCP after changing
its environment. Startup waits for the chosen AVD's `sys.boot_completed=1` and
passes its actual serial to the agent. It does not reserve a fixed emulator or
gRPC port. Existing emulators are also checked for boot completion before reuse.
With the daemon scheduler, launch through diagnostics first, then submit tasks
against the returned serial.

Emulator launch requests first check live ADB identities. An already running AVD
is reused even after the manager/MCP process restarts, without requiring another
emulator process or an SDK binary for reuse. Concurrent requests in one manager
share its in-progress launch; a different AVD cannot overwrite that active launch.
Resetting the progress panel while boot tracking is active is rejected.

Boot readiness requires both `sys.boot_completed=1` and a working Android package
service (`pm path android`). A screenshot/hierarchy probe remains the acceptance
check for UI automation. Explicitly requested device information in diagnostics
is taken from that device's inventory entry, matching helper and smoke-test targets.

AVD inventory is cached for 10 seconds and concurrent callers share a scan. SDK
file changes, user AVD directory changes and environment-path changes invalidate
the cache immediately; an explicit readiness refresh also clears it. Only static
AVD names are cached. SDK discovery runs outside the readiness event loop so a
slow inventory command does not block other requests.

Portable unit regressions cover selection, AVD identity, nondefault serials,
offline devices, timeout/crash handling and Windows/POSIX process isolation.
They do not boot an emulator in CI. Keep local screenshots, traces and temporary
device validation scripts under ignored `artifacts/` directories.

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

## 视觉缓存命中回归

默认离线回归覆盖跨结构局部复用、压缩强度噪声、局部文字/像素变化、移动/禁用/
隐藏目标、弹窗/键盘/其他应用窗口、重复目标、无边界/纯色图块、Activity/设备/
应用隔离、库存去重、纠错和持久化统计。历史步骤分页也验证规划虚拟步骤只出现
一次、孤立工具归属、跨会话隔离及分页间 SSE 事件保留。默认测试不访问真实模型。

真机测量应分别统计 XML 直接定位、冷查询、同截图重复查询、新截图查询和纠错后
查询。XML 命中不能算作视觉缓存命中；`visual_batches` 是进入视觉检测的批次数，
不能解释成计费请求数或模型内部重试次数。总体命中率包含冷查询、主动纠错和
保护性未命中，重复定位收益应单独统计。跨 Activity、遮挡或目标变化时拒绝复用
是正确行为。设备截图、日志和本机测试产物留在 `artifacts/`，不提交到仓库。

## Flash 延迟摘要与回退验证

`agent.flash.step_summarizer.defer_until_steps` 默认 3，0 恢复立即摘要。短任务保留
原始截图、操作与 UI 记录，结束时标记摘要 `deferred`，不为了收尾额外调用模型。
达到动作数门槛后补齐积压并恢复后续即时调度。以下条件也会提前恢复：

- 已测上下文达到 `memory.transcript.start_ratio`；
- 历史压缩器需要某一步摘要（未就绪时仍遵循原有保留原图的宽限窗口）；
- 动作失败，或操作记录未能持久化；
- 延迟队列中原始图片达到 8 MiB。

Pro 仍使用即时摘要。摘要失败继续走既有并发上限、重试上限、flush 超时及原始
截图召回流程。`deferred` 只表示未生成文字摘要，不能解释成验证通过或失败。
如需每一步都有文字摘要的导出报告，将该阈值设为 0 后运行。

影子模型对比只读取通过上述真机回归获得的三个页面，不执行候选模型给出的动作：

```powershell
.venv\Scripts\python.exe scripts/benchmark_codex_settings.py --frames artifacts/android/2026-10-07/speed-baseline-frames --repeats 2 --output artifacts/android/2026-10-07/model-shadow-benchmark.json
```

脚本轮换 Sol medium、Sol low、Luna low 的顺序，记录成功率、缓存计数和中位/最大
延迟；无权限/超时单独记失败，不悄悄换模型。这只是已知页面的语义动作选择测试，
不覆盖真实点击坐标、长流程恢复、完整 Flash 工具集合和复杂应用，不可据此自动
修改生产默认模型。小样本不报告有统计意义的 P95。
