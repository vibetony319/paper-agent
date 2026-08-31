# 测试策略

测试按确定性优先：后端单元/集成测试与前端 Vitest 不依赖真实模型、互联网、用户 PDF 或仓库运行数据。当前命令、开发环境和 fake 注入方式见 [本地开发](development.md)；接口语义见 [HTTP API 业务语义](api.md)。

## 覆盖层次

- **后端单元测试**：领域模型、存储、迁移、解析器、vLLM 协议适配和服务规则。重点覆盖 Citation Guard、请求幂等、模型快照、删除恢复和图谱证据校验。
- **后端集成测试**：FastAPI 路由、schema、稳定错误与跨层持久化。测试 fixture 用临时目录创建应用与生成的 PDF；模型能力通过 fake provider/client 注入。
- **前端 Vitest**：React 组件、阅读器几何、SSE 解析、客户端请求、状态 reducer 与样式契约，在 `jsdom` 中执行。
- **Playwright（计划中）**：确定性浏览器验收将在后续 Tasks 4–5 提供专用 E2E 服务器、运行时生成 fixture PDF 与脚本。现在没有 Playwright 配置，也没有 `npm run test:e2e`；不要把该命令写入 CI 或发布门禁。
- **真实模型手工检查（可选）**：只在受控本地环境测试已配置的模型档案能力与人机流程；它补充而不替代确定性测试。

## 命令矩阵

| 范围 | 工作目录 | 命令 | 外部依赖 | 预期结果 |
| --- | --- | --- | --- | --- |
| 后端单元 | 仓库根目录 | `./.venv/Scripts/python.exe -m pytest tests/unit -v` | 无网络、无真实模型 | 通过单元测试。 |
| 后端集成 | 仓库根目录 | `./.venv/Scripts/python.exe -m pytest tests/integration -v` | 无网络、无真实模型；临时 SQLite/PDF | 通过 API 与跨层测试。 |
| 后端全量 | 仓库根目录 | `./.venv/Scripts/python.exe -m pytest -v` | 同上 | 全部 pytest 通过；适合代码改动后的回归。 |
| 前端测试 | `web/` | `npm test` | 已执行 `npm ci`；无真实后端/模型 | Vitest 单次运行通过。 |
| 前端构建 | `web/` | `npm run build` | 已执行 `npm ci` | TypeScript 检查和 Vite 构建通过。 |
| 文档检查 | 仓库根目录 | `git diff --check -- docs` | 无 | 无空白错误。 |
| 浏览器 E2E（计划中） | 待后续任务创建 | **当前无可执行命令** | 将使用本地确定性 server | 不适用；不得假定 `npm run test:e2e` 存在。 |

为避免 shell 方言混淆，后端命令的可复制版本如下。

Windows PowerShell：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit -v
.\.venv\Scripts\python.exe -m pytest tests\integration -v
```

POSIX shell：

```bash
.venv/bin/python -m pytest tests/unit -v
.venv/bin/python -m pytest tests/integration -v
```

在受限 Windows 环境中，如果 pytest 无法使用系统临时目录，可使用仓库本地、已忽略的位置作为**临时 workaround**，而不是默认命令：

```powershell
.\.venv\Scripts\python.exe -m pytest -v --basetemp=.paper-agent\pytest-tmp
```

## Fake 模型与请求边界

后端测试应构造 `Settings(data_dir=tmp_path / ...)`，使数据库、PDF 与秘密目录均在测试临时目录。为使 Agent、图谱和选区辅助可重复：

- 为请求级 provider 注入或替换带固定 `ModelSnapshot` 的 fake clients；不要调用实际 vLLM。
- chat fake 为 `stream_text()` 产生固定片段；structured fake 返回符合 schema 的对象；tool fake 返回确定的 tool turn 和最终 JSON。
- 用不同的 `request_id` 覆盖新请求；用相同请求 ID 覆盖完成重放、运行冲突或失败重试；检查模型快照无密钥。
- 当验证 HTTP 行为时，使用 TestClient 与实际路由，而不是只断言私有辅助函数。

真实模型手工检查前，先通过 `POST /api/model-profiles/{profile_id}/test` 或 `POST /api/agent/health` 验证能力。只使用本地测试档案和非敏感示例；将实际模型响应视为人工观察，不写入测试断言或文档基线。

## 计划中的 Playwright 约束

后续 Playwright 必须在本地确定性环境运行，且**不得依赖**：公共互联网、真实 vLLM、浏览器登录、用户 PDF、仓库数据目录或已有 `.paper-agent/` 内容。测试服务器应创建独立临时目录和运行时 fixture PDF，提供固定模型行为；浏览器只访问该测试服务器与本地前端。
