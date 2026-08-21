# 批注、解释翻译与笔记记忆 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为可复制文字的 PDF 提供持久文本锚点、高亮、手写笔记、一键解释/翻译自动笔记，并让 Agent 自动检索相关笔记。

**Architecture:** 文本锚点保存原文和单页归一化矩形，高亮与笔记分别引用锚点；解释/翻译通过 SSE 流式展示，只有完整结束后才原子创建正式笔记。`NoteMemoryService` 使用中文字符片段、英文词项、页码和来源权重做本地排序，并把有限笔记作为不可信参考数据注入 Agent。

**Tech Stack:** Python 3.12、FastAPI StreamingResponse、Pydantic 2、SQLAlchemy 2、SQLite、vLLM chat client、pytest。

**Spec:** `docs/superpowers/specs/2026-08-21-reading-workspace-annotations-model-profiles-design.md`

## Global Constraints

- 必须先完成 `docs/superpowers/plans/2026-08-21-model-profiles-provenance.md`。
- 首版只支持可复制文字 PDF 和单页内跨行选区；跨页选区返回稳定中文错误。
- 所有坐标归一化到 `[0, 1]`，服务端重新验证，不能信任客户端。
- 解释和翻译使用聊天框传入的当前 `model_profile_id`，不创建主聊天消息。
- 解释/翻译只有在流完整结束时才保存正式笔记；取消或失败不保存残缺结果。
- Agent 自动读取相关笔记，但笔记不能替代 Citation Guard 要求的论文原文证据。
- 模型生成笔记必须保留 AI 来源、模型快照和用户编辑状态。
- 不引入必须配置的向量模型或外部向量数据库。
- 用户可见 API 错误使用中文稳定错误码，不泄露模型原始响应。
- `sources/` 为只读参考目录；不得暂存环境提供的 `AGENTS.md`。

## File Map

**Create**

- `src/paper_agent/annotations.py`：文本锚点、高亮、笔记类型和选区草稿领域类型。
- `src/paper_agent/annotation_storage.py`：锚点、高亮、扩展笔记和幂等生成请求持久化。
- `src/paper_agent/services/annotations.py`：坐标、页码、原文与论文归属校验。
- `src/paper_agent/services/selection_assists.py`：解释/翻译 prompt、SSE 事件与完成后入笔记。
- `src/paper_agent/services/note_memory.py`：本地笔记检索、排序和上下文预算。
- `src/paper_agent/routes/annotations.py`：高亮、扩展笔记和选区辅助 API。
- `tests/unit/test_annotation_storage.py`
- `tests/unit/services/test_annotations.py`
- `tests/unit/services/test_selection_assists.py`
- `tests/unit/services/test_note_memory.py`
- `tests/integration/test_annotations_api.py`
- `tests/integration/test_selection_assists_api.py`
- `tests/integration/test_agent_note_memory_api.py`
- `docs/pdf-annotations.md`
- `docs/note-memory.md`

**Modify**

- `src/paper_agent/migrations.py`
- `src/paper_agent/database.py`
- `src/paper_agent/domain.py`
- `src/paper_agent/schemas.py`
- `src/paper_agent/storage.py`
- `src/paper_agent/app.py`
- `src/paper_agent/routes/__init__.py`
- `src/paper_agent/routes/papers.py`
- `src/paper_agent/routes/agent.py`
- `src/paper_agent/services/agent_runtime.py`
- `tests/unit/test_migrations.py`
- `tests/unit/test_storage.py`
- `tests/unit/services/test_agent_runtime.py`
- `tests/integration/test_papers_api.py`
- `tests/integration/test_agent_api.py`

---

### Task 1: 添加文本锚点、高亮和扩展笔记 schema

**Files:**
- Create: `src/paper_agent/annotations.py`
- Modify: `src/paper_agent/database.py`
- Modify: `src/paper_agent/migrations.py`
- Modify: `src/paper_agent/domain.py`
- Test: `tests/unit/test_migrations.py`
- Test: `tests/unit/services/test_annotations.py`

