# Codex 客户端 Provider（零 API Key）

> 架构分层、已执行的拆分内容和独立仓库路线见
> [Codex Provider 分层与独立仓库计划](./codex-provider-extraction-plan.md)。

通用适配层已经独立发布：
[GitHub 中文说明](https://github.com/HJunLong601/codex-client-provider/blob/main/README_CN.md) ·
[PyPI 0.1.0](https://pypi.org/project/codex-client-provider/0.1.0/)。Artemis 固定依赖
`codex-client-provider==0.1.0`，仓库内保留 Artemis 身份、路由、模型图片预处理和诊断接入层。

Artemis 可以复用本机 Codex 客户端的 ChatGPT 登录态。该模式不读取
`~/.codex/auth.json`，不复制 access token，也不要求 `OPENAI_API_KEY`；它启动
官方 `codex app-server` 子进程，通过 JSONL/stdio 调用当前账号可用的模型。

## 已落地的调用链

1. `ModelProvider.CODEX` 把 Artemis 的模型角色路由到
   `CodexAppServerChatModel`。
2. 适配器执行 App Server 的 `initialize → thread/start → turn/start` 协议。
3. 每次调用使用临时 thread、`read-only` sandbox 和 `never` approval，Codex
   不直接操作设备或工作区。
4. Artemis 工具 schema 通过受约束的 JSON 输出交给 Codex。返回值被还原成
   LangChain `AIMessage.tool_calls`，工具仍由 Artemis 的执行器调用。
5. 文本、截图和 Pydantic/JSON 结构化输出使用同一条通道。
6. 后台步骤摘要、历史 capsule 压缩、轻量校验节点也按 provider 路由，不再
   回退到写死的 Gemini 客户端。

协议选择依据是 OpenAI 的 [Codex App Server 文档](https://developers.openai.com/codex/app-server)。
登录方式和 API Key 的区别见 [Codex Authentication](https://developers.openai.com/codex/auth)。

## 本机配置

`start.sh`、`start.bat` 和 `scripts/install_deps.*` 会在缺失时通过 OpenAI 官方
standalone installer 安装 Codex CLI。安装完成后登录并确认状态：

```powershell
codex login status
```

浏览器回调无法使用时，可执行 `codex login --device-auth`。

预期输出包含 `Logged in using ChatGPT`。默认配置已经使用：

```jsonc
{
  "default": {
    "provider": "codex",
    "model": "gpt-6.1-sol",
    "reasoning_effort": "medium",
    "fallback": {
      "provider": "codex",
      "model": "gpt-6-luna",
      "reasoning_effort": "low"
    }
  }
}
```

模型名必须存在于当前 Codex 客户端返回的模型目录中。客户端升级或账号权限变化
后，可以通过 App Server 的 `model/list` 查看实时目录。若 `codex` 不在 `PATH`，
设置 `ARTEMIS_CODEX_BIN` 为可执行文件的绝对路径。

主模型使用 6.1 Sol，轻量摘要、历史压缩和校验节点保留 6 Luna 档位。
使用能在 `model/list` 中列出 `gpt-6.1-sol` 的新版 Codex 客户端；旧客户端可能拒绝该模型。

### 截图尺寸与压缩

默认策略位于 [`artemis/llm/image_inputs.py`](../artemis/llm/image_inputs.py)，由
[`CodexAppServerChatModel`](../artemis/llm/codex_app_server.py) 在通用适配器之前调用。
本地文件和 data URL 图片等比缩放到短边最多 720、长边最多 1600 像素，再以 WebP
有损质量 70/100（`lossless=False`、`method=4`）编码，文件最多 768 KiB。不放大小图，
竖屏 1080×2400 变为 720×1600，横屏 1920×1080 变为 1280×720。超过字节预算时继续
缩小尺寸，保留质量参数。已满足限制、无附加元数据的普通有损 WebP 保持字节不变，
避免重复编码；PNG/JPEG 即使未超限也转换为 WebP。EXIF 方向先校正，透明底合成为白色，
编码时去除元数据。远程 URL 不在本地下载或转换。

通用适配器负责将结果写为临时 `.webp`，以 App Server `localImage` 发送，调用结束后
删除临时文件。原始截图、历史消息及设备坐标尺寸保持不变。图片转换独立于遥测开关。
可以通过环境变量调整：

```powershell
$env:ARTEMIS_CODEX_IMAGE_SHORT_EDGE = "720"
$env:ARTEMIS_CODEX_IMAGE_MAX_EDGE = "1600"
$env:ARTEMIS_CODEX_IMAGE_MAX_BYTES = "786432"
$env:ARTEMIS_CODEX_IMAGE_WEBP_QUALITY = "70"
```

通用 Provider 的 `CODEX_CLIENT_IMAGE_MAX_EDGE` / `CODEX_CLIENT_IMAGE_MAX_BYTES` 优先于
对应的 `ARTEMIS_` 限制，避免底层重新转为 JPEG。无效值使用默认值；长边最小 320、
字节预算最小 64 KiB，质量范围 0–100。旧 `ARTEMIS_CODEX_IMAGE_JPEG_QUALITY` 只影响
关闭 Artemis 预处理后的通用 Provider JPEG 路径。

设置 `ARTEMIS_CODEX_IMAGE_PREPROCESSING=0` 并重启，或构造
`CodexAppServerChatModel(..., image_preprocessing_enabled=False)` 可恢复通用图片策略，
也用于格式/原尺寸基准对照。脱敏实测和测量限制见 [中文 README](../README_CN.md#图片输入基准测试2026-10-10)。

## 哪些功能还可能需要 Key

| 功能 | 默认是否需要 Key | 说明 |
|---|---:|---|
| Planner、Operator、Checker、Explorer、Outputter | 否 | 使用 Codex 客户端登录态 |
| 截图理解、通用视频关键帧分析 | 否 | 图片发送给 Codex；视频走关键帧通用引擎 |
| 步骤摘要、历史压缩、轻量校验 | 否 | 使用 `gpt-6-luna` |
| Google Cloud Vision OCR | 可选 | 只有启用云 OCR 时需要 `OCR_API_KEY` |
| Gemini 原生 Files API 视频流 | 可选 | 仅切换到 Google provider 时需要 Google Key |
| OpenAI/Anthropic/OpenRouter/xAI provider | 可选 | 只有主动切换对应 provider 时需要其 API Key |

## 运行与诊断

```powershell
.venv\Scripts\artemis.exe doctor
.venv\Scripts\artemis.exe restart --port 8001
```

全部主模型使用 Codex 时，诊断页应显示 `Active (Codex client)`。如果显示未登录，
先比较普通用户终端与 MCP 服务环境中的 `codex login status`，检查 `CODEX_HOME`、
CLI 路径及沙箱访问权限。普通用户环境也未登录时再执行 `codex login`，随后重启 MCP
服务。不要把登录令牌复制到沙箱或聊天中。如果模型不可用，将 `config/artemis.jsonc`
中的模型改为当前客户端目录内的模型。

凭据诊断与 SDK 使用相同的主模型列表，覆盖 Planner、Operator、Checker、Explorer、
视频分析和工具节点。混用 Provider 时，每个主模型所需的凭据都必须可用；无关的
Google Key 不能替代 OpenAI Key 或 Codex 登录态。仅作为 fallback 或未被主模型使用
的凭据不阻断就绪检查。

`mobile_diagnose(verify_credentials=true)` 会在线验证已配置的 API Key；未使用的
Key 验证失败标为可选问题。Codex 只检查 CLI 登录状态，Vertex AI 检查本地 ADC 和
项目配置，这两项不代表模型推理或远程访问已经成功。真机任务仍需单独验证。

需要确认模型实际可调用时，使用
`mobile_diagnose(verify_models=true)`：检查当前客户端模型目录、各节点推理强度，
再对每个不同的模型发送一个固定 READY 请求，不上传设备内容。`models.checks`
包含节点、主/备用角色、推理结果和秒数；同一模型复用一次推理结果。只使用目录支持
的 `low` 强度做连通性探测，配置中其他强度仅校验目录声明。主模型失败阻断就绪，
仅备用模型失败标为可选问题。目前该检查覆盖 Codex；其他 Provider 使用凭据诊断。

每次 SDK 任务开始前自动执行轻量目录预检，不额外调用推理模型。同一进程内目录缓存
60 秒；显式 `verify_models=true` 强制刷新。预检失败发生在设备驱动连接/应用操作
之前。客户端没有列出的模型将直接报错，Codex 包装的 `invalid_request_error` 也会
按不可重试错误处理，避免因不支持的模型反复等待或无限暂停。

LLM 暂停时，`mobile_manage_task(action="status", ...)` 返回 `status="paused"`
及原因、发生时间、恢复提示。暂停记录按会话保存；终态不会被旧暂停记录覆盖。

## 边界与取舍

缓存遥测现由 Artemis 对锁定的 `codex-client-provider==0.1.0` 做局部响应适配，
保持原有进程池、图片文件落盘、请求/工具契约和错误传播；WebP 策略在调用前单独处理。不修改 site-packages，不对
公共模块做全局替换。将原始 `usage.last.cachedInputTokens` 映射到 LangChain 的
`usage_metadata.input_token_details.cache_read`，并记录 `inference_seconds`。
`cache_usage_available=false` 表示未知，不应当解释为零命中。无效计数被忽略，
不会重发已经完成的推理请求。该桥接依赖锁定版本的私有转换助手，升级 provider
时必须跑契约兼容测试；上游公开扩展入口后应移除此桥接。

设置 `ARTEMIS_CODEX_TELEMETRY=0` 并重启服务可恢复原 provider 响应实现；仅缺少缓存/
耗时扩展统计，不改变图片预处理、当前模型或工具权限。也可单独构造
`CodexAppServerChatModel(..., telemetry_enabled=False)`。计时覆盖 provider 请求
（包括启动/图片转换）；Flash trace 的 `duration` 覆盖整个网关调用，包括重试等待，
与单次 provider 的 `provider_inference_seconds` 区分。

- 这是本地客户端集成，消耗当前 ChatGPT/Codex 账号的使用额度，而不是 API 余额。
- App Server 协议随 Codex 客户端发布；升级客户端后应重新跑适配器集成测试。
- Codex 通用视觉模型没有 Gemini Robotics ER 的专用坐标能力。默认保留较快的
  `flash` 单次视觉路径；复杂页面可以把 Explorer 切到 `pro` 多轮路径。元素索引和
  UI XML 仍是首选定位方式。
- 每个 LangChain 调用建立临时 Codex thread，避免不同 Agent 角色之间混入上下文；
  App Server 进程本身会在 Artemis 进程内复用。
