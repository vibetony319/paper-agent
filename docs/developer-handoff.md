# paper-agent 开发交接

更新时间：2026-09-16（Asia/Shanghai）

## 最新进度（接手先读）

### 上下文占用指示 + paperqa2 语义检索（2026-09-16 第二轮）

- **上下文占用指示（对话区右下角）**：显示"下一次请求"的估算占用——系统提示 + 最近 6 条持久化消息 + 1000 token 工具定义开销，与自动压缩（ADR 0004）同一估算器。后端 `context_budget.py` 新增 `ContextUsage`/`context_usage()`；`agent_runtime.conversation_usage()` 复用历史组装逻辑。三条通道：非流式 `AgentMessageResponse.context_usage`、SSE `completed` 事件 payload 新增 `context_usage` 字段、新只读端点 `GET /api/papers/{paper_id}/agent/conversations/{conversation_id}/context-usage?model_profile_id=...`（前端在会话建立/切档案时刷新）。档案未配置 `context_length` 时四项上限字段为 null，前端显示「上下文 X（未设上限）」。前端 `ChatComposer` 提示行改 flex，右侧 `ContextUsageBadge`：≥75% 黄（压缩预警）、≥90% 红（硬截断）、title 说明估算口径与阈值。
- **paperqa2 语义检索（设计见 [ADR 0005](adr/0005-paperqa2-semantic-retrieval.md)）**：`services/paper_search.py` 的 `SemanticPaperSearchService` 对全部元素文本批量嵌入（paperqa2 `embedding_model_factory` 的 `st-` 前缀 → sentence-transformers CPU 推理，默认 `st-paraphrase-multilingual-MiniLM-L12-v2`，`PAPER_AGENT_EMBEDDING_MODEL` 可覆盖），余弦 top-k。索引持久化到 `.paper-agent/search-indexes/{paper_id}.npz` + JSON sidecar（指纹=元素 ID/序/全文 SHA-256、模型名），惰性重建：指纹或模型不匹配即重建（首问多花数秒），进程内 LRU 缓存 4 篇，单锁串行嵌入调用。paperqa2/嵌入失败 → warning + 空结果，`search_paper` 回退纯子串。
- **`search_paper` 合并检索**：子串命中优先（`search_mode: "substring"`、score 1.0）、语义补足（`search_mode: "semantic"`、score=余弦相似度）、去重、按 `limit` 截断；跨论文 ID 丢弃；`evidence_element_ids`（仅 located）契约不变。工具描述与系统提示更新为语义检索说明（含跨语言提示）。
- **移除首末页固定投喂**：删除 `agent_runtime` 的 overview 关键词检测、`elements[:3] + elements[-1:]` 播种块与 `_seeded_evidence_message`；工具循环恒 `range(MAX_TOOL_TURNS)`。所有问题统一走"模型自己搜索→读→答"，中文概括类问题可经语义检索命中英文论文的摘要/结论。
- **Windows 安装坑**：本机 `LongPathsEnabled=0` 且 worktree 路径深，`pip install "paper-qa[local]"` 在 litellm 深层文件上撞 MAX_PATH 失败。解法已写入 README：单独下载 litellm wheel，Python 以 `\\?\` 前缀解压进 site-packages，再重跑 pip（其余包路径浅，正常安装）。未改系统注册表。
- 测试：集成测试的 fake runtime 注入 substring-only `PaperToolRegistry`（避免 CI 加载真实嵌入模型）；`test_paper_search.py` 以确定性假嵌入向量覆盖建索引/磁盘复用/指纹失效重建/模型切换/失败回退/空文本（6 项）；`test_agent_tools.py` 新增合并/截断/服务失败 3 项；overview 测试改写为"模型经 search_paper 自行检索后作答、无播种 system 消息"。上下文占用 3 个单元 + 3 个路由测试。

### 章节解析、弹窗交互与上下文管理（2026-09-16）

- **章节解析重写（`parsers/pymupdf_stage1.py`）**：PDF 书签大纲（`get_toc`）有效条目 ≥3 时作为权威来源（标题、顺序、层级）；无大纲时的启发式修复——全大写只统计 ASCII 字母（中文行不再误判）、加粗按"加粗字符占比 ≥60%"（作者行/arXiv 水印/STEP/图注不再入列）、跳过旋转文本（侧边水印）、首页标题区横幅守卫、垃圾标题后置过滤。`Section` 新增 `level`（迁移 7），前端章节导航按层级缩进。实测 InfoGain-RAG 论文从 49 条（含大量垃圾）降为 21 条干净章节。**需重新上传论文才应用新解析**（无重新解析入口，避免破坏已有高亮/笔记挂接）。
- **解释/翻译弹窗可拖拽缩放（`InlineAssistantPopover.tsx`）**：标题栏拖拽（pointer capture，视口 12px 边距 clamp，键盘方向键微移 8/24px）、右下角缩放把手（最小 240×160）；用户交互后停止自动锚定、只保持视口内；宽高持久化到 localStorage。指针事件 stopPropagation，不影响阅读器的选区捕获契约（region aria-label、按钮名、z-index、流式渲染均不变）。
- **模型档案 token 配置（迁移 6）**：`model_profiles` 新增可空 `context_length`（1,000–10,000,000）与 `max_output_tokens`（1–200,000，须小于上下文）；留空=不限制。全链路：域校验、存储、API（Create/Patch/Response，PATCH 显式 null 清除）、环境变量 `PAPER_AGENT_REASONING_CONTEXT_LENGTH`/`PAPER_AGENT_REASONING_MAX_OUTPUT_TOKENS`、前端设置表单两个数字输入（正整数 + 输出<上下文校验，编辑回填，列表展示配置值）。字段不进模型快照、不清空能力探测；6 个既有测试夹具文件同步补齐新字段（`npm run build` 的类型检查覆盖测试文件，缺字段会构建失败）。
- **`max_output_tokens` 下发 provider**：`VllmModelConfig.max_output_tokens` 非空时以 `max_tokens` 传入全部四处 `chat.completions.create`（结构化两个分支、普通、流式）与 `request_tool_turn`；未配置省略参数，保持提供方默认。
- **Agent 上下文压缩（`services/context_budget.py` + `agent_runtime.py`，设计见 [ADR 0004](adr/0004-context-compaction.md)）**：token 估算不用 tokenizer（CJK ≈1/字、其余 ≈4 字符/token，+1000 工具定义开销）；有效预算 = `context_length − (max_output_tokens 或 8192)`，超 0.75× 触发压缩——head 系统块与当前用户消息之间的历史折叠为一条摘要 system 消息（同一 client 非流式生成，中文提示词保留论文主题/事实/证据元素 ID/当前任务），工具循环中途压缩保留末尾 assistant(tool_calls)+tool 配对；摘要失败降级为丢弃最旧完整对话轮的硬截断（至 0.9×）。压缩是请求组装层瞬态行为：不落库、不影响 `request_id` 重放。未配置 `context_length` 完全不压缩不截断。
- **测试中发现并修复的缺陷**：`partition_messages` 尾部回溯把 assistant(tool_calls) 划入 middle 而非 tail（off-by-one），压缩会拆散 OpenAI 工具配对、留下孤儿 tool 消息被 API 拒绝；修复为 `tail_start = probe - 1` 并有回归测试。
- 验证：后端 **450 项通过**（新增 context_budget 16 项、压缩行为 6 项、vllm/env 9 项、档案边界 5 项）；前端 **229 项通过**（弹窗拖拽缩放 6 项、设置表单 5 项新增）、`tsc` 无错；`npm run build` 通过。真实论文需删除重传以应用新章节解析。

### 选区辅助可点击性与流式失败恢复（2026-09-15 第二轮）

- **解释/翻译不再静默无响应**：此前没有可用模型时这两个按钮被 `disabled`，只有 hover 才显示「暂不可用」，点下去毫无反馈（其他三个按钮不经过模型判定，所以表现成"只有这两个失效"）。现在按钮始终可点，缺模型时由浮层给出中文提示；等待首个增量时显示「正在生成解释/翻译…」，不再是一个只有「取消」按钮的空面板。
- **请求 ID 统一走 `newRequestId()`**（`web/src/api/ids.ts`）：`crypto.randomUUID` 只在安全上下文存在，用非 localhost 的 http 地址打开时点击会在事件处理器里抛 `TypeError`（表现为点了没反应）。Agent、笔记、高亮、选区辅助共 5 处调用点改为该封装，缺失时回退随机串。
- **工具栏按下不再有清空选区的风险**：`.selection-toolbar` 加 `user-select: none`，容器 `onMouseDown` preventDefault、`onPointerUp` 阻止冒泡，避免按下按钮导致选区塌陷 → `selection/clear` → 工具栏在 click 之前被卸载。Chromium 实测该竞态不触发，属防御性修复（覆盖触摸与其他浏览器）；`AnnotationOverlay` 的 document 级 pointerdown（关闭高亮菜单）不受影响。
- **流式中断保留已生成内容**：`conversation/stream-failed` 不再把 `streaming` 置空，而是标记 `interrupted`；聊天区保留半截回答并提示「回答已中断，以上为已生成的部分。」并可直接重新提问。被更新请求取代的旧请求失败不再覆盖新答案的错误提示（此前会误报通用失败文案）。
- **失败原因可见且不泄漏上游文本**：`client.ts` 新增 `apiErrorMessage()`，按错误码映射中文原因（与后端 `_STREAM_ERROR_DETAILS` 对应）；服务端 `detail` 原文仍然不回落给用户。后端新增错误码 `answer_unavailable`（回答已生成，但引用信息无法解析）。
- **后端流健壮性与可观测性**：`_streamed_completion` 跳过只带 usage、`choices` 为空的收尾分片（原会抛 `IndexError`，把已经流出内容的流打断）；`event_stream` 内部兜住异常并以错误帧结束，不再让 `HTTPException` 逃逸导致 SSE 在响应中途断开；Agent 回合与选区辅助失败时把异常类型写入 stderr（仅类型，不含 provider 文本）——此前项目没有日志配置，流式故障在磁盘上不留任何痕迹。
- **本轮"流式输出一半报错"的实际原因**：ARK 档案 `glm-5.3` 返回 `429 AccountQuotaExceeded`（5 小时配额用尽，按响应中提示的时间重置），配额耗尽会掐断已经在传输的流；同 provider 的 `deepseek-flash` 实测正常。切换模型即可绕过，与代码无关的部分已在上一条完成兜底。
- 验证：后端 400 项、前端 217 项、`tsc` 无错、生产构建通过；真实 DeepSeek 三轮流式对话 + 重复请求重放 + 与非流式端点一致性通过；Playwright 完整套件 6 项全部通过（`selection-assist-regression` 与修正后的 `reader-workspace`，桌面与小屏两个 project），此前遗留的失败项随本轮一并清除。
- **顺带修复的既有可访问性问题**：修复后的 `reader-workspace` 审计暴露 `--ink-faint` 在 `--paper` 背景上只有 4.46:1（WCAG AA 要求 4.5:1）。该 token 由 `#6f7672` 调深为 `#6b726e`（4.73:1，白色背景 4.93:1），仍明显浅于 `--ink-soft`，三级墨色层次不变。
- 顺带清理：`web/e2e/reader-workspace.spec.ts` 移除已删除的图谱步骤、改等待 `/agent/messages/stream` 并在路由处理中缓冲流后解析 `completed` 帧；删除论文弹窗文案不再提"知识图谱"；仓库根目录 4 个针对已修复缺陷的临时验证产物（`capture_calls.json`、`capture_overview_failure.py`、`verify_fix_real*.py`）已删除。
- **新增 `scripts/` 流式验证脚本**：`verify_stream_http.py`（自带兼容桩与合成 PDF，无网络运行，验证分片确实增量到达而非一次性缓冲）与 `verify_stream_real.py`（对真实 provider 验证同一套契约，provider 与密钥只取自 `PAPER_AGENT_REASONING_*` 环境变量，`--pdf` 可指定真实论文）。两者都不写用户运行数据，用法见[测试策略](testing.md)。

