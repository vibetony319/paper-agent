import json
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import event, insert, select
from sqlalchemy.exc import IntegrityError

from paper_agent.database import (
    conversation_message_anchors,
    conversation_message_citations,
    conversation_message_note_citations,
    conversation_messages,
    conversations,
    document_elements,
    graph_edge_evidence,
    graph_nodes,
    highlights,
    model_profiles,
    note_anchors,
    notes,
    pages,
    papers,
    processing_runs,
    selection_assist_requests,
    sections,
    text_anchor_rects,
    text_anchors,
)
from paper_agent.domain import (
    AgentMessageRole,
    BoundingBox,
    Conversation,
    ConversationMessage,
    DocumentElement,
    GraphEdge,
    GraphNode,
    GraphStage,
    Note,
    Page,
    Paper,
    PaperGraph,
    ProcessingStatus,
    Section,
)
from paper_agent.model_profiles import ModelSnapshot
from paper_agent.storage import ConversationReferenceError, GraphReferenceError, PaperRepository


@pytest.fixture
def repository(tmp_path: Path) -> PaperRepository:
    return PaperRepository(f"sqlite:///{tmp_path / 'paper-agent.db'}")


def test_repository_round_trips_locatable_and_unlocated_elements(repository):
    """Breaks if semantic-only elements gain location data or lose save order."""
    paper = repository.create_paper(
        original_filename="example.pdf", stored_filename="paper.pdf"
    )
    repository.save_page(paper.id, Page(number=1, width=200, height=300))
    located = DocumentElement.paragraph(
        "Located", page_number=1, bbox=BoundingBox(0, 0, 1, 0.1)
    )
    unlocated = DocumentElement.paragraph("Semantic only", location_status="unlocated")

    repository.save_element(paper.id, located)
    repository.save_element(paper.id, unlocated)

    elements = repository.get_document(paper.id).elements
    assert [(item.text, item.location_status, item.page_number, item.bbox) for item in elements] == [
        ("Located", "located", 1, BoundingBox(0, 0, 1, 0.1)),
        ("Semantic only", "unlocated", None, None),
    ]


def test_repository_reloads_document_records_in_stable_position_order(repository):
    """Breaks if SQLite query order changes document reading order after reload."""
    paper = repository.create_paper(
        original_filename="example.pdf", stored_filename="paper.pdf"
    )
    repository.save_page(paper.id, Page(number=2, width=200, height=300))
    repository.save_page(paper.id, Page(number=1, width=200, height=300))
    later = repository.save_section(paper.id, Section(title="Later", order=20, page_number=2))
    earlier = repository.save_section(paper.id, Section(title="Earlier", order=10, page_number=1))
    repository.save_element(
        paper.id,
        DocumentElement.paragraph(
            "Second", page_number=1, bbox=BoundingBox(0, 0.2, 1, 0.3), section_id=later.id, order=2
        ),
    )
    repository.save_element(
        paper.id,
        DocumentElement.paragraph(
            "First", page_number=1, bbox=BoundingBox(0, 0, 1, 0.1), section_id=earlier.id, order=1
        ),
    )
    repository.create_note(paper.id, Note(body="Second note"))
    repository.create_note(paper.id, Note(body="First note"))

    document = repository.get_document(paper.id)

    assert [page.number for page in document.pages] == [1, 2]
    assert [section.title for section in document.sections] == ["Earlier", "Later"]
    assert [element.text for element in document.elements] == ["First", "Second"]
    assert [note.body for note in document.notes] == ["Second note", "First note"]


def test_repository_persists_bbox_as_four_real_columns(repository):
    """Breaks if geometry is hidden in one serialized value instead of queryable columns."""
    paper = repository.create_paper(
        original_filename="example.pdf", stored_filename="paper.pdf"
    )
    repository.save_page(paper.id, Page(number=1, width=200, height=300))
    repository.save_element(
        paper.id,
        DocumentElement.paragraph(
            "Located", page_number=1, bbox=BoundingBox(0.1, 0.2, 0.7, 0.8)
        ),
    )

    with repository.engine.connect() as connection:
        row = connection.execute(
            select(
                document_elements.c.bbox_x0,
                document_elements.c.bbox_y0,
                document_elements.c.bbox_x1,
                document_elements.c.bbox_y1,
            ).where(document_elements.c.paper_id == paper.id)
        ).one()

    assert tuple(row) == pytest.approx((0.1, 0.2, 0.7, 0.8))


def test_stage1_batch_write_rolls_back_sections_and_paragraphs_on_persistence_error(repository):
    """Breaks if one Stage 1 transaction leaves earlier semantic rows durable."""
    paper = repository.create_paper(
        original_filename="example.pdf", stored_filename="paper.pdf"
    )
    repository.save_page(paper.id, Page(number=1, width=200, height=300))
    section = Section(title="Introduction", order=0)
    duplicate_id = "duplicate-stage1-paragraph"
    elements = (
        DocumentElement(
            kind="paragraph",
            text="First paragraph",
            page_number=1,
            bbox=BoundingBox(0, 0, 1, 0.1),
            section_id=section.id,
            id=duplicate_id,
        ),
        DocumentElement(
            kind="paragraph",
            text="Second paragraph",
            page_number=1,
            bbox=BoundingBox(0, 0.2, 1, 0.3),
            section_id=section.id,
            id=duplicate_id,
        ),
    )

    with pytest.raises(IntegrityError):
        repository.save_stage1_document(paper.id, (section,), elements)

    assert repository.get_sections(paper.id) == ()
    assert repository.get_elements(paper.id) == ()


