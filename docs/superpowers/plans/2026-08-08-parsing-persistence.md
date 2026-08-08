# Parsing and Persistence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn a local PDF into a durable, source-locatable `PaperDocument` exposed through a small local FastAPI API.

**Architecture:** A Python package owns stable domain models and SQLite repositories. PyMuPDF produces Stage 0 geometry and asset metadata; MarkItDown produces Stage 1 Markdown, which an alignment service maps to PyMuPDF text blocks without guessing locations. A synchronous ingestion service persists processing state and is called by thin FastAPI routes.

**Tech Stack:** Python 3.12+, FastAPI, Pydantic v2, SQLAlchemy 2, SQLite, PyMuPDF, MarkItDown, pytest, httpx test client.

## Global Constraints

- Work locally; no external model endpoint is required for this increment.
- Use `MarkItDown.convert_local()` only for application-owned, validated local PDF paths.
- Treat PyMuPDF page and block geometry as the authoritative citation geometry.
- Persist normalized bounding boxes in `[0, 1]`; do not expose local filesystem paths in API responses.
- Preserve successful Stage 0 data if Stage 1 fails, and explicitly report the partial state.
- Write each behavior test first, observe the expected failure, then implement the minimum code to pass it.

---

## File Structure

```text
pyproject.toml
src/paper_agent/
  __init__.py                 package marker
  app.py                      FastAPI application factory
  config.py                   local data and database settings
  database.py                 SQLAlchemy engine and schema setup
  domain.py                   immutable document and processing value objects
  schemas.py                  public API request/response models
  storage.py                  local PDF storage and SQLite repositories
  parsers/
    __init__.py
    base.py                   parser result interfaces
    pymupdf_stage0.py         PyMuPDF geometry extraction
    markitdown_stage1.py      Markdown conversion and structure parsing
    alignment.py              normalized-text matching to source blocks
  services/
    __init__.py
    ingestion.py              stage orchestration and persistence
  routes/
    __init__.py
    health.py
    papers.py
tests/
  conftest.py                 isolated settings and generated sample PDFs
  unit/test_domain.py
  unit/test_storage.py
  unit/parsers/test_pymupdf_stage0.py
  unit/parsers/test_alignment.py
  unit/parsers/test_markitdown_stage1.py
  unit/services/test_ingestion.py
  integration/test_papers_api.py
  integration/test_health_api.py
```

## Task 1: Project package, settings, and application health

**Files:**
- Create: `pyproject.toml`
- Create: `src/paper_agent/__init__.py`
- Create: `src/paper_agent/config.py`
- Create: `src/paper_agent/database.py`
- Create: `src/paper_agent/app.py`
- Create: `src/paper_agent/routes/__init__.py`
- Create: `src/paper_agent/routes/health.py`
- Test: `tests/conftest.py`
- Test: `tests/integration/test_health_api.py`

**Interfaces:**
- Produces `Settings(data_dir: Path, database_url: str)` from `get_settings()`.
- Produces `create_app(settings: Settings | None = None) -> FastAPI`.
- Produces `GET /health -> {"status": "ok"}`.

- [ ] **Step 1: Write the failing health API test**

```python
def test_health_returns_ok(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

- [ ] **Step 2: Run it and verify the expected failure**

Run: `python -m pytest tests/integration/test_health_api.py::test_health_returns_ok -v`

Expected: FAIL because `paper_agent.app` does not exist.

- [ ] **Step 3: Implement the smallest package and application factory**

```python
def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title="paper-agent")
    app.state.settings = settings or get_settings()
    app.include_router(router)
    return app
```

Implement a `Settings` dataclass that creates `data_dir`, `data_dir / "papers"`, and its parent database directory on first use. Keep all mutable configuration on the app instance, so tests can inject temporary directories.

- [ ] **Step 4: Run the focused test**

Run: `python -m pytest tests/integration/test_health_api.py::test_health_returns_ok -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml src/paper_agent tests/conftest.py tests/integration/test_health_api.py
git commit -m "feat: bootstrap local paper agent API"
```

## Task 2: Stable document model and SQLite repositories

**Files:**
- Create: `src/paper_agent/domain.py`
- Create: `src/paper_agent/storage.py`
- Modify: `src/paper_agent/database.py`
- Test: `tests/unit/test_domain.py`
- Test: `tests/unit/test_storage.py`

**Interfaces:**
- Produces `ProcessingStatus` values: `queued`, `running`, `completed`, `partial`, `failed`.
- Produces `BoundingBox.from_page_rect(rect, page_width, page_height) -> BoundingBox`.
- Produces `PaperRepository.create_paper(...)`, `get_paper(paper_id)`, `save_page(...)`, `save_element(...)`, `save_section(...)`, `create_note(...)`, and their read counterparts.
- `DocumentElement` stores `kind`, `text`, `page_number | None`, `bbox | None`, `section_id | None`, and `location_status`.

- [ ] **Step 1: Write failing domain tests**

```python
def test_bounding_box_is_normalized_and_clamped():
    bbox = BoundingBox.from_page_rect((10, 20, 70, 80), 100, 100)

    assert bbox == BoundingBox(x0=0.1, y0=0.2, x1=0.7, y1=0.8)


