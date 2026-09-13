# paper-agent 开发交接

更新时间：2026-09-11（Asia/Shanghai）

## 最新进度（接手先读）

### 真实接口兼容修复（2026-09-11，验收基线之后）

配置的模型接口拒绝 `json_schema`、拒绝只有系统消息的工具探测，并可能忽略禁止并行调用参数。现增加明确不支持 Schema 时才启用的 JSON 对象兼容路径与本地 Schema 校验；工具探测带用户消息；实际只读工具批次按顺序执行，总调用预算仍为 6，拒绝重复 ID 和超预算批次。能力 UI 使用“检测未通过”，不再误称模型“不支持”。详情见[模型服务](model-services.md)。

真实接口三项能力检测通过；独立临时数据中的合成论文对话返回 HTTP 200、grounded 和 3 条引用。未向真实用户论文或会话写入测试消息。普通 JSON 模式不保证每次模型输出都正确，返回仍须通过本地校验和 Citation Guard，不会直接放行无效答案。

Plan 5 的文档、确定性浏览器验收与全新依赖安装验证已完成。中文 README、贡献指南、专题开发文档基线为 `526d802`；本轮补充三份 ADR、E2E 服务器、Playwright 脚本及故障修复。具体命令、环境和已知限制见[整体验收记录](release-validation.md)。

- 修复永久删除在数据库提交后 marker 写入失败时错误恢复源 PDF 的问题；新增故障注入测试证明提交前还原、提交后保留恢复标记并继续清理。
- 最新后端全量：**416 项通过**；前端 Vitest：**206 项通过**；生产构建通过（仍有大包体积提示）。
- 浏览器脚本位于 `web/e2e/`，专用服务器位于 `tests/e2e/`；仅替换模型传输，不绕过生产服务和持久化。使用本机 **Edge** 的桌面/小屏 **4 项全部通过，18.6 秒，退出码 0**，包含键盘与 axe 严重/关键问题检查；不是固定版本 Chromium 的验证记录。
- 浏览器发现并修复高亮容器的 ARIA 角色，以及手写笔记编辑器自动聚焦后选区丢失、弹框消失的问题。现在笔记弹框保存独立锚点快照，并有单元与浏览器双层回归测试。
- 2026-09-11：全新 Python 3.12.14 虚拟环境安装成功、依赖一致性检查通过，**416 项通过**；前端 `npm ci` 后 **206 项通过**，构建通过。固定 Chromium 153.0.8010.12（revision 1243）桌面/小屏连续两轮 **8 项通过，30.4 秒，退出码 0**。
- 已检查桌面和小屏截图，README 截图仅使用运行时合成论文。受限环境下测试进程收尾曾挂起，在获准的非受限环境重跑后正常退出。正常服务关闭会清理临时数据，强制结束可能留下系统临时目录。
- 独立静态审查未发现本轮实际缺陷；文档链接、空白检查通过。移除了旧计划文档中不应出现的凭证文本，但未改写历史；仍有效的凭证必须由持有者更换。
- 下一步：由用户决定推送/合并，以及是否安排真实 vLLM 联调；可继续优化前端包体积。创建高亮会清除临时选区；重复拖选前可按 Escape 清除残留选区，浏览器脚本也遵循此交互。

接手前先检查 Git 状态与最新提交并保留已有改动。下文是 2026-08-24 历史交接快照，旧的“未开始”“工作树干净”及测试数量不代表当前状态。`AGENTS.md` 仍是不可暂存的环境文件。

## 1. 当前结论

项目已完成“论文阅读工作区升级”的 Plan 1–4；Plan 5 尚未开始。

- 当前分支：`codex/reader-workspace-v2`
- 最新功能提交：`b349b8f feat: polish the chinese paper reading workspace`。
- Plan 1“多模型档案与调用追溯”：Task 1–7 全部完成并提交。
- Plan 2“批注、解释翻译与笔记记忆”：Task 1–7 全部完成并提交。
- Plan 3“论文及关联数据安全删除”：Task 1–6 全部完成并提交（含文档 `docs/data-model.md`）。
- Plan 4“中文论文阅读工作区前端”：Task 1–8 已完成并提交。Task 7 将阅读区改为可调整的双栏，小屏为保留状态的论文/工具切换；右侧固定为论文助手、知识图谱、笔记三 tab，聊天框固定在其下。Task 8 经全量前端测试和生产构建验证，完成暖灰/白纸/靛蓝/黄色批注视觉收敛、中文化、焦点与缩减动态、880px 边界一致性、右侧 tab 键盘模式、分隔条取消清理、笔记最新优先排序及 PDF 前端采集文档。聊天、图谱构建及既有选区辅助统一使用聊天框当前模型，响应和图谱构建均可显示持久模型快照。
- Plan 5“开发者文档与整体验收”尚未开始。

接手规则：当前所有工作均已提交，工作树干净；仍不要 reset、checkout、clean 或覆盖工作区。`AGENTS.md` 是环境文件，禁止暂存。

## 2. 事实来源

按以下顺序阅读：