**Interfaces:**
- Produces: `TextAnchorRect`, `TextAnchorDraft`, `TextAnchor`, `Highlight`, `NoteType`
- Adds tables: `text_anchors`, `text_anchor_rects`, `highlights`, `note_anchors`, `selection_assist_requests`, `conversation_message_note_citations`, `conversation_message_anchors`
- Extends `notes` with type, provenance and timestamps

- [ ] **Step 1: 写领域和迁移失败测试**

```python
def test_text_anchor_draft_accepts_ordered_single_page_rectangles():
    draft = TextAnchorDraft(
        quote="selected paper text",
        page_number=2,
        rects=(
            TextAnchorRect(order=0, x0=0.1, y0=0.2, x1=0.8, y1=0.24),
            TextAnchorRect(order=1, x0=0.1, y0=0.25, x1=0.5, y1=0.29),
        ),
    )
    assert draft.page_number == 2


@pytest.mark.parametrize("coordinates", [
    (-0.1, 0.2, 0.8, 0.3),
    (0.8, 0.2, 0.1, 0.3),
])
def test_text_anchor_rect_rejects_invalid_normalized_geometry(coordinates):
    with pytest.raises(ValueError):
        TextAnchorRect(order=0, x0=coordinates[0], y0=coordinates[1], x1=coordinates[2], y1=coordinates[3])
```

迁移测试从旧 `notes` schema 启动，确认新列和七张新表存在，执行两次迁移仍幂等。

- [ ] **Step 2: 运行测试并确认失败**

Run: `python -m pytest tests/unit/test_migrations.py tests/unit/services/test_annotations.py -v`

Expected: FAIL，无法导入 `TextAnchorDraft`。

- [ ] **Step 3: 实现领域类型**

```python
class NoteType(StrEnum):
    manual = "manual"
    explanation = "explanation"
    translation = "translation"


@dataclass(frozen=True)
class TextAnchorRect:
    order: int
    x0: float
    y0: float
    x1: float
    y1: float

    def __post_init__(self) -> None:
        if self.order < 0 or not (0 <= self.x0 <= self.x1 <= 1 and 0 <= self.y0 <= self.y1 <= 1):
            raise ValueError("anchor rectangle must be ordered and normalized")


@dataclass(frozen=True)
class TextAnchorDraft:
    quote: str
    page_number: int
    rects: tuple[TextAnchorRect, ...]
    element_id: str | None = None

    def __post_init__(self) -> None:
        normalized_quote = " ".join(self.quote.split())
        if not normalized_quote or len(normalized_quote) > 12_000:
            raise ValueError("anchor quote must be nonempty and bounded")
        if self.page_number < 1 or not self.rects:
            raise ValueError("anchor page and rectangles are required")
        if tuple(rect.order for rect in self.rects) != tuple(range(len(self.rects))):
            raise ValueError("anchor rectangle order must be contiguous")


@dataclass(frozen=True)
class TextAnchor:
    paper_id: str
    quote: str
    page_number: int
    rects: tuple[TextAnchorRect, ...]
    element_id: str | None = None
    id: str = field(default_factory=lambda: str(uuid4()))


@dataclass(frozen=True)
class Highlight:
    anchor: TextAnchor
    color: Literal["yellow"] = "yellow"
    id: str = field(default_factory=lambda: str(uuid4()))
```

扩展现有 `Note`，新增字段都提供兼容默认值：`note_type=manual`、`anchor_ids=()`、`model_profile_id=None`、`model_snapshot=None`、`ai_generated=False`、`user_edited=False`、`created_at`、`updated_at`。

- [ ] **Step 4: 实现迁移 2**

新表全部以 `paper_id` 参与外键或复合约束。`text_anchor_rects` 用 `(anchor_id, order_index)` 唯一；`highlights.anchor_id` 唯一；`selection_assist_requests` 用 `(paper_id, idempotency_key)` 唯一。现有 notes 行迁移为 `manual` 且 `ai_generated = 0`。

- [ ] **Step 5: 运行迁移与领域测试**

Run: `python -m pytest tests/unit/test_migrations.py tests/unit/services/test_annotations.py tests/unit/test_domain.py -v`

Expected: PASS。

- [ ] **Step 6: 提交 schema**

