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

## 下一步优先顺序

1. **按任务选执行方式。** 固定回归用脚本；简单自主交互用 Flash；需要计划、检查点、
   日志分析和审计报告时用 Pro。Pro 可按任务选择 `verification_level="final"`，
   但不能为提速删除用户要求的中途检查点。
2. **精简每轮输入。** 此次 Flash 主循环 3 次调用各携带 20,944 / 22,587 / 24,105
   输入 tokens。先分项测量系统提示词、工具 schema、历史和截图，按需缩减工具集、
   剪裁历史。原生 App Server 进程已经按事件循环复用，无需再做一次进程池。
3. **优化短任务摘要策略。** 此次 Flash 另有 2 次后台视觉摘要，输入合计 31,414
   tokens。可实验“短任务延迟启动摘要”，保留长任务压缩能力；后台摘要并行运行，
   不能把其耗时直接加到或从端到端时间中扣除。当前默认配置未修改。
4. **对实际页面做模型/强度 A/B。** 对比 Sol medium、Sol low 和 Luna low，至少
   多轮重复，同时记录成功率、误点、重试、P50/P95。优先降低简单节点的强度，
   不凭 READY 微基准全局切换模型。
5. **补齐缓存与单轮耗时遥测。** 当前 `codex_client_provider` 将 usage 映射为
   LangChain 元数据时只保留 input/output/total，未传 `cachedInputTokens`。
   因而日志里的 cached=0 不能证明服务端缓存未命中。Flash 的 `FlashRunner`
   trace 也未单独记录每轮 duration；应先在公共 provider/Flash 入口补齐指标，
   再评估复用 thread 或优化稳定前缀。不同角色直接共享 thread 有上下文串扰风险。

统计模型调用时只数 `traces` 表中的 `name='llm_usage'`，不要把同一调用的
`CodexAppServerChatModel`/`FlashRunner` trace 再算一次。Pro 的 30 次分布为：
Planner 3、Operator 11、Checker 11、视觉摘要 3、Outputter 2；Flash 是主循环 3、
视觉摘要 2。