### 图谱移除与 Markdown 流式回答（2026-09-15）

- **图谱功能整体删除**：路由、`GraphConstructionService`、图谱抽取、`storage`/`database`/`domain` 里的节点边与证据表、Agent 的四个图谱工具、`PaperSummary` 的 stage2/3 字段，以及前端的图谱状态、`@xyflow/react` 与 `elkjs` 依赖全部移除。`PAPER_DELETE_ORDER` 不再包含图谱表，旧库里的这些表会保留但不再被读写。
- **Agent 回答改为 Markdown 并支持流式**：新增 `POST /api/papers/{paper_id}/agent/messages/stream`（事件 `started`/`delta`/`completed`/`error`），与原有非流式端点共用 `_prepare_turn`，差别只在回答怎么返回。格式约定集中在 `services/answer_format.py`：`[[element_id]]` 内联引用、可选首行 `[[status:insufficient_evidence]]`、单独一行 `---` 之后是背景知识。整段回答到达前不写库，中断的流不会留下半条助手消息。
- **前端**：流式文本先显示在消息挂载点，完成后进入 exchanges；新增无依赖的 Markdown 渲染器 `MarkdownText.tsx`，把已知的 `[[element_id]]` 渲染成可点击的页码标记，未知 ID 保持纯文本。
- **门禁调整**：Agent 回合现在只要求 `tool_calling`（Markdown 回答不再需要 `response_format`），`structured output` 只作为档案页的能力展示。
- **真实模型联调发现并修复的三个问题**：思考模式模型要求把 assistant 工具轮的 `reasoning_content` 回传，否则 400（现已保存并回传）；provider 可能忽略 `parallel_tool_calls=False` 一次返回多个调用，超预算批次现在截断到上限并基于已有证据作答，不再整轮失败；模型给出无效工具参数（例如不存在的 `section_id`）现在作为工具结果反馈给它自我纠正，而不是 502。
- 验证：后端 **398 项通过**、前端 **208 项通过**；真实 DeepSeek（`deepseek-flash`）四轮连续流式对话通过——事件顺序、增量分片、内联引用落库、`---` 背景分段、重复请求重放、与非流式端点一致性均符合预期；另有本地真实 HTTP（uvicorn + 兼容桩）验证分片确实是增量到达。上文“主 Agent 仍使用非流式 JSON / 等待完整回答”的描述由本条取代。
- 已知质量项（本轮未改行为）：概览类问题（“这篇论文讲了什么”）只预读开头三个与末尾一个有定位元素，模型常因此答复证据不足；要更好的概览需要改选取策略或允许概览路径继续检索。