```bash
git add src/paper_agent/annotations.py src/paper_agent/database.py src/paper_agent/migrations.py src/paper_agent/domain.py tests/unit/test_migrations.py tests/unit/services/test_annotations.py tests/unit/test_domain.py
git commit -m "feat: add durable paper text anchors"
```

---

### Task 2: 实现高亮和扩展笔记存储

**Files:**
- Create: `src/paper_agent/annotation_storage.py`
- Modify: `src/paper_agent/storage.py`
- Test: `tests/unit/test_annotation_storage.py`
- Test: `tests/unit/test_storage.py`

**Interfaces:**
- `PaperAnnotationRepository.create_highlight(paper_id, draft, color, request_id) -> Highlight`
- `PaperAnnotationRepository.list_highlights(paper_id) -> tuple[Highlight, ...]`
- `PaperAnnotationRepository.delete_highlight(paper_id, highlight_id) -> bool`
- `PaperAnnotationRepository.create_note(paper_id, note, anchor_draft=None, request_id=None) -> Note`
- `PaperAnnotationRepository.update_note(paper_id, note_id, body, expected_updated_at) -> Note`
- `PaperAnnotationRepository.delete_note(paper_id, note_id) -> bool`
- `PaperAnnotationRepository.get_notes(paper_id) -> tuple[Note, ...]`

- [ ] **Step 1: 写存储失败测试**

```python
def test_highlight_round_trip_preserves_rect_order(repository, prepared_paper):
    highlight = repository.create_highlight(
        prepared_paper.id,
        _draft(page=2, quote="routing tokens", rect_count=2),
        color="yellow",
        request_id="highlight-request-a",
    )

    loaded = repository.list_highlights(prepared_paper.id)
    assert loaded == (highlight,)
    assert [rect.order for rect in loaded[0].anchor.rects] == [0, 1]


def test_generated_note_and_anchor_are_atomic(repository, prepared_paper, monkeypatch):
    monkeypatch.setattr(repository, "_insert_note_anchor", Mock(side_effect=RuntimeError))
    with pytest.raises(RuntimeError):
        repository.create_note(
            prepared_paper.id,
            _generated_note(),
            anchor_draft=_draft(page=1),
            request_id="assist-a",
        )
    assert repository.get_notes(prepared_paper.id) == ()
    assert repository.list_anchors(prepared_paper.id) == ()
```

覆盖：跨论文 element 拒绝、页码不存在拒绝、颜色只允许 `yellow`、重复 request ID 返回同一对象、删除高亮保留笔记锚点、删除笔记保留独立高亮、编辑冲突。

- [ ] **Step 2: 运行测试并确认失败**

Run: `python -m pytest tests/unit/test_annotation_storage.py -v`

Expected: FAIL，缺少 repository。

- [ ] **Step 3: 实现共享锚点写入和读取**

所有组合写入使用一个 `engine.begin()`。先校验论文、页码和 element 归属，再插入 `text_anchors` 与按 order 排序的 rect。原文哈希使用：

```python
def quote_hash(quote: str) -> str:
    normalized = unicodedata.normalize("NFKC", " ".join(quote.split())).casefold()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
```

重复幂等键先读取现有业务对象；如果请求字段与原始对象不一致，抛出 `IdempotencyConflictError`，不能静默复用。

- [ ] **Step 4: 兼容现有 PaperRepository 笔记读取**

让 `PaperRepository.get_notes()` 和 `get_document()` 映射新增 note 字段与关联锚点，旧笔记仍返回 `manual`。新写路由使用 `PaperAnnotationRepository`，现有 document API 不改变路径。

- [ ] **Step 5: 运行存储回归**

Run: `python -m pytest tests/unit/test_annotation_storage.py tests/unit/test_storage.py -v`

Expected: PASS。

- [ ] **Step 6: 提交批注存储**

```bash
git add src/paper_agent/annotation_storage.py src/paper_agent/storage.py tests/unit/test_annotation_storage.py tests/unit/test_storage.py
git commit -m "feat: persist highlights and anchored notes"
```

---

### Task 3: 暴露高亮和笔记 CRUD API

