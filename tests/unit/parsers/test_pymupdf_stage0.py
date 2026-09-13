import struct

import pymupdf
import pytest

from paper_agent.domain import BoundingBox
from paper_agent.parsers.base import PdfParseError
from paper_agent.parsers.pymupdf_stage0 import PyMuPdfStage0Parser


def test_stage0_extracts_page_text_and_normalized_block_location(sample_pdf):
    """Breaks if source text geometry is not preserved in normalized page coordinates."""
    result = PyMuPdfStage0Parser().parse(sample_pdf)

    assert result.pages[0].number == 1
    assert result.text_blocks[0].text == "Sample body text."
    assert result.text_blocks[0].bbox.x0 == pytest.approx(0.1)
    assert 0 <= result.text_blocks[0].bbox.y0 < 1


def test_stage0_extracts_image_and_drawing_geometry(visual_pdf):
    """Breaks if source image or vector candidates are omitted or lose location."""
    result = PyMuPdfStage0Parser().parse(visual_pdf)

    assert [(item.kind, item.page_number) for item in result.visual_elements] == [
        ("image", 1),
        ("drawing", 1),
    ]
    assert result.visual_elements[0].bbox.x0 == pytest.approx(0.1)
    assert result.visual_elements[1].bbox.y0 == pytest.approx(0.6)
    assert all(item.caption is None for item in result.visual_elements)


def test_stage0_skips_drawing_rectangles_that_normalize_to_empty(
    visual_pdf_with_empty_drawings,
):
    """Breaks if empty or fully off-page drawing candidates become visuals."""
    result = PyMuPdfStage0Parser().parse(visual_pdf_with_empty_drawings)

    assert [item.kind for item in result.visual_elements] == ["drawing"]
    assert result.visual_elements[0].bbox.x0 == pytest.approx(0.1)


def test_stage0_uses_display_space_for_rotated_cropped_pdf_geometry_and_png(
    rotated_cropped_pdf,
):
    """Breaks if stored geometry disagrees with the rotated CropBox PNG space."""
    parser = PyMuPdfStage0Parser()
    result = parser.parse(rotated_cropped_pdf)

    with pymupdf.open(rotated_cropped_pdf) as document:
        page = document[0]
        expected_page_rect = page.rect
        expected_text_rect = pymupdf.Rect(page.get_text("blocks")[0][:4]) * page.rotation_matrix
        expected_drawing_rect = page.get_drawings()[0]["rect"] * page.rotation_matrix

    png_width, png_height = struct.unpack(">II", parser.render_page_png(rotated_cropped_pdf, 1)[16:24])
    assert (result.pages[0].width, result.pages[0].height) == pytest.approx(
        (expected_page_rect.width, expected_page_rect.height)
    )
    assert (png_width, png_height) == pytest.approx(
        (result.pages[0].width, result.pages[0].height)
    )
    assert result.text_blocks[0].bbox == BoundingBox.from_page_rect(
        tuple(expected_text_rect), expected_page_rect.width, expected_page_rect.height
    )
    drawing = next(item for item in result.visual_elements if item.kind == "drawing")
    assert drawing.bbox == BoundingBox.from_page_rect(
        tuple(expected_drawing_rect), expected_page_rect.width, expected_page_rect.height
    )


def test_stage0_rejects_a_malformed_pdf(tmp_path):
    """Breaks if parsing leaks backend-specific details for unreadable PDFs."""
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"not a pdf")

    with pytest.raises(PdfParseError, match="could not be opened"):
        PyMuPdfStage0Parser().parse(broken)


def test_stage0_rejects_authentication_required_pdf(encrypted_pdf):
    """Breaks if encrypted sources leak PyMuPDF authentication errors."""
    with pytest.raises(PdfParseError, match="requires authentication"):
        PyMuPdfStage0Parser().parse(encrypted_pdf)
