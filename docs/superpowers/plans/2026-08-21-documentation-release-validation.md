# 开发者文档与整体验收 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 补齐中文开发者文档、架构决策记录和真实浏览器端到端测试，使其他开发者可以安全启动、理解、验证并继续扩展项目。

**Architecture:** 文档以 README 为入口，链接到架构、数据、API、模型、批注、笔记、开发和测试专题；ADR 固定关键取舍。Playwright 启动真实 Vite 与 FastAPI 测试实例，后端注入确定性 vLLM 测试双，用一份运行时生成的可复制文字 PDF 完成整条用户路径。

**Tech Stack:** Markdown、Mermaid、OpenAPI、Python 3.12、pytest、Node、Playwright、Vitest、Vite、FastAPI。

**Spec:** `docs/superpowers/specs/2026-08-21-reading-workspace-annotations-model-profiles-design.md`

## Global Constraints

- 本计划在前四份实施计划全部完成后执行。
- README 和主要开发文档使用简体中文；命令、环境变量、API 路径和代码标识保持原样。
- 文档中的每条命令必须在 Windows PowerShell 或 Linux/macOS shell 中至少有一种可直接复制形式。
- 文档不得包含真实 API 密钥、真实用户路径、临时 PDF 或本地数据库。
- 端到端测试不得依赖公网、真实 vLLM、已有浏览器登录或用户私有文件。
- 测试 PDF 在运行时临时生成，测试结束后由受限临时目录自动清理，不提交二进制 PDF。
- 视觉验收使用确定性测试数据，桌面和小屏分别检查。
- README 截图只能来自最终本地 UI，不使用与产品不一致的概念图。
- OpenAPI 是接口结构事实来源；手写文档解释业务语义、错误码和示例。
- `sources/` 为只读参考目录；不得暂存环境提供的 `AGENTS.md`。

## File Map

**Create**

- `CONTRIBUTING.md`
- `docs/architecture.md`
- `docs/api.md`
- `docs/development.md`
- `docs/testing.md`
- `docs/roadmap.md`
- `docs/adr/0001-request-scoped-model-profiles.md`
- `docs/adr/0002-pdf-text-anchors.md`
- `docs/adr/0003-crash-recoverable-paper-deletion.md`
- `docs/assets/reader-workspace.png`
- `web/playwright.config.ts`
- `web/e2e/reader-workspace.spec.ts`
- `web/e2e/testApi.ts`
- `tests/e2e/__init__.py`
- `tests/e2e/server.py`
- `tests/e2e/test_server.py`

**Modify**

- `README.md`
- `.env.example`
- `pyproject.toml`
- `web/package.json`
- `web/package-lock.json`
- `.gitignore`
- all existing topical docs for cross-links and final signatures

---

### Task 1: 将 README 改为中文入口并建立贡献指南

**Files:**
- Modify: `README.md`
- Create: `CONTRIBUTING.md`
- Modify: `.env.example`
- Modify: `.gitignore`

**Interfaces:**
- README links to every topical document and shows verified startup commands
- CONTRIBUTING defines branch, TDD, review, documentation and secret rules

- [ ] **Step 1: 盘点实际命令和环境变量**

Run: `rg -n "scripts|requires-python|PAPER_AGENT_|uvicorn|npm run" pyproject.toml web/package.json src README.md docs`

Expected: 输出实际 Python/Node 版本、服务命令与环境变量；以代码和 package scripts 为准更新文档。

- [ ] **Step 2: 重写中文 README**

README 固定章节顺序：

```text
# paper-agent
项目简介
功能概览
界面截图
技术架构
环境要求
快速开始（Windows PowerShell）
快速开始（Linux/macOS）
配置多个 vLLM 模型
数据目录与永久删除
测试
开发者文档索引
当前限制
安全说明
```

功能概览必须提及连续 PDF、选文工具栏、高亮、解释/翻译自动笔记、Agent 笔记记忆、知识图谱、多模型和永久删除。当前限制明确 OCR、多用户、云同步和跨页选区未支持。

