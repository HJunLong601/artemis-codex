# Codex 客户端 Provider（零 API Key）

> 架构分层、已执行的拆分内容和独立仓库路线见
> [Codex Provider 分层与独立仓库计划](./codex-provider-extraction-plan.md)。

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
    "model": "gpt-5.6-terra",
    "reasoning_effort": "medium",
    "fallback": {
      "provider": "codex",
      "model": "gpt-5.6-luna",
      "reasoning_effort": "low"
    }
  }
}
```

模型名必须存在于当前 Codex 客户端返回的模型目录中。客户端升级或账号权限变化
后，可以通过 App Server 的 `model/list` 查看实时目录。若 `codex` 不在 `PATH`，
设置 `ARTEMIS_CODEX_BIN` 为可执行文件的绝对路径。

### 截图尺寸与压缩

发送给 Codex 的本地截图默认限制最长边为 1600 像素、文件大小为 768 KiB。未超过
限制的图片保持原始字节；超过任一限制时，适配器会等比缩放并逐步降低 JPEG 质量，
再通过 App Server 的 `localImage` 输入发送。可以通过环境变量调整：

```powershell
$env:ARTEMIS_CODEX_IMAGE_MAX_EDGE = "1600"
$env:ARTEMIS_CODEX_IMAGE_MAX_BYTES = "786432"
$env:ARTEMIS_CODEX_IMAGE_JPEG_QUALITY = "82"
```

## 哪些功能还可能需要 Key

| 功能 | 默认是否需要 Key | 说明 |
|---|---:|---|
| Planner、Operator、Checker、Explorer、Outputter | 否 | 使用 Codex 客户端登录态 |
| 截图理解、通用视频关键帧分析 | 否 | 图片发送给 Codex；视频走关键帧通用引擎 |
| 步骤摘要、历史压缩、轻量校验 | 否 | 使用 `gpt-5.6-luna` |
| Google Cloud Vision OCR | 可选 | 只有启用云 OCR 时需要 `OCR_API_KEY` |
| Gemini 原生 Files API 视频流 | 可选 | 仅切换到 Google provider 时需要 Google Key |
| OpenAI/Anthropic/OpenRouter/xAI provider | 可选 | 只有主动切换对应 provider 时需要其 API Key |

## 运行与诊断

```powershell
.venv\Scripts\artemis.exe doctor
.venv\Scripts\artemis.exe restart --port 8001
```

诊断页应显示 `Active (Codex client)`。如果显示未登录，执行 `codex login`；如果
模型不可用，将 `config/artemis.jsonc` 中的模型改为当前客户端目录内的模型。

## 边界与取舍

- 这是本地客户端集成，消耗当前 ChatGPT/Codex 账号的使用额度，而不是 API 余额。
- App Server 协议随 Codex 客户端发布；升级客户端后应重新跑适配器集成测试。
- Codex 通用视觉模型没有 Gemini Robotics ER 的专用坐标能力。默认保留较快的
  `flash` 单次视觉路径；复杂页面可以把 Explorer 切到 `pro` 多轮路径。元素索引和
  UI XML 仍是首选定位方式。
- 每个 LangChain 调用建立临时 Codex thread，避免不同 Agent 角色之间混入上下文；
  App Server 进程本身会在 Artemis 进程内复用。
