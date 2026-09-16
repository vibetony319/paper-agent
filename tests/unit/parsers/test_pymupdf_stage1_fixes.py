from pathlib import Path

import pymupdf

from paper_agent.parsers.pymupdf_stage1 import PyMuPdfStage1Parser


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


def test_parser_keeps_chinese_body_lines_out_of_sections(tmp_path: Path):
    """Breaks if CJK lines satisfy the all-caps heuristic and flood the TOC."""

    def build(page):
        _body(page, 60, "本文提出了一种新的检索增强生成方法")
        _body(page, 100, "该方法通过重排序提升了生成的质量")
        page.insert_text((40, 140), "一、引言", fontsize=12, fontname="china-s")
        _body(page, 180, "检索增强生成受到广泛关注。")

    result = PyMuPdfStage1Parser().parse(
        _write_pdf(tmp_path / "paper.pdf", [build])
    )

    assert [section.title for section in result.sections] == ["一、引言"]
    assert all(paragraph.section_id is None for paragraph in result.paragraphs[:2])
    assert result.paragraphs[-1].section_id == result.sections[0].id


def test_parser_detects_chinese_label_headings(tmp_path: Path):
    """Breaks if 摘要/结论 labels are not recognized at body size."""

    def build(page):
        page.insert_text((40, 60), "摘要", fontsize=10, fontname="china-s")
        _body(page, 90, "本文研究了检索增强生成的重排序问题。")
        page.insert_text((40, 130), "结论", fontsize=10, fontname="china-s")
        _body(page, 160, "本文验证了所提出方法的有效性。")

    result = PyMuPdfStage1Parser().parse(
        _write_pdf(tmp_path / "paper.pdf", [build])
    )

    assert [section.title for section in result.sections] == ["摘要", "结论"]


def test_parser_keeps_inline_bold_words_out_of_headings(tmp_path: Path):
    """Breaks if a single bold term promotes a whole body line to a section."""

    def build(page):
        writer = pymupdf.TextWriter(page.rect)
        writer.append((40, 60), "reranker, we propose ", font=pymupdf.Font("helv"), fontsize=10)
        writer.append((155, 60), "InfoGain-RAG", font=pymupdf.Font("hebo"), fontsize=10)
        writer.append((220, 60), ", a comprehensive framework.", font=pymupdf.Font("helv"), fontsize=10)
        writer.write_text(page)
        page.insert_text((40, 100), "2. Method", fontsize=16)
        _body(page, 140, "The framework reranks the retrieved passages.")

    result = PyMuPdfStage1Parser().parse(
        _write_pdf(tmp_path / "paper.pdf", [build])
    )

    assert [section.title for section in result.sections] == ["2. Method"]
    assert any(
        "InfoGain-RAG" in paragraph.text for paragraph in result.paragraphs
    )


def test_parser_ignores_rotated_margin_stamps(tmp_path: Path):
    """Breaks if the rotated arXiv watermark becomes a 20pt section."""

    def build(page):
        page.insert_text(
            (385, 60), "arXiv:2509.12765v1 [cs.IR] 16 Sep 2025",
            fontsize=20, rotate=90,
        )
        _body(page, 60, "We study routing for sparse experts.")
        page.insert_text((40, 100), "1. Introduction", fontsize=16)
        _body(page, 140, "Routing assigns tokens to experts.")

    result = PyMuPdfStage1Parser().parse(
        _write_pdf(tmp_path / "paper.pdf", [build])
    )

    assert [section.title for section in result.sections] == ["1. Introduction"]
    assert all(
        "arXiv" not in paragraph.text for paragraph in result.paragraphs
    )


def test_parser_reads_two_column_pages_column_by_column(tmp_path: Path):
    """Breaks if the right column is read before the lower left column."""

    def build(page):
        # Left column: body lines + heading 3.1.1 near the bottom.
        for index in range(6):
            page.insert_text((40, 60 + index * 20), f"Left body line {index} here.", fontsize=10)
        page.insert_text((40, 200), "3.1.1 Answer Generation", fontsize=12)
        page.insert_text((40, 220), "Left column tail body line.", fontsize=10)
        # Right column: body lines + heading 3.1.2 at the same height.
        for index in range(6):
            page.insert_text((240, 60 + index * 20), f"Right body line {index}.", fontsize=10)
        page.insert_text((240, 200), "3.1.2 Calculation of DIG", fontsize=12)
        page.insert_text((240, 220), "Right column tail body line.", fontsize=10)

    result = PyMuPdfStage1Parser().parse(
        _write_pdf(tmp_path / "paper.pdf", [build])
    )

    assert [section.title for section in result.sections] == [
        "3.1.1 Answer Generation",
        "3.1.2 Calculation of DIG",
    ]
    assert [section.level for section in result.sections] == [3, 3]


