# HTTP API 业务语义

**OpenAPI 是路径、HTTP 方法、请求/响应 schema 与状态码的结构性真相**：请从运行中的 `create_app().openapi()` 获取。本文拥有业务语义、稳定业务错误、幂等规则与示例；为防止手抄字段漂移，重复字段一律链接到 schema 名称而不逐项复刻。系统边界见 [系统架构](architecture.md)，批注锚点见 [PDF 文本批注后端契约](pdf-annotations.md)，模型档案见 [模型服务与模型档案](model-services.md)。

## 通用约定

- 所有 `{paper_id}`、`{profile_id}`、`{conversation_id}`、`request_id` 均为 UUID（文档元素、图节点和高亮/笔记 ID 的类型以 OpenAPI schema 为准）。
- `model_profile_id` 是模型型请求必填的**请求作用域**选择，不隐式使用默认档案。服务会保存不含密钥的 `ModelSnapshotResponse`（档案 ID、显示名、URL、模型名、修订号）。
- 除单独列出的稳定 `{code, detail}` 错误外，FastAPI 的验证与资源错误以 OpenAPI/路由当前响应为准。`model-profiles` 的请求验证固定为 `422 {code:"validation_error", detail:"模型档案请求无效。"}`。
- `request_id` 不是全局去重键，必须连同论文理解。相同标识只能重试同一业务请求；若请求体与已保存身份不一致，应生成新的标识，而不能要求服务器猜测意图。

## 健康检查（1 个操作）

| 方法与路径 | 用途与重要字段 | 成功响应 | 稳定错误 / 幂等 |
| --- | --- | --- | --- |
| `GET /health` | 进程存活检查。 | `200 {"status":"ok"}`。 | 无业务副作用，可安全重复。 |

## 论文（8 个操作）