**Files:**
- Create: `src/paper_agent/services/annotations.py`
- Create: `src/paper_agent/routes/annotations.py`
- Modify: `src/paper_agent/routes/__init__.py`
- Modify: `src/paper_agent/routes/papers.py`
- Modify: `src/paper_agent/schemas.py`
- Modify: `src/paper_agent/app.py`
- Test: `tests/integration/test_annotations_api.py`
- Test: `tests/integration/test_papers_api.py`

**Interfaces:**
- `GET /api/papers/{paper_id}/annotations`
- `POST /api/papers/{paper_id}/highlights`
- `DELETE /api/papers/{paper_id}/highlights/{highlight_id}`
- `POST/PATCH/DELETE /api/papers/{paper_id}/notes`
- Responses expose quote, page and rects but never local paths

- [ ] **Step 1: 写 API 失败测试**

```python
def test_create_highlight_returns_normalized_anchor(client, uploaded_paper):
    response = client.post(
        f"/api/papers/{uploaded_paper.id}/highlights",
        json={
            "quote": "selected text",
            "page_number": 1,
            "rects": [{"order": 0, "x0": 0.1, "y0": 0.2, "x1": 0.8, "y1": 0.25}],
            "color": "yellow",
            "request_id": str(uuid4()),
        },
    )
    assert response.status_code == 201
    assert response.json()["anchor"]["page_number"] == 1
```

增加 422 非法坐标、422 跨页结构、404 论文、409 幂等冲突、笔记编辑/删除和英文异常不泄漏测试。

- [ ] **Step 2: 运行测试并确认失败**

Run: `python -m pytest tests/integration/test_annotations_api.py -v`

Expected: FAIL，路由返回 404。

- [ ] **Step 3: 实现 schema 和服务校验**

```python
class TextAnchorDraftRequest(BaseModel):
    quote: str = Field(min_length=1, max_length=12_000)
    page_number: int = Field(ge=1)
    rects: list[TextAnchorRectRequest] = Field(min_length=1, max_length=200)
    element_id: UUID | None = None


class HighlightCreateRequest(TextAnchorDraftRequest):
    color: Literal["yellow"] = "yellow"
    request_id: UUID
```

`AnnotationService` 将 Pydantic 请求转换为领域对象，并在 repository 前验证 page 与 element。所有领域异常映射为 `{code, detail}` 中文响应。

- [ ] **Step 4: 扩展笔记路由**

保留原 `POST /notes` 路径，但请求增加可选 `anchor`、`note_type` 只允许客户端写 `manual`、`request_id`。PATCH 只允许正文和 `expected_updated_at`；客户端不能伪造 `ai_generated`、模型快照或生成类型。

- [ ] **Step 5: 运行 API 回归**

Run: `python -m pytest tests/integration/test_annotations_api.py tests/integration/test_papers_api.py -v`

Expected: PASS。

- [ ] **Step 6: 提交 CRUD API**

```bash
git add src/paper_agent/services/annotations.py src/paper_agent/routes/annotations.py src/paper_agent/routes/__init__.py src/paper_agent/routes/papers.py src/paper_agent/schemas.py src/paper_agent/app.py tests/integration/test_annotations_api.py tests/integration/test_papers_api.py
git commit -m "feat: expose paper annotation and note api"
```

---

### Task 4: 实现解释和翻译 SSE 流及成功后自动入笔记

**Files:**
- Create: `src/paper_agent/services/selection_assists.py`
- Modify: `src/paper_agent/routes/annotations.py`
- Modify: `src/paper_agent/schemas.py`
- Test: `tests/unit/services/test_selection_assists.py`
- Test: `tests/integration/test_selection_assists_api.py`

**Interfaces:**
- `SelectionAssistService.stream(*, paper_id: str, draft: TextAnchorDraft, action: SelectionAssistAction, client: VllmChatClient, model_snapshot: ModelSnapshot, request_id: str) -> Iterator[SelectionAssistEvent]`
- `POST /api/papers/{paper_id}/selection-assists` returns `text/event-stream`
- Events: `started`, `delta`, `completed`, `error`

- [ ] **Step 1: 写成功、取消和失败测试**

