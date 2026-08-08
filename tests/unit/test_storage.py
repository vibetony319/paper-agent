import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError

from paper_agent.database import document_elements, graph_nodes, notes, sections
from paper_agent.domain import (
    BoundingBox,
    DocumentElement,
    GraphEdge,
    GraphNode,
    GraphStage,
    Note,
    Page,
    PaperGraph,
    ProcessingStatus,
    Section,
)
from paper_agent.storage import GraphReferenceError, PaperRepository


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