def test_stage0_batch_write_rolls_back_pages_and_elements_on_second_element_failure(
    repository,
):
    """Breaks if a failed Stage 0 element write leaves earlier source rows durable."""
    paper = repository.create_paper(
        original_filename="example.pdf", stored_filename="paper.pdf"
    )
    pages_to_save = (Page(number=1, width=200, height=300),)
    elements_to_save = (
        DocumentElement(
            kind="image",
            text="",
            page_number=1,
            bbox=BoundingBox(0, 0, 0.2, 0.2),
        ),
        DocumentElement(
            kind="drawing",
            text="",
            page_number=1,
            bbox=BoundingBox(0.2, 0.2, 0.4, 0.4),
        ),
    )
    with repository.engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TRIGGER fail_stage0_drawing
            BEFORE INSERT ON document_elements
            WHEN NEW.kind = 'drawing'
            BEGIN
                SELECT RAISE(FAIL, 'stage0 drawing insert failed');
            END;
            """
        )

    with pytest.raises(IntegrityError, match="stage0 drawing insert failed"):
        repository.save_stage0_document(paper.id, pages_to_save, elements_to_save)

    assert repository.get_pages(paper.id) == ()
    assert repository.get_elements(paper.id) == ()


def test_sqlite_rejects_unlocated_element_with_source_geometry(repository):
    """Breaks if direct persistence can create a false source location."""
    paper = repository.create_paper(
        original_filename="example.pdf", stored_filename="paper.pdf"
    )

    with pytest.raises(IntegrityError):
        with repository.engine.begin() as connection:
            connection.execute(
                insert(document_elements).values(
                    id="invalid-element",
                    paper_id=paper.id,
                    kind="paragraph",
                    text="semantic only",
                    page_number=1,
                    location_status="unlocated",
                    order_index=0,
                )
            )


def test_sqlite_rejects_located_element_with_out_of_range_bbox(repository):
    """Breaks if direct persistence can store geometry outside normalized bounds."""
    paper = repository.create_paper(
        original_filename="example.pdf", stored_filename="paper.pdf"
    )

    with pytest.raises(IntegrityError):
        with repository.engine.begin() as connection:
            connection.execute(
                insert(document_elements).values(
                    id="out-of-range-element",
                    paper_id=paper.id,
                    kind="paragraph",
                    text="invalid geometry",
                    page_number=1,
                    bbox_x0=-0.1,
                    bbox_y0=0,
                    bbox_x1=1,
                    bbox_y1=1,
                    location_status="located",
                    order_index=0,
                )
            )


def test_sqlite_rejects_element_section_from_another_paper(repository):
    """Breaks if direct persistence can attach an element to another paper's section."""
    first = repository.create_paper(
        original_filename="first.pdf", stored_filename="first.pdf"
    )
    second = repository.create_paper(
        original_filename="second.pdf", stored_filename="second.pdf"
    )
    with repository.engine.begin() as connection:
        connection.execute(
            insert(sections).values(
                id="second-section", paper_id=second.id, title="Other", order_index=0
            )
        )

    with pytest.raises(IntegrityError):
        with repository.engine.begin() as connection:
            connection.execute(
                insert(document_elements).values(
                    id="cross-paper-element",
                    paper_id=first.id,
                    section_id="second-section",
                    kind="paragraph",
                    text="wrong section",
                    page_number=1,
                    bbox_x0=0,
                    bbox_y0=0,
                    bbox_x1=1,
                    bbox_y1=1,
                    location_status="located",
                    order_index=0,
                )
            )


def test_sqlite_rejects_note_element_from_another_paper(repository):
    """Breaks if direct persistence can attach a note to another paper's element."""
    first = repository.create_paper(
        original_filename="first.pdf", stored_filename="first.pdf"
    )
    second = repository.create_paper(
        original_filename="second.pdf", stored_filename="second.pdf"
    )
    with repository.engine.begin() as connection:
        connection.execute(
            insert(document_elements).values(
                id="second-element",
                paper_id=second.id,
                kind="paragraph",
                text="other paper",
                page_number=1,
                bbox_x0=0,
                bbox_y0=0,
                bbox_x1=1,
                bbox_y1=1,
                location_status="located",
                order_index=0,
            )
        )

    with pytest.raises(IntegrityError):
        with repository.engine.begin() as connection:
            connection.execute(
                insert(notes).values(
                    id="cross-paper-note",
                    paper_id=first.id,
                    element_id="second-element",
                    body="wrong element",
                    order_index=0,
                )
            )


def test_repository_migrates_legacy_processing_runs_and_appends_status(tmp_path: Path):
    """Breaks if a Task 2 database cannot load or extend processing history."""
    database_path = tmp_path / "legacy.db"
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE papers (
                id VARCHAR(36) PRIMARY KEY,
                original_filename VARCHAR NOT NULL,
                stored_filename VARCHAR NOT NULL,
                status VARCHAR(16) NOT NULL
            );
            CREATE TABLE processing_runs (
                id VARCHAR(36) PRIMARY KEY,
                paper_id VARCHAR(36) NOT NULL,
                status VARCHAR(16) NOT NULL,
                FOREIGN KEY(paper_id) REFERENCES papers(id)
            );
            """
        )
        connection.execute(
            "INSERT INTO papers VALUES (?, ?, ?, ?)",
            ("legacy-paper", "legacy.pdf", "legacy.pdf", "running"),
        )
        connection.executemany(
            "INSERT INTO processing_runs VALUES (?, ?, ?)",
            [
                ("legacy-run-1", "legacy-paper", "queued"),
                ("legacy-run-2", "legacy-paper", "running"),
            ],
        )

    repository = PaperRepository(f"sqlite:///{database_path}")

    assert repository.get_processing_statuses("legacy-paper") == (
        ProcessingStatus.queued,
        ProcessingStatus.running,
    )
    repository.record_processing_status("legacy-paper", ProcessingStatus.completed)
    assert repository.get_processing_statuses("legacy-paper") == (
        ProcessingStatus.queued,
        ProcessingStatus.running,
        ProcessingStatus.completed,
    )
    reopened = PaperRepository(f"sqlite:///{database_path}")
    reopened.record_processing_status("legacy-paper", ProcessingStatus.partial)
    assert reopened.get_processing_statuses("legacy-paper") == (
        ProcessingStatus.queued,
        ProcessingStatus.running,
        ProcessingStatus.completed,
        ProcessingStatus.partial,
    )
    assert reopened.get_paper("legacy-paper").source_published is False


@pytest.mark.parametrize(
    ("paper_id", "paper_status", "stage0_status", "stage0_error", "expected_published"),
    [
        (
            "legacy-success",
            "completed",
            "completed",
            None,
            True,
        ),
        (
            "legacy-source-failure",
            "failed",
            "failed",
            "The PDF source could not be stored.",
            False,
        ),
    ],
)
def test_repository_migration_backfills_only_verified_legacy_sources(
    tmp_path: Path,
    paper_id: str,
    paper_status: str,
    stage0_status: str,
    stage0_error: str | None,
    expected_published: bool,
):
    """Breaks if a legacy source is hidden or a known failed source is exposed."""
    database_path = tmp_path / "legacy-sources.db"
    sources_dir = tmp_path / "papers"
    sources_dir.mkdir()
    (sources_dir / f"{paper_id}.pdf").write_bytes(b"%PDF-1.7\nlegacy source")
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE papers (
                id VARCHAR(36) PRIMARY KEY,
                original_filename VARCHAR NOT NULL,
                stored_filename VARCHAR NOT NULL,
                status VARCHAR(16) NOT NULL
            );
            CREATE TABLE processing_runs (
                id VARCHAR(36) PRIMARY KEY,
                paper_id VARCHAR(36) NOT NULL,
                sequence INTEGER NOT NULL,
                stage VARCHAR(16),
                status VARCHAR(16) NOT NULL,
                error_summary VARCHAR,
                FOREIGN KEY(paper_id) REFERENCES papers(id)
            );
            """
        )
        connection.execute(
            "INSERT INTO papers VALUES (?, ?, ?, ?)",
            (paper_id, f"{paper_id}.pdf", f"{paper_id}.pdf", paper_status),
        )
        connection.execute(
            "INSERT INTO processing_runs VALUES (?, ?, ?, ?, ?, ?)",
            (
                f"{paper_id}-stage0",
                paper_id,
                0,
                "stage0",
                stage0_status,
                stage0_error,
            ),
        )

    repository = PaperRepository(f"sqlite:///{database_path}")

    assert repository.get_paper(paper_id).source_published is expected_published


