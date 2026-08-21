# 论文及关联数据安全删除 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 提供可确认、可回滚、可从进程崩溃中恢复的论文永久删除，清理源 PDF 及全部论文关联数据。

**Architecture:** `PaperOperationCoordinator` 在单进程本地服务中阻止删除与写操作并发；`PaperDeletionService` 先把源 PDF 原子暂存到 `.trash` 并写恢复标记，再在一个 SQLite 事务中按外键逆序删除。启动恢复器根据论文行是否仍存在决定恢复源文件或完成暂存清理。

**Tech Stack:** Python 3.12、FastAPI、SQLAlchemy 2、SQLite、pathlib、threading、pytest。

**Spec:** `docs/superpowers/specs/2026-08-21-reading-workspace-annotations-model-profiles-design.md`

## Global Constraints

- 必须先完成模型档案计划和批注/笔记记忆计划，以便删除覆盖最终 schema。
- 删除为永久操作，必须要求二次确认；不实现用户回收站。
- 只能访问和删除 `Settings.data_dir / "papers"` 与 `Settings.data_dir / ".trash"` 内经过校验的路径。
- 不使用未解析环境变量、glob、`~`、用户目录或仓库根目录作为删除目标。
- 提交数据库前失败必须恢复源 PDF 并回滚；提交后清理失败必须留下可重试恢复标记。
- 删除论文不能删除全局模型档案或模型 secret。
- 活跃 Agent、图谱、批注和解释/翻译写操作与删除互斥。
- Windows 文件占用做有界重试，超过上限返回 `PAPER_BUSY`，不强制删除。
- 删除成功后所有论文 API 和源 PDF 都不可访问。
- `sources/` 为只读参考目录；不得暂存环境提供的 `AGENTS.md`。

## File Map

**Create**

- `src/paper_agent/services/paper_operations.py`：论文级活动操作计数和删除互斥。
- `src/paper_agent/services/paper_deletion.py`：路径校验、暂存、事务删除和恢复标记。
- `tests/unit/services/test_paper_operations.py`
- `tests/unit/services/test_paper_deletion.py`
- `tests/integration/test_paper_deletion_api.py`
- `docs/data-model.md`

**Modify**

- `src/paper_agent/config.py`
- `src/paper_agent/storage.py`
- `src/paper_agent/schemas.py`
- `src/paper_agent/app.py`
- `src/paper_agent/routes/papers.py`
- `src/paper_agent/routes/agent.py`
- `src/paper_agent/routes/graph.py`
- `src/paper_agent/routes/annotations.py`
- `src/paper_agent/services/selection_assists.py`
- `tests/unit/test_storage.py`
- `tests/integration/test_papers_api.py`
- `docs/pdf-annotations.md`
- `docs/note-memory.md`

---

### Task 1: 建立论文级操作协调器

**Files:**
- Create: `src/paper_agent/services/paper_operations.py`
- Test: `tests/unit/services/test_paper_operations.py`

**Interfaces:**
- `PaperOperationCoordinator.operation(paper_id: str) -> ContextManager[None]`
- `PaperOperationCoordinator.deletion(paper_id: str) -> ContextManager[None]`
- Raises: `PaperBusyError`, `PaperDeletingError`

- [ ] **Step 1: 写互斥失败测试**

```python
def test_delete_is_rejected_while_a_write_operation_is_active():
    coordinator = PaperOperationCoordinator()
    with coordinator.operation("paper-a"):
        with pytest.raises(PaperBusyError):
            with coordinator.deletion("paper-a"):
                raise AssertionError("deletion must not start")


def test_new_write_is_rejected_while_deletion_is_active():
    coordinator = PaperOperationCoordinator()
    with coordinator.deletion("paper-a"):
        with pytest.raises(PaperDeletingError):
            with coordinator.operation("paper-a"):
                raise AssertionError("operation must not start")
```

增加：不同 paper 可并发、异常退出正确递减、状态归零后可再次操作、重复删除返回 busy。

- [ ] **Step 2: 运行测试并确认失败**

Run: `python -m pytest tests/unit/services/test_paper_operations.py -v`

Expected: FAIL，模块不存在。

- [ ] **Step 3: 实现协调器**

