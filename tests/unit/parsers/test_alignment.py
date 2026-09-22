from paper_agent.domain import BoundingBox
from paper_agent.parsers.alignment import TextAligner
from paper_agent.parsers.base import TextBlock
from paper_agent.parsers.pymupdf_stage1 import Stage1Paragraph


BOX = BoundingBox(0.1, 0.2, 0.8, 0.3)


def test_aligner_ignores_whitespace_and_unicode_punctuation():
    """Breaks if equivalent PDF punctuation or spacing prevents exact alignment."""
    blocks = [
        TextBlock(
            text="We introduce a method — today.",
            page_number=2,
            bbox=BOX,
            order=0,
        )
    ]
    paragraphs = [Stage1Paragraph(text="We   introduce a method - today.")]

    aligned = TextAligner().align(paragraphs, blocks)

    assert aligned[0].location_status == "located"
    assert aligned[0].page_number == 2
    assert aligned[0].bbox == BOX


def test_aligner_ignores_unicode_punctuation_variants_beyond_dashes():
    """Breaks if punctuation outside the finite dash/quote mapping blocks alignment."""
    blocks = [
        TextBlock(
            text="We compare methods、then report results.",
            page_number=3,
            bbox=BOX,
            order=0,
        )
    ]
    paragraphs = [Stage1Paragraph(text="We compare methods, then report results.")]

    aligned = TextAligner().align(paragraphs, blocks)

    assert aligned[0].location_status == "located"
    assert aligned[0].page_number == 3
    assert aligned[0].bbox == BOX


def test_aligner_maps_unicode_minus_sign_to_ascii_hyphen():
    """Breaks if the prior U+2212 compatibility is lost by generic P* handling."""
    blocks = [
        TextBlock(
            text="Signal\u2212to noise is measurable.",
            page_number=5,
            bbox=BOX,
            order=0,
        )
    ]
    paragraphs = [Stage1Paragraph(text="Signal - to noise is measurable.")]

    aligned = TextAligner().align(paragraphs, blocks)

    assert aligned[0].location_status == "located"
    assert aligned[0].page_number == 5
    assert aligned[0].bbox == BOX


def test_aligner_leaves_repeated_text_unlocated():
    """Breaks if an ambiguous exact match borrows either source location."""
    repeated = "Repeated evidence text " * 3
    blocks = [
        TextBlock(text=repeated, page_number=1, bbox=BOX, order=0),
        TextBlock(text=repeated, page_number=2, bbox=BOX, order=0),
    ]

    element = TextAligner().align([Stage1Paragraph(text=repeated)], blocks)[0]

    assert element.location_status == "unlocated"
    assert element.page_number is None
    assert element.bbox is None


def test_aligner_requires_dehyphenation_on_both_sides():
    """Breaks if one stage dehyphenates a word the other stage leaves split.

    Alignment matches exact text; a hyphen-broken word that survives on
    either side strands the paragraph without page or bounding box.
    """
    blocks = [
        TextBlock(
            text="The model scales its capa-\nbilities across routed experts.",
            page_number=2,
            bbox=BOX,
            order=0,
        )
    ]
    paragraphs = [
        Stage1Paragraph(text="The model scales its capabilities across routed experts.")
    ]

    element = TextAligner().align(paragraphs, blocks)[0]

    assert element.location_status == "unlocated"

    # With both sides dehyphenated (as both parsers now do) the paragraph
    # aligns and keeps its citation geometry.
    aligned_blocks = [
        TextBlock(
            text="The model scales its capabilities across routed experts.",
            page_number=2,
            bbox=BOX,
            order=0,
        )
    ]
    element = TextAligner().align(paragraphs, aligned_blocks)[0]

    assert element.location_status == "located"
    assert element.page_number == 2
    assert element.bbox == BOX


def test_aligner_uses_a_unique_long_containment_match():
    """Breaks if a long semantic paragraph cannot align within one source block."""
    paragraph_text = (
        "This deterministic paragraph has enough distinctive content for containment."
    )
    blocks = [
        TextBlock(
            text=f"1  {paragraph_text}  Supplemental marker",
            page_number=4,
            bbox=BOX,
            order=3,
        )
    ]

    element = TextAligner().align(
        [Stage1Paragraph(text=paragraph_text, section_id="methods", order=7)],
        blocks,
    )[0]

    assert element.location_status == "located"
    assert element.page_number == 4
    assert element.bbox == BOX
    assert element.section_id == "methods"
    assert element.order == 7


def test_aligner_leaves_a_unique_short_containment_candidate_unlocated():
    """Breaks if short containment matches borrow an unsupported source location."""
    paragraph_text = "Unique short evidence"
    blocks = [
        TextBlock(
            text=f"Prefix {paragraph_text} suffix",
            page_number=4,
            bbox=BOX,
            order=3,
        )
    ]

    element = TextAligner().align([Stage1Paragraph(text=paragraph_text)], blocks)[0]

    assert element.location_status == "unlocated"
    assert element.page_number is None
    assert element.bbox is None
