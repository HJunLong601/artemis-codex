# Codex Provider 分层与独立仓库计划

本文档定义零 API Key Codex 集成的代码边界。目标是让 Artemis 继续稳定使用
Codex，同时让协议实现能够在不依赖 Artemis 的情况下单独测试、发布和迁移。

## 一、通用适配层

目录：`packages/codex-client-provider`

职责：

1. 查找 Codex CLI 并检查由 Codex 管理的登录状态。
2. 启动和复用 `codex app-server` JSONL/stdio 子进程。
3. 实现 `initialize`、`thread/start`、`turn/start` 和事件归并。
4. 提供 LangChain `BaseChatModel`，支持文本、图片、工具调用和结构化输出。
5. 对超大图片进行等比缩放和 JPEG 压缩，并清理临时文件。
6. 提供超时、并发请求、进程退出和错误转换。
7. 暴露客户端名称、版本和 service name，让宿主在协议元数据中表明身份。

明确不负责：

- Artemis 的 Agent、设备工具、提示词和执行策略；
- Artemis 配置文件、Web 控制台、诊断页和安装向导；
- 读取、复制或保存 Codex 登录 Token；
- 模拟完整 OpenAI REST API。

公共 API：

```python
from codex_client_provider import (
    CodexAppServerChatModel,
    CodexAppServerClient,
    codex_client_status,
    find_codex_binary,
)
```

通用环境变量：

- `CODEX_CLIENT_BIN`
- `CODEX_CLIENT_IMAGE_MAX_EDGE`
- `CODEX_CLIENT_IMAGE_MAX_BYTES`
- `CODEX_CLIENT_IMAGE_JPEG_QUALITY`

迁移期间仍兼容现有 `ARTEMIS_CODEX_*` 名称，独立仓库稳定后再按弃用周期移除。

## 二、Artemis 接入层

入口：`artemis/llm/codex_app_server.py`

职责：

1. 保留原有导入路径，避免已有调用方失效。
2. 用子类注入 `artemis` 客户端名称、标题、版本和 service name。
3. 在 `ModelProvider.CODEX` 中注册通用 LangChain Provider。
4. 校验 Codex 安装和登录状态。
5. 把状态接入 `artemis init`、`artemis doctor` 和 Web 控制台。
6. 维护 Artemis 的模型选择、工具强制调用、Agent 节点和回退策略。
7. 维护启动脚本中的 Codex 安装以及 Windows/macOS/Linux 前置依赖。

Artemis 层不得重新实现 App Server JSON-RPC、图片压缩或 LangChain 消息转换。

## 三、本次执行内容

- [x] 建立独立 workspace package 和发布元数据。
- [x] 将 App Server 与 LangChain 逻辑移出 `artemis` 包。
- [x] 使用标准 Python 日志，移除通用包对 Artemis 的导入。
- [x] 将工具约束提示词改为宿主无关表述。
- [x] 增加通用客户端身份字段，Artemis 通过薄封装注入自身身份。
- [x] 将通用测试移入 package，并保留 Artemis 路由集成测试。
- [x] 保留旧环境变量和旧 Python 导入路径兼容性。
- [ ] 发布前确定 PyPI 包名和版本策略。
- [ ] 发布前建立独立 CI 矩阵和协议兼容性测试。

## 四、抽成独立仓库的后续步骤

1. 将 `packages/codex-client-provider` 原样迁入新仓库根目录。
2. 配置 Windows、macOS、Linux，Python 3.10-3.13 测试矩阵。
3. 使用当前 Codex CLI 生成的 JSON Schema 增加协议契约测试。
4. 增加可选的真实 App Server smoke test，覆盖登录状态、文本、图片、工具调用和超时。
5. 发布首个版本，并将 Artemis workspace 依赖切换为固定版本依赖。
6. 保留一个发布周期的 workspace 回退方式，确认安装脚本和锁文件稳定。
7. 独立仓库稳定后，再评估 OpenAI Chat/Responses 兼容本地网关。

## 五、验收标准

- 通用包源码中不存在 `import artemis`。
- 通用包测试可以在不导入 Artemis 的情况下通过。
- Artemis 的 `ModelProvider.CODEX` 仍返回带 Artemis 身份的模型。
- 原有 `artemis.llm.codex_app_server` 导入保持兼容。
- 文本、工具调用、结构化输出、小图透传、大图压缩和临时文件清理测试全部通过。
- `uv lock --check`、相关单元测试和 Ruff 检查通过。

## 六、本次验证结果

- 通用 Provider 与 Artemis 身份透传测试：`8 passed`。
- 配置、CLI、SDK、凭据和管理控制台相关回归测试：`95 passed`。
- Ruff lint、Ruff format、Pyright、`uv lock --check` 和 `git diff --check`：通过。
- 独立构建：成功生成 `codex_client_provider-0.1.0.tar.gz` 和
  `codex_client_provider-0.1.0-py3-none-any.whl`。
- 真实登录检测：通用层和 Artemis 层均返回 `Logged in using ChatGPT`。
- 真实 App Server 调用：返回 `READY`，响应元数据包含 provider、实际模型和 thread id。
- 全量测试运行结果：`2184 passed, 4 skipped`；另有 16 项环境相关失败，其中 12 项旧测试
  实例化 Gemini 但当前环境没有 Google Key，4 项旧测试断言端口 8000 而当前运行端口为
  8001。失败路径未经过新 Provider 包。