```python
def test_completed_explanation_creates_one_generated_note(service, repository):
    events = list(service.stream(
        paper_id="paper-a",
        draft=_draft(quote="Mixture of Experts"),
        action=SelectionAssistAction.explain,
        client=FakeChatClient(["这是", "一种专家混合结构。"]),
        model_snapshot=_snapshot(),
        request_id="assist-a",
    ))
    assert [event.event for event in events] == ["started", "delta", "delta", "completed"]
    notes = repository.get_notes("paper-a")
    assert len(notes) == 1
    assert notes[0].note_type is NoteType.explanation
    assert notes[0].ai_generated is True


def test_closed_stream_does_not_create_partial_note(service, repository):
    stream = service.stream(
        paper_id="paper-a",
        draft=_draft(),
        action=SelectionAssistAction.translate,
        client=FakeChatClient(["部分", "结果"]),
        model_snapshot=_snapshot(),
        request_id="assist-b",
    )
    next(stream)
    next(stream)
    stream.close()
    assert repository.get_notes("paper-a") == ()
```

覆盖：重复完成请求只返回既有 completed、运行中同 key 返回冲突、基础对话能力不足、原文附近上下文只用于解释、错误事件不包含模型原文异常。

- [ ] **Step 2: 运行测试并确认失败**

Run: `python -m pytest tests/unit/services/test_selection_assists.py tests/integration/test_selection_assists_api.py -v`

Expected: FAIL，缺少 selection assist service。

- [ ] **Step 3: 实现固定 prompt 与流状态机**

事件对象固定为：

```python
SelectionAssistEventName = Literal["started", "delta", "completed", "error"]


@dataclass(frozen=True)
class SelectionAssistEvent:
    event: SelectionAssistEventName
    payload: dict[str, object]
```

```python
EXPLAIN_SYSTEM_PROMPT = (
    "你是论文阅读助手。只解释被 <selected_text> 包围的原文；"
    "附近内容仅用于理解语境。使用简体中文，区分原文含义与补充说明。"
)
TRANSLATE_SYSTEM_PROMPT = (
    "把 <selected_text> 中的学术文本准确翻译为简体中文。"
    "保留公式、变量、引用编号和专有名词，不增加原文没有的结论。"
)
```

生成器先写 `selection_assist_requests=running`，逐个 yield delta 并只在内存累计不超过 64,000 字符。正常耗尽后在一个事务中创建锚点、生成笔记并把请求标记 completed；`GeneratorExit`、客户端异常或长度超限时标记 failed，不创建 note。

- [ ] **Step 4: 实现 SSE 序列化和路由**

请求 schema 固定为：

```python
class SelectionAssistRequest(TextAnchorDraftRequest):
    action: Literal["explain", "translate"]
    model_profile_id: UUID
    request_id: UUID
```

```python
def encode_sse(event: SelectionAssistEvent) -> bytes:
    payload = json.dumps(event.payload, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event.event}\ndata: {payload}\n\n".encode("utf-8")
```

路由先解析当前模型 provider，要求 `basic_chat=True`，再返回 `StreamingResponse(iterator, media_type="text/event-stream")`。响应设置 `Cache-Control: no-store` 与 `X-Accel-Buffering: no`。

- [ ] **Step 5: 运行流式测试**

Run: `python -m pytest tests/unit/services/test_selection_assists.py tests/integration/test_selection_assists_api.py -v`

Expected: PASS。

- [ ] **Step 6: 提交流式解释翻译**

```bash
git add src/paper_agent/services/selection_assists.py src/paper_agent/routes/annotations.py src/paper_agent/schemas.py tests/unit/services/test_selection_assists.py tests/integration/test_selection_assists_api.py
git commit -m "feat: turn selection assists into durable notes"
```

---

### Task 5: 实现中文友好的本地笔记检索

**Files:**
- Create: `src/paper_agent/services/note_memory.py`
- Test: `tests/unit/services/test_note_memory.py`

**Interfaces:**
- `NoteMemoryService.retrieve(paper_id: str, query: str, selection: TextAnchorDraft | None, max_notes: int = 8, max_chars: int = 6000) -> NoteMemoryContext`
- `NoteMemoryContext.prompt_block: str`
- `NoteMemoryContext.references: tuple[NoteMemoryReference, ...]`

- [ ] **Step 1: 写排序和预算失败测试**