def test_repository_migrates_old_processing_runs_before_backfilling_source(
    tmp_path: Path,
):
    """Breaks if source backfill reads columns before their legacy migration."""
    database_path = tmp_path / "oldest-legacy.db"
    sources_dir = tmp_path / "papers"
    sources_dir.mkdir()
    (sources_dir / "oldest-legacy.pdf").write_bytes(b"%PDF-1.7\nlegacy source")
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE papers (
                id VARCHAR(36) PRIMARY KEY,
                original_filename VARCHAR NOT NULL,
                stored_filename VARCHAR NOT NULL,
                status VARCHAR(16) NOT NULL
            );
            CREATE TABLE processing_runs (
                id VARCHAR(36) PRIMARY KEY,
                paper_id VARCHAR(36) NOT NULL,
                status VARCHAR(16) NOT NULL,
                FOREIGN KEY(paper_id) REFERENCES papers(id)
            );
            """
        )
        connection.execute(
            "INSERT INTO papers VALUES (?, ?, ?, ?)",
            ("oldest-legacy", "oldest-legacy.pdf", "oldest-legacy.pdf", "completed"),
        )
        connection.execute(
            "INSERT INTO processing_runs VALUES (?, ?, ?)",
            ("oldest-legacy-run", "oldest-legacy", "completed"),
        )

    repository = PaperRepository(f"sqlite:///{database_path}")

    assert repository.get_paper("oldest-legacy").source_published is True
    assert repository.get_processing_statuses("oldest-legacy") == (
        ProcessingStatus.completed,
    )


def test_repository_backfills_existing_unpublished_legacy_source(tmp_path: Path):
    """Breaks if a prior default-false migration can never be repaired."""
    database_path = tmp_path / "already-migrated.db"
    sources_dir = tmp_path / "papers"
    sources_dir.mkdir()
    (sources_dir / "already-migrated.pdf").write_bytes(b"%PDF-1.7\nlegacy source")
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE papers (
                id VARCHAR(36) PRIMARY KEY,
                original_filename VARCHAR NOT NULL,
                stored_filename VARCHAR NOT NULL,
                status VARCHAR(16) NOT NULL,
                source_published BOOLEAN NOT NULL DEFAULT 0
            );
            CREATE TABLE processing_runs (
                id VARCHAR(36) PRIMARY KEY,
                paper_id VARCHAR(36) NOT NULL,
                sequence INTEGER NOT NULL,
                stage VARCHAR(16),
                status VARCHAR(16) NOT NULL,
                error_summary VARCHAR,
                FOREIGN KEY(paper_id) REFERENCES papers(id)
            );
            """
        )
        connection.execute(
            "INSERT INTO papers VALUES (?, ?, ?, ?, ?)",
            (
                "already-migrated",
                "already-migrated.pdf",
                "already-migrated.pdf",
                "completed",
                False,
            ),
        )
        connection.execute(
            "INSERT INTO processing_runs VALUES (?, ?, ?, ?, ?, ?)",
            ("already-migrated-run", "already-migrated", 0, "stage0", "completed", None),
        )

    repository = PaperRepository(f"sqlite:///{database_path}")

    assert repository.get_paper("already-migrated").source_published is True


def test_repository_returns_latest_durable_stage_status(repository) -> None:
    """Breaks if public summaries cannot ask for one stage's newest durable state."""
    paper = repository.create_paper(
        original_filename="example.pdf",
        stored_filename="paper.pdf",
        status=ProcessingStatus.running,
    )
    repository.record_processing_status(paper.id, ProcessingStatus.queued, stage="stage0")
    repository.record_processing_status(paper.id, ProcessingStatus.completed, stage="stage0")
    repository.record_processing_status(paper.id, ProcessingStatus.queued, stage="stage1")
    repository.record_processing_status(paper.id, ProcessingStatus.running, stage="stage1")

    assert repository.get_latest_stage_status(paper.id, "stage0") == ProcessingStatus.completed
    assert repository.get_latest_stage_status(paper.id, "stage1") == ProcessingStatus.running
    assert repository.get_latest_stage_status(paper.id, "missing") is None


def test_repository_lists_papers_by_filename_then_id(repository) -> None:
    """Breaks if a paper library is not returned in stable display order."""
    repository.create_paper(
        paper_id="zeta-id",
        original_filename="zeta.pdf",
        stored_filename="zeta.pdf",
    )
    repository.create_paper(
        paper_id="beta-id",
        original_filename="alpha.pdf",
        stored_filename="alpha-first.pdf",
    )
    repository.create_paper(
        paper_id="alpha-id",
        original_filename="alpha.pdf",
        stored_filename="alpha-second.pdf",
    )

    assert repository.list_papers() == (
        repository.get_paper("alpha-id"),
        repository.get_paper("beta-id"),
        repository.get_paper("zeta-id"),
    )


def test_repository_returns_newest_aggregate_processing_error(repository) -> None:
    """Breaks if multiple aggregate failures raise instead of returning the newest error."""
    paper = repository.create_paper(
        original_filename="example.pdf", stored_filename="paper.pdf"
    )
    repository.record_processing_status(
        paper.id, ProcessingStatus.failed, error_summary="older processing error"
    )
    repository.record_processing_status(
        paper.id, ProcessingStatus.partial, error_summary="newer processing error"
    )

    assert repository.get_processing_error(paper.id) == "newer processing error"


@pytest.mark.parametrize("target", ("section", "element", "note"))
@pytest.mark.parametrize("page_owner", ("missing", "other"))
def test_repository_rejects_non_owned_page_references(repository, target: str, page_owner: str) -> None:
    """Breaks if repository writes can point at missing or another paper's page."""
    paper = repository.create_paper(
        original_filename="first.pdf", stored_filename="first.pdf"
    )
    other_paper = repository.create_paper(
        original_filename="second.pdf", stored_filename="second.pdf"
    )
    page_number = 1
    if page_owner == "other":
        repository.save_page(other_paper.id, Page(number=page_number, width=200, height=300))

    if target == "section":
        operation = lambda: repository.save_section(
            paper.id, Section(title="Invalid", order=0, page_number=page_number)
        )
    elif target == "element":
        operation = lambda: repository.save_element(
            paper.id,
            DocumentElement.paragraph(
                "Invalid", page_number=page_number, bbox=BoundingBox(0, 0, 1, 0.1)
            ),
        )
    else:
        operation = lambda: repository.create_note(
            paper.id, Note(body="Invalid", page_number=page_number)
        )

    with pytest.raises(ValueError, match="page target does not belong to paper") as error:
        operation()
    assert type(error.value).__name__ == "PageReferenceError"


def test_repository_allows_owned_and_null_page_references(repository) -> None:
    """Breaks if repository page ownership validation rejects allowed references."""
    paper = repository.create_paper(
        original_filename="example.pdf", stored_filename="paper.pdf"
    )
    repository.save_page(paper.id, Page(number=1, width=200, height=300))

    repository.save_section(paper.id, Section(title="Located", order=0, page_number=1))
    repository.save_section(paper.id, Section(title="Unlocated", order=1))
    repository.save_element(
        paper.id,
        DocumentElement.paragraph(
            "Located", page_number=1, bbox=BoundingBox(0, 0, 1, 0.1)
        ),
    )
    repository.create_note(paper.id, Note(body="Located", page_number=1))
    repository.create_note(paper.id, Note(body="Unlocated"))