### 聊天布局调整（2026-09-14）

- 用户问题发送后立即显示在消息区右侧，助手生成状态显示在其下方；输入框立即清空，失败后恢复问题供重试。
- 输入框固定在底部，保留模型切换和选文附件；Enter 发送、Shift+Enter 换行，输入法组合期间不触发发送。
- RightPanel 提供临时消息挂载点，ChatComposer 将等待/本地问候显示到消息区；请求成功后由已有 exchanges 显示正式回答，切换论文继续使用请求版本隔离。
- 前端全量 201 项通过，新增消息挂载与键盘交互回归后专项 9 项通过；生产构建通过，浏览器已检查真实阅读区中的消息布局。主聊天仍等待后端完整回答，尚未实现逐字流式输出。

### 阅读交互修复（2026-09-14）

- 解释/翻译使用独立的选文、位置、模型快照；每次打开生成新的组件身份与请求，不随浏览器 selectionchange 消失。异常 Promise 会进入可重试状态，卸载时清理运行中的控制器。
- 中文概括类问题先经实际 read_element 工具读取开头三个及末尾一个有定位的元素（去重），再生成有引用的概括；这是局部概览，不应承诺已阅读全文。模型回答不再被 Citation Guard 替换；引用只作为可选的页面跳转，无法定位的引用会被隐藏。
- 纯问候由输入框本地提示回应，不调用论文检索、不持久化成论文结论。论文问题发送后立即显示问题与已等待秒数；仍是非流式最终回答，不假造工具阶段，也不在最终回答完成前提前展示答案。
- 验证：后端 443 项、前端 201 项、生产构建通过；此前桌面/小屏专项浏览器 2 项通过。旧完整浏览器套件未通过（包括已移除图谱入口与既有可访问性检查），需随当前产品界面另行同步；未修改产品以迎合旧脚本。