```python
def test_retrieval_matches_chinese_bigrams_and_prefers_manual_note(service):
    manual = _note(body="路由负载均衡损失", note_type=NoteType.manual)
    generated = _note(body="路由负载均衡损失", note_type=NoteType.explanation)
    service.repository.replace_notes("paper-a", (generated, manual))

    context = service.retrieve("paper-a", "负载是怎么均衡的？", selection=None)

    assert [reference.note_id for reference in context.references[:2]] == [manual.id, generated.id]
    assert "不可信笔记参考" in context.prompt_block
    assert "AI 生成" in context.prompt_block


def test_retrieval_never_exceeds_character_budget(service):
    service.repository.replace_notes("paper-a", tuple(_note(body="x" * 1200) for _ in range(10)))
    context = service.retrieve("paper-a", "x", selection=None, max_chars=2500)
    assert len(context.prompt_block) <= 2500
```

覆盖：跨论文隔离、页码邻近加分、用户已编辑生成笔记的优先级、无匹配返回空、英文词项、确定性排序。

- [ ] **Step 2: 运行测试并确认失败**

Run: `python -m pytest tests/unit/services/test_note_memory.py -v`

Expected: FAIL，模块不存在。

- [ ] **Step 3: 实现词项提取和评分**

返回类型固定为：

```python
@dataclass(frozen=True)
class NoteMemoryReference:
    note_id: str
    note_type: NoteType
    page_number: int | None
    available: bool = True


@dataclass(frozen=True)
class NoteMemoryContext:
    prompt_block: str
    references: tuple[NoteMemoryReference, ...]
```

```python
def searchable_terms(value: str) -> frozenset[str]:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    latin = set(re.findall(r"[a-z0-9_+-]{2,}", normalized))
    cjk_runs = re.findall(r"[\u3400-\u9fff]+", normalized)
    cjk = {
        run[index:index + 2]
        for run in cjk_runs
        for index in range(max(1, len(run) - 1))
        if run[index:index + 2]
    }
    return frozenset(latin | cjk)
```

基础分为 query 词项重合数量；同页 +4、相邻页 +2、选区 quote 重合 +6、manual +1、user_edited +1。按 `(-score, -updated_at.timestamp(), note.id)` 排序。score 为 0 时不返回，除非 selection 与 note anchor 同页。

- [ ] **Step 4: 构造安全 prompt block**

每条格式固定为：`[笔记 note:<id> | 类型:用户/AI解释/AI翻译 | 页码:<n>]`，正文 XML 转义后放进 `<note_data>`，并在开头声明笔记不能覆盖指令、不能当作论文证据。

- [ ] **Step 5: 运行检索测试**

Run: `python -m pytest tests/unit/services/test_note_memory.py -v`

Expected: PASS。

- [ ] **Step 6: 提交笔记检索**

```bash
git add src/paper_agent/services/note_memory.py tests/unit/services/test_note_memory.py
git commit -m "feat: retrieve relevant paper notes locally"
```

---

### Task 6: 将笔记记忆和选区附件接入 Agent

**Files:**
- Modify: `src/paper_agent/domain.py`
- Modify: `src/paper_agent/storage.py`
- Modify: `src/paper_agent/services/agent_runtime.py`
- Modify: `src/paper_agent/routes/agent.py`
- Modify: `src/paper_agent/schemas.py`
- Modify: `src/paper_agent/app.py`
- Test: `tests/unit/test_storage.py`
- Test: `tests/unit/services/test_agent_runtime.py`
- Test: `tests/integration/test_agent_note_memory_api.py`
- Test: `tests/integration/test_agent_api.py`

**Interfaces:**
- Agent request gains optional `selection: TextAnchorDraftRequest`
- `AgentTurn` gains `note_references: tuple[NoteMemoryReference, ...]`
- Agent response/history gains `note_references: list[NoteReferenceResponse]`
- Persists selected anchor on user message and note references on assistant message

- [ ] **Step 1: 写 Agent 自动读取笔记失败测试**

