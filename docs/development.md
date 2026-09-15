# 本地开发

本文给出当前仓库可直接执行的开发流程。接口字段与业务错误见 [HTTP API 业务语义](api.md)，服务边界见 [系统架构](architecture.md)，当前产品边界以[阅读工作区设计规格](superpowers/specs/2026-08-21-reading-workspace-annotations-model-profiles-design.md)为准；贡献规则仍以[根目录贡献指南](../CONTRIBUTING.md)为准。

## 前置条件与安装

- 后端需要 Python 3.12 或更高版本；前端使用 Node.js `^22.14.0 || >=24.0.0`。
- 模型服务不是安装、单元测试、集成测试或 Vitest 的前置条件。需要联调时再在模型档案中配置，或使用只读环境回退。

Windows PowerShell：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
Set-Location web
npm ci
```

POSIX shell：

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e '.[dev]'
cd web
npm ci
```

`npm ci` 必须在 `web/` 目录运行，并严格采用提交的 `package-lock.json`。若锁文件与 `package.json` 已不一致，使用 `npm install` 让 npm 重新计算并审查生成的差异；**绝不手工编辑** `package-lock.json`。

## 两个开发进程

在仓库根目录启动后端，在第二个终端的 `web/` 启动前端。Vite 只代理 `/api` 至后端。

Windows PowerShell：

```powershell
# 终端一：仓库根目录
.\.venv\Scripts\uvicorn.exe paper_agent.app:create_app --factory --host 127.0.0.1 --port 8000 --reload

# 终端二：仓库根目录
Set-Location web
npm run dev
```

POSIX shell：

```bash
# 终端一：仓库根目录
.venv/bin/uvicorn paper_agent.app:create_app --factory --host 127.0.0.1 --port 8000 --reload

# 终端二：仓库根目录
cd web
npm run dev
```

默认访问地址是后端 `http://127.0.0.1:8000` 与 Vite 输出的本地地址（默认通常为 `http://127.0.0.1:5173`）。若端口已占用，先停止已有本地开发进程，或在后端命令改用另一个端口，并把 `web/vite.config.ts` 的 `/api` 代理同步改为同一地址；不要让前端悄悄指向旧服务。

## 数据目录与迁移

默认 `create_app()` 以当前工作目录下的 `.paper-agent/` 作为数据目录，其中包含 SQLite、`papers/`、`.trash/` 与秘密文件。不要提交该目录、PDF、SQLite 文件或秘密文件。

当前 CLI 没有 `PAPER_AGENT_DATA_DIR` 环境变量。需要覆写数据目录时，调用公开的 `get_settings(data_dir=...)` API 并把它传给 `create_app()`：

Windows PowerShell：

```powershell
.\.venv\Scripts\python.exe -c "from pathlib import Path; import uvicorn; from paper_agent.app import create_app; from paper_agent.config import get_settings; uvicorn.run(create_app(get_settings(data_dir=Path('.paper-agent-dev'))), host='127.0.0.1', port=8000)"
```

POSIX shell：

```bash
.venv/bin/python -c "from pathlib import Path; import uvicorn; from paper_agent.app import create_app; from paper_agent.config import get_settings; uvicorn.run(create_app(get_settings(data_dir=Path('.paper-agent-dev'))), host='127.0.0.1', port=8000)"
```

这两个覆写命令直接传入应用实例，故不使用 `--factory --reload`。如需热重载，保持默认 factory 命令，或在本地创建未提交的启动包装器。

数据库迁移没有独立脚本：`PaperRepository` 与 `ModelProfileRepository` 初始化时都会调用 `initialize_database(database_url)`，后者依次 `metadata.create_all()`、兼容旧表，再执行 `run_schema_migrations(engine)`。对一个指定数据库执行同样的当前 API：

```powershell
New-Item -ItemType Directory -Path .paper-agent-dev -Force | Out-Null
.\.venv\Scripts\python.exe -c "from paper_agent.database import initialize_database; initialize_database('sqlite:///./.paper-agent-dev/paper-agent.db')"
```

```bash
mkdir -p .paper-agent-dev
.venv/bin/python -c "from paper_agent.database import initialize_database; initialize_database('sqlite:///./.paper-agent-dev/paper-agent.db')"
```

迁移是递增且幂等的；不要改写已发布版本。表与删除关系见 [数据模型与删除恢复](data-model.md)。

## 模型联调与可测试注入

若没有启用的数据库档案，可设置三项环境变量形成只读回退档案；它只用于本地兼容调用，不能编辑或删除：

```powershell
$env:PAPER_AGENT_REASONING_BASE_URL = "http://127.0.0.1:8001/v1"
$env:PAPER_AGENT_REASONING_MODEL = "your-served-model"
$env:PAPER_AGENT_REASONING_API_KEY = "EMPTY"
```

请求仍显式携带 `model_profile_id`；模型选择、能力门禁、无密钥快照和 usage lease 见 [模型服务与模型档案](model-services.md)。不要把真实密钥写进脚本、示例、SQLite 或日志。

测试不连接真实模型。现有集成测试通过替换 `app.state.reasoning_client_provider` / `app.state.model_profile_service`，或 monkeypatch `ReasoningClientProvider` 创建的 `VllmChatClient`、`VllmToolCallingClient`，注入确定性的 fake client。例如 `tests/integration/test_selection_assists_api.py` 替换 chat client，`tests/integration/test_agent_api.py` 替换请求级 provider 与 tool client。fake 必须返回与测试场景相符的普通文本、严格 JSON 或工具回合，不能用网络 mock 掩盖协议问题。

vLLM 联调失败时先在模型档案的测试操作中查看三项能力：basic chat、structured output、tool calling。前两项失败通常说明服务地址、模型名、鉴权或 JSON schema 支持不匹配；仅工具调用失败时，检查服务端是否为该模型启用了适配的 chat template 与 tool-call parser，并确认其 OpenAI-compatible 返回中含可解析的 tool calls。项目客户端要求：Agent 使用工具调用（请求里带 `parallel_tool_calls=False`，但 provider 可能忽略它，运行时按批处理并截断到预算上限）；能力探测里的结构化输出检查使用严格 `json_schema`。能力未满足时不要绕过门禁硬接主 Agent。

## 文档与变更入口

- [系统架构](architecture.md)：`app.state`、服务边界、关键时序和并发限制。
- [HTTP API 业务语义](api.md)：32 个当前操作、schema 索引、稳定错误与幂等。
- [测试策略](testing.md)：目标测试、Vitest、确定性 Playwright 与手工模型检查。
- [模型服务与模型档案](model-services.md)：请求作用域模型、密钥和能力。
- [数据模型与删除恢复](data-model.md)：持久化、迁移和永久删除。
- [文档导航](README.md)：新开发者阅读顺序、规格与计划历史。