```python
@dataclass
class _PaperState:
    active_operations: int = 0
    deleting: bool = False


class PaperOperationCoordinator:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._states: dict[str, _PaperState] = {}

    @contextmanager
    def operation(self, paper_id: str) -> Iterator[None]:
        with self._lock:
            state = self._states.setdefault(paper_id, _PaperState())
            if state.deleting:
                raise PaperDeletingError("paper deletion is active")
            state.active_operations += 1
        try:
            yield
        finally:
            with self._lock:
                state.active_operations -= 1
                self._prune(paper_id, state)

    @contextmanager
    def deletion(self, paper_id: str) -> Iterator[None]:
        with self._lock:
            state = self._states.setdefault(paper_id, _PaperState())
            if state.deleting or state.active_operations:
                raise PaperBusyError("paper has active operations")
            state.deleting = True
        try:
            yield
        finally:
            with self._lock:
                state.deleting = False
                self._prune(paper_id, state)
```

- [ ] **Step 4: 运行测试**

Run: `python -m pytest tests/unit/services/test_paper_operations.py -v`

Expected: PASS。

- [ ] **Step 5: 提交协调器**

```bash
git add src/paper_agent/services/paper_operations.py tests/unit/services/test_paper_operations.py
git commit -m "feat: coordinate paper write and delete operations"
```

---

### Task 2: 实现单事务关联数据删除

**Files:**
- Modify: `src/paper_agent/storage.py`
- Test: `tests/unit/test_storage.py`

**Interfaces:**
- Produces: `PaperRepository.delete_paper_data(paper_id: str) -> bool`
- Does not touch filesystem or model profiles

- [ ] **Step 1: 写全量删除和回滚失败测试**

```python
def test_delete_paper_data_removes_every_paper_owned_row(repository, fully_populated_paper):
    deleted = repository.delete_paper_data(fully_populated_paper.id)
    assert deleted is True
    with repository.engine.connect() as connection:
        for table_name in PAPER_OWNED_TABLES:
            count = connection.exec_driver_sql(
                f"SELECT COUNT(*) FROM {table_name} WHERE paper_id = ?",
                (fully_populated_paper.id,),
            ).scalar_one()
            assert count == 0, table_name
        assert connection.exec_driver_sql(
            "SELECT COUNT(*) FROM model_profiles"
        ).scalar_one() == 2


def test_delete_rolls_back_all_rows_when_a_middle_delete_fails(repository, fully_populated_paper, monkeypatch):
    original = repository._delete_rows
    calls = 0
    def fail_in_middle(connection, table, paper_id):
        nonlocal calls
        calls += 1
        if calls == 5:
            raise RuntimeError("synthetic delete failure")
        return original(connection, table, paper_id)
    monkeypatch.setattr(repository, "_delete_rows", fail_in_middle)
    with pytest.raises(RuntimeError):
        repository.delete_paper_data(fully_populated_paper.id)
    assert repository.get_paper(fully_populated_paper.id) is not None
```

`PAPER_OWNED_TABLES` 在测试中显式列出所有表，不能从 production 删除列表导入，避免同一遗漏同时出现在实现和断言中。

- [ ] **Step 2: 运行测试并确认失败**

Run: `python -m pytest tests/unit/test_storage.py -k "delete_paper_data" -v`

Expected: FAIL，repository 没有删除方法。

- [ ] **Step 3: 实现固定逆序删除**

```python
PAPER_DELETE_ORDER = (
    conversation_message_note_citations,
    conversation_message_anchors,
    conversation_message_citations,
    conversation_messages,
    conversations,
    selection_assist_requests,
    note_anchors,
    notes,
    highlights,
    text_anchor_rects,
    text_anchors,
    graph_edge_evidence,
    graph_node_evidence,
    graph_edges,
    graph_nodes,
    document_elements,
    sections,
    pages,
    processing_runs,
)


def delete_paper_data(self, paper_id: str) -> bool:
    with self.engine.begin() as connection:
        exists = connection.execute(
            select(papers.c.id).where(papers.c.id == paper_id)
        ).scalar_one_or_none()
        if exists is None:
            return False
        for table in PAPER_DELETE_ORDER:
            connection.execute(delete(table).where(table.c.paper_id == paper_id))
        connection.execute(delete(papers).where(papers.c.id == paper_id))
    return True
```