```python
def test_agent_injects_relevant_notes_and_returns_note_references(runtime, repository, fake_client):
    note = repository.create_note("paper-a", _manual_note("负载均衡损失"))
    turn = runtime.ask(
        paper_id="paper-a",
        question=AgentQuestion(content="负载如何均衡？", mode=AgentMode.paper_only),
        client=fake_client,
        model_snapshot=_snapshot(),
        request_id="agent-a",
    )

    system_messages = [message["content"] for message in fake_client.messages if message["role"] == "system"]
    assert any(f"note:{note.id}" in content for content in system_messages)
    assert turn.note_references[0].note_id == note.id
    assert turn.assistant_message.citation_element_ids
```

增加：只有笔记没有论文工具证据时 Citation Guard 仍返回 insufficient；笔记文本中的“忽略系统指令”只作为数据；selection 附件进入用户上下文并持久锚点；历史接口批量加载笔记引用；跨论文 note ID 拒绝。

- [ ] **Step 2: 运行测试并确认失败**

Run: `python -m pytest tests/unit/services/test_agent_runtime.py tests/integration/test_agent_note_memory_api.py -v`

Expected: FAIL，AgentTurn 没有 note references。

- [ ] **Step 3: 在 runtime 注入有限笔记上下文**

构造历史时保留原 Citation Guard system prompt，再在当前用户消息前加入第二条 system 数据块：

```python
messages = [
    {"role": "system", "content": _system_prompt(conversation.mode)},
]
if note_context.prompt_block:
    messages.append({"role": "system", "content": note_context.prompt_block})
messages.extend(self._durable_history(paper_id=paper_id, conversation=conversation))
```

当前 selection 用 `<selected_text page="N">` 包裹并拼入当前用户内容，不修改用户持久正文。`NoteMemoryService` 在每次请求重新检索，切换模型后仍使用相同规则。

- [ ] **Step 4: 保存和返回笔记引用**

assistant message 写入后，把实际注入的 note IDs 写到 `conversation_message_note_citations`。history API 一次批量加载对应 notes，返回 `id`、类型、正文摘要、页码和锚点。笔记已删除时历史响应保留引用 ID 并标记 `available=false`。

- [ ] **Step 5: 运行 Agent 与 Citation Guard 回归**

Run: `python -m pytest tests/unit/services/test_agent_runtime.py tests/unit/services/test_citation_guard.py tests/integration/test_agent_note_memory_api.py tests/integration/test_agent_api.py -v`

Expected: PASS。

- [ ] **Step 6: 提交 Agent 笔记记忆**

```bash
git add src/paper_agent/domain.py src/paper_agent/storage.py src/paper_agent/services/agent_runtime.py src/paper_agent/routes/agent.py src/paper_agent/schemas.py src/paper_agent/app.py tests/unit/test_storage.py tests/unit/services/test_agent_runtime.py tests/integration/test_agent_note_memory_api.py tests/integration/test_agent_api.py
git commit -m "feat: ground agent context in paper notes"
```

---

### Task 7: 编写批注与笔记记忆文档并运行回归

**Files:**
- Create: `docs/pdf-annotations.md`
- Create: `docs/note-memory.md`
- Test: all backend tests

**Interfaces:**
- Documents anchor geometry, note provenance, SSE contract, retrieval scoring and citation separation.

- [ ] **Step 1: 编写 PDF 批注后端契约**

`docs/pdf-annotations.md` 写明：坐标系、单页限制、矩形顺序、quote hash、幂等键、锚点/高亮/笔记删除关系、所有 API 请求与响应示例。前端 PDF.js 采集部分留给前端计划补充，但文档中明确服务端不接受像素坐标。

- [ ] **Step 2: 编写笔记记忆文档**

`docs/note-memory.md` 写明：四种 UI 筛选、三种 note type、AI 来源和用户编辑标记、检索评分、6000 字符预算、笔记注入安全边界、论文引用与笔记引用的差异。

- [ ] **Step 3: 验证 SSE 文档事件与实现一致**

Run: `rg -n "started|delta|completed|error" src/paper_agent/services/selection_assists.py docs/pdf-annotations.md`

Expected: 两个文件均包含四种事件，名称完全一致。

- [ ] **Step 4: 运行完整后端测试**

Run: `python -m pytest -v`

Expected: 全部测试 PASS，0 failures。

- [ ] **Step 5: 提交文档**

```bash
git add docs/pdf-annotations.md docs/note-memory.md
git commit -m "docs: explain annotations and note memory"
```
