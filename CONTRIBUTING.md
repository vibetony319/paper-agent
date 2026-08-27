# paper-agent 贡献与开发指南

本文档面向继续开发本项目的工程师。当前功能断点请同时阅读 [开发交接](docs/developer-handoff.md)。

## 1. 技术栈与运行边界

- 后端：Python 3.12、FastAPI、SQLAlchemy、SQLite、PyMuPDF、MarkItDown。
- 模型服务：OpenAI-compatible vLLM；项目不包含 Model Gateway。
- 前端：React、TypeScript、Vite、PDF.js、React Flow、ELK、Vitest。
- 产品形态：本地优先、单用户、单服务进程。
- 当前不承诺多 worker 或多进程并发。模型密钥文件锁、请求幂等锁和模型使用租约均是进程内机制。

## 2. 本地安装

后端要求 Python 3.12 或更高版本：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

前端要求 Node.js 22.14+（Node 22 系列）或 Node.js 24+：

```powershell
Set-Location web
npm ci
```

模型服务不是运行自动化测试的前置条件。需要联调时，在启动后端前配置：

```powershell
$env:PAPER_AGENT_REASONING_BASE_URL = "http://127.0.0.1:8001/v1"
$env:PAPER_AGENT_REASONING_MODEL = "your-served-model"
$env:PAPER_AGENT_REASONING_API_KEY = "EMPTY"
```

环境变量只提供只读兼容档案；正式产品路径使用数据库中的多个模型档案。API 密钥不得写入 SQLite、API 响应、日志或模型快照。

## 3. 启动开发服务

后端：

```powershell
.\.venv\Scripts\uvicorn.exe paper_agent.app:create_app --factory --port 8000 --reload
```

前端另开终端：

```powershell
Set-Location web
npm run dev
```

默认地址：后端 `http://127.0.0.1:8000`，前端 `http://127.0.0.1:5173`。Vite 只代理 `/api`。

本地运行数据默认写入启动目录下的 `.paper-agent/`：

- `paper-agent.db`：SQLite 数据库；
- `papers/`：按系统生成 ID 保存的原始 PDF；
- 模型密钥文件：由配置指定的数据目录保存，严禁提交。

## 4. 代码地图

```text
src/paper_agent/
├── app.py                         FastAPI 装配和进程级服务生命周期
├── config.py                      本地配置与环境变量 fallback
├── domain.py                      领域模型；不放 HTTP 细节
├── database.py                    SQLAlchemy live metadata
├── migrations.py                  冻结、递增、幂等的数据库迁移
├── storage.py                     论文、消息、图谱等持久层
├── model_profile_storage.py       模型档案持久层
├── schemas.py                     API 请求/响应 schema
├── parsers/                       PyMuPDF Stage 0、MarkItDown Stage 1、对齐
├── models/vllm.py                 OpenAI-compatible vLLM 客户端
├── services/                      业务服务、Agent、Citation Guard、图谱
└── routes/                        只负责 HTTP 校验与安全错误映射

web/src/                            React 阅读工作台
tests/unit/                         领域、持久层和服务单元测试
tests/integration/                  FastAPI 与跨层集成测试
docs/superpowers/specs/             产品/架构规格
docs/superpowers/plans/             可执行实施计划
```

主要数据流：

```text
PDF -> PyMuPDF geometry + MarkItDown semantic text -> Alignment -> SQLite
                                                             |
                                                             +-> PDF reader / notes
                                                             +-> evidence graph
                                                             +-> Agent tools -> Citation Guard -> response

Model profile -> request-scoped vLLM client -> immutable secret-free snapshot
```

## 5. 测试与质量门禁

后端每次改动至少执行目标测试；提交前执行完整回归：

```powershell
.\.venv\Scripts\python.exe -m pytest -v
```

如果受限执行环境不能访问系统临时目录，可以把 pytest 的临时根目录限定在已忽略的本地数据目录：

```powershell
.\.venv\Scripts\python.exe -m pytest -v --basetemp=.paper-agent\pytest-tmp
```

前端任务执行：

```powershell
Set-Location web
npm ci
npm test
npm run build
```

最终发布门禁还包括 Playwright 的桌面与移动端完整阅读流程；不要只用 Vitest 或旧版 Web UI 测试代替真实浏览器验收。

提交前再执行：

```powershell
git diff --check
git status --short
```

不得暂存 PDF、SQLite、`.paper-agent/`、密钥、`sources/` 或环境提供的 `AGENTS.md`。

## 6. 开发规则

1. 先读当前设计规格和子计划，再修改代码；历史 MVP 文档不能覆盖新规格。
2. 行为变更先写失败测试，再写最小实现。
3. 后端计划按“模型档案 → 批注/笔记 → 删除 → 前端 → 最终文档”顺序执行。
4. 路由只做 HTTP 边界；事务、默认档案回退、密钥补偿、删除协调等放在 service/repository。
5. API 的 catch-all 错误必须返回稳定、安全、无异常链泄漏的信息。
6. 未经 Citation Guard 验证的模型文本不得作为论文答案持久化或流向 UI。
7. 模型快照只保存 `profile_id`、展示名、base URL、模型名和 revision 等公开字段，不保存密钥。
8. 用户可见文案优先简体中文；技术标识和协议字段保持准确。
9. 不编辑、移动或删除 `sources/` 下的同步参考材料。
10. UI 改动先执行 `$design-taste-frontend` 预检，并用真实桌面、小屏和键盘路径验证可访问性。
11. 数据 schema、公开 API 或持久行为变化必须同时更新迁移、自动化测试和对应专题文档。

## 7. 数据库迁移规则

- 已发布迁移是冻结历史，禁止通过修改旧迁移来改变现有数据库的升级结果。
- 新 schema 变化必须增加下一个版本，并同时覆盖空库、旧库、重复启动和 live metadata 已建表场景。
- legacy 行新增列应尽量 nullable，并提供安全降级；损坏的模型追溯数据必须向 API 暴露为 `model: null`，不能伪造来源。
- 每个迁移测试都应断言精确列集、索引边界和幂等性。

## 8. Git 与交接

- 默认从最新 `main` 创建 `codex/<feature>` 分支；如果仓库另有明确约定，以项目约定为准。
- 先写能证明目标行为的失败测试，再写最小实现；文档任务先验证代码和命令的事实来源。
- 每个可独立验收的小任务使用独立提交，提交信息写清行为结果，不混入无关文件。
- 不使用 `git reset --hard`、`git checkout --` 或 `git clean` 处理不明改动。
- 一个任务完成后先跑测试并做独立审查；不要把失败中的实验改动伪装成完成状态。
- 当前工作区有未提交代码时，接手者必须先阅读 [开发交接](docs/developer-handoff.md) 并保存 `git diff`，再继续工作。
- 推送、合并、删除分支和创建 PR 需要明确授权。

## 9. 文档同步

公开 API、配置、数据模型、用户路径或运行约束变化时，至少同步：

- 对应设计规格或 ADR；
- 产生接口的实施计划及消费方计划；
- README/专题文档；
- 测试和示例请求。

最终文档与发布验收计划见 [2026-08-21-documentation-release-validation.md](docs/superpowers/plans/2026-08-21-documentation-release-validation.md)。