def _paper_with_located_element(repository: PaperRepository, name: str):
    paper = repository.create_paper(
        original_filename=f"{name}.pdf", stored_filename=f"{name}.pdf"
    )
    repository.save_page(paper.id, Page(number=1, width=200, height=300))
    element = repository.save_element(
        paper.id,
        DocumentElement.paragraph(
            f"{name} evidence", page_number=1, bbox=BoundingBox(0, 0, 1, 0.1)
        ),
    )
    return paper, element


def _conversation_for_paper(repository: PaperRepository):
    paper, element = _paper_with_located_element(repository, "conversation")
    conversation = repository.create_conversation(
        Conversation(paper_id=paper.id)
    )
    return paper, element, conversation


def _user_message(
    conversation,
    content: str,
    citation_element_ids: tuple[str, ...] = (),
    *,
    model_snapshot: ModelSnapshot | None = None,
    request_id: str | None = None,
):
    return ConversationMessage(
        conversation_id=conversation.id,
        paper_id=conversation.paper_id,
        role=AgentMessageRole.user,
        content=content,
        citation_element_ids=citation_element_ids,
        model_profile_id=None if model_snapshot is None else model_snapshot.profile_id,
        model_snapshot=model_snapshot,
        request_id=request_id,
    )


def _assistant_message(
    conversation,
    content: str,
    citation_element_ids: tuple[str, ...] = (),
    *,
    model_snapshot: ModelSnapshot | None = None,
    request_id: str | None = None,
    background_explanation: str | None = None,
):
    return ConversationMessage(
        conversation_id=conversation.id,
        paper_id=conversation.paper_id,
        role=AgentMessageRole.assistant,
        content=content,
        citation_element_ids=citation_element_ids,
        model_profile_id=None if model_snapshot is None else model_snapshot.profile_id,
        model_snapshot=model_snapshot,
        request_id=request_id,
        background_explanation=background_explanation,
    )


def _model_snapshot() -> ModelSnapshot:
    return ModelSnapshot(
        profile_id="10000000-0000-0000-0000-000000000001",
        display_name="Agent model",
        base_url="http://127.0.0.1:8000/v1",
        model_name="agent-model",
        revision=3,
    )


def test_repository_round_trips_paper_conversations_and_located_message_citations(repository):
    """Breaks if conversation reads lose their messages or source-element citations."""
    paper, element, conversation = _conversation_for_paper(repository)
    user = repository.append_conversation_message(_user_message(conversation, "Explain it."))
    assistant = repository.append_conversation_message(
        _assistant_message(conversation, "It routes tokens.", (element.id,))
    )

    assert repository.get_conversation(paper.id, conversation.id) == conversation
    assert repository.get_conversation("another-paper", conversation.id) is None
    assert repository.get_conversation_messages(paper.id, conversation.id) == (user, assistant)


def test_repository_round_trips_agent_model_audit_metadata_without_secrets(repository):
    """Breaks if a durable Agent turn loses audit parity or serializes credential fields."""
    paper, element, conversation = _conversation_for_paper(repository)
    snapshot = _model_snapshot()
    request_id = "20000000-0000-0000-0000-000000000002"
    user = repository.append_conversation_message(
        _user_message(
            conversation,
            "Explain it.",
            model_snapshot=snapshot,
            request_id=request_id,
        )
    )
    assistant = repository.append_conversation_message(
        _assistant_message(
            conversation,
            "It routes tokens.",
            (element.id,),
            model_snapshot=snapshot,
            request_id=request_id,
        )
    )

    assert repository.get_conversation_messages(paper.id, conversation.id) == (
        user,
        assistant,
    )
    with repository.engine.connect() as connection:
        payloads = connection.execute(
            select(conversation_messages.c.model_snapshot_json).order_by(
                conversation_messages.c.sequence
            )
        ).scalars().all()
    assert [set(json.loads(payload)) for payload in payloads] == [
        {"profile_id", "display_name", "base_url", "model_name", "revision"},
        {"profile_id", "display_name", "base_url", "model_name", "revision"},
    ]
    assert "secret" not in "".join(payloads).casefold()
    assert "api_key" not in "".join(payloads).casefold()


def test_repository_persists_exact_assistant_background_and_ordered_citation_snapshots(
    repository,
):
    """Breaks if replay depends on mutable element geometry or citation ID sorting."""
    paper, first, conversation = _conversation_for_paper(repository)
    second = repository.save_element(
        paper.id,
        DocumentElement.paragraph(
            "second evidence",
            page_number=1,
            bbox=BoundingBox(0.2, 0.3, 0.8, 0.4),
        ),
    )
    assistant = repository.append_conversation_message(
        _assistant_message(
            conversation,
            "Grounded answer",
            (second.id, first.id),
            model_snapshot=_model_snapshot(),
            request_id="20000000-0000-0000-0000-000000000020",
            background_explanation="Stable external background.",
        )
    )

    with repository.engine.begin() as connection:
        connection.execute(
            document_elements.update()
            .where(document_elements.c.id == second.id)
            .values(kind="changed", page_number=1, bbox_x0=0.0, bbox_y0=0.0,
                    bbox_x1=0.1, bbox_y1=0.1)
        )

    reloaded = repository.get_conversation_messages(paper.id, conversation.id)[0]

    assert reloaded.background_explanation == "Stable external background."
    assert reloaded.citation_element_ids == (second.id, first.id)
    assert [snapshot.id for snapshot in reloaded.citation_snapshots] == [
        second.id,
        first.id,
    ]
    assert reloaded.citation_snapshots[0].kind == second.kind
    assert reloaded.citation_snapshots[0].bbox == second.bbox
    with repository.engine.connect() as connection:
        citation_rows = connection.execute(
            select(
                conversation_message_citations.c.ordinal,
                conversation_message_citations.c.citation_snapshot_json,
            ).where(conversation_message_citations.c.message_id == assistant.id)
        ).all()
    assert sorted(row.ordinal for row in citation_rows) == [0, 1]
    payloads = [row.citation_snapshot_json for row in citation_rows]
    assert all(set(json.loads(payload)) == {"id", "kind", "page_number", "bbox"} for payload in payloads)
    assert "secret" not in "".join(payloads).casefold()
    assert "api_key" not in "".join(payloads).casefold()


