from paper_agent.domain import BoundingBox, DocumentElement, Section
from paper_agent.services.passages import build_passages, section_breadcrumbs


def _element(
    element_id: str,
    text: str,
    *,
    kind: str = "paragraph",
    section_id: str | None = None,
    order: int | None = None,
    page_number: int | None = 1,
) -> DocumentElement:
    return DocumentElement(
        id=element_id,
        kind=kind,
        text=text,
        section_id=section_id,
        order=order,
        page_number=page_number,
        bbox=None if page_number is None else BoundingBox(0, 0, 1, 0.1),
        location_status="located" if page_number is not None else "unlocated",
    )


def test_consecutive_elements_of_one_section_merge_into_one_passage():
    """Breaks if retrieval units stay tiny and context-free."""
    sections = (Section(id="s1", title="Method", order=0, page_number=1),)
    elements = (
        _element("a", "First paragraph.", section_id="s1", order=0),
        _element("b", "Second paragraph.", section_id="s1", order=1),
    )

    passages = build_passages(elements, sections)

    assert len(passages) == 1
    assert passages[0].key == "a"
    assert passages[0].element_ids == ("a", "b")
    assert passages[0].text == "Method\n\nFirst paragraph.\n\nSecond paragraph."
    assert passages[0].section_id == "s1"
    assert passages[0].section_title == "Method"
    assert passages[0].breadcrumb == "Method"
    assert passages[0].page_number == 1


def test_long_elements_split_at_the_target_length():
    """Breaks if passages grow without bound and bury their neighbors."""
    sections = (Section(id="s1", title="Method", order=0, page_number=1),)
    elements = (
        _element("a", "x" * 600, section_id="s1", order=0),
        _element("b", "y" * 600, section_id="s1", order=1),
    )

    passages = build_passages(elements, sections)

    assert [passage.element_ids for passage in passages] == [("a",), ("b",)]


def test_tables_stand_alone_between_paragraphs():
    """Breaks if a table's Markdown is glued onto prose."""
    sections = (Section(id="s1", title="Method", order=0, page_number=1),)
    elements = (
        _element("a", "Lead-in sentence.", section_id="s1", order=0),
        _element("t", "| Expert |\n| --- |\n| FFN |", kind="table", section_id="s1", order=1),
        _element("b", "Follow-up sentence.", section_id="s1", order=2),
    )

    passages = build_passages(elements, sections)

    assert [passage.element_ids for passage in passages] == [("a",), ("t",), ("b",)]
    assert passages[1].text == "Method\n\n| Expert |\n| --- |\n| FFN |"


def test_stage0_kinds_never_form_passages():
    """Breaks if stage-0 text blocks duplicate stage-1 prose in the index."""
    sections = (Section(id="s1", title="Method", order=0, page_number=1),)
    elements = (
        _element("a", "Indexed paragraph.", section_id="s1", order=0),
        _element("b", "Indexed paragraph.", kind="text_block", order=1),
        _element("c", "", kind="image", order=2),
        _element("d", "", kind="drawing", order=3),
    )

    passages = build_passages(elements, sections)

    assert [passage.element_ids for passage in passages] == [("a",)]


def test_heading_elements_join_their_sections_passages():
    """Breaks if heading elements (now retrievable) are dropped."""
    sections = (Section(id="s1", title="3 Method", order=0, page_number=1),)
    elements = (
        _element("h", "3 Method", kind="heading", section_id="s1", order=0),
        _element("a", "Body text.", section_id="s1", order=1),
    )

    passages = build_passages(elements, sections)

    assert [passage.element_ids for passage in passages] == [("h", "a")]


def test_sectionless_elements_lead_the_document():
    """Breaks if title/abstract passages are buried behind sections."""
    sections = (Section(id="s1", title="Method", order=0, page_number=1),)
    elements = (
        _element("a", "Section body.", section_id="s1", order=0),
        _element("front", "Abstract sentence.", order=1),
    )

    passages = build_passages(elements, sections)

    assert [passage.element_ids for passage in passages] == [("front",), ("a",)]
    assert passages[0].section_id is None
    assert passages[0].breadcrumb is None
    assert passages[0].text == "Abstract sentence."


def test_members_are_ordered_by_element_order():
    """Breaks if two-column reading order is lost to insertion order."""
    sections = (Section(id="s1", title="Method", order=0, page_number=1),)
    elements = (
        _element("b", "Second in reading order.", section_id="s1", order=1),
        _element("a", "First in reading order.", section_id="s1", order=0),
    )

    passages = build_passages(elements, sections)

    assert passages[0].key == "a"
    assert passages[0].element_ids == ("a", "b")


def test_passage_page_comes_from_the_first_located_member():
    """Breaks if unlocated members erase the page a passage cites."""
    sections = (Section(id="s1", title="Method", order=0, page_number=1),)
    elements = (
        _element("a", "Unlocated opener.", section_id="s1", order=0, page_number=None),
        _element("b", "Located body.", section_id="s1", order=1, page_number=3),
    )

    passages = build_passages(elements, sections)

    assert passages[0].page_number == 3

    only_unlocated = (
        _element("a", "Unlocated opener.", section_id="s1", order=0, page_number=None),
    )
    passages = build_passages(only_unlocated, sections)
    assert passages[0].page_number is None


def test_breadcrumbs_track_nested_section_levels():
    """Breaks if subsection paths lose their chapter context."""
    sections = (
        Section(id="c3", title="3 General Infrastructures", order=0, page_number=1),
        Section(id="s31", title="3.1 Training", order=1, page_number=2, level=2),
        Section(id="s32", title="3.2 Serving", order=2, page_number=5, level=2),
        Section(id="c4", title="4 Experiments", order=3, page_number=7),
    )

    breadcrumbs = section_breadcrumbs(sections)

    assert breadcrumbs == {
        "c3": "3 General Infrastructures",
        "s31": "3 General Infrastructures > 3.1 Training",
        "s32": "3 General Infrastructures > 3.2 Serving",
        "c4": "4 Experiments",
    }


def test_passages_embed_their_breadcrumb_header():
    """Breaks if the semantic index embeds bare text without section context."""
    sections = (
        Section(id="c3", title="3 General Infrastructures", order=0, page_number=1),
        Section(id="s31", title="3.1 Training", order=1, page_number=2, level=2),
    )
    elements = (
        _element("a", "Training uses a balanced router.", section_id="s31", order=0),
    )

    passages = build_passages(elements, sections)

    assert passages[0].text == (
        "3 General Infrastructures > 3.1 Training\n\n"
        "Training uses a balanced router."
    )