如果某连接表设计为不含 `paper_id`，在本任务先补迁移使其包含 `paper_id`；不要通过未受约束的子查询删除其他论文数据。

- [ ] **Step 4: 运行存储测试**

Run: `python -m pytest tests/unit/test_storage.py -v`

Expected: PASS。

- [ ] **Step 5: 提交事务删除**

```bash
git add src/paper_agent/storage.py tests/unit/test_storage.py
git commit -m "feat: delete all paper-owned database rows atomically"
```

---

### Task 3: 实现安全路径、PDF 暂存和启动恢复

**Files:**
- Create: `src/paper_agent/services/paper_deletion.py`
- Modify: `src/paper_agent/config.py`
- Test: `tests/unit/services/test_paper_deletion.py`

**Interfaces:**
- `DeletionJournal(paper_id, original_name, trash_name, state, created_at)`
- `PaperDeletionService.delete(paper_id: str) -> None`
- `PaperDeletionService.recover_pending() -> DeletionRecoveryReport`
- `Settings.papers_dir: Path` and `Settings.trash_dir: Path`
- Raises: `PaperDeletionNotFoundError`, `PaperDeletionPathError`, `PaperDeletionBusyError`

- [ ] **Step 1: 写路径和恢复失败测试**

```python
def test_delete_restores_source_when_database_transaction_fails(service, repository, source_pdf, monkeypatch):
    monkeypatch.setattr(repository, "delete_paper_data", Mock(side_effect=RuntimeError))
    with pytest.raises(RuntimeError):
        service.delete("paper-a")
    assert source_pdf.exists()
    assert list(service.trash_dir.glob("paper-a-*.pdf")) == []
    assert repository.get_paper("paper-a") is not None


def test_startup_recovery_restores_staged_file_when_paper_row_exists(service, source_pdf):
    journal = service._stage_for_test("paper-a", source_pdf)
    assert not source_pdf.exists()
    report = service.recover_pending()
    assert source_pdf.exists()
    assert report.restored == (journal.paper_id,)


def test_path_escape_is_rejected_without_touching_external_file(service, repository, tmp_path):
    external = tmp_path / "outside.pdf"
    external.write_bytes(b"%PDF-1.4")
    repository.force_stored_filename("paper-a", str(external))
    with pytest.raises(PaperDeletionPathError):
        service.delete("paper-a")
    assert external.exists()
```

覆盖：论文行不存在、数据库已删除时启动清理 trash、损坏 marker 安全保留并报告、Windows 前两次 PermissionError 后第三次成功、超过重试返回 busy、最终 trash unlink 失败保留 marker。

- [ ] **Step 2: 运行测试并确认失败**

Run: `python -m pytest tests/unit/services/test_paper_deletion.py -v`

Expected: FAIL，删除服务不存在。

- [ ] **Step 3: 实现严格路径解析**

恢复结果类型固定为：

```python
@dataclass(frozen=True)
class DeletionRecoveryReport:
    restored: tuple[str, ...] = ()
    cleaned: tuple[str, ...] = ()
    damaged_markers: tuple[str, ...] = ()
```

```python
def managed_source_path(papers_dir: Path, stored_filename: str) -> Path:
    if Path(stored_filename).name != stored_filename:
        raise PaperDeletionPathError("stored filename is not a basename")
    root = papers_dir.resolve(strict=True)
    unresolved = root / stored_filename
    if unresolved.is_symlink():
        raise PaperDeletionPathError("paper source cannot be a symbolic link")
    candidate = unresolved.resolve(strict=True)
    if not candidate.is_relative_to(root):
        raise PaperDeletionPathError("paper source escaped managed storage")
    return candidate
```

为 `Settings` 增加只读 `papers_dir = data_dir / "papers"` 与 `trash_dir = data_dir / ".trash"` 属性，并在 `__post_init__` 创建两者。marker 只保存 basename，不保存任意绝对路径。marker 用临时文件原子替换，包含 `state=prepared|staged|cleanup_pending`。

- [ ] **Step 4: 实现删除和恢复状态机**

删除顺序固定：写 prepared marker → 对 `Path.replace()` 做 3 次有界重试（50ms、150ms、350ms）→ 写 staged marker → 调用 `delete_paper_data()` → 写 cleanup_pending → 删除暂存 PDF → 删除 marker。