def test_repository_ignores_corrupt_or_non_allowlisted_citation_snapshots(repository):
    """Breaks if citation snapshot corruption or hidden fields reach replay callers."""
    paper, element, conversation = _conversation_for_paper(repository)
    assistant = repository.append_conversation_message(
        _assistant_message(conversation, "Answer", (element.id,))
    )
    with repository.engine.begin() as connection:
        connection.execute(
            conversation_message_citations.update()
            .where(conversation_message_citations.c.message_id == assistant.id)
            .values(
                citation_snapshot_json=(
                    '{"id":"' + element.id + '","kind":"paragraph",'
                    '"page_number":1,"bbox":{"x0":0,"y0":0,"x1":1,"y1":0.1},'
                    '"api_key":"must-not-escape"}'
                )
            )
        )

    reloaded = repository.get_conversation_messages(paper.id, conversation.id)[0]

    assert reloaded.citation_element_ids == (element.id,)
    assert reloaded.citation_snapshots == (None,)
    assert "must-not-escape" not in repr(reloaded)


def test_repository_sanitizes_valid_but_mismatched_model_provenance_pairs(repository):
    """Breaks if syntactically valid false provenance is exposed for either turn row."""
    paper, _, conversation = _conversation_for_paper(repository)
    request_id = "20000000-0000-0000-0000-000000000021"
    snapshot = _model_snapshot()
    user = repository.append_conversation_message(
        _user_message(
            conversation,
            "Question",
            model_snapshot=snapshot,
            request_id=request_id,
        )
    )
    assistant = repository.append_conversation_message(
        _assistant_message(
            conversation,
            "Answer",
            model_snapshot=snapshot,
            request_id=request_id,
        )
    )
    other_profile_id = "10000000-0000-0000-0000-000000000002"
    with repository.engine.begin() as connection:
        connection.execute(
            conversation_messages.update()
            .where(conversation_messages.c.id == assistant.id)
            .values(
                model_profile_id=other_profile_id,
                model_snapshot_json=(
                    '{"base_url":"http://127.0.0.1:8000/v1",'
                    '"display_name":"Other","model_name":"other",'
                    f'"profile_id":"{other_profile_id}","revision":1}}'
                ),
            )
        )

    complete = repository.get_agent_turn_by_request(paper.id, request_id)
    history = repository.get_conversation_messages(paper.id, conversation.id)

    assert complete is not None
    assert all(message.model_profile_id is None for message in complete)
    assert all(message.model_snapshot is None for message in complete)
    assert all(message.model_profile_id is None for message in history)
    assert all(message.model_snapshot is None for message in history)

    with repository.engine.begin() as connection:
        connection.execute(
            conversation_messages.update()
            .where(conversation_messages.c.id == user.id)
            .values(model_snapshot_json=connection.execute(
                select(conversation_messages.c.model_snapshot_json).where(
                    conversation_messages.c.id == assistant.id
                )
            ).scalar_one())
        )
    row_mismatch = repository.get_conversation_messages(paper.id, conversation.id)
    assert row_mismatch[0].model_snapshot is None


def test_repository_rejects_valid_snapshot_whose_profile_does_not_match_its_row(
    repository,
):
    """Breaks if a single partial row can bind a valid snapshot from another profile."""
    paper, _, conversation = _conversation_for_paper(repository)
    request_id = "20000000-0000-0000-0000-000000000022"
    user = repository.append_conversation_message(
        _user_message(
            conversation,
            "Partial question",
            model_snapshot=_model_snapshot(),
            request_id=request_id,
        )
    )
    other_profile_id = "10000000-0000-0000-0000-000000000002"
    with repository.engine.begin() as connection:
        connection.execute(
            conversation_messages.update()
            .where(conversation_messages.c.id == user.id)
            .values(
                model_snapshot_json=(
                    '{"base_url":"http://127.0.0.1:8000/v1",'
                    '"display_name":"Other","model_name":"other",'
                    f'"profile_id":"{other_profile_id}","revision":1}}'
                )
            )
        )

    partial = repository.get_agent_user_message_by_request(paper.id, request_id)

    assert partial is not None
    assert partial.model_profile_id == _model_snapshot().profile_id
    assert partial.model_snapshot is None


def test_repository_returns_only_a_complete_agent_pair_for_a_request(repository):
    """Breaks if complete replay accepts a partial, cross-paper, or malformed request set."""
    paper, element, conversation = _conversation_for_paper(repository)
    snapshot = _model_snapshot()
    request_id = "20000000-0000-0000-0000-000000000003"
    user = repository.append_conversation_message(
        _user_message(
            conversation,
            "Explain it.",
            model_snapshot=snapshot,
            request_id=request_id,
        )
    )

    assert repository.get_agent_turn_by_request(paper.id, request_id) is None
    assert repository.get_agent_user_message_by_request(paper.id, request_id) == user
    assert repository.get_agent_user_message_by_request("another-paper", request_id) is None

    assistant = repository.append_conversation_message(
        _assistant_message(
            conversation,
            "It routes tokens.",
            (element.id,),
            model_snapshot=snapshot,
            request_id=request_id,
        )
    )

    assert repository.get_agent_turn_by_request(paper.id, request_id) == (
        user,
        assistant,
    )
    assert repository.get_agent_user_message_by_request(paper.id, request_id) is None
    assert repository.get_agent_turn_by_request("another-paper", request_id) is None


def test_repository_reads_legacy_and_corrupt_snapshot_rows_as_model_null(repository):
    """Breaks if old rows fail history reads or corrupt/secret-bearing JSON reaches callers."""
    paper, _, conversation = _conversation_for_paper(repository)
    legacy = repository.append_conversation_message(
        _user_message(conversation, "Legacy question")
    )
    corrupt = repository.append_conversation_message(
        _assistant_message(
            conversation,
            "Corrupt snapshot answer",
            model_snapshot=_model_snapshot(),
            request_id="20000000-0000-0000-0000-000000000004",
        )
    )
    credential_url = repository.append_conversation_message(
        _assistant_message(
            conversation,
            "Credential URL snapshot answer",
            model_snapshot=_model_snapshot(),
            request_id="20000000-0000-0000-0000-000000000005",
        )
    )
    with repository.engine.begin() as connection:
        connection.execute(
            conversation_messages.update()
            .where(conversation_messages.c.id == corrupt.id)
            .values(model_snapshot_json='{"api_key":"do-not-expose"}')
        )
        connection.execute(
            conversation_messages.update()
            .where(conversation_messages.c.id == credential_url.id)
            .values(
                model_snapshot_json=(
                    '{"base_url":"http://credential-secret@127.0.0.1:8000/v1",'
                    '"display_name":"Agent model","model_name":"agent-model",'
                    '"profile_id":"10000000-0000-0000-0000-000000000001",'
                    '"revision":3}'
                )
            )
        )

    reloaded = repository.get_conversation_messages(paper.id, conversation.id)

    assert reloaded[0] == legacy
    assert reloaded[0].model_snapshot is None
    assert reloaded[1].model_snapshot is None
    assert reloaded[2].model_snapshot is None
    assert "do-not-expose" not in repr(reloaded)
    assert "credential-secret" not in repr(reloaded)


def test_repository_returns_messages_in_durable_sequence_order(repository):
    """Breaks if a full reload is capped or uses unstable SQLite insertion order."""
    paper, _, conversation = _conversation_for_paper(repository)
    persisted = tuple(
        repository.append_conversation_message(
            _user_message(conversation, f"message-{index}")
        )
        for index in range(8)
    )

    messages = repository.get_conversation_messages(paper.id, conversation.id)

    assert [message.id for message in messages] == [message.id for message in persisted]
    assert [message.sequence for message in messages] == list(range(8))