def test_unlocated_element_cannot_have_geometry():
    with pytest.raises(ValueError, match="unlocated"):
        DocumentElement.paragraph(text="semantic only", location_status="unlocated", page_number=1)
```

- [ ] **Step 2: Run them and verify the expected failure**

Run: `python -m pytest tests/unit/test_domain.py -v`

Expected: FAIL because the domain module is missing.

- [ ] **Step 3: Implement immutable value objects and create database schema**

Use SQLAlchemy models or Core tables for `papers`, `processing_runs`, `pages`, `sections`, `document_elements`, and `notes`. Use UUID strings as IDs. Store bounding boxes as four real columns. Ensure a persisted paper can be reloaded with its pages, sections, elements, and notes in stable page/order position.

- [ ] **Step 4: Write failing repository tests**

```python
def test_repository_round_trips_locatable_and_unlocated_elements(repository):
    paper = repository.create_paper(original_filename="example.pdf", stored_filename="paper.pdf")
    located = DocumentElement.paragraph("Located", page_number=1, bbox=BoundingBox(0, 0, 1, 0.1))
    unlocated = DocumentElement.paragraph("Semantic only", location_status="unlocated")

    repository.save_element(paper.id, located)
    repository.save_element(paper.id, unlocated)

    elements = repository.get_document(paper.id).elements
    assert [(item.text, item.location_status) for item in elements] == [
        ("Located", "located"),
        ("Semantic only", "unlocated"),
    ]
```

- [ ] **Step 5: Run repository tests, implement, and rerun**

Run: `python -m pytest tests/unit/test_storage.py -v`

Expected before implementation: FAIL because repository methods are missing.

Implement only the repository behavior exercised by the tests, then rerun the same command until it passes.

- [ ] **Step 6: Commit**

```bash
git add src/paper_agent/domain.py src/paper_agent/storage.py src/paper_agent/database.py tests/unit
git commit -m "feat: persist paper document records"
```

## Task 3: Stage 0 PyMuPDF geometry extraction

**Files:**
- Create: `src/paper_agent/parsers/__init__.py`
- Create: `src/paper_agent/parsers/base.py`
- Create: `src/paper_agent/parsers/pymupdf_stage0.py`
- Test: `tests/conftest.py`
- Test: `tests/unit/parsers/test_pymupdf_stage0.py`

**Interfaces:**
- Produces `Stage0Result(pages, text_blocks, visual_elements)`.
- Produces `PyMuPdfStage0Parser.parse(pdf_path: Path) -> Stage0Result`.
- A `TextBlock` carries source text, page number, normalized bbox, and per-page reading order.
- A `VisualElement` carries `kind` (`image`, `drawing`, or `unknown_visual`), page number, normalized bbox, and an optional caption placeholder.

- [ ] **Step 1: Write a generated-PDF fixture and failing extraction test**

```python
def test_stage0_extracts_page_text_and_normalized_block_location(sample_pdf):
    result = PyMuPdfStage0Parser().parse(sample_pdf)

    assert result.pages[0].number == 1
    assert result.text_blocks[0].text == "Introduction"
    assert result.text_blocks[0].bbox.x0 == pytest.approx(0.1)
    assert 0 <= result.text_blocks[0].bbox.y0 < 1
```

Create `sample_pdf` with PyMuPDF in `tests/conftest.py`, using a 200×200 point page and text inserted at `(20, 20)`. This avoids committing opaque binary test assets.

- [ ] **Step 2: Run the test and verify the expected failure**

Run: `python -m pytest tests/unit/parsers/test_pymupdf_stage0.py::test_stage0_extracts_page_text_and_normalized_block_location -v`

Expected: FAIL because `PyMuPdfStage0Parser` does not exist.

- [ ] **Step 3: Implement Stage 0 parser**

Open the document with `pymupdf.open(pdf_path)`. For each page, record dimensions and use `page.get_text("blocks", sort=True)` to produce text blocks. Derive normalized coordinates from each block's four coordinates. Use image and drawing block metadata only to identify possible visual regions; do not invent figure/table semantics.

- [ ] **Step 4: Add failing malformed-PDF test and minimal error wrapping**

```python
def test_stage0_rejects_a_malformed_pdf(tmp_path):
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"not a pdf")

    with pytest.raises(PdfParseError, match="could not be opened"):
        PyMuPdfStage0Parser().parse(broken)