数据库异常时先 rollback（repository context 已处理），再把 trash 文件 replace 回源路径并删除 marker。最终清理失败不恢复已提交数据库，而是保留 cleanup_pending marker；`recover_pending()` 看到论文行不存在时完成物理清理。

- [ ] **Step 5: 运行删除服务测试**

Run: `python -m pytest tests/unit/services/test_paper_deletion.py -v`

Expected: PASS。

- [ ] **Step 6: 提交删除状态机**

```bash
git add src/paper_agent/services/paper_deletion.py src/paper_agent/config.py tests/unit/services/test_paper_deletion.py
git commit -m "feat: recover paper deletion across process crashes"
```

---

### Task 4: 将操作协调器接入全部论文写路径

**Files:**
- Modify: `src/paper_agent/app.py`
- Modify: `src/paper_agent/routes/agent.py`
- Modify: `src/paper_agent/routes/graph.py`
- Modify: `src/paper_agent/routes/annotations.py`
- Modify: `src/paper_agent/services/selection_assists.py`
- Test: `tests/integration/test_paper_deletion_api.py`

**Interfaces:**
- `app.state.paper_operation_coordinator`
- Every Agent, graph, highlight, note and selection-assist write holds `operation(paper_id)` for its full durable operation

- [ ] **Step 1: 写写入期间删除冲突测试**

```python
def test_delete_returns_conflict_while_selection_stream_is_active(client, app, prepared_paper):
    stream_started = threading.Event()
    release_stream = threading.Event()
    app.state.selection_assist_service.client = BlockingChatClient(stream_started, release_stream)
    worker = Thread(target=_consume_selection_stream, args=(client, prepared_paper.id))
    worker.start()
    assert stream_started.wait(timeout=2)

    response = client.request(
        "DELETE",
        f"/api/papers/{prepared_paper.id}",
        json={"confirmation": prepared_paper.id},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "PAPER_BUSY"
    release_stream.set()
    worker.join(timeout=2)
```

分别覆盖 Agent、图谱和普通笔记写入。测试线程必须在 finally 中释放 event，避免失败时挂住测试进程。

- [ ] **Step 2: 运行测试并确认失败**

Run: `python -m pytest tests/integration/test_paper_deletion_api.py -k "while" -v`

Expected: FAIL，写路径未注册 active operation。

- [ ] **Step 3: 装配协调器并包裹同步写入**

`create_app()` 创建一个 coordinator，同时传给删除服务。Agent、图谱、note/highlight route 在业务服务调用外使用：

```python
with request.app.state.paper_operation_coordinator.operation(paper_id):
    return annotation_service.create_highlight(paper_id=paper_id, payload=payload)
```

SSE 必须在生成器内部持有 context，而不是只在返回 `StreamingResponse` 前持有：

```python
def coordinated_events():
    with coordinator.operation(paper_id):
        yield from selection_assist_service.stream(
            paper_id=paper_id,
            draft=draft,
            action=action,
            client=resolved.chat,
            model_snapshot=resolved.snapshot,
            request_id=request_id,
        )
```

- [ ] **Step 4: 运行并发测试**

Run: `python -m pytest tests/integration/test_paper_deletion_api.py -k "while" -v`

Expected: PASS。

- [ ] **Step 5: 提交写入协调**

```bash
git add src/paper_agent/app.py src/paper_agent/routes/agent.py src/paper_agent/routes/graph.py src/paper_agent/routes/annotations.py src/paper_agent/services/selection_assists.py tests/integration/test_paper_deletion_api.py
git commit -m "feat: block paper deletion during active writes"
```

---

### Task 5: 暴露永久删除 API 并在启动时恢复

**Files:**
- Modify: `src/paper_agent/schemas.py`
- Modify: `src/paper_agent/routes/papers.py`
- Modify: `src/paper_agent/app.py`
- Test: `tests/integration/test_paper_deletion_api.py`
- Test: `tests/integration/test_papers_api.py`

**Interfaces:**
- `DELETE /api/papers/{paper_id}` body `{ "confirmation": "<paper-id>" }`
- Success: `204 No Content`
- Errors: `404 PAPER_NOT_FOUND`, `409 PAPER_BUSY`, `422 DELETE_CONFIRMATION_MISMATCH`, `500 DELETE_RECOVERY_REQUIRED`

