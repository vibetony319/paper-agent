# paper-agent 开发交接

更新时间：2026-08-24 22:50（Asia/Shanghai）

## 1. 当前结论

项目正在按“论文阅读工作区升级”总计划推进。Plan 1–3 已全部完成，Plan 4 前端工作区已完成五个任务。

- 当前分支：`codex/reader-workspace-v2`
- 最新功能提交：`ad09c0a feat: select and highlight paper text`。
- Plan 1“多模型档案与调用追溯”：Task 1–7 全部完成并提交。
- Plan 2“批注、解释翻译与笔记记忆”：Task 1–7 全部完成并提交。
- Plan 3“论文及关联数据安全删除”：Task 1–6 全部完成并提交（含文档 `docs/data-model.md`）。
- Plan 4“中文论文阅读工作区前端”：Task 1–6 已完成并提交。Task 6 增加解释/翻译流式浮层、仅在完成事件后持久化的生成笔记、选区旁手写笔记、中文笔记筛选与编辑/复制/删除，并让 annotations bundle 返回 anchors 以便刷新后定位无高亮笔记。
- Plan 5“开发者文档与整体验收”尚未开始。

接手规则：当前所有工作均已提交，工作树干净；仍不要 reset、checkout、clean 或覆盖工作区。`AGENTS.md` 是环境文件，禁止暂存。

## 2. 事实来源

按以下顺序阅读：

1. [2026-08-21 设计规格](superpowers/specs/2026-08-21-reading-workspace-annotations-model-profiles-design.md)
2. [总实施计划](superpowers/plans/2026-08-21-reading-workspace-program.md)
3. [模型档案计划](superpowers/plans/2026-08-21-model-profiles-provenance.md)（已全部完成）
4. [批注与笔记记忆计划](superpowers/plans/2026-08-21-annotations-note-memory.md)（已全部完成）
5. [论文删除计划](superpowers/plans/2026-08-21-paper-deletion.md)（已全部完成）
6. [前端工作区计划](superpowers/plans/2026-08-21-reader-workspace-frontend.md)（当前执行中，Task 3 起）
7. [贡献与开发指南](../CONTRIBUTING.md)
8. [模型服务文档](model-services.md)、[PDF 批注契约](pdf-annotations.md)、[笔记记忆](note-memory.md)、[数据模型与删除恢复](data-model.md)
9. 本文档

本地 SDD 裁决记录在 `.superpowers/sdd/`，但该目录被本地规则忽略，不应作为远程接手的唯一资料；关键裁决已在本文档重述。

## 3. 已完成提交

| 范围 | 关键提交 | 状态 |
|---|---|---|
| Plan 1 Task 1 迁移与模型列 | `556c6b1`, `c61c22b` | 已提交 |
| Plan 1 Task 2 档案与密钥 | `26a3ee5`, `1ab1486` | 已提交 |
| Plan 1 Task 3 动态客户端 | `b8652f8`, `9c8ea0e` | 已提交 |
| Plan 1 Task 4 模型 API | `ff30f00`, `4eed9b6` | 已提交 |
| Plan 1 Task 5 Agent 模型追溯 | `c109425`, `1cd7992` | 已提交 |
| Plan 1 Task 6 图谱模型追溯 | `525428b` | 已提交 |
| Plan 1 Task 7 模型文档 | `00d0da8` | 已提交 |
| Plan 2 Task 1 锚点 schema | `5a009d1` | 已提交 |
| Plan 2 Task 2 批注存储 | `a2861e0` | 已提交 |
| Plan 2 Task 3 批注 API | `2d34557` | 已提交 |
| Plan 2 Task 4 解释翻译 SSE | `993223f` | 已提交 |
| Plan 2 Task 5 本地笔记检索 | `c1ea776` | 已提交 |
| Plan 2 Task 6 Agent 笔记记忆 | `f03ed85` | 已提交 |
| Plan 2 Task 7 批注与笔记文档 | `bcbbf2c` | 已提交 |
| Plan 3 Task 1 论文操作协调器 | `4dfd1d5` | 已提交 |
| Plan 3 Task 2 单事务关联删除 | `e2ef36d` | 已提交 |
| Plan 3 Task 3 删除状态机与恢复 | `38b4c15` | 已提交 |
| Plan 3 Task 4–5 写路径协调与删除 API | `b0459aa` | 已提交 |
| Plan 3 Task 6 数据模型与删除文档 | `d74feb9` | 已提交 |
| Plan 4 Task 1 前端 API 契约与 SSE | `1446697` | 已提交 |
| Plan 4 Task 2 模型档案与选择器 | `6b5d7eb` | 已提交 |
| Plan 4 Task 3 论文库与永久删除体验 | `feat: focus the chinese paper library and deletion flow` | 已提交 |
| Plan 4 Task 4 连续 PDF、虚拟化与 TextLayer | `a9b88ae feat: render selectable continuous pdf pages` | 已提交 |
| Plan 4 Task 5 浏览器选区、高亮与覆盖层 | `ad09c0a feat: select and highlight paper text` | 已提交 |
| Plan 4 Task 6 解释、翻译与锚定笔记 | `100c231 feat: explain selections into anchored notes` | 已提交 |

