from pathlib import Path

import pymupdf
import pytest

from paper_agent.parsers.pymupdf_stage1 import PyMuPdfStage1Parser, Stage1ParseError


def _write_pdf(path: Path, pages) -> Path:
    document = pymupdf.open()
    for build_page in pages:
        page = document.new_page(width=400, height=600)
        build_page(page)
    document.save(path)
    document.close()
    return path


def _body(page, y: float, text: str, size: float = 10.0) -> None:
    page.insert_text((40, y), text, fontsize=size)


def test_parser_assigns_larger_font_lines_to_sections_with_page_numbers(tmp_path: Path):
    """Breaks if section headings do not structure paragraphs or lose their page."""

    def page_one(page):
        page.insert_text((40, 60), "1. Introduction", fontsize=16)
        page.insert_text((40, 100), "We study routing for sparse experts.", fontsize=10)

    def page_two(page):
        page.insert_text((40, 60), "2. Method", fontsize=16)
        page.insert_text((40, 100), "Tokens are routed to experts.", fontsize=10)

    result = PyMuPdfStage1Parser().parse(
        _write_pdf(tmp_path / "paper.pdf", [page_one, page_two])
    )

    assert [(section.title, section.order, section.page_number) for section in result.sections] == [
        ("1. Introduction", 0, 1),
        ("2. Method", 1, 2),
    ]
    assert [paragraph.text for paragraph in result.paragraphs] == [
        "We study routing for sparse experts.",
        "Tokens are routed to experts.",
    ]
    assert [paragraph.section_id for paragraph in result.paragraphs] == [
        result.sections[0].id,
        result.sections[1].id,
    ]
    assert [paragraph.order for paragraph in result.paragraphs] == [0, 1]


def test_parser_detects_bold_same_size_headings_and_known_labels(tmp_path: Path):
    """Breaks if same-size bold headings or standalone labels are missed."""

    def build(page):
        _body(page, 60, "We study routing for sparse experts.")
        page.insert_text((40, 100), "Related Work", fontsize=10, fontname="hebo")
        _body(page, 140, "Prior routers ignore balance.")
        page.insert_text((40, 180), "References", fontsize=10)

    result = PyMuPdfStage1Parser().parse(_write_pdf(tmp_path / "paper.pdf", [build]))

    assert [section.title for section in result.sections] == ["Related Work", "References"]
    assert [paragraph.section_id for paragraph in result.paragraphs] == [
        None,
        result.sections[0].id,
    ]


def test_parser_keeps_the_title_and_author_banner_out_of_sections(tmp_path: Path):
    """Breaks if the page-1 title/author banner becomes navigation sections."""

    def build(page):
        page.insert_text((40, 50), "Routing Strategies for Sparse Experts", fontsize=22)
        page.insert_text((40, 80), "Alice Smith and Bob Lee", fontsize=12)
        page.insert_text((40, 110), "Abstract", fontsize=10, fontname="hebo")
        _body(page, 140, "We introduce a balanced routing loss.")
        page.insert_text((40, 180), "1. Introduction", fontsize=16)
        _body(page, 220, "Routing assigns tokens to experts.")

    result = PyMuPdfStage1Parser().parse(_write_pdf(tmp_path / "paper.pdf", [build]))

    assert [section.title for section in result.sections] == ["Abstract", "1. Introduction"]
    banner_text = " ".join(
        paragraph.text for paragraph in result.paragraphs if paragraph.section_id is None
    )
    assert "Routing Strategies" in banner_text
    assert "Alice Smith" in banner_text
    assert [paragraph.section_id for paragraph in result.paragraphs][-1] == result.sections[1].id


def test_parser_merges_two_line_headings_into_one_section(tmp_path: Path):
    """Breaks if a wrapped heading becomes one section per rendered line."""

    def build(page):
        page.insert_textbox(
            pymupdf.Rect(40, 50, 170, 130),
            "2. A Longer Section Title",
            fontsize=16,
        )
        page.insert_text((40, 150), "Body text follows the heading.", fontsize=10)

    result = PyMuPdfStage1Parser().parse(_write_pdf(tmp_path / "paper.pdf", [build]))

    assert [section.title for section in result.sections] == ["2. A Longer Section Title"]
    assert result.sections[0].page_number == 1


def test_parser_returns_no_sections_without_heading_signals(tmp_path: Path):
    """Breaks if ordinary body text is promoted to navigation sections."""

    def build(page):
        _body(page, 60, "We study routing for sparse experts.")
        _body(page, 100, "The loss balances expert workload.")

    result = PyMuPdfStage1Parser().parse(_write_pdf(tmp_path / "paper.pdf", [build]))

    assert result.sections == ()
    assert all(paragraph.section_id is None for paragraph in result.paragraphs)


def test_parser_rejects_sentence_ending_bold_lines_as_headings(tmp_path: Path):
    """Breaks if emphasized body sentences become navigation sections."""

    def build(page):
        _body(page, 60, "We study routing for sparse experts.")
        page.insert_text((40, 100), "This claim is emphasized.", fontsize=10, fontname="hebo")
        _body(page, 140, "The loss balances expert workload.")

    result = PyMuPdfStage1Parser().parse(_write_pdf(tmp_path / "paper.pdf", [build]))

    assert result.sections == ()
    assert all(paragraph.section_id is None for paragraph in result.paragraphs)
    assert "This claim is emphasized." in " ".join(
        paragraph.text for paragraph in result.paragraphs
    )


def test_parser_raises_a_stage1_error_for_unreadable_files(tmp_path: Path):
    """Breaks if unreadable sources surface raw pymupdf errors upstream."""
    path = tmp_path / "broken.pdf"
    path.write_bytes(b"this is not a pdf")

    with pytest.raises(Stage1ParseError):
        PyMuPdfStage1Parser().parse(path)