- [ ] **Step 3: 编写 CONTRIBUTING**

写明：从 `main` 创建 `codex/<feature>` 或项目约定分支；先写失败测试；每个小任务独立提交；后端运行 `python -m pytest -v`；前端运行 `npm test` 与 `npm run build`；UI 改动执行 `$design-taste-frontend` 预检；数据 schema、API 或行为变化必须同步文档和迁移。

- [ ] **Step 4: 完善安全模板和忽略规则**

`.env.example` 保留 vLLM fallback，并增加注释说明 UI 档案优先。`.gitignore` 明确忽略 `.paper-agent/`、`.e2e-data/`、`web/test-results/`、`web/playwright-report/`，但不忽略 `docs/assets/reader-workspace.png`。

- [ ] **Step 5: 检查 README 中无旧三栏与英文产品文案**

Run: `rg -n "three panes|Paper library|Build core graph|Local research desk" README.md CONTRIBUTING.md`

Expected: 无匹配。

- [ ] **Step 6: 提交中文入口文档**

```bash
git add README.md CONTRIBUTING.md .env.example .gitignore
git commit -m "docs: make chinese documentation the project entrypoint"
```

---

### Task 2: 完成架构、API、开发、测试和路线图文档

**Files:**
- Create: `docs/architecture.md`
- Create: `docs/api.md`
- Create: `docs/development.md`
- Create: `docs/testing.md`
- Create: `docs/roadmap.md`
- Modify: `docs/model-services.md`
- Modify: `docs/data-model.md`
- Modify: `docs/pdf-annotations.md`
- Modify: `docs/note-memory.md`

**Interfaces:**
- Every production module and public route has a discoverable document owner
- All docs cross-link without stale paths

- [ ] **Step 1: 编写 architecture.md**

包含 Mermaid 系统图和四条时序：论文上传解析、选文解释自动入笔记、Agent 检索笔记并校验证据、永久删除恢复。列出 `app.state` 装配对象和每个 service/repository 的责任边界；说明主 Agent 非流式是 Citation Guard 的安全要求，解释/翻译 SSE 不属于主聊天。

- [ ] **Step 2: 编写 api.md**

从 OpenAPI 路径生成清单：

Run: `python -c "from paper_agent.app import create_app; print('\n'.join(sorted(create_app().openapi()['paths'])))"`

Expected: 输出所有 `/api` 路径。将每个路径归入论文、批注/笔记、模型、图谱和 Agent，并写出完整 request/response、SSE 四事件、稳定错误码和幂等键语义。

- [ ] **Step 3: 编写 development.md**

包含：Python venv、editable install、Node 安装、`npm ci`、前后端双进程启动、Windows PowerShell 和 POSIX 命令、数据目录覆盖、数据库迁移执行、如何注入 fake clients、常见端口冲突、vLLM parser 能力排查和 package-lock 修复方式。

- [ ] **Step 4: 编写 testing.md 与 roadmap.md**

testing.md 解释单元、集成、Vitest、Playwright、真实模型手工检查和命令矩阵；roadmap.md 把 OCR、多用户、云同步、跨页选区、可编辑图谱列为后续，并写清进入条件，不写承诺日期。

- [ ] **Step 5: 校对专题文档签名**

Run: `rg -n "model_profile_id|request_id|selection-assists|DELETE /api/papers|TextLayer|6000" docs`

Expected: 相应专题文档包含最终字段和数值，且没有互相冲突的旧接口。

- [ ] **Step 6: 提交专题文档**

```bash
git add docs/architecture.md docs/api.md docs/development.md docs/testing.md docs/roadmap.md docs/model-services.md docs/data-model.md docs/pdf-annotations.md docs/note-memory.md
git commit -m "docs: hand off architecture api and development workflows"
```

---

### Task 3: 记录关键架构决策

