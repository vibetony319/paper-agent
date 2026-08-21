# 论文阅读工作区升级 Program Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按可审查的依赖顺序执行多模型、批注记忆、安全删除、中文阅读器和开发者交接五个实施计划，最终交付完整论文阅读体验。

**Architecture:** 后端能力先行，前端只依赖已经测试稳定的 API；高风险删除在最终数据表确定后实现；中文阅读器整合全部能力；最后用真实 FastAPI/Vite/Chromium 流程和中文文档完成交接。每个子计划都产生可运行、可测试的增量并设置审查关口。

**Tech Stack:** Python 3.12、FastAPI、SQLite、SQLAlchemy、vLLM、React、TypeScript、PDF.js、Vitest、Playwright。

**Spec:** `docs/superpowers/specs/2026-08-21-reading-workspace-annotations-model-profiles-design.md`

## Global Constraints

- 执行前完整阅读设计规格和当前子计划，不从旧 MVP 三栏或单模型假设推断行为。
- 默认使用 `superpowers:subagent-driven-development`，每个 Task 使用独立执行者并经过规格审查与代码质量审查。
- 每个代码行为先写失败测试，再写最小实现，再运行目标测试和回归测试。
- UI 任务必须使用 `$design-taste-frontend`；不得加入未确认的总结、评价、相关推荐或分享功能。
- 不跨计划提前调用尚未产生的接口；接口变更必须同步下游计划和文档。
- 所有用户可见文案优先简体中文；技术标识保持准确。
- API 密钥、本地数据、PDF、数据库和 `AGENTS.md` 不进入 Git。
- 每个子计划结束时检查 `git status --short`、目标测试和该阶段回归命令。

## Dependency Graph

```text
Plan 1 多模型档案与调用追溯
          │
          ▼
Plan 2 批注、解释翻译与笔记记忆
          │
          ▼
Plan 3 论文及关联数据安全删除
          │
          ▼
Plan 4 中文论文阅读工作区前端
          │
          ▼
Plan 5 开发者文档与整体验收
```

Plan 3 必须位于 Plan 2 之后，因为删除顺序需要覆盖最终锚点、生成请求和笔记引用表。Plan 4 必须位于全部后端计划之后，避免使用临时 mock 契约替代真实 API。Plan 5 最后执行，但前四份计划内的专题文档仍须随代码同步完成。

## Cross-Plan Interface Contract

| 生产计划 | 接口 | 消费计划 |
|---|---|---|
| Plan 1 | `model_profile_id`、`ModelSnapshot`、`ReasoningClientProvider` | Plan 2、Plan 4 |
| Plan 1 | 模型档案 CRUD/测试、Agent/图谱请求模型参数 | Plan 4、Plan 5 |
| Plan 2 | 文本锚点、高亮、扩展笔记、SSE 四事件 | Plan 3、Plan 4、Plan 5 |
| Plan 2 | Agent 笔记引用和可选 selection | Plan 4、Plan 5 |
| Plan 3 | `DELETE /api/papers/{paper_id}`、稳定删除错误码 | Plan 4、Plan 5 |
| Plan 4 | 最终中文角色、label、用户路径 | Plan 5 Playwright 与 README 截图 |

如果生产接口签名发生变化，执行者必须先修改产生该接口的计划文档和对应专题文档，再修改消费方；不能只在下游临时适配。

---

### Task 1: 执行多模型档案与调用追溯计划

**Files:**
- Plan: `docs/superpowers/plans/2026-08-21-model-profiles-provenance.md`
- Review: `docs/superpowers/specs/2026-08-21-reading-workspace-annotations-model-profiles-design.md` sections 9, 10, 11, 12, 15, 18

**Interfaces:**
- Produces request-scoped vLLM clients, model settings API and durable model snapshots.

- [ ] **Step 1: 逐 Task 执行 Plan 1**

严格完成 Plan 1 的七个 Task，不把前端模型 UI 混入后端提交。

- [ ] **Step 2: 运行 Plan 1 阶段门禁**

Run: `python -m pytest -v`

Expected: 全部后端测试 PASS。

Run: `git status --short`

Expected: 没有未提交的 Plan 1 代码或文档；环境 `AGENTS.md` 保持未跟踪且未暂存。

- [ ] **Step 3: 审查接口产物**

确认 OpenAPI 包含模型档案、请求模型 ID 和模型快照；确认同一会话可切换模型；确认 API 密钥不在响应和快照中。

---

### Task 2: 执行批注、解释翻译与笔记记忆计划

**Files:**
- Plan: `docs/superpowers/plans/2026-08-21-annotations-note-memory.md`
- Review: design spec sections 7, 8, 10, 11, 12, 14, 15

**Interfaces:**
- Consumes: Plan 1 `ReasoningClientProvider` and `ModelSnapshot`
- Produces: anchors, highlights, generated notes, SSE and Agent note memory

- [ ] **Step 1: 逐 Task 执行 Plan 2**

严格完成 Plan 2 的七个 Task；保持解释/翻译与主聊天分离。

- [ ] **Step 2: 运行 Plan 2 阶段门禁**

Run: `python -m pytest -v`

Expected: 全部后端测试 PASS。