## 4. 当前未提交内容

Task 5 的生产代码、测试和最小行为样式已提交。仅三个未跟踪文件：`AGENTS.md`（环境提供，禁止暂存）、`CONTRIBUTING.md`、`docs/README.md`（指南与导航，历史上一直未跟踪，保持原样，是否纳入版本库留待 Plan 5 决定）。`.superpowers/sdd/` 下的任务报告是本地执行记录，不纳入提交。

## 5. 关键裁决与偏离

- 批注计划原文写“迁移 2”，但迁移 2 已被 Plan 1 Task 5 冻结使用。批注 schema 实际使用迁移 3–5：迁移 3 建七张批注表，迁移 4 为 `notes`/`highlights` 增加幂等 `request_id` 与部分唯一索引，迁移 5 为 `text_anchor_rects` 增加 `paper_id`。
- 计划没有给高亮和笔记的 `request_id` 持久列；为了诚实支持“重复请求返回同一对象、冲突字段报错”，增加了迁移 4。
- live metadata 中 `conversation_message_note_citations.note_id` 不设外键，允许笔记删除后历史消息保留悬空引用并在响应中标记 `available=false`。迁移 3 的冻结 DDL 本来就无此外键，两者现在一致。
- 笔记检索的 `manual +1` 只在基础词项重合或与选区同页时参与排序，避免所有手写笔记永远进入上下文。
- 部署边界仍是单用户、单服务进程；协调器、幂等锁、模型使用租约和删除恢复状态都是进程内机制。
- Plan 3 Task 4–5 审查修复：删除 API 错误从 `HTTPException(detail={code, ...})` 改为 `PaperDeletionHttpError` + app 级处理器（`src/paper_agent/routes/papers.py`、`src/paper_agent/app.py`），错误响应体顶层恒为 `{code, detail}`，与批注、模型档案错误格式一致。
- 前端 `Note`/`AgentMessage`/`PaperSummary` 的新字段（model、note_references、stage 模型快照等）声明为可选：既有测试 fixture 属后续 Task 范围，后端运行时始终返回这些字段，后续任务重写 fixture 时可收紧为必填。
- `ModelSelector` 暂放在 App 顶栏；Task 7 引入固定 `ChatComposer` 时移入聊天框。
- 前端 `ModelProfile` 补 `read_only` 字段（后端契约包含但计划样例未列）；只读档案在设置弹窗中禁用编辑/删除。
- `clear_api_key` 是前端便捷字段，客户端翻译为 `api_key: null` 发送，请求体不出现 `clear_api_key`。
- Task 5 的页面所有权 DOM 契约为 `[data-pdf-page]`，坐标归一化基准为 `.pdf-page-view__surface`；计划中的旧 `.pdf-page__surface` 不再使用。批注使用与 document/graph/notes 同一 abort signal 和 generation guard 独立加载，未替换既有 notes 请求路径。
- `GET /api/papers/{paper_id}/annotations` 现以可选的 additive `anchors` 字段返回所有文本锚点；前端在新旧服务端响应间兼容空 anchors，并将矩形并集统一转换为 `SourceTarget`。这让手写或生成、但未创建高亮的笔记在刷新后仍可显示原文并定位。
- Task 6 只启用选区解释、翻译和记笔记；无当前模型时前两项保持禁用并给出中文提示，`问助手` 仍明确禁用，等待 Task 7 聊天框。

## 6. 最新验证证据

后端验证命令沿用共享虚拟环境，并因本机 pytest 临时目录权限问题需要覆盖 basetemp：

```powershell
& "C:\Users\Admin\.codex\.chatgpt-projects\g-p-6a76dd5037e88191acdf69ce4d033042\.venv\Scripts\python.exe" -m pytest -q -W error -o pythonpath=src -p no:cacheprovider --basetemp "C:\Users\Admin\AppData\Local\Temp\paper-agent-pytest-515d"
```

前端命令（node v24.18.0，满足 `^22.14.0 || >=24.0.0`）：

```powershell
cd web; npm test
npx tsc --noEmit --project tsconfig.app.json
```

