# paper-agent 文档导航

本目录保存当前产品事实、开发入口、专题契约与规格/计划历史。新开发者不要只阅读根目录 README；先按以下顺序建立对当前代码的理解。

## 推荐阅读顺序

1. [当前开发交接](developer-handoff.md)：当前分支、验证记录与续作步骤。
2. [贡献与开发指南](../CONTRIBUTING.md)：安装、目录、迁移、安全与提交规则。
3. [系统架构](architecture.md)：运行边界、`app.state`、关键时序和并发限制。
4. [HTTP API 业务语义](api.md)：OpenAPI 的业务解释、稳定错误与幂等。
5. [本地开发](development.md) 与 [测试策略](testing.md)：可执行环境、测试层次和当前 E2E 状态。
6. [论文阅读工作区设计规格](superpowers/specs/2026-08-21-reading-workspace-annotations-model-profiles-design.md)：最终产品行为和架构边界。
7. [总实施计划](superpowers/plans/2026-08-21-reading-workspace-program.md)：子计划依赖与最终验收门禁。

## 当前专题文档

- [模型服务与模型档案](model-services.md)：请求作用域模型、能力、密钥与快照。
- [数据模型与删除恢复](data-model.md)：SQLite、迁移、所有权与永久删除。
- [PDF 文本批注后端契约](pdf-annotations.md)：TextLayer、单页归一化锚点、SSE 与笔记。
- [笔记记忆与 Agent 注入](note-memory.md)：6,000 字符预算、检索和 Citation Guard 边界。
- [后续路线图](roadmap.md)：尚未实现的 OCR、协作、跨页选区和可编辑图谱。

## 当前实施计划

必须按以下顺序执行，不要让前端依赖尚未稳定的临时 API：

1. [多模型档案与调用追溯](superpowers/plans/2026-08-21-model-profiles-provenance.md)
2. [批注、解释翻译与笔记记忆](superpowers/plans/2026-08-21-annotations-note-memory.md)
3. [论文及关联数据安全删除](superpowers/plans/2026-08-21-paper-deletion.md)
4. [中文论文阅读工作区前端](superpowers/plans/2026-08-21-reader-workspace-frontend.md)
5. [开发者文档与整体验收](superpowers/plans/2026-08-21-documentation-release-validation.md)

## 规格、计划与历史

以下文档记录 MVP 的实现过程，可用于理解已有代码，但不能覆盖 2026-08-21 的新规格：

- [MVP 设计](superpowers/specs/2026-08-08-paper-agent-design.md)
- [解析与持久化](superpowers/plans/2026-08-08-parsing-persistence.md)
- [知识图谱](superpowers/plans/2026-08-08-knowledge-graph.md)
- [Agent Runtime](superpowers/plans/2026-08-09-agent-runtime.md)
- [旧版 Web UI](superpowers/plans/2026-08-13-web-ui.md)

## 文档优先级

发生冲突时，按以下顺序判断：

1. 用户本次明确需求；
2. 2026-08-21 设计规格；
3. 当前子计划及其已记录的架构裁决；
4. 当前 OpenAPI 与已通过测试的公开接口；
5. 历史 MVP 文档。

实现如果改变公开接口，必须同时更新生产该接口的计划、消费方计划、测试和专题文档。