```

- [ ] **Step 5: Verify tests pass and commit**

Run: `python -m pytest tests/unit/parsers/test_pymupdf_stage0.py -v`

Expected: PASS.

```bash
git add src/paper_agent/parsers tests/conftest.py tests/unit/parsers/test_pymupdf_stage0.py
git commit -m "feat: extract PDF geometry with pymupdf"
```

## Task 4: Stage 1 Markdown structure and evidence alignment

**Files:**
- Create: `src/paper_agent/parsers/markitdown_stage1.py`
- Create: `src/paper_agent/parsers/alignment.py`
- Test: `tests/unit/parsers/test_alignment.py`
- Test: `tests/unit/parsers/test_markitdown_stage1.py`

**Interfaces:**
- Produces `MarkdownDocument(sections, paragraphs)` from `MarkItDownStage1Parser.parse(pdf_path)`.
- Produces `TextAligner.align(paragraphs, text_blocks) -> list[DocumentElement]`.
- A paragraph is `located` only when normalized text equality or a deterministic unique containment match identifies exactly one source block; otherwise it is `unlocated`.

- [ ] **Step 1: Write a failing exact-normalization test**

```python
def test_aligner_ignores_whitespace_and_unicode_punctuation():
    blocks = [TextBlock(text="We introduce a method — today.", page_number=2, bbox=BOX, order=0)]
    paragraphs = [MarkdownParagraph(text="We   introduce a method - today.")]

    aligned = TextAligner().align(paragraphs, blocks)

    assert aligned[0].location_status == "located"
    assert aligned[0].page_number == 2
```

- [ ] **Step 2: Run it and verify the expected failure**

Run: `python -m pytest tests/unit/parsers/test_alignment.py::test_aligner_ignores_whitespace_and_unicode_punctuation -v`

Expected: FAIL because `TextAligner` does not exist.

- [ ] **Step 3: Implement deterministic normalization and matching**

Normalize Unicode punctuation, lowercase, collapse whitespace, and remove soft hyphens. First match exact normalized text. Then permit a unique containment match only if its normalized string is at least 40 characters. For zero or multiple candidates, persist the paragraph as `unlocated`.

- [ ] **Step 4: Add and run ambiguity test**

```python
def test_aligner_leaves_repeated_text_unlocated():
    blocks = [TextBlock(text="Repeated evidence text " * 3, page_number=1, bbox=BOX, order=0),
              TextBlock(text="Repeated evidence text " * 3, page_number=2, bbox=BOX, order=0)]

    element = TextAligner().align([MarkdownParagraph(text="Repeated evidence text " * 3)], blocks)[0]

    assert element.location_status == "unlocated"
    assert element.page_number is None
```

- [ ] **Step 5: Write a failing MarkItDown adapter test**

```python
def test_markitdown_adapter_calls_convert_local_for_owned_pdf(monkeypatch, sample_pdf):
    calls = []
    monkeypatch.setattr("paper_agent.parsers.markitdown_stage1.MarkItDown", FakeMarkItDown(calls, "# Intro\n\nBody"))

    result = MarkItDownStage1Parser().parse(sample_pdf)

    assert calls == [sample_pdf]
    assert result.sections[0].title == "Intro"
```

- [ ] **Step 6: Implement adapter, run all Stage 1 tests, and commit**

Implement the adapter with `MarkItDown(enable_plugins=False).convert_local(pdf_path)` and read `result.text_content`. Parse ATX headings into sections and nonempty body groups into paragraphs. Raise `MarkdownParseError` on conversion failure; do not catch it in the adapter.

Run: `python -m pytest tests/unit/parsers/test_alignment.py tests/unit/parsers/test_markitdown_stage1.py -v`

Expected: PASS.

```bash
git add src/paper_agent/parsers tests/unit/parsers/test_alignment.py tests/unit/parsers/test_markitdown_stage1.py
git commit -m "feat: align markdown structure to PDF evidence"
```

## Task 5: Ingestion workflow, source storage, and paper API

**Files:**
- Create: `src/paper_agent/services/__init__.py`
- Create: `src/paper_agent/services/ingestion.py`
- Create: `src/paper_agent/schemas.py`
- Create: `src/paper_agent/routes/papers.py`
- Modify: `src/paper_agent/app.py`
- Modify: `src/paper_agent/storage.py`
- Test: `tests/unit/services/test_ingestion.py`
- Test: `tests/integration/test_papers_api.py`

**Interfaces:**
- Produces `PaperIngestionService.ingest(upload: UploadPayload) -> PaperSummary`.
- Produces `PaperIngestionService.get_source_path(paper_id) -> Path` for internal route use only.
- Produces `POST /api/papers`, `GET /api/papers/{paper_id}`, `GET /api/papers/{paper_id}/document`, `GET /api/papers/{paper_id}/source`, `GET /api/papers/{paper_id}/pages/{page_number}/image`, and `POST/GET /api/papers/{paper_id}/notes`.
- Public `PaperSummary` exposes `id`, `original_filename`, `status`, `stage0_status`, and `stage1_status`, never a storage path.

- [ ] **Step 1: Write a failing happy-path service test**

```python
def test_ingestion_persists_source_geometry_and_document(service, sample_pdf_bytes):
    paper = service.ingest(UploadPayload(filename="paper.pdf", content=sample_pdf_bytes, media_type="application/pdf"))

    assert paper.status == ProcessingStatus.completed
    document = service.get_document(paper.id)
    assert document.pages[0].number == 1
    assert any(element.location_status == "located" for element in document.elements)