1. [2026-08-21 设计规格](superpowers/specs/2026-08-21-reading-workspace-annotations-model-profiles-design.md)
2. [总实施计划](superpowers/plans/2026-08-21-reading-workspace-program.md)
3. [模型档案计划](superpowers/plans/2026-08-21-model-profiles-provenance.md)（已全部完成）
4. [批注与笔记记忆计划](superpowers/plans/2026-08-21-annotations-note-memory.md)（已全部完成）
5. [论文删除计划](superpowers/plans/2026-08-21-paper-deletion.md)（已全部完成）
6. [前端工作区计划](superpowers/plans/2026-08-21-reader-workspace-frontend.md)（Task 1–8 已完成）
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
| Plan 4 Task 7 统一当前模型、右侧工作台与可调整双栏 | `0ab07a0 feat: use one model across the reading workspace` | 已提交 |
| Plan 4 Task 8 视觉系统、中文化与可访问性预检 | `b349b8f feat: polish the chinese paper reading workspace` | 已提交并验证 |

## 4. 当前未提交内容

Plan 4 的生产代码、测试和文档均已提交。仅三个未跟踪文件：`AGENTS.md`（环境提供，禁止暂存）、`CONTRIBUTING.md`、`docs/README.md`（指南与导航，历史上一直未跟踪，保持原样，是否纳入版本库留待 Plan 5 决定）。`.superpowers/sdd/` 下的任务报告是本地执行记录，不纳入提交。

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
- Task 7 的 `GraphBuildInput` 与完整 `AskAgentInput` 均强制 `model_profile_id` 和 `request_id`。工作区在每个聊天或图谱构建请求内部使用 `crypto.randomUUID()`，因此不再发送旧的空 POST body。
- 当前模型选择器只存在于固定 `ChatComposer`。切换模型不会重置当前会话；聊天、图谱构建和选区解释/翻译均使用这一选择。无模型时发送、两类图谱构建和选区辅助均禁用并给出中文提示。回答范围（仅基于论文/允许背景知识）选择已整体移除，Agent 固定仅基于论文作答，接口不再接受 `mode` 字段。
- `问助手` 将当前 `TextAnchorDraft` 作为可移除附件写入聊天框并聚焦，不自动发送；成功后清除附件，失败保留以供重试。Agent 消息显示其不可变模型徽章，笔记引用与论文引用分开并可通过持久 anchor 或元素来源定位。
- `ResizableSplit` 默认 62%，限制 45%–78%，持久化键为 `paper-agent:reader-split`；键盘左右键每次调整 2%。小于 880px 时显示论文/工具切换而不卸载任一子树。

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
- Plan 4 Task 7：聚焦前端 `npm test -- src/api/client.test.ts src/components/ChatComposer.test.tsx src/components/ResizableSplit.test.tsx src/components/WorkspaceShell.test.tsx src/components/GraphPanel.test.tsx src/workspace` 为 `85 passed`（7 个测试文件）；完整前端套件 `npm test` 为 `175 passed`（23 个测试文件）；`npm run build` 通过。构建保留既有 PDF.js 大于 500 kB 提示。
- Plan 4 Task 8：完整前端套件 `npm test` 为 `188 passed`（24 个测试文件）；`npm run build` 通过。构建保留既有 PDF.js 大于 500 kB 提示；`git diff --check` 无空白错误。

## 7. 准确的接手步骤

1. 确认位于 `codex/reader-workspace-v2`，并核对第 4 节所列的三个未跟踪环境/指南文件保持原样。
2. 进入 Plan 5 前先保留 Plan 4 的 anchors 合同，不将生成笔记写入主聊天历史。
3. Plan 5 的浏览器验收应覆盖桌面与 880px 以下的阅读器、选区工具栏和固定聊天框；不需要重做 Task 8 的视觉 token 审计。
4. 每个任务严格按计划的测试命令验证后再提交，提交信息以计划为准。
5. Plan 4 完成后进入 Plan 5。

## 8. 下一阶段清单

- Plan 5：中文 README、架构/ADR、Playwright 桌面与移动端、最终发布证据。

## 9. 不可破坏的约束

- Stage1（`pymupdf_stage1.py`）用 PyMuPDF span 字号/加粗启发式识别章节标题并组装段落，段落与 Stage0 文本块同源（`get_text` blocks/dict 分块一致），因此精确对齐；citation 几何不依赖任何 Markdown 中间产物。章节标题启发式：字号大于正文（比例阈值）、加粗、全大写、或整行命中标准章节标签（Abstract/References/编号标题等）；第 1 页首个正文字号行之前的内容视为标题/作者横幅，不作为章节。
- Citation Guard 完成前，不向 UI 流式输出或持久化未经验证的论文回答。
- API 密钥不进入数据库、响应、快照、缓存 key、日志或异常文本。
- 模型档案和幂等锁只支持单服务进程；没有新设计前不宣称多 worker 安全。
- 删除只访问 `Settings.papers_dir` 和 `Settings.trash_dir` 内经校验的路径，不删除全局模型档案或 secret。
- 最终 UI 面向中文用户；不增加未确认的总结、评价、相关推荐、分享等范围外标签。
- 未获得明确授权前，不推送、合并、删除分支或创建 PR。
- `sources/` 为只读参考目录；不得暂存环境提供的 `AGENTS.md`。

## 10. 完成定义

只有五份计划全部完成、后端/前端/浏览器测试有新鲜成功输出、中文文档与真实 API 一致、Git 中没有 PDF/SQLite/secret/本地数据后，才能进入合并或发布阶段。部分演示或单项测试通过不等于项目完成。