def test_repository_limits_recent_messages_in_sql_and_returns_chronological_order(
    repository,
):
    """Breaks if bounded model history still fetches the full durable conversation."""
    paper, _, conversation = _conversation_for_paper(repository)
    for index in range(10):
        repository.append_conversation_message(
            _user_message(conversation, f"message-{index}")
        )

    statements: list[str] = []

    def capture_statement(
        connection, cursor, statement, parameters, context, executemany
    ) -> None:
        statements.append(statement)

    event.listen(repository.engine, "before_cursor_execute", capture_statement)
    try:
        messages = repository.get_conversation_messages(
            paper.id, conversation.id, limit=6
        )
    finally:
        event.remove(repository.engine, "before_cursor_execute", capture_statement)

    assert [message.content for message in messages] == [
        "message-4",
        "message-5",
        "message-6",
        "message-7",
        "message-8",
        "message-9",
    ]
    message_reads = [
        statement
        for statement in statements
        if "conversation_messages.content" in statement
        and "FROM conversation_messages" in statement
        and "FROM conversation_message_citations" not in statement
    ]
    assert len(message_reads) == 1
    normalized_read = " ".join(message_reads[0].upper().split())
    descending_order = "ORDER BY CONVERSATION_MESSAGES.SEQUENCE DESC"
    assert descending_order in normalized_read
    assert normalized_read.index(descending_order) < normalized_read.index("LIMIT")


def test_repository_batches_citations_for_the_returned_message_set(repository):
    """Breaks if conversation reads issue one citation query for every message."""
    paper, _, conversation = _conversation_for_paper(repository)
    first_citation = repository.save_element(
        paper.id,
        DocumentElement(
            id="citation-z",
            kind="paragraph",
            text="First citation",
            page_number=1,
            bbox=BoundingBox(0, 0.1, 1, 0.2),
        ),
    )
    second_citation = repository.save_element(
        paper.id,
        DocumentElement(
            id="citation-a",
            kind="paragraph",
            text="Second citation",
            page_number=1,
            bbox=BoundingBox(0, 0.2, 1, 0.3),
        ),
    )
    repository.append_conversation_message(_user_message(conversation, "Question"))
    for index in range(3):
        repository.append_conversation_message(
            _assistant_message(
                conversation,
                f"Answer {index}",
                (first_citation.id, second_citation.id),
            )
        )

    statements: list[str] = []

    def capture_statement(
        connection, cursor, statement, parameters, context, executemany
    ) -> None:
        statements.append(statement)

    event.listen(repository.engine, "before_cursor_execute", capture_statement)
    try:
        messages = repository.get_conversation_messages(paper.id, conversation.id)
    finally:
        event.remove(repository.engine, "before_cursor_execute", capture_statement)

    message_reads = [
        statement
        for statement in statements
        if "conversation_messages.content" in statement
        and "FROM conversation_messages" in statement
        and "FROM conversation_message_citations" not in statement
    ]
    citation_reads = [
        statement
        for statement in statements
        if "FROM conversation_message_citations" in statement
    ]
    assert len(message_reads) == 1
    assert len(citation_reads) == 1
    assert [message.citation_element_ids for message in messages] == [
        (),
        ("citation-z", "citation-a"),
        ("citation-z", "citation-a"),
        ("citation-z", "citation-a"),
    ]


def test_repository_rejects_user_message_citations(repository):
    """Breaks if untrusted user text can be persisted as a sourced paper claim."""
    _, element, conversation = _conversation_for_paper(repository)

    with pytest.raises(ConversationReferenceError, match="user"):
        repository.append_conversation_message(
            _user_message(conversation, "This is sourced.", (element.id,))
        )


@pytest.mark.parametrize("conversation_id", ("missing-conversation", "other-conversation"))
def test_repository_rejects_unknown_or_mismatched_message_conversations(
    repository, conversation_id: str
):
    """Breaks if a message can be appended without an owned conversation."""
    paper, _, conversation = _conversation_for_paper(repository)
    if conversation_id == "other-conversation":
        _, _, other_conversation = _conversation_for_paper(repository)
        message = ConversationMessage(
            conversation_id=other_conversation.id,
            paper_id=paper.id,
            role=AgentMessageRole.user,
            content="Wrong paper conversation.",
        )
    else:
        message = ConversationMessage(
            conversation_id=conversation_id,
            paper_id=conversation.paper_id,
            role=AgentMessageRole.user,
            content="Unknown conversation.",
        )

    with pytest.raises(ConversationReferenceError, match="conversation"):
        repository.append_conversation_message(message)


def test_repository_rejects_a_message_citation_from_another_paper(repository):
    """Breaks if a paper answer can cite an element from a different paper."""
    _, _, conversation = _conversation_for_paper(repository)
    _, second_element = _paper_with_located_element(repository, "second-citation")

    with pytest.raises(ConversationReferenceError, match="citation"):
        repository.append_conversation_message(
            _assistant_message(conversation, "A grounded answer.", (second_element.id,))
        )


def test_repository_rejects_unlocated_message_citations(repository):
    """Breaks if a citation can point at semantic text without a source location."""
    paper, _, conversation = _conversation_for_paper(repository)
    unlocated = repository.save_element(
        paper.id, DocumentElement.paragraph("semantic only", location_status="unlocated")
    )

    with pytest.raises(ConversationReferenceError, match="citation"):
        repository.append_conversation_message(
            _assistant_message(conversation, "A grounded answer.", (unlocated.id,))
        )


def test_repository_rejects_duplicate_assistant_citations_before_persistence(
    repository,
):
    """Breaks if duplicate citation IDs escape validation as a raw database error."""
    paper, element, conversation = _conversation_for_paper(repository)

    statements: list[str] = []

    def capture_statement(
        connection, cursor, statement, parameters, context, executemany
    ) -> None:
        statements.append(statement)

    event.listen(repository.engine, "before_cursor_execute", capture_statement)
    try:
        with pytest.raises(ConversationReferenceError, match="duplicate"):
            repository.append_conversation_message(
                _assistant_message(
                    conversation,
                    "Repeated citation.",
                    (element.id, element.id),
                )
            )
    finally:
        event.remove(repository.engine, "before_cursor_execute", capture_statement)

    assert not [
        statement
        for statement in statements
        if statement.lstrip().upper().startswith("INSERT INTO CONVERSATION_MESSAGES")
    ]
    assert repository.get_conversation_messages(paper.id, conversation.id) == ()


def _deep_nodes(element_id: str) -> tuple[GraphNode, ...]:
    return (
        GraphNode(
            id="old-deep-node",
            node_type="component",
            name="Encoder",
            summary="Encodes inputs.",
            stage=GraphStage.deep,
            evidence_element_ids=(element_id,),
        ),
    )