- [ ] **Step 1: 写全链路删除失败测试**

```python
def test_delete_api_removes_source_and_all_related_data(client, fully_populated_paper, data_dir):
    response = client.request(
        "DELETE",
        f"/api/papers/{fully_populated_paper.id}",
        json={"confirmation": fully_populated_paper.id},
    )
    assert response.status_code == 204
    assert client.get(f"/api/papers/{fully_populated_paper.id}").status_code == 404
    assert client.get(f"/api/papers/{fully_populated_paper.id}/source").status_code == 404
    assert not (data_dir / "papers" / fully_populated_paper.stored_filename).exists()
```

增加：错误确认不改变任何数据、重复删除返回 404、路径异常使用安全错误、模型档案仍存在、启动恢复在注册路由前运行。

- [ ] **Step 2: 运行测试并确认失败**

Run: `python -m pytest tests/integration/test_paper_deletion_api.py -v`

Expected: FAIL，DELETE 返回 405。

- [ ] **Step 3: 实现请求 schema 和路由**

```python
class PaperDeleteRequest(BaseModel):
    confirmation: UUID


@router.delete("/{paper_id}", status_code=204)
def delete_paper(paper_id: UUID, payload: PaperDeleteRequest, request: Request) -> Response:
    paper_id_text = str(paper_id)
    if str(payload.confirmation) != paper_id_text:
        raise public_error(422, "DELETE_CONFIRMATION_MISMATCH", "删除确认与论文不匹配。")
    with request.app.state.paper_operation_coordinator.deletion(paper_id_text):
        request.app.state.paper_deletion_service.delete(paper_id_text)
    return Response(status_code=204)
```

将领域异常映射为稳定码。不要在错误中返回源路径或 trash 名称。

- [ ] **Step 4: 在应用启动时执行恢复**

`create_app()` 在对外提供路由前调用 `paper_deletion_service.recover_pending()`。恢复报告只记录数量和 paper ID；损坏 marker 记安全日志并保留供人工检查，不阻止其他论文启动。

- [ ] **Step 5: 运行删除和论文 API 回归**

Run: `python -m pytest tests/integration/test_paper_deletion_api.py tests/integration/test_papers_api.py -v`

Expected: PASS。

- [ ] **Step 6: 提交删除 API**

```bash
git add src/paper_agent/schemas.py src/paper_agent/routes/papers.py src/paper_agent/app.py tests/integration/test_paper_deletion_api.py tests/integration/test_papers_api.py
git commit -m "feat: expose recoverable permanent paper deletion"
```

---

### Task 6: 文档化数据所有权与执行完整回归

**Files:**
- Create: `docs/data-model.md`
- Modify: `docs/pdf-annotations.md`
- Modify: `docs/note-memory.md`
- Test: all backend tests

**Interfaces:**
- Documents every paper-owned table, foreign-key direction, delete order, marker states and recovery rules.

- [ ] **Step 1: 编写数据模型与删除关系文档**

`docs/data-model.md` 使用 Mermaid ER 图和明确表格覆盖：论文解析、图谱、锚点、高亮、笔记、会话、消息、两类引用、模型快照。另画删除时序：lock → marker → move → transaction → cleanup。

- [ ] **Step 2: 补充批注和笔记删除语义**

在 `docs/pdf-annotations.md` 写明删除高亮不删除笔记；在 `docs/note-memory.md` 写明删除笔记后历史引用 `available=false`；两份文档链接到 data model。

- [ ] **Step 3: 运行完整后端测试**

Run: `python -m pytest -v`

Expected: 全部测试 PASS，0 failures。

- [ ] **Step 4: 验证数据库外键和暂存目录安全**

Run: `python -c "from pathlib import Path; from paper_agent.config import get_settings; s=get_settings(data_dir=Path('.tmp-paper-agent-check')); assert s.trash_dir.parent == s.data_dir"`

Expected: exit 0。运行后只删除明确创建的 `.tmp-paper-agent-check` 测试目录，并先解析确认其位于仓库工作区内。

- [ ] **Step 5: 提交文档**

```bash
git add docs/data-model.md docs/pdf-annotations.md docs/note-memory.md
git commit -m "docs: describe paper data deletion and recovery"
```
