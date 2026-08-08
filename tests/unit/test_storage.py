from pathlib import Path

import pytest
from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError

from paper_agent.database import document_elements
from paper_agent.domain import BoundingBox, DocumentElement, Note, Page, Section
from paper_agent.storage import PaperRepository


@pytest.fixture
def repository(tmp_path: Path) -> PaperRepository:
    return PaperRepository(f"sqlite:///{tmp_path / 'paper-agent.db'}")


def test_repository_round_trips_locatable_and_unlocated_elements(repository):
    """Breaks if semantic-only elements gain location data or lose save order."""
    paper = repository.create_paper(
        original_filename="example.pdf", stored_filename="paper.pdf"
    )
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
