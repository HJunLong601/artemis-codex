# 模型调用与真机回归耗时

2026-10-07 在 `a94265fd`（Xiaomi M2002J9E / Android 12 / MIUI 13.0.7）上实测。
这是单次样本，联网推理延迟会波动；三种任务的验证深度不同，不是严格 A/B 基准。

| 路径 | 耗时 | 模型调用数 | 输入 tokens | 验证范围 |
|---|---:|---:|---:|---|
| Pro | 384.4 s | 30 | 696,918 | 规划、3 个断言、Checker、报告 |
| Flash | 143.6 s | 5 | 99,050 | 设置→显示→返回，自主观察/操作/结束 |
| 固定脚本 | 8.33 s | 0 | 0 | 3 个明确页面断言、稳定截图、ADB 前台核对 |

Pro trace：`6748c371-729e-43f6-b605-a10b71fb9dc3`。
Flash trace：`7d3f335b-bdb0-4cbf-ac31-5b720d8a32c7`。
固定脚本报告：`artifacts/android/2026-10-07/settings-regression-2/report.json`。
Flash 从已滚动到“显示”可见的首页开始，Pro 多做了一次滚动。固定脚本时间是脚本
内部计时，包含连接、清理，不含 Python 导入时间；自主任务按 MCP 起止时间计时。

最小 READY 推理使用目录支持的 `low` 强度：Sol 7.079 s，Luna 6.937 s；目录查询
0.937 s。该样本不能证明换小模型有稳定延迟收益，也不能预测复杂视觉任务的速度。
证据在 `artifacts/android/2026-10-07/model-preflight.json`。

## 已落实

- 已知 UI 路径固化为 `scripts/real_device_settings_regression.py`，通过 ARTEMIS
  屏幕后端/控制器执行，使用条件等待，不为每一步重新规划。
- 模型目录在单个进程内缓存 60 秒；主/备用节点去重。任务启动只查目录，显式
  `mobile_diagnose(verify_models=true)` 才发送最小推理；状态轮询不发送模型请求。
- 不支持的模型直接失败，LLM 暂停通过 MCP 明确返回原因和恢复提示，减少误判为
  “仍在执行”后反复等待的时间。

## 2026-10-07 第二轮实现与验证

已实现短任务延迟摘要及缓存/耗时遥测。`defer_until_steps=3` 时，两次导航动作
暂不生成视觉摘要；原图与动作仍保存在 DataEngine，历史需要、上下文压力、动作
失败或队列大小越界时提前恢复旧调度，达到门槛后补齐积压。设置 0 可回退为即时
摘要；Pro 不受此延迟策略影响。详见 [回归与回退说明](testing.md)。

同一路径新 Flash trace `0db41ae4-db49-4ae6-837f-af243ff5a758`：**63.4 s、3 次
模型调用、0 次视觉摘要调用**，动作保持显示→返回。三个主调用分别 17.203 / 18.468 /
12.437 s；输入合计 75,108 tokens，其中第二次测到 9,216 个缓存命中 tokens。
两个动作的 `summary_status=deferred`，原始截图继续可用。

门槛回退验收 trace `0e351e7b-c43e-4ab4-8ef1-951abad22547`：连续两轮显示→返回，
92.7 s 完成；5 次主循环调用、4 次摘要调用。四个动作的摘要最终全部为 `ready`，
证明第三个动作触发后能补齐前两步，并恢复后续正常摘要。结束后的 MCP 页面结构和
ADB `mResumedActivity` 均确认处于设置首页。

旧 Flash 样本是 143.6 s / 5 次调用。减少两次摘要调用已被 trace 证实；输入变大
（保留原始证据）、服务延迟与页面位置也有变化，**不能把全部耗时差归因于优化**。
已有页面的确定性脚本本轮仍通过三个断言，内部计时 8.02 s。

同一组已验证的设置/显示/返回截图做了两轮影子推理，每种模型共 6 次，只比较语义
动作选择，不执行返回动作。三个候选轮换测试顺序，结果为：

| 候选 | 正确次数 | 中位延迟 | 最大延迟 |
|---|---:|---:|---:|
| Sol medium | 6/6 | 14.524 s | 19.563 s |
| Sol low | 6/6 | 15.750 s | 20.750 s |
| Luna low | 5/6 | 13.820 s | 17.593 s |

Luna 的错误：第二轮最终页面已返回设置首页，历史明确说明任务已完成，却再次建议
`open_display`，正确动作应为 `finish`。这是影子测试，未发到手机。Sol low 本样本
没有速度收益，因此**保留默认 Sol medium，不自动启用候选模型或降低推理强度**。
每组仅 6 个决策，不能外推复杂场景成功率或稳定 P95。

原始对比结果在 `artifacts/android/2026-10-07/model-shadow-benchmark.json`，可用
`scripts/benchmark_codex_settings.py` 复跑。缓存统计是服务端返回的缓存读取计数，
不是本地猜测；缺失值以 `cache_usage_available=false` 表示。该设计与官方
[App Server 的 usage 事件](https://learn.chatgpt.com/docs/app-server)及
[缓存观测说明](https://developers.openai.com/api/docs/guides/prompt-caching/diagnostics)
一致。桥接保留了 provider 的原请求契约，并提供 `ARTEMIS_CODEX_TELEMETRY=0`
回退开关。

## 第一轮提出的优化方向（进度已更新）

1. **按任务选执行方式。** 固定回归用脚本；简单自主交互用 Flash；需要计划、检查点、
   日志分析和审计报告时用 Pro。Pro 可按任务选择 `verification_level="final"`，
   但不能为提速删除用户要求的中途检查点。
2. **精简每轮输入（尚未实施）。** 第一轮 Flash 主循环 3 次调用各携带 20,944 / 22,587 / 24,105
   输入 tokens。先分项测量系统提示词、工具 schema、历史和截图，按需缩减工具集、
   剪裁历史。原生 App Server 进程已经按事件循环复用，无需再做一次进程池。
3. **优化短任务摘要策略（已实现）。** 旧 Flash 另有 2 次后台视觉摘要，输入合计 31,414
   tokens。延迟摘要保留长任务压缩能力；后台摘要并行运行，
   不能把其耗时直接加到或从端到端时间中扣除。当前已加入有提前启动条件的延迟策略。
4. **对实际页面做模型/强度 A/B（已完成首轮影子比较）。** 对比 Sol medium、Sol low 和 Luna low，至少
   多轮重复，同时记录成功率、误点、重试、P50/P95。优先降低简单节点的强度，
   不凭 READY 微基准全局切换模型。
5. **补齐缓存与单轮耗时遥测（已实现 Artemis 局部桥接）。** 上游 `codex_client_provider` 将 usage 映射为
   LangChain 元数据时只保留 input/output/total，未传 `cachedInputTokens`。
   因而旧日志里的 cached=0 不能证明服务端缓存未命中。Flash 的 `FlashRunner`
   trace 原来未单独记录每轮 duration；现在 Flash 入口已补齐 duration，并透传缓存计数。
   后续应将缓存映射上移到公共 provider 的扩展入口，
   再评估复用 thread 或优化稳定前缀。不同角色直接共享 thread 有上下文串扰风险。

统计模型调用时只数 `traces` 表中的 `name='llm_usage'`，不要把同一调用的
`CodexAppServerChatModel`/`FlashRunner` trace 再算一次。Pro 的 30 次分布为：
Planner 3、Operator 11、Checker 11、视觉摘要 3、Outputter 2；旧 Flash 是主循环 3、
视觉摘要 2。
