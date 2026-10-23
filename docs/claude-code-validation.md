# Claude Code 接入验证记录

日期：2026-10-23。分支：`codex/claude-code-provider`。没有 Claude 账号，因此以下记录严格区分软件测试、原版 CLI 的未登录验证与真实模型/设备验收。

## 已验证

- 后端、MCP、凭据诊断、模型预检、Flash 及相关回归：570 passed，3 skipped。没有改动原有两份未跟踪本地测试文件。
- 前端：160 passed；Angular 生产构建通过。
- 真实受控子进程测试覆盖原生 NDJSON 输入、结构化结果、图像、工具参数校验、主机网关与 Flash 绑定、认证/限流/异常输出、并发隔离、超时取消、进程退出和私有临时文件清理。
- SDK 共用任务执行器在未登录时，于设备锁获取和设备连接/动作之前失败。测试使用受控 CLI，不代表真实 Claude 推理。
- 选中 Claude Code 时，不检查或调用未选中的 Codex。认证失败不会切换到 Codex；独立配置禁止跨客户端的主节点或 fallback。
- 配置切换保留另一套配置及顶层非模型设置；写入保存原文件精确备份，失败保留原文件；配置和切换接口有本机、同源和显式操作保护。
- 原版 Claude Code 2.1.309 在临时空认证目录中报告 `not_logged_in`。使用实际调用参数发起最小请求，返回脱敏的认证错误 401，没有手机动作，也没有将认证失败误报为推理成功。
- 隔离后台 8016 页面实际完成 Codex → Claude Code 切换；对应认证面板互斥展示，配置保留；未登录 Claude Code 的实际推理按钮禁用。补齐切换时旧 Codex 认证结果不得复用的前端测试。
- 本地 wheel 构建通过；确认新 Provider、配置选择、后台接口和新版前端开关均已进入 wheel。没有发布或安装到全局。
- Ruff、`git diff --check`、Bash 安装脚本语法检查通过。uv 锁文件已更新并检查，新增 jsonschema 直接依赖。
- 变更敏感模式扫描未发现个人路径、真实设备编号、内部域名、私钥或真实令牌。配置备份和临时文件已加入忽略规则。

### 本地双配置补充

本地 `config/artemis.jsonc` 已加入 `native_clients`，默认选中 Codex。补充检查确认：Codex 的 20 个主模型路由展开结果与添加前一致；内存中切换到 Claude Code 后，20 个主模型及其 fallback 均属于 Claude Code。后台视觉摘要通过空模型名和空 Provider 跟随所选 `summarizer` 角色，默认由原先固定 Luna 改为继承所选主模型；其他运行配置保留。相关配置及预检测试 21 passed。这是配置解析与软件测试，不代表真实 Claude 推理或真机验收。

## 尚未验证，不作为已通过结果

- 真实 Claude 账号的推理权限、图片理解、工具选择、Claude 驱动的 Run Task / MCP 真机闭环。
- 真实账号下的限流恢复、长任务、性能和供应商 prompt-cache 命中；视觉缓存复用仍需实际设备日志证明。
- Windows / Linux 原生运行与 PowerShell 安装脚本的实际执行；本次软件测试环境为 macOS。

图谱分析识别到共享模型路由和认证入口的高影响范围，因此执行了原有 Codex 与新客户端的共同回归。图谱静态结果不替代上述真实账号/真机验收。

## 主要复现命令

```bash
.venv/bin/python -m pytest tests/unit/admin_console tests/unit/mcp tests/unit/core/test_provider_credentials.py tests/unit/test_claude_code_client.py tests/unit/test_claude_code_model.py tests/unit/test_claude_code_preflight.py tests/unit/test_claude_code_setup.py tests/unit/test_model_preflight.py tests/unit/test_codex_image_inputs.py tests/unit/agents/test_flash_runner.py --no-cov -q
npm --prefix apps/showcase_ui run build
npm --prefix apps/showcase_ui test -- --watch=false --browsers=ChromeHeadless --reporters=dots
bash -n scripts/install_deps.sh
uv lock --check
uv build --wheel
```

前端命令需要满足项目要求的 Node.js 版本和本机 Chrome。测试生成的配置和后台数据应放在独立临时目录，不应切换日常使用中的模型配置来模拟验收。
