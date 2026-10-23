# Claude Code 本机模型接入

状态：实现分支 `codex/claude-code-provider`。默认模型仍为 Codex；`anthropic` API Provider 保持独立。真实 Claude 账号推理和 Claude 驱动真机验收尚未完成，不应把模拟测试或未登录诊断作为实际可用证明。

本次构建、测试与验收边界见[验证记录](./claude-code-validation.md)。

## 安装和认证

安装原版 [Claude Code CLI](https://code.claude.com/docs/en/setup)，然后在运行 Artemis 的同一操作系统用户与环境中执行：

```bash
claude --version
claude auth login
claude auth status
```

项目依赖脚本支持显式选装，不会默认安装 Claude Code：

```bash
bash scripts/install_deps.sh --with-claude-code
```

Windows PowerShell：`./scripts/install_deps.ps1 -WithClaudeCode`。Windows 需满足官方 CLI 的系统依赖，安装脚本本身不代办登录。

Artemis 发现顺序：显式程序路径 / `ARTEMIS_CLAUDE_CODE_BIN` → PATH 中的 `claude` 或 `claude.exe` → 用户 `.local/bin`。显式无效路径不会静默换成其他安装。修改进程环境后重启 MCP / 后台。普通认证检查不调用模型，不采集邮箱、账号 ID 或令牌。

不得将当前 Claude 聊天或桌面会话视为后台认证。后台运行用户、PATH、`CLAUDE_CONFIG_DIR` 和凭据库访问权限必须一致；保留官方 API Key 和云平台认证方式，不复制令牌解决环境隔离问题。

## 模型配置

后台 **AI Model Setup → 模型客户端** 提供 Codex / Claude Code 显式开关。两套模型配置独立保存，未选中的客户端不进入任务认证和模型预检，也不会成为选中客户端的备用。切换不登录、不退出账号、不修改另一套配置，不影响正在执行的任务；新启动的工作进程读取当前选择。认证失败时显示错误，不自动切换到另一个客户端。

配置保存在有效 `artemis.jsonc` 的 `native_clients.codex` 和 `native_clients.claude_code` 下，由 `native_clients.active` 选择。首次切换会保留已有的默认/节点配置作为对应客户端的独立配置；原顶层 `default`、`nodes` 和其他设置不被替换。没有 `native_clients.active` 的旧文件继续按原有路由工作。启用独立配置后，主节点和备用节点必须属于选中的同一 Provider，不允许跨 Codex / Claude Code 回退。

Claude Code 面板只保存自己的模型设置；`artemis init` 也提供两者的独立选择。每次写入会在原配置同目录保存权限受限的原文件备份。写入会重排 JSON、去除新文件中的注释，原注释在备份中保留；旧版非统一配置和只读模板需要手工迁移。备份可能含私有端点，不能提交或分享。

也可以手工编辑有效 `artemis.jsonc`。本地源码的 [`config/artemis.jsonc`](../config/artemis.jsonc) 已包含两套独立配置，默认 `active: "codex"`；打包模板不等于运行时配置。源码与全局安装可能读取不同文件。在源码环境中执行以下只读命令确认路径：

```bash
uv run python -c 'from artemis.config.paths import get_config_path; print(get_config_path("artemis.jsonc"))'
```

全局安装需使用安装 Artemis 的 Python 环境执行相同的 `-c` 表达式，并保持与后台/MCP 相同的环境变量，而不是在当前仓库运行 `uv run` 来推断全局配置。

`ARTEMIS_ARTEMIS_JSONC` 可指定另一份已存在的文件，必须传给实际启动后台/MCP 的进程。文件发现顺序为显式环境变量、源码 `config/`、项目根目录、用户配置/应用目录、最后打包模板；不能假设全局安装使用当前源码文件。

以下是双客户端模型配置示例。只修改 `native_clients.active` 即可切换；合并到现有文件时保留各客户端已有的 `nodes` 和其他运行设置，不要用本示例整份覆盖：

```json
{
  "native_clients": {
    "active": "codex",
    "codex": {
      "default": {
        "provider": "codex",
        "model": "gpt-6.1-sol",
        "reasoning_effort": "low",
        "timeout": 180,
        "fallback": { "provider": "codex", "model": "gpt-6-luna", "reasoning_effort": "low" }
      },
      "nodes": {}
    },
    "claude_code": {
      "default": {
        "provider": "claude_code",
        "model": "sonnet",
        "reasoning_effort": "low",
        "timeout": 180,
        "fallback": { "provider": "claude_code", "model": "haiku", "reasoning_effort": "low" }
      },
      "nodes": {}
    }
  }
}
```

修改 Codex 模型请编辑 `native_clients.codex.default/nodes`，修改 Claude 模型请编辑 `native_clients.claude_code.default/nodes`；顶层旧 `default/nodes` 在启用独立配置后不参与模型路由。每套 `nodes` 只继承自己的 `default`，不会继承顶层旧 `nodes` 或另一套配置。`fallback` 仅用于同一客户端的模型回退，例如 Sonnet → Haiku，而不是 Claude Code → Codex。

本地配置的 `agent.flash.step_summarizer` 使用 `"provider": null` 和 `"model": ""`，让 Flash/Pro 共用的后台视觉摘要跟随当前 `summarizer` 角色。若手工写死 Codex 或 Claude 模型，切换主配置不会自动覆盖这些显式值。当前默认摘要继承所选客户端主模型；如需轻量摘要，在两套配置各自的 `nodes.summarizer` 中分别设置，例如 Codex 的 `{"model": "gpt-6-luna"}`、Claude Code 的 `{"model": "haiku"}`，勿交叉混用。

手工修改后运行 `uv run artemis restart`（全局安装使用 `artemis restart`）重启对应后台，并在 IDE 中重载 MCP 服务；已有任务不迁移到新客户端。后台开关保存的选择用于随后启动的工作进程，不会替当前运行任务重新认证。

模型名称需与自己的账号权限一致；不能把可用 CLI/认证当成模型授权。支持 `low/medium/high/xhigh/max`（具体模型是否支持及组织上限仍由官方 CLI 决定），不把不支持的 `ultra/minimal` 静默降级。CLI 不提供 Artemis 可直接设置的 temperature/max_tokens，需使用 API Provider 时请选 `anthropic`。

## MCP、后台和任务执行

- MCP 仍启动 `artemis mcp`，工具名称和任务参数不变；模型由同一份配置选择。
- 后台 Run Task、CLI、SDK 和 MCP 共用模型工厂、主路由凭据检查和任务预检，不新增另一条设备执行路径。
- `mobile_diagnose` 只做普通诊断；显式 `verify_models=true` 或后台“验证实际推理”才发送最小 READY 请求，可能消耗额度，不发送手机内容。
- 后台接口：`GET /api/system/model-clients`、显式 `POST .../select`（`provider` 为 `codex` 或 `claude_code`）；Claude 专用 `GET /api/system/claude-code/auth`、显式 `POST .../verify`、显式 `POST .../configure`。仅允许本机同源访问；POST 还要求操作意图标记。认证检查失败、未安装、未登录和 CLI 不兼容分开呈现。
- 认证正常后，再从 Run Task 执行无破坏的手机任务。Flash/Pro、Explorer、视觉缓存、历史压缩、录屏、报告和重试/备用模型继续使用现有机制；是否正确复用缓存须看实际日志和命中统计，不能仅凭模型有回复推断。

## 执行与隐私边界

使用未修改的 `claude -p`、NDJSON 图片输入和 JSON Schema 结果。内置工具关闭，MCP 配置为空且禁止嵌套 MCP 工具，启用 safe mode 隔离用户插件、项目指令和自定义钩子；组织强制策略不绕过。系统提示通过私有临时文件传入，成功、错误、超时和取消均清理；截图以协议消息传输，不另存模型输入图片。关闭 CLI 会话持久化。官方 CLI 自身的认证存储、遥测及组织策略仍由官方和用户设置管理。

每次模型调用为独立 CLI 进程/会话，由 Artemis 传入已有历史，避免角色和设备之间共享会话。首版不承诺等同 Codex App Server 进程池的延迟；真实延迟、长历史和 prompt-cache 命中须用真实账号测量。这里只汇报 CLI 返回的缓存计数，不伪造缓存命中；视觉定位缓存与供应商 prompt cache 是两套机制。

图片复用现有等比缩放、去元数据和 WebP 策略；可通过 `ARTEMIS_CLAUDE_CODE_IMAGE_SHORT_EDGE/MAX_EDGE/MAX_BYTES/WEBP_QUALITY` 分别设置，默认值与 Codex 相同。不会继承 Codex 专属图片环境变量；不主动下载远程 URL 图片。

## 条款和验收

产品集成必须遵守 [官方条件](https://code.claude.com/docs/en/legal-and-compliance)：运行原版程序、每个用户自行认证、保留官方认证方式，不中转或转售用户额度。这里不是 Claude API 兼容代理，也不提供自己的 Claude OAuth 登录。商业分发前仍应核对适用条款。

无账号可验证范围：受控子进程协议、图片/工具响应转换、认证失败、额度错误、超时取消与临时文件清理、配置备份、后台接口权限、前端状态以及官方 CLI 未登录诊断。

真实账号验收清单：最小推理成功；截图理解；主/备用模型切换；Flash/Pro 从 Run Task 和 MCP 完成手机操作；Explorer 视觉缓存复用；长任务与并发隔离；停止任务和录屏清理；登录过期、限流及实际用量统计。这些项目尚不能以无账号测试替代。