```

- [ ] **Step 2: Run it and verify the expected failure**

Run: `python -m pytest tests/unit/services/test_ingestion.py::test_ingestion_persists_source_geometry_and_document -v`

Expected: FAIL because the ingestion service does not exist.

- [ ] **Step 3: Implement durable upload and processing state transitions**

Reject content that is not a PDF by both filename suffix and `%PDF-` header. Write the original bytes once to an ID-derived filename under `data_dir / "papers"`. Create `queued`, then `running` processing records. Persist Stage 0 first. If Stage 1 raises `MarkdownParseError`, keep Stage 0 and set the paper to `partial`; if Stage 0 fails, set it to `failed` with a safe summary.

- [ ] **Step 4: Add failing partial-processing test**

```python
def test_stage1_failure_keeps_stage0_and_marks_paper_partial(service, sample_pdf_bytes, monkeypatch):
    def fail_stage1(*_args, **_kwargs):
        raise MarkdownParseError("converter failed")

    monkeypatch.setattr(service, "_run_stage1", fail_stage1)

    paper = service.ingest(UploadPayload(filename="paper.pdf", content=sample_pdf_bytes, media_type="application/pdf"))

    assert paper.status == ProcessingStatus.partial
    assert service.get_document(paper.id).pages
```

- [ ] **Step 5: Implement thin API routes and write failing integration tests**

```python
def test_upload_then_read_document_and_source(client, sample_pdf_bytes):
    upload = client.post("/api/papers", files={"file": ("paper.pdf", sample_pdf_bytes, "application/pdf")})
    paper_id = upload.json()["id"]

    assert client.get(f"/api/papers/{paper_id}/document").status_code == 200
    assert client.get(f"/api/papers/{paper_id}/source").headers["content-type"] == "application/pdf"
    assert client.get(f"/api/papers/{paper_id}/pages/1/image").headers["content-type"] == "image/png"
```

Implement routes by delegating all work to `PaperIngestionService`; use `FileResponse` only with the service-owned source path. Render the page image from that same source PDF through the PyMuPDF adapter, returning a PNG response without disclosing its local path. Return 404 for unknown paper IDs and 422 for invalid uploads.

- [ ] **Step 6: Add notes behavior and run the full suite**

```python
def test_note_can_target_a_source_element(client, uploaded_paper):
    document = client.get(f"/api/papers/{uploaded_paper['id']}/document").json()
    element_id = document["elements"][0]["id"]
    response = client.post(f"/api/papers/{uploaded_paper['id']}/notes", json={"body": "Check this", "element_id": element_id})

    assert response.status_code == 201
    assert response.json()["element_id"] == element_id
```

Run: `python -m pytest -v`

Expected: PASS with normal ingestion, invalid upload, malformed PDF, partial Stage 1, alignment ambiguity, API source serving, and note coverage.

- [ ] **Step 7: Commit**

```bash
git add src/paper_agent tests/unit/services tests/integration
git commit -m "feat: ingest and serve parsed local papers"
```

## Task 6: Developer runbook and baseline verification

**Files:**
- Modify: `README.md`
- Test: all existing tests

**Interfaces:**
- Documents local setup, data directory behavior, test command, and how to start `uvicorn paper_agent.app:create_app --factory`.


- [ ] **Step 1: Document the local setup and run commands**

Document these commands exactly:

```bash
python -m pip install -e ".[dev]"
python -m pytest -v
uvicorn paper_agent.app:create_app --factory --reload
```

- [ ] **Step 2: Run full verification**

Run: `python -m pytest -v`

Expected: all tests pass with no warnings.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: add local development runbook"
```

## Plan Self-Review

- Spec coverage: Tasks 1–5 cover API bootstrap, local storage, Stage 0, Stage 1, alignment, partial failure retention, source assets, and notes. Later graph, Agent, and WebUI scope remains explicitly deferred.
- Placeholder scan: no unspecified task, unresolved interface, or generic test instruction remains.
- Type consistency: `PaperIngestionService` owns source access and `PaperRepository` owns persistence; routes return `PaperSummary` and document DTOs only.