**Files:**
- Create: `docs/adr/0001-request-scoped-model-profiles.md`
- Create: `docs/adr/0002-pdf-text-anchors.md`
- Create: `docs/adr/0003-crash-recoverable-paper-deletion.md`
- Modify: `docs/architecture.md`

**Interfaces:**
- ADR sections: 状态、背景、决策、后果、被否决方案

- [ ] **Step 1: 写模型档案 ADR**

记录选择请求级 provider 而不是启动单例或多供应商 Gateway；解释会话内切换模型、快照、逻辑删除、secret 分离和环境变量 fallback 的后果。

- [ ] **Step 2: 写文本锚点 ADR**

记录使用 PDF.js TextLayer 同 viewport 采集单页归一化矩形；解释为什么不从 PyMuPDF 文本块反推字符框、不把像素坐标持久化、不在首版做 OCR/跨页选择。

- [ ] **Step 3: 写删除 ADR**

记录协调器、trash marker、数据库逆序事务与启动恢复；解释为什么不直接开启全表 cascade、不先 unlink PDF、不提供用户回收站。

- [ ] **Step 4: 从架构文档链接 ADR**

architecture.md 新增“关键决策”表，链接三个 ADR，并标注状态为`已接受`。

- [ ] **Step 5: 提交 ADR**

```bash
git add docs/adr docs/architecture.md
git commit -m "docs: record reader workspace architecture decisions"
```

---

### Task 4: 建立确定性 FastAPI E2E 测试服务器

**Files:**
- Create: `tests/e2e/__init__.py`
- Create: `tests/e2e/server.py`
- Create: `tests/e2e/test_server.py`
- Modify: `pyproject.toml`

**Interfaces:**
- `tests.e2e.server:app` starts with a dedicated TemporaryDirectory
- `GET /__e2e__/fixture.pdf` returns a runtime-generated selectable PDF
- Fake provider supports chat streaming, strict JSON graph output and tool-calling Agent flow

- [ ] **Step 1: 写测试服务器失败测试**

```python
def test_e2e_server_exposes_selectable_pdf_and_two_model_profiles(e2e_client):
    pdf = e2e_client.get("/__e2e__/fixture.pdf")
    assert pdf.status_code == 200
    assert pdf.headers["content-type"].startswith("application/pdf")
    assert b"routing" in fitz.open(stream=pdf.content, filetype="pdf")[0].get_text().lower().encode()
    profiles = e2e_client.get("/api/model-profiles").json()
    assert [profile["display_name"] for profile in profiles] == ["测试 Qwen", "测试 DeepSeek"]
```

增加：fake selection stream 输出固定中文解释/翻译；Agent 返回有效 element citation 和 note reference；graph 输出一个有效节点；TemporaryDirectory 不在仓库数据目录。

- [ ] **Step 2: 运行测试并确认失败**

Run: `python -m pytest tests/e2e/test_server.py -v`

Expected: FAIL，E2E server 不存在。

- [ ] **Step 3: 实现运行时 PDF fixture**

```python
def fixture_pdf_bytes() -> bytes:
    document = fitz.open()
    page = document.new_page(width=612, height=792)
    page.insert_textbox(
        fitz.Rect(72, 72, 540, 220),
        "Routing Paper\nWe introduce a routing load balancing loss for sparse experts.",
        fontsize=14,
    )
    payload = document.tobytes()
    document.close()
    return payload
```

测试 route 直接返回 bytes，不写入仓库。测试实例使用 `TemporaryDirectory(prefix="paper-agent-e2e-")` 创建 Settings；进程退出自动清理该精确目录。

- [ ] **Step 4: 实现 fake provider**

provider 按两个固定 profile ID 返回不同 display_name，但使用同一确定性 fake clients。chat stream 对 explain 输出“这段文字说明作者使用负载均衡损失来分配专家。”，对 translate 输出固定中文翻译；structured graph 返回引用真实 element ID 的 schema；tool client 先调用 read/search tool，再返回通过 Citation Guard 的答案。

