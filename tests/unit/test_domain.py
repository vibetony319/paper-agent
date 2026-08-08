import pytest

from paper_agent.domain import BoundingBox, DocumentElement, ProcessingStatus


def test_bounding_box_is_normalized_and_clamped():
    """Breaks if page-coordinate conversion leaks out-of-bounds geometry."""
    bbox = BoundingBox.from_page_rect((-10, 20, 70, 120), 100, 100)

    assert bbox == BoundingBox(x0=0.0, y0=0.2, x1=0.7, y1=1.0)


def test_unlocated_element_cannot_have_page_or_geometry():
    """Breaks if semantic-only content can falsely claim a source location."""
    with pytest.raises(ValueError, match="unlocated"):
        DocumentElement.paragraph(
            text="semantic only", location_status="unlocated", page_number=1
        )

    with pytest.raises(ValueError, match="unlocated"):
        DocumentElement.paragraph(
            text="semantic only",
            location_status="unlocated",
            bbox=BoundingBox(0, 0, 1, 0.1),
        )


def test_located_element_requires_a_complete_source_location():
    """Breaks if a purportedly located element can be persisted half-located."""
    with pytest.raises(ValueError, match="located"):
        DocumentElement.paragraph(text="missing geometry", page_number=1)


def test_processing_status_exposes_persisted_state_values():
    """Breaks if persisted processing states drift from the stable contract."""
    assert [status.value for status in ProcessingStatus] == [
        "queued",
        "running",
        "completed",
        "partial",
        "failed",
    ]