新鲜结果：

- 后端完整回归（`d74feb9` 之后）：`402 passed in 88.13s`。
- 前端 Plan 4 Task 1 之后：`110 passed`（11 个测试文件）；lockfile 修复后 `npm ci --ignore-scripts` 全新安装 exit 0。
- 前端 Plan 4 Task 2 之后：`138 passed`（14 个测试文件），`tsc --noEmit` 通过。
- Plan 4 Task 3：聚焦测试 `npm test -- src/App.test.tsx src/components/ConfirmDeleteDialog.test.tsx` 为 `25 passed`（2 个测试文件）；完整前端套件 `npm test` 为 `147 passed`（15 个测试文件）；`npm run build` 通过。构建输出仅有 Vite 对 PDF.js 主包大于 500 kB 的提示，没有构建或类型错误。
- Plan 4 Task 4：聚焦测试 `npm test -- src/pdfjs.test.ts src/components/PdfPageView.test.tsx src/components/PdfReader.test.tsx` 为 `13 passed`（3 个测试文件）；完整前端套件 `npm test` 为 `149 passed`（16 个测试文件）；`npm run build` 通过。构建输出仅有 Vite 对 PDF.js 主包大于 500 kB 的提示，没有构建或类型错误。
- Plan 4 Task 5：聚焦测试 `npm test -- src/components/pdfSelection.test.ts src/components/SelectionToolbar.test.tsx src/components/AnnotationOverlay.test.tsx src/components/PdfReader.test.tsx src/workspace` 为 `52 passed`（6 个测试文件）；完整前端套件 `npm test` 为 `160 passed`（19 个测试文件）；`npm run build` 通过。构建输出仅有 Vite 对 PDF.js 主包大于 500 kB 的提示，没有构建或类型错误。
- Plan 4 Task 6：后端 `tests/integration/test_annotations_api.py` 为 `6 passed`；聚焦前端 `npm test -- src/components/InlineAssistantPopover.test.tsx src/components/NotesPanel.test.tsx src/workspace` 为 `45 passed`（4 个测试文件）；完整前端套件为 `168 passed`（21 个测试文件）；`npm run build` 通过。构建保留既有 PDF.js 大于 500 kB 提示。

## 7. 准确的接手步骤

1. 确认位于 `codex/reader-workspace-v2`，并核对第 4 节所列的三个未跟踪环境/指南文件保持原样。
2. 从 Plan 4 Task 7 继续：右侧工作台三 tab、固定聊天框统一当前模型、可调整双栏与小屏切换；不要回退 Task 6 的 anchors 合同或将生成笔记写入主聊天历史。
3. 依次执行 Task 7–8。Task 8 编辑 CSS 前重读 `$design-taste-frontend`（`C:/Users/Admin/.codex/skills/taste-skill/SKILL.md`）并按其 preflight 记录检查；设计参数固定 `DESIGN_VARIANCE=4`、`MOTION_INTENSITY=3`、`VISUAL_DENSITY=7`。
4. 每个任务严格按计划的测试命令验证后再提交，提交信息以计划为准。
5. Plan 4 完成后进入 Plan 5。

## 8. 下一阶段清单

- Plan 4 Task 7：右侧工作台三 tab、固定聊天框统一当前模型、可调整双栏与小屏切换。
- Plan 4 Task 8：视觉系统、中文化、可访问性预检、完整前端验证与生产构建。
- Plan 5：中文 README、架构/ADR、Playwright 桌面与移动端、最终发布证据。

## 9. 不可破坏的约束

- MarkItDown 负责语义文本，PyMuPDF 负责 page/bbox/渲染；citation 几何不能依赖 MarkItDown。
- Citation Guard 完成前，不向 UI 流式输出或持久化未经验证的论文回答。
- API 密钥不进入数据库、响应、快照、缓存 key、日志或异常文本。
- 模型档案和幂等锁只支持单服务进程；没有新设计前不宣称多 worker 安全。
- 删除只访问 `Settings.papers_dir` 和 `Settings.trash_dir` 内经校验的路径，不删除全局模型档案或 secret。
- 最终 UI 面向中文用户；不增加未确认的总结、评价、相关推荐、分享等范围外标签。
- 未获得明确授权前，不推送、合并、删除分支或创建 PR。
- `sources/` 为只读参考目录；不得暂存环境提供的 `AGENTS.md`。

## 10. 完成定义

只有五份计划全部完成、后端/前端/浏览器测试有新鲜成功输出、中文文档与真实 API 一致、Git 中没有 PDF/SQLite/secret/本地数据后，才能进入合并或发布阶段。部分演示或单项测试通过不等于项目完成。
