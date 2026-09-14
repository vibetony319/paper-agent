# 测试策略

测试按确定性优先：后端单元/集成测试与前端 Vitest 不依赖真实模型、互联网、用户 PDF 或仓库运行数据。当前命令、开发环境和 fake 注入方式见 [本地开发](development.md)；接口语义见 [HTTP API 业务语义](api.md)。

## 覆盖层次

- **后端单元测试**：领域模型、存储、迁移、解析器、vLLM 协议适配和服务规则。重点覆盖模型回答结构解析、可选引用回显、请求幂等、模型快照、删除恢复和图谱证据校验。
- **后端集成测试**：FastAPI 路由、schema、稳定错误与跨层持久化。测试 fixture 用临时目录创建应用与生成的 PDF；模型能力通过 fake provider/client 注入。
- **前端 Vitest**：React 组件、阅读器几何、SSE 解析、客户端请求、状态 reducer 与样式契约，在 `jsdom` 中执行。
- **Playwright**：使用专用 E2E 服务器和运行时生成的两页 PDF，覆盖桌面（1440×900）与小屏（390×844）阅读路径及 axe 严重/关键可访问性问题。脚本已添加；最新实际验收结果见[开发交接](developer-handoff.md)。
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
| 浏览器 E2E | `web/` | `npm run test:e2e` | 已安装 Playwright Chromium；本地 Python 开发环境 | 桌面与小屏流程通过；不调用真实模型。 |

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

## Playwright 使用与隔离

从 `web/` 执行（首次安装浏览器需要网络，测试本身不需要）：

```powershell
npx playwright install chromium
npm run test:e2e
```

Windows 已安装 Edge 时，可在当前 PowerShell 会话设置 `$env:PLAYWRIGHT_CHANNEL = 'msedge'` 使用本机浏览器；清除该变量后恢复默认 Chromium。报告应注明实际浏览器，不能将 Edge 运行宣称为固定版本 Chromium 验收。

配置自动启动端口 8000 的 `tests.e2e.server:app` 与端口 5173 的前端，不复用已有服务；端口被占用时应先确认占用进程，不要终止未知服务。优先使用仓库 `.venv`，不存在时使用 PATH 中的 Python。

服务器只替换模型传输层，解析、数据库、模型档案、Agent 工具、模型回答解析、笔记和删除均使用生产代码。每次启动创建独立系统临时目录，正常关闭时清理；强制终止进程可能留下临时目录，但不会写入用户运行数据。每个浏览器用例只删除自己上传的论文。测试 PDF 运行时生成，不提交二进制论文或私人截图。

失败时查看 `web/playwright-report/`、`web/test-results/` 中的错误上下文、截图和 trace（均已忽略）。完整阅读用例还保存 `reader-workspace.png`。只有确认截图是合成测试数据后，才使用 `UPDATE_README_SCREENSHOT=1` 更新文档截图。