def _duplicate_nodes(element_id: str) -> tuple[GraphNode, ...]:
    return (
        GraphNode(
            id="duplicate-one",
            node_type="method",
            name="Router",
            summary="Routes tokens.",
            stage=GraphStage.core,
            evidence_element_ids=(element_id,),
        ),
        GraphNode(
            id="duplicate-two",
            node_type="method",
            name="router",
            summary="Routes inputs.",
            stage=GraphStage.core,
            evidence_element_ids=(element_id,),
        ),
    )


def test_repository_rejects_graph_evidence_from_another_paper(repository):
    """Breaks if graph claims can cite a source element from another paper."""
    first, _ = _paper_with_located_element(repository, "first")
    _, second_element = _paper_with_located_element(repository, "second")
    node = GraphNode(
        node_type="method",
        name="Router",
        summary="Routes tokens.",
        stage=GraphStage.core,
        evidence_element_ids=(second_element.id,),
    )

    with pytest.raises(GraphReferenceError, match="evidence"):
        repository.replace_graph_stage(first.id, GraphStage.core, (node,), ())


def test_repository_rejects_unlocated_graph_evidence(repository):
    """Breaks if a graph can cite semantic content without a source location."""
    paper = repository.create_paper(
        original_filename="paper.pdf", stored_filename="paper.pdf"
    )
    element = repository.save_element(
        paper.id, DocumentElement.paragraph("semantic only", location_status="unlocated")
    )
    node = GraphNode(
        node_type="method",
        name="Router",
        summary="Routes tokens.",
        stage=GraphStage.core,
        evidence_element_ids=(element.id,),
    )

    with pytest.raises(GraphReferenceError, match="evidence"):
        repository.replace_graph_stage(paper.id, GraphStage.core, (node,), ())


def test_repository_rejects_edge_evidence_from_another_paper(repository):
    """Breaks if a graph relation can cite source evidence from another paper."""
    first, first_element = _paper_with_located_element(repository, "first")
    _, second_element = _paper_with_located_element(repository, "second")
    nodes = (
        GraphNode(
            id="first-node",
            node_type="method",
            name="Router",
            summary="Routes tokens.",
            stage=GraphStage.core,
            evidence_element_ids=(first_element.id,),
        ),
        GraphNode(
            id="second-node",
            node_type="claim",
            name="Efficiency",
            summary="Improves efficiency.",
            stage=GraphStage.core,
            evidence_element_ids=(first_element.id,),
        ),
    )
    edge = GraphEdge(
        source_node_id="first-node",
        target_node_id="second-node",
        relation_type="supports",
        stage=GraphStage.core,
        evidence_element_ids=(second_element.id,),
    )

    with pytest.raises(GraphReferenceError, match="evidence"):
        repository.replace_graph_stage(first.id, GraphStage.core, nodes, (edge,))


def test_core_replacement_rolls_back_and_preserves_existing_graph(repository):
    """Breaks if a failed core replacement deletes a prior deep graph."""
    paper, element = _paper_with_located_element(repository, "paper")
    old = _deep_nodes(element.id)
    repository.replace_graph_stage(paper.id, GraphStage.deep, old, ())

    with pytest.raises(IntegrityError):
        repository.replace_graph_stage(
            paper.id, GraphStage.core, _duplicate_nodes(element.id), ()
        )

    assert repository.get_graph(paper.id).nodes == old


def test_repository_rejects_canonical_equivalent_graph_node_names(repository):
    """Breaks if direct persistence stores canonical-equivalent node names separately."""
    paper, element = _paper_with_located_element(repository, "paper")
    nodes = (
        GraphNode(
            id="cafe-nfc",
            node_type="method",
            name="Caf\u00e9",
            summary="Normalizes Unicode node names.",
            stage=GraphStage.core,
            evidence_element_ids=(element.id,),
        ),
        GraphNode(
            id="cafe-nfd",
            node_type="method",
            name="Cafe\u0301",
            summary="Rejects duplicate durable identities.",
            stage=GraphStage.core,
            evidence_element_ids=(element.id,),
        ),
    )

    with pytest.raises(IntegrityError):
        repository.replace_graph_stage(paper.id, GraphStage.core, nodes, ())

    assert repository.get_graph(paper.id).nodes == ()


def test_graph_replacement_normalizes_names_and_respects_stage_dependencies(repository):
    """Breaks if case variants coexist or stage replacement clears the wrong records."""
    paper, element = _paper_with_located_element(repository, "paper")
    core = GraphNode(
        id="core-method",
        node_type="method",
        name="Router",
        summary="Routes tokens.",
        stage=GraphStage.core,
        evidence_element_ids=(element.id,),
    )
    deep = _deep_nodes(element.id)
    repository.replace_graph_stage(paper.id, GraphStage.core, (core,), ())
    repository.replace_graph_stage(paper.id, GraphStage.deep, deep, ())

    with repository.engine.connect() as connection:
        assert connection.execute(
            select(graph_nodes.c.normalized_name).where(graph_nodes.c.id == core.id)
        ).scalar_one() == "router"

    replacement = GraphNode(
        id="replacement-component",
        node_type="component",
        name="Decoder",
        summary="Decodes outputs.",
        stage=GraphStage.deep,
        evidence_element_ids=(element.id,),
    )
    repository.replace_graph_stage(paper.id, GraphStage.deep, (replacement,), ())
    assert repository.get_graph(paper.id).nodes == (core, replacement)

    replacement_core = GraphNode(
        id="replacement-method",
        node_type="method",
        name="Retriever",
        summary="Retrieves context.",
        stage=GraphStage.core,
        evidence_element_ids=(element.id,),
    )
    assert repository.replace_graph_stage(
        paper.id, GraphStage.core, (replacement_core,), ()
    ).nodes == (replacement_core,)
    assert repository.get_graph(paper.id).nodes == (replacement_core,)


def test_repository_reads_graph_nodes_neighbors_subgraphs_and_directed_paths(repository):
    """Breaks if graph reads or traversal omit reachable same-paper records."""
    paper, element = _paper_with_located_element(repository, "paper")
    nodes = (
        GraphNode(
            id="node-a",
            node_type="method",
            name="Router",
            summary="Routes tokens.",
            stage=GraphStage.core,
            evidence_element_ids=(element.id,),
        ),
        GraphNode(
            id="node-b",
            node_type="claim",
            name="Efficiency",
            summary="Improves efficiency.",
            stage=GraphStage.core,
            evidence_element_ids=(element.id,),
        ),
        GraphNode(
            id="node-c",
            node_type="experiment",
            name="Benchmark",
            summary="Tests the method.",
            stage=GraphStage.core,
            evidence_element_ids=(element.id,),
        ),
    )
    edges = (
        GraphEdge(
            id="edge-a-b",
            source_node_id="node-a",
            target_node_id="node-b",
            relation_type="supports",
            stage=GraphStage.core,
            evidence_element_ids=(element.id,),
        ),
        GraphEdge(
            id="edge-b-c",
            source_node_id="node-b",
            target_node_id="node-c",
            relation_type="tests",
            stage=GraphStage.core,
            evidence_element_ids=(element.id,),
        ),
    )
    repository.replace_graph_stage(paper.id, GraphStage.core, nodes, edges)

    assert repository.get_graph_node(paper.id, "missing") is None
    assert repository.get_graph_node(paper.id, "node-b") == nodes[1]
    assert repository.get_graph_neighbors(paper.id, "node-b") == PaperGraph(
        nodes=nodes,
        edges=edges,
    )
    assert repository.get_graph_subgraph(paper.id, ("node-a",), depth=1) == PaperGraph(
        nodes=nodes[:2], edges=edges[:1]
    )
    assert repository.get_graph_subgraph(paper.id, ("node-a",), depth=2) == PaperGraph(
        nodes=nodes, edges=edges
    )
    assert repository.find_graph_paths(paper.id, "node-a", "node-c", max_depth=2) == (
        ("node-a", "node-b", "node-c"),
    )
    assert repository.find_graph_paths(paper.id, "node-c", "node-a", max_depth=2) == ()