- [ ] **Step 5: 运行测试并提交**

Run: `python -m pytest tests/e2e/test_server.py -v`

Expected: PASS。

```bash
git add tests/e2e pyproject.toml
git commit -m "test: add deterministic paper agent e2e server"
```

---

### Task 5: 添加 Playwright 完整阅读路径

**Files:**
- Create: `web/playwright.config.ts`
- Create: `web/e2e/testApi.ts`
- Create: `web/e2e/reader-workspace.spec.ts`
- Modify: `web/package.json`
- Modify: `web/package-lock.json`
- Modify: `.gitignore`

**Interfaces:**
- Script: `npm run test:e2e`
- Desktop project: Chromium 1440×900
- Mobile project: Chromium 390×844

- [ ] **Step 1: 安装浏览器测试依赖**

Run from `web`: `npm install --save-dev @playwright/test @axe-core/playwright`

Expected: package.json 与 lockfile 更新。

Run from `web`: `npx playwright install chromium`

Expected: Chromium 安装成功。

- [ ] **Step 2: 配置两个真实开发服务器**

```typescript
export default defineConfig({
  testDir: './e2e',
  use: { baseURL: 'http://127.0.0.1:5173', trace: 'retain-on-failure' },
  webServer: [
    {
      command: 'python -m uvicorn tests.e2e.server:app --host 127.0.0.1 --port 8000',
      cwd: '..',
      url: 'http://127.0.0.1:8000/health',
      reuseExistingServer: false,
    },
    {
      command: 'npm run dev -- --host 127.0.0.1 --port 5173',
      cwd: '.',
      url: 'http://127.0.0.1:5173',
      reuseExistingServer: false,
    },
  ],
  projects: [
    { name: 'desktop-chromium', use: { viewport: { width: 1440, height: 900 } } },
    { name: 'mobile-chromium', use: { viewport: { width: 390, height: 844 } } },
  ],
});
```

- [ ] **Step 3: 写完整桌面路径测试**

测试步骤必须真实执行：从 `/__e2e__/fixture.pdf` 取得 buffer → 上传 → 打开论文 → 在 TextLayer 拖动选择原文 → 高亮 → 解释并等待“已存入笔记” → 在笔记 tab 验证 → Agent 提问并看到“参考 1 条笔记” → 切换测试 DeepSeek 且 conversation ID 不变 → 构建核心图谱并显示模型 → 刷新确认高亮恢复 → 永久删除并返回空论文库。

每一步使用 role/label，不使用脆弱 class selector；只有模拟真实文字拖动时允许用 `[data-pdf-page] .textLayer span`。

- [ ] **Step 4: 写小屏和可访问性测试**

小屏验证`论文`/`工具`切换、无水平溢出、聊天框模型仍可选择。桌面和小屏各运行 Axe，断言无 `serious` 或 `critical` violations；检查键盘可打开模型设置、Esc 关闭、separator 箭头调整。

- [ ] **Step 5: 运行 Playwright 并提交**

Run from `web`: `npm run test:e2e`

Expected: desktop 和 mobile 项目全部 PASS。

```bash
git add web/playwright.config.ts web/e2e web/package.json web/package-lock.json .gitignore
git commit -m "test: cover the paper reading workflow in chromium"
```

---

### Task 6: 完成视觉检查并更新 README 截图

**Files:**
- Create: `docs/assets/reader-workspace.png`
- Modify: `README.md`
- Modify: `web/src/styles.css` only if the visual check finds a concrete issue

**Interfaces:**
- README screenshot is generated from the desktop E2E fixture at 1440×900

- [ ] **Step 1: 启动最终本地 E2E 服务**

Run from repository root: `python -m uvicorn tests.e2e.server:app --host 127.0.0.1 --port 8000`