def test_parser_keeps_bold_page_one_titles_out_of_sections(tmp_path: Path):
    """Breaks if a bold paper title or bold author line becomes a section."""

    def build(page):
        page.insert_text((40, 50), "Routing Strategies for Sparse Experts", fontsize=22, fontname="hebo")
        page.insert_text((40, 80), "Alice Smith and Bob Lee", fontsize=10, fontname="hebo")
        page.insert_text((40, 110), "1. Introduction", fontsize=16)
        _body(page, 140, "Routing assigns tokens to experts.")

    result = PyMuPdfStage1Parser().parse(
        _write_pdf(tmp_path / "paper.pdf", [build])
    )

    assert [section.title for section in result.sections] == ["1. Introduction"]


def test_parser_drops_figure_labels_and_algorithm_steps(tmp_path: Path):
    """Breaks if subfigure labels or algorithm-box text become sections."""

    def build(page):
        _body(page, 60, "We study routing for sparse experts.")
        page.insert_text((40, 100), "(a)", fontsize=10, fontname="hebo")
        _body(page, 130, "The subfigure caption body text.")
        page.insert_text((40, 160), "STEP 1: Input the query", fontsize=10, fontname="hebo")
        _body(page, 190, "The algorithm box body text.")

    result = PyMuPdfStage1Parser().parse(
        _write_pdf(tmp_path / "paper.pdf", [build])
    )

    assert result.sections == ()
    assert any("(a)" in paragraph.text for paragraph in result.paragraphs)


def test_parser_uses_the_pdf_outline_when_available(tmp_path: Path):
    """Breaks if embedded bookmarks are ignored in favor of heuristics."""
    document = pymupdf.open()
    page = document.new_page(width=400, height=600)
    page.insert_text((40, 60), "Routing for Sparse Experts", fontsize=22)
    page.insert_text((40, 90), "Alice Smith and Bob Lee", fontsize=10)
    page.insert_text((40, 120), "Abstract", fontsize=14)
    page.insert_text((40, 150), "We introduce a balanced routing loss.", fontsize=10)
    page.insert_text((40, 200), "1. Introduction", fontsize=14)
    page.insert_text((40, 230), "Routing assigns tokens to experts.", fontsize=10)
    page.insert_text((40, 280), "1.1 Motivation", fontsize=12)
    page.insert_text((40, 310), "Sparse expert models are promising.", fontsize=10)
    document.set_toc([
        [1, "Abstract", 1],
        [1, "1. Introduction", 1],
        [2, "1.1 Motivation", 1],
        [1, "2. Experiments", 1],
    ])
    path = tmp_path / "outlined.pdf"
    document.save(path)
    document.close()

    result = PyMuPdfStage1Parser().parse(path)

    assert [section.title for section in result.sections] == [
        "Abstract",
        "1. Introduction",
        "1.1 Motivation",
        "2. Experiments",
    ]
    assert [section.level for section in result.sections] == [1, 1, 2, 1]
    abstract_paragraphs = [
        paragraph
        for paragraph in result.paragraphs
        if "balanced routing loss" in paragraph.text
    ]
    motivation_paragraphs = [
        paragraph
        for paragraph in result.paragraphs
        if "Sparse expert models" in paragraph.text
    ]
    assert abstract_paragraphs[0].section_id == result.sections[0].id
    assert motivation_paragraphs[0].section_id == result.sections[2].id
    banner = " ".join(
        paragraph.text for paragraph in result.paragraphs if paragraph.section_id is None
    )
    assert "Routing for Sparse Experts" in banner


def test_parser_derives_levels_from_numbering_depth(tmp_path: Path):
    """Breaks if 3.1.2-style headings do not carry a deeper navigation level."""

    def build(page):
        page.insert_text((40, 60), "3 Method", fontsize=16)
        _body(page, 80, "The method reranks retrieved passages.")
        page.insert_text((40, 120), "3.1 Setup", fontsize=12)
        _body(page, 140, "The setup uses standard benchmarks.")
        page.insert_text((40, 180), "3.1.2 Calculation", fontsize=12)
        _body(page, 200, "The calculation follows the metric.")

    result = PyMuPdfStage1Parser().parse(
        _write_pdf(tmp_path / "paper.pdf", [build])
    )

    assert [section.title for section in result.sections] == [
        "3 Method",
        "3.1 Setup",
        "3.1.2 Calculation",
    ]
    assert [section.level for section in result.sections] == [1, 2, 3]
