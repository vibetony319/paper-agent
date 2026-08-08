import pytest

from paper_agent.parsers.base import PdfParseError
from paper_agent.parsers.pymupdf_stage0 import PyMuPdfStage0Parser


def test_stage0_extracts_page_text_and_normalized_block_location(sample_pdf):
    """Breaks if source text geometry is not preserved in normalized page coordinates."""
    result = PyMuPdfStage0Parser().parse(sample_pdf)

    assert result.pages[0].number == 1
    assert result.text_blocks[0].text == "Introduction"
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