| 方法与路径 | 用途与重要请求/响应字段 | 稳定错误 / 幂等 |
| --- | --- | --- |
| `GET /api/papers` | 列出 `PaperSummaryResponse`；含 `id`、原文件名、总体与 Stage 0/1/2/3 状态、可选模型快照与公开错误摘要。 | 只读，可重复。 |
| `POST /api/papers` | `multipart/form-data` 的 `file`；仅接受 `.pdf` 且内容以 `%PDF-` 开头。 | `201 PaperSummaryResponse`；无效上传 `422 Invalid PDF upload.`。不以请求键去重，重复上传会创建独立论文。 |
| `GET /api/papers/{paper_id}` | 获取一篇论文的阶段摘要。 | `404 Paper resource not found.`；只读。 |
| `DELETE /api/papers/{paper_id}` | 请求体 [`PaperDeleteRequest`](#schema-索引) 的 `confirmation` 必须等于路径论文 ID；执行永久删除。 | 成功 `204`。稳定错误是 `404 PAPER_NOT_FOUND`、`409 PAPER_BUSY`、`422 DELETE_CONFIRMATION_MISMATCH`、`500 DELETE_RECOVERY_REQUIRED`（均为 `{code,detail}`）。不是一般幂等删除：已删除后再次调用为 not found；忙碌时应稍后重试。 |
| `GET /api/papers/{paper_id}/document` | 返回 [`PaperDocumentResponse`](#schema-索引)：论文、页面、章节、元素与笔记。 | `404`；只读。 |
| `GET /api/papers/{paper_id}/source` | 返回原始 PDF，`application/pdf`。 | `404`（论文不存在、源文件未发布或不可用）；只读。 |
| `GET /api/papers/{paper_id}/pages/{page_number}/image` | 返回单页 PNG，`image/png`。 | 页码不可解析、论文/源文件/页面不可用均为 `404`；只读。 |
| `GET /api/papers/{paper_id}/notes` | 返回该论文的 [`NoteResponse`](#schema-索引) 数组。 | `404`；只读。 |

上传会同步执行 Stage 0/1；客户端以摘要状态判断可用性。读取与删除的持久化/恢复细节见 [数据模型与删除恢复](data-model.md)。

## 批注与笔记（7 个操作）

选区字段使用 [`TextAnchorDraftRequest`](#schema-索引)：`quote`、`page_number`、0 到 1 的 `rects` 与可选 `element_id`。同页文字层锚点限制见 [PDF 文本批注后端契约](pdf-annotations.md)。

| 方法与路径 | 用途与重要请求/响应字段 | 稳定错误 / 幂等 |
| --- | --- | --- |
| `GET /api/papers/{paper_id}/annotations` | 返回 [`AnnotationBundleResponse`](#schema-索引)：`highlights`、`notes`、`anchors`。 | `{code:"annotation_not_found"}` 为 `404`；只读。 |
| `POST /api/papers/{paper_id}/highlights` | 锚点字段、`color:"yellow"`、必填 `request_id`；返回 `201 HighlightResponse`。 | `404 annotation_not_found`、`409 idempotency_conflict` / `paper_busy`、`422 validation_error`、`500 annotation_error`。同论文同 request_id 且内容一致重放同一高亮；不一致为冲突。 |
| `DELETE /api/papers/{paper_id}/highlights/{highlight_id}` | 删除一个高亮。 | 成功 `204`；同上 `404/409/500` 业务错误。无删除幂等重放，缺失资源为 not found。 |
| `POST /api/papers/{paper_id}/notes` | [`NoteRequest`](#schema-索引)：`body`、可选 `element_id`、`page_number`、`anchor`、可选 `request_id`；只允许 `note_type:"manual"`。返回 `201 NoteResponse`。 | `404 annotation_not_found`、`409 idempotency_conflict` / `paper_busy`、`422 validation_error`、`500 annotation_error`。提供 request_id 时，相同论文和等价内容重放笔记，不同内容冲突；未提供时每次创建新笔记。 |
| `PATCH /api/papers/{paper_id}/notes/{note_id}` | [`NoteUpdateRequest`](#schema-索引) 的 `body` 与可选 `expected_updated_at`；返回更新后的 `NoteResponse`。 | `404 annotation_not_found`、`409 idempotency_conflict` / `paper_busy`、`422 validation_error`、`500 annotation_error`。不使用 request_id；`expected_updated_at` 不匹配时以 `idempotency_conflict` 表示更新并发冲突。 |
| `DELETE /api/papers/{paper_id}/notes/{note_id}` | 删除一个笔记。 | 成功 `204`；缺失资源为 `404 annotation_not_found`，删除期间为 `409 paper_busy`。 |
| `POST /api/papers/{paper_id}/selection-assists` | 选区字段加 `action:"explain"|"translate"`、`model_profile_id`、`request_id`；返回 `text/event-stream`。 | 请求前论文不存在是 `404 annotation_not_found`；无效锚点 `422`；模型未配置 `503`、能力不足 `409`。流内错误见下节。 |

### 选区辅助 SSE（全部 4 个事件）

`POST /selection-assists` 设置 `Cache-Control: no-store` 与 `X-Accel-Buffering: no`。SSE 数据采用 `event: <name>\ndata: <JSON>\n\n`：

| 事件 | 数据 | 语义 |
| --- | --- | --- |
| `started` | `{ "request_id": "..." }` | 已登记并开始生成。 |
| `delta` | `{ "text": "..." }` | 一个可增量显示的文本片段，不代表持久化。 |
| `completed` | `{ "note": NoteResponse }` | 已创建锚点和完整 explanation/translation 笔记；这是唯一的成功终态。 |
| `error` | `{ "code": "...", "detail": "..." }` | 失败终态，例如 `assist_running`、`assist_too_long`、`assist_failed`、`paper_busy`。 |

**完全相同的 `paper_id + request_id` 重试规则：**已完成请求直接重放一个 `completed` 事件和已保存的 `note`，不再调用模型、也不重放旧 `delta`；运行中请求流出 `error/assist_running`；失败请求以同一标识重新开始。客户端不得把不同 action、不同模型或不同选区静默塞进同一个 `request_id`；应生成新的标识。选区辅助与 Agent 聊天不同：它仅调用 basic chat、使用 SSE，并自动创建笔记。

## 模型档案（6 个操作）

写操作均要求 `If-Match: <正整数 revision>`，缺失为 `428 if_match_required`，格式错误为 `400 invalid_if_match`。档案响应不含 API 密钥，只暴露 `has_api_key`、掩码和能力状态。

| 方法与路径 | 用途与重要请求/响应字段 | 稳定错误 / 幂等 |
| --- | --- | --- |
| `GET /api/model-profiles` | 返回 [`ModelProfileResponse`](#schema-索引) 数组；无启用持久档案时可包含只读环境回退档案。 | `ModelProfileErrorResponse`；只读。 |
| `POST /api/model-profiles` | [`ModelProfileCreateRequest`](#schema-索引)：显示名、`base_url`、`model_name`、可选 `api_key`、`enabled`、`is_default`。 | `201`；输入 `422 validation_error`，秘密存储/模型不可用 `503`，其他为 `500 model_profile_error`。非幂等创建。 |
| `PATCH /api/model-profiles/{profile_id}` | 有 `If-Match`；部分更新档案字段和可选 `api_key`，返回新 revision。 | `404 profile_not_found`、`409 profile_read_only` / `revision_conflict`、`422 validation_error`、`503 secret_store_unavailable`。相同 revision 的一次 compare-and-swap；重放旧 revision 可能冲突。 |
| `DELETE /api/model-profiles/{profile_id}` | 有 `If-Match`；软删除并删除关联秘密，若删除默认档案会选另一启用档案为默认。 | 成功 `204`；`409 profile_in_use` 表示请求持有 usage lease，稍后重试；其余同 PATCH。不是删除幂等，已删除为 not found。 |
| `POST /api/model-profiles/{profile_id}/default` | 有 `If-Match`；将启用档案设为默认，返回档案。 | `404 profile_not_found`、`409 profile_read_only` / `revision_conflict`、`422 validation_error`。compare-and-swap 语义。 |
| `POST /api/model-profiles/{profile_id}/test` | 有 `If-Match`；探测 basic chat、structured output、tool calling，并在可写档案上保存能力结果及新 revision。 | `404 profile_not_found`、`409 revision_conflict` / `profile_read_only`、`503 model_unavailable`、`422 validation_error`。会探测外部模型，不能当作无副作用重试。 |

## Agent 与会话（4 个操作）

主 Agent 的最终回答是 Markdown，不再使用结构化 JSON：服务完成工具循环后流式生成正文，再从正文里解析出可选的页面链接。两种传输共用同一套回合准备（前置校验、用户消息落库、笔记注入、工具循环、引用清洗），差别只在回答怎么返回。

答案格式约定（提示词与解析器同源，见 `services/answer_format.py`）：

- 正文里的 `[[element_id]]` 是内联引用标记；能被定位到当前论文的 ID 会保存为可选页面链接，其余忽略，且不影响正文。
- 首行可选的 `[[status:insufficient_evidence]]` 表示证据不足；缺省为 `grounded`。
- 单独一行的 `---` 之后是背景知识（非论文证据），解析后单独存进 `background_explanation`。
- 只有正文为空（解析不出任何答案）才返回 `502`。

| 方法与路径 | 用途与重要请求/响应字段 | 稳定错误 / 幂等 |
| --- | --- | --- |
| `POST /api/papers/{paper_id}/agent/messages` | [`AgentMessageRequest`](#schema-索引)：`content`、可选 `conversation_id`/`selection`、必填 `model_profile_id` 和 `request_id`。返回 [`AgentMessageResponse`](#schema-索引)：会话/消息 ID、`grounded|insufficient_evidence`、答案、引用、模型快照和实际注入笔记引用。 | `404` 论文/会话不存在；`409` 前置阶段不完整、工具调用能力不足、删除活跃或 request_id 与已存重试不一致；`422` 无效选区；`503` 模型未配置；`502` 模型或最终输出不能完成。相同论文和完全相同 request_id 已完成时重放保存的回合；部分用户消息的重试还必须匹配内容、会话、档案及不可变快照。 |
| `POST /api/papers/{paper_id}/agent/messages/stream` | 同一请求体，`text/event-stream` 响应：`started`（`request_id`）、`delta`（`text`，增量 Markdown）、`completed`（`message`，与 `AgentMessageResponse` 同构）、`error`（`code`、`detail`）。 | 请求校验（`404`/`409`/`422`/`503`）仍在开流前以普通 JSON 错误返回，开流后的失败是 `error` 事件。整段回答到达前不写库，中断的流不会留下半条助手消息；已完成回合重放只发 `started` + `completed`。 |
| `GET /api/papers/{paper_id}/agent/conversations/{conversation_id}` | 返回 [`ConversationResponse`](#schema-索引)，含用户/助手消息、历史模型快照、引用和笔记引用可用性。 | 论文或会话不存在 `404`；只读。 |
| `POST /api/agent/health` | 校验默认/环境回退模型的工具调用能力，返回 `AgentHealthResponse`（默认 `status: "ok"`）。 | `503` 工具调用不可用；只做能力探测，不写业务数据，可安全重复。 |

模型正文会直接保存并返回；内联引用标记只是可选页面跳转，重复或无法定位的 ID 会被去重/忽略，不会改写回答。工具调用是 Agent 回合唯一必须通过的能力检测——`structured output` 现在只是模型档案里的展示信息，不再门禁聊天（Markdown 回答不需要 `response_format`）。

## Schema 索引

以下 schema 均以运行时 OpenAPI 为准：`PaperSummaryResponse`、`PaperDocumentResponse`、`PaperDeleteRequest`、`TextAnchorDraftRequest`、`AnnotationBundleResponse`、`HighlightCreateRequest`、`HighlightResponse`、`NoteRequest`、`NoteUpdateRequest`、`NoteResponse`、`SelectionAssistRequest`、`ModelProfileCreateRequest`、`ModelProfilePatchRequest`、`ModelProfileResponse`、`ModelProfileErrorResponse`、`AgentMessageRequest`、`AgentMessageResponse`、`ConversationResponse`、`AgentHealthResponse`。

重点响应字段：

- `NoteResponse.model`、Agent 消息的 `model`、论文摘要的模型快照均是无密钥快照；`NoteResponse` 还标明 `note_type`、`ai_generated`、`user_edited`、锚点和时间。
- `AgentMessageResponse.paper_answer` 是模型返回的 Markdown 正文（含内联 `[[element_id]]` 标记），`background_explanation` 是 `---` 之后的背景段落。
- `AgentMessageResponse.citations` 的每一项包含元素 ID、类型、页码与归一化 `bbox`；`note_references` 仅说明本次检索到的笔记，不是论文证据。