PAPER_OWNED_TABLES = (
    "conversation_message_note_citations",
    "conversation_message_anchors",
    "conversation_message_citations",
    "conversation_messages",
    "conversations",
    "selection_assist_requests",
    "note_anchors",
    "notes",
    "highlights",
    "text_anchor_rects",
    "text_anchors",
    "graph_edge_evidence",
    "graph_node_evidence",
    "graph_edges",
    "graph_nodes",
    "document_elements",
    "sections",
    "pages",
    "processing_runs",
)


def _fully_populated_paper(repository: PaperRepository) -> Paper:
    paper = repository.create_paper(
        original_filename="full.pdf", stored_filename="full.pdf"
    )
    repository.save_page(paper.id, Page(number=1, width=200, height=300))
    section = repository.save_section(
        paper.id, Section(title="Method", order=0, page_number=1)
    )
    element = repository.save_element(
        paper.id,
        DocumentElement.paragraph(
            "router text",
            page_number=1,
            bbox=BoundingBox(0, 0, 1, 0.1),
            section_id=section.id,
        ),
    )
    note = repository.create_note(paper.id, Note(body="note body", page_number=1))
    conversation = repository.create_conversation(
        Conversation(paper_id=paper.id)
    )
    user = repository.append_conversation_message(
        ConversationMessage(
            conversation_id=conversation.id,
            paper_id=paper.id,
            role=AgentMessageRole.user,
            content="question",
            request_id="delete-request",
        )
    )
    assistant = repository.append_conversation_message(
        ConversationMessage(
            conversation_id=conversation.id,
            paper_id=paper.id,
            role=AgentMessageRole.assistant,
            content="answer",
            citation_element_ids=(element.id,),
            request_id="delete-request",
        )
    )
    repository.replace_graph_stage(
        paper.id,
        GraphStage.core,
        (
            GraphNode(
                id="node-1",
                node_type="method",
                name="Router",
                summary="Routes tokens.",
                stage=GraphStage.core,
                evidence_element_ids=(element.id,),
            ),
        ),
        (),
    )
    repository.record_processing_status(
        paper.id, ProcessingStatus.completed, stage="stage1"
    )
    with repository.engine.begin() as connection:
        connection.execute(
            insert(model_profiles).values(
                [
                    {
                        "id": "profile-a",
                        "display_name": "First",
                        "base_url": "http://localhost:8000/v1",
                        "model_name": "first",
                        "enabled": True,
                        "is_default": True,
                        "revision": 1,
                        "created_at": "2026-08-23T00:00:00+00:00",
                        "updated_at": "2026-08-23T00:00:00+00:00",
                    },
                    {
                        "id": "profile-b",
                        "display_name": "Second",
                        "base_url": "http://localhost:8000/v1",
                        "model_name": "second",
                        "enabled": True,
                        "is_default": False,
                        "revision": 1,
                        "created_at": "2026-08-23T00:00:00+00:00",
                        "updated_at": "2026-08-23T00:00:00+00:00",
                    },
                ]
            )
        )
        connection.execute(
            insert(text_anchors).values(
                id="anchor-1",
                paper_id=paper.id,
                quote="router text",
                quote_hash="hash-1",
                element_id=element.id,
                page_number=1,
                created_at="2026-08-23T00:00:00+00:00",
            )
        )
        connection.execute(
            insert(text_anchor_rects).values(
                anchor_id="anchor-1",
                order_index=0,
                paper_id=paper.id,
                x0=0,
                y0=0,
                x1=1,
                y1=0.1,
            )
        )
        connection.execute(
            insert(highlights).values(
                id="highlight-1",
                paper_id=paper.id,
                anchor_id="anchor-1",
                color="yellow",
                created_at="2026-08-23T00:00:00+00:00",
            )
        )
        connection.execute(
            insert(note_anchors).values(
                note_id=note.id,
                paper_id=paper.id,
                anchor_id="anchor-1",
            )
        )
        connection.execute(
            insert(selection_assist_requests).values(
                id="assist-1",
                paper_id=paper.id,
                request_id="assist-request",
                action="explain",
                status="running",
                created_at="2026-08-23T00:00:00+00:00",
            )
        )
        connection.execute(
            insert(conversation_message_anchors).values(
                paper_id=paper.id, message_id=user.id, anchor_id="anchor-1"
            )
        )
        connection.execute(
            insert(conversation_message_note_citations).values(
                paper_id=paper.id,
                message_id=assistant.id,
                note_id=note.id,
                ordinal=0,
            )
        )
    return paper


def test_delete_paper_data_removes_every_paper_owned_row(repository):
    """Breaks if paper deletion leaves any owned row or touches model profiles."""
    paper = _fully_populated_paper(repository)

    deleted = repository.delete_paper_data(paper.id)

    assert deleted is True
    with repository.engine.connect() as connection:
        for table_name in PAPER_OWNED_TABLES:
            count = connection.exec_driver_sql(
                f"SELECT COUNT(*) FROM {table_name} WHERE paper_id = ?",
                (paper.id,),
            ).scalar_one()
            assert count == 0, table_name
        assert connection.exec_driver_sql(
            "SELECT COUNT(*) FROM model_profiles"
        ).scalar_one() == 2


def test_delete_rolls_back_all_rows_when_a_middle_delete_fails(
    repository, monkeypatch
):
    """Breaks if a failed delete commits partial row removal."""
    paper = _fully_populated_paper(repository)
    original = repository._delete_rows
    calls = 0

    def fail_in_middle(connection, table, paper_id):
        nonlocal calls
        calls += 1
        if calls == 5:
            raise RuntimeError("synthetic delete failure")
        return original(connection, table, paper_id)

    monkeypatch.setattr(repository, "_delete_rows", fail_in_middle)

    with pytest.raises(RuntimeError, match="synthetic delete failure"):
        repository.delete_paper_data(paper.id)

    assert repository.get_paper(paper.id) is not None
    with repository.engine.connect() as connection:
        assert connection.exec_driver_sql(
            "SELECT COUNT(*) FROM notes WHERE paper_id = ?", (paper.id,)
        ).scalar_one() == 1