Run from `web` in a second terminal: `npm run dev -- --host 127.0.0.1 --port 5173`

Expected: backend health 与 Vite 页面都可访问。

- [ ] **Step 2: 使用浏览器做 design-taste preflight 后检查**

在 1440×900 检查：PDF 是否明显占主视觉、右侧是否紧凑、聊天框是否固定、浮层是否靠近选区、长笔记是否可滚动、模型 badge 是否不过度抢眼。检查 warm gray、white paper、ink、indigo、yellow highlight；确认无渐变、英文 eyebrow、巨型标题和多余圆角。

在 390×844 检查：无横向溢出；论文/工具切换可用；删除弹窗和模型设置不超出 viewport；触控目标至少约 40px。

- [ ] **Step 3: 只修复观察到的具体视觉问题**

每个修复先增加组件或 Playwright 断言，再修改 CSS。重新运行对应 Vitest 和 Playwright 用例，避免无测试的主观重排。

- [ ] **Step 4: 捕获最终截图**

使用 Playwright 在论文已打开、存在高亮、右侧显示解释笔记的稳定状态执行：

```typescript
await page.screenshot({
  path: '../docs/assets/reader-workspace.png',
  fullPage: false,
});
```

README 用相对路径 `docs/assets/reader-workspace.png` 展示，并提供中文 alt 文本。

- [ ] **Step 5: 重新运行视觉路径并提交**

Run from `web`: `npm run test:e2e -- --project=desktop-chromium`

Expected: PASS。

```bash
git add docs/assets/reader-workspace.png README.md web/src/styles.css web/src/**/*.test.tsx web/e2e/reader-workspace.spec.ts
git commit -m "docs: show the finished paper reading workspace"
```

---

### Task 7: 执行完整发布前验证

**Files:**
- Modify documentation or tests only when a verification command identifies a concrete discrepancy
- Test: complete repository

**Interfaces:**
- Produces a clean, reproducible verification record for handoff

- [ ] **Step 1: 验证全新 Python 环境契约**

Run: `python -m pip install -e ".[dev]"`

Expected: exit 0。

Run: `python -m pytest -v`

Expected: 全部测试 PASS，0 failures。

- [ ] **Step 2: 验证全新前端安装、测试和构建**

Run from `web`: `npm ci`

Expected: exit 0。

Run from `web`: `npm test`

Expected: 全部 Vitest PASS，0 failures。

Run from `web`: `npm run build`

Expected: TypeScript 与 Vite build exit 0。

- [ ] **Step 3: 验证真实浏览器流程**

Run from `web`: `npm run test:e2e`

Expected: desktop 与 mobile Chromium 全部 PASS。

- [ ] **Step 4: 验证仓库卫生和敏感数据**

Run: `git diff --check`

Expected: 无输出，exit 0。

Run: `git status --short`

Expected: 只显示明确准备提交的文件；不得包含 `.paper-agent`、`.e2e-data`、PDF、数据库、secret 或 `AGENTS.md`。

Run: `rg -n --hidden -g '!web/node_modules/**' -g '!.git/**' "(sk-|Bearer )[A-Za-z0-9_-]{12,}|huyaohua369" .`

Expected: 无真实凭证匹配；测试中的固定 `top-secret` 不属于真实凭证，但只能存在测试或安全说明中。

- [ ] **Step 5: 核对文档链接和延期项**

Run: `rg -n "docs/(architecture|api|data-model|pdf-annotations|model-services|note-memory|development|testing|roadmap)\.md" README.md CONTRIBUTING.md docs`

Expected: README 至少链接全部九份专题文档；roadmap 明确 OCR、多用户和跨页选区延期。

- [ ] **Step 6: 提交验证中产生的必要修正**

如果前五步没有产生修正，不创建空提交。如果产生修正，只暂存对应文件并提交：

```bash
git add README.md CONTRIBUTING.md docs web/src web/e2e tests
git commit -m "chore: finalize reader workspace verification"
```