- [ ] **Step 3: 审查安全边界**

确认跨页和非法坐标被拒绝；取消 SSE 不生成笔记；笔记注入不能绕过 Citation Guard；AI 笔记保留来源和模型快照。

---

### Task 3: 执行论文及关联数据安全删除计划

**Files:**
- Plan: `docs/superpowers/plans/2026-08-21-paper-deletion.md`
- Review: design spec sections 11, 13, 14, 15

**Interfaces:**
- Consumes: Plans 1–2 final database schema
- Produces: deletion coordinator, crash recovery and permanent delete API

- [ ] **Step 1: 逐 Task 执行 Plan 3**

严格完成 Plan 3 的六个 Task；先验证路径，再执行任何文件移动或清理。

- [ ] **Step 2: 运行 Plan 3 阶段门禁**

Run: `python -m pytest -v`

Expected: 全部后端测试 PASS，包括故障注入和启动恢复。

- [ ] **Step 3: 审查数据完整性**

使用测试数据库逐表确认论文关联行清零、全局模型档案保留、commit 前失败恢复源 PDF、commit 后 cleanup failure 可在启动时完成。

---

### Task 4: 执行中文论文阅读工作区前端计划

**Files:**
- Plan: `docs/superpowers/plans/2026-08-21-reader-workspace-frontend.md`
- Review: design spec sections 5, 6, 7, 8, 9, 16, 17

**Interfaces:**
- Consumes: Plans 1–3 public APIs
- Produces: final Chinese reading workflow and accessible responsive UI

- [ ] **Step 1: 读取 design-taste-frontend 并逐 Task 执行 Plan 4**

完整读取 `C:/Users/Admin/.codex/skills/taste-skill/SKILL.md` 后执行八个 Task。保留测试先行顺序，不能先完成 CSS 再补交互测试。

- [ ] **Step 2: 运行 Plan 4 阶段门禁**

Run from `web`: `npm ci`

Expected: exit 0。

Run from `web`: `npm test`

Expected: 全部 Vitest PASS。

Run from `web`: `npm run build`

Expected: TypeScript 与 Vite build exit 0。

- [ ] **Step 3: 审查产品决策**

确认双栏、三标签、固定 composer、当前模型统一调用、同会话切换模型、解释/翻译浮层自动笔记、中文状态、永久删除和小屏切换全部存在；确认未增加范围外标签。

---

### Task 5: 执行开发者文档与整体验收计划

**Files:**
- Plan: `docs/superpowers/plans/2026-08-21-documentation-release-validation.md`
- Review: complete design spec

**Interfaces:**
- Consumes: final backend and frontend
- Produces: Chinese handoff docs, ADRs, Playwright workflow and release evidence

- [ ] **Step 1: 逐 Task 执行 Plan 5**

完成七个 Task；README 截图只在最终视觉检查后捕获。

- [ ] **Step 2: 运行最终门禁**

Run: `python -m pytest -v`

Expected: 全部后端和 E2E server 测试 PASS。

Run from `web`: `npm ci && npm test && npm run build && npm run test:e2e`

Expected: 安装、Vitest、build、desktop/mobile Playwright 全部 exit 0。

- [ ] **Step 3: 验证 Git 与秘密卫生**

Run: `git diff --check`

Expected: exit 0。

Run: `git status --short`

Expected: 无意外 PDF、SQLite、secret、本地数据目录或 `AGENTS.md` 被暂存。

---

## Spec Coverage Matrix

| 规格要求 | 实施位置 |
|---|---|
| 双栏阅读工作区与独立论文库 | Plan 4 Tasks 3, 7, 8 |
| 连续 PDF、TextLayer、单页跨行选择 | Plan 4 Tasks 4, 5 |
| 解释/翻译轻量浮层并自动存笔记 | Plan 2 Task 4；Plan 4 Task 6 |
| 手写笔记、高亮、反向定位 | Plan 2 Tasks 1–3；Plan 4 Tasks 5–6 |
| Agent 自动读取相关笔记 | Plan 2 Tasks 5–6；Plan 4 Task 7 |
| 多 vLLM 配置和能力检测 | Plan 1 Tasks 1–4；Plan 4 Task 2 |
| 聊天框当前模型统一控制所有调用 | Plan 1 Tasks 5–6；Plan 4 Task 7 |
| 切换模型继续同一会话并显示快照 | Plan 1 Task 5；Plan 4 Task 7 |
| PDF 与全部关联数据永久删除 | Plan 3 Tasks 1–5；Plan 4 Task 3 |
| 中文 UI 与视觉品质 | Plan 4 Tasks 3, 6–8 |
| 中文 README 与开发者文档 | Plan 5 Tasks 1–3 |
| 后端、前端、浏览器与视觉验收 | 每份计划回归；Plan 5 Tasks 4–7 |
| OCR、多用户、跨页选择延期 | Plan 4 中文提示；Plan 5 roadmap |

## Completion Rule

只有五份计划全部完成、最终门禁有新鲜成功输出、README 与专题文档和代码一致、真实浏览器路径通过，才能使用 `superpowers:finishing-a-development-branch` 进入合并或 PR 阶段。预算、时间或部分功能演示都不能替代完成条件。
