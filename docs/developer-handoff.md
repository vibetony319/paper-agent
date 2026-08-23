# paper-agent 开发交接

更新时间：2026-08-23（Asia/Shanghai）

## 1. 当前结论

项目正在按“论文阅读工作区升级”总计划推进，前两个子计划已经完成，第三个子计划进行到最后的 API 接线与验证。

- 当前分支：`codex/reader-workspace-v2`
- 当前 HEAD：`38b4c15 feat: recover paper deletion across process crashes`
- Plan 1“多模型档案与调用追溯”：Task 1–7 全部完成并提交。
- Plan 2“批注、解释翻译与笔记记忆”：Task 1–7 全部完成并提交。
- Plan 3“论文及关联数据安全删除”：Task 1–3 已提交；Task 4–5 的写路径协调、删除 API 和启动恢复已在工作区实现，尚未运行测试或提交。
- Plan 4“中文论文阅读工作区前端”和 Plan 5“开发者文档与整体验收”尚未开始。

最重要的接手规则：**不要 reset、checkout、clean 或覆盖当前工作区。** 未提交的 Plan 3 Task 4–5 改动只存在于本工作区；从 GitHub 克隆 `38b4c15` 不会得到这些改动。

## 2. 事实来源

按以下顺序阅读：

1. [2026-08-21 设计规格](superpowers/specs/2026-08-21-reading-workspace-annotations-model-profiles-design.md)
2. [总实施计划](superpowers/plans/2026-08-21-reading-workspace-program.md)
3. [模型档案计划](superpowers/plans/2026-08-21-model-profiles-provenance.md)（已全部完成）
4. [批注与笔记记忆计划](superpowers/plans/2026-08-21-annotations-note-memory.md)（已全部完成）
5. [论文删除计划](superpowers/plans/2026-08-21-paper-deletion.md)（当前执行中）
6. [贡献与开发指南](../CONTRIBUTING.md)
7. [模型服务文档](model-services.md)、[PDF 批注契约](pdf-annotations.md)、[笔记记忆](note-memory.md)
8. 本文档

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

## 4. 当前未提交内容

工作树包含 Plan 3 Task 4–5 的第一版实现，尚未运行测试，也未经过审查。文件如下：

```text
src/paper_agent/app.py
src/paper_agent/routes/agent.py
src/paper_agent/routes/annotations.py
src/paper_agent/routes/graph.py
src/paper_agent/routes/papers.py
src/paper_agent/schemas.py
src/paper_agent/services/paper_deletion.py
tests/integration/test_paper_deletion_api.py（新增，未跟踪）
```

已实现方向：

- `create_app()` 创建 `PaperOperationCoordinator` 和 `PaperDeletionService`，在注册路由前执行 `recover_pending()`。
- Agent、图谱、高亮、笔记写路径用 `coordinator.operation(paper_id)` 包裹；选区解释/翻译 SSE 在生成器内部持有 operation。
- 删除 API 为 `DELETE /api/papers/{paper_id}`，请求体 `{"confirmation": "<paper-id>"}`；错误码固定为 `PAPER_NOT_FOUND`、`PAPER_BUSY`、`DELETE_CONFIRMATION_MISMATCH`、`DELETE_RECOVERY_REQUIRED`。
- `recover_pending()` 对每个 marker 单独容错，单个损坏或占用的 marker 不会阻止其他论文恢复。
- 新增集成测试覆盖：删除全链路、确认不匹配、重复删除、活跃选区流期间 `PAPER_BUSY`、启动恢复先于路由注册。

## 5. 关键裁决与偏离

- 批注计划原文写“迁移 2”，但迁移 2 已被 Plan 1 Task 5 冻结使用。批注 schema 实际使用迁移 3–5：迁移 3 建七张批注表，迁移 4 为 `notes`/`highlights` 增加幂等 `request_id` 与部分唯一索引，迁移 5 为 `text_anchor_rects` 增加 `paper_id`。
- 计划没有给高亮和笔记的 `request_id` 持久列；为了诚实支持“重复请求返回同一对象、冲突字段报错”，增加了迁移 4。
- live metadata 中 `conversation_message_note_citations.note_id` 不设外键，允许笔记删除后历史消息保留悬空引用并在响应中标记 `available=false`。迁移 3 的冻结 DDL 本来就无此外键，两者现在一致。
- 笔记检索的 `manual +1` 只在基础词项重合或与选区同页时参与排序，避免所有手写笔记永远进入上下文。
- 部署边界仍是单用户、单服务进程；协调器、幂等锁、模型使用租约和删除恢复状态都是进程内机制。

## 6. 最新验证证据

验证命令沿用共享虚拟环境，并因本机 pytest 临时目录权限问题需要覆盖 basetemp：

```powershell
& "C:\Users\Admin\.codex\.chatgpt-projects\g-p-6a76dd5037e88191acdf69ce4d033042\.venv\Scripts\python.exe" -m pytest -q -W error -o pythonpath=src -p no:cacheprovider --basetemp "C:\Users\Admin\AppData\Local\Temp\paper-agent-pytest-515d"
```

新鲜结果：

- `bcbbf2c` 之后、Plan 3 开始前：完整后端 `383 passed in 82.32s`。
- Plan 3 Task 2 聚焦回归：迁移、存储、批注存储 `67 passed`。
- Plan 3 Task 3 删除服务：`9 passed`。
- Plan 3 Task 4–5 尚未运行任何测试；特别是并发流测试和完整后端回归必须在提交前补齐。

## 7. 准确的接手步骤

1. 确认位于 `codex/reader-workspace-v2`，HEAD 是 `38b4c15`，并看到第 4 节的未提交文件和未跟踪 `AGENTS.md`。
2. 先用 `git diff --binary 38b4c15 > %TEMP%\plan3-task45.patch` 保存当前差异副本；补丁只用于本地恢复，不要提交。
3. 审查 `git diff 38b4c15`，重点检查：写路径 operation 持有边界、SSE 生成器内的 context、删除 API 错误码、启动恢复的容错顺序。
4. 先运行：

```powershell
python -m pytest tests/integration/test_paper_deletion_api.py tests/integration/test_papers_api.py -q -W error -o pythonpath=src -p no:cacheprovider --basetemp <task-basetemp>
```

预期发现任何失败后先修复，再运行完整后端回归。确认干净后提交，建议信息 `feat: expose recoverable permanent paper deletion`。
5. 完成 Plan 3 Task 6：编写 `docs/data-model.md`，并在批注与笔记文档中补充删除语义链接。
6. 按总计划顺序进入 Plan 4（读取 `$design-taste-frontend` 后执行八个前端任务）和 Plan 5。

## 8. 下一阶段清单

- Plan 3 Task 6：数据模型 Mermaid ER 图、删除顺序与 marker 状态机文档。
- Plan 4：连续 PDF TextLayer、选区工具栏、高亮、解释/翻译浮层、中文双栏工作台、多模型切换、论文库和删除确认。
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