### 真实接口兼容修复（2026-09-11，验收基线之后）

配置的模型接口拒绝 `json_schema`、拒绝只有系统消息的工具探测，并可能忽略禁止并行调用参数。现增加明确不支持 Schema 时才启用的 JSON 对象兼容路径与本地 Schema 校验；工具探测带用户消息；实际只读工具批次按顺序执行，总调用预算仍为 6，拒绝重复 ID 和超预算批次。能力 UI 使用“检测未通过”，不再误称模型“不支持”。详情见[模型服务](model-services.md)。

真实接口三项能力检测通过；独立临时数据中的合成论文对话返回 HTTP 200、grounded 和 3 条引用。未向真实用户论文或会话写入测试消息。普通 JSON 模式不保证每次模型输出都正确；服务只检查响应是否符合传输结构，不对回答内容做引用正确性判定。

### 模型回答展示策略（2026-09-14）

- 已移除“引用/证据校验失败就返回固定拒答”的行为。模型返回的 `paper_answer`、`status` 和 `background_explanation` 会直接展示并持久化。
- `citation_element_ids` 现在只是可选的页面跳转链接：重复 ID 会去重，无法定位到当前论文的 ID 会被忽略；这些处理不会改变模型正文。
- 仍保留 JSON 结构解析和敏感错误收敛，目的是处理接口协议异常，不是判断回答是否正确。主 Agent 仍使用非流式 JSON；解释/翻译继续使用独立 SSE。
- 相关回归测试覆盖：无工具证据仍保留回答、背景说明保留、图谱 ID 不导致拒答、结构化响应异常只返回安全错误。实现见 `src/paper_agent/services/citation_guard.py` 与 `src/paper_agent/services/agent_runtime.py`。

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
- Task 7 的 `GraphBuildInput` 与完整 `AskAgentInput` 均强制 `model_profile_id` 和 `request_id`。工作区为每个聊天请求生成请求 ID（现为 `web/src/api/ids.ts` 的 `newRequestId()`），因此不再发送旧的空 POST body。
- 当前模型选择器只存在于固定 `ChatComposer`。切换模型不会重置当前会话，选区解释/翻译使用同一选择。无模型时发送被禁用并给出中文提示；解释/翻译保持可点，由浮层提示先选择可用模型（见"选区辅助可点击性与流式失败恢复"）。回答范围（仅基于论文/允许背景知识）选择已整体移除，Agent 固定仅基于论文作答，接口不再接受 `mode` 字段。
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
- 主 Agent 使用非流式结构化响应；模型正文不再经过引用正确性拦截，引用只作为可选页面跳转。
- API 密钥不进入数据库、响应、快照、缓存 key、日志或异常文本。
- 模型档案和幂等锁只支持单服务进程；没有新设计前不宣称多 worker 安全。
- 删除只访问 `Settings.papers_dir` 和 `Settings.trash_dir` 内经校验的路径，不删除全局模型档案或 secret。
- 最终 UI 面向中文用户；不增加未确认的总结、评价、相关推荐、分享等范围外标签。
- 未获得明确授权前，不推送、合并、删除分支或创建 PR。
- `sources/` 为只读参考目录；不得暂存环境提供的 `AGENTS.md`。

## 10. 完成定义

只有五份计划全部完成、后端/前端/浏览器测试有新鲜成功输出、中文文档与真实 API 一致、Git 中没有 PDF/SQLite/secret/本地数据后，才能进入合并或发布阶段。部分演示或单项测试通过不等于项目完成。
