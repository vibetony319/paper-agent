"""Group parsed document elements into retrieval passages.

A passage merges the consecutive elements of one section up to a target
length and prefixes them with the section breadcrumb, so semantic search
embeds context ("3 General Infrastructures > 3.1 Training Infrastructure")
instead of bare 200-character blocks, and a hit carries enough text to be
useful on its own. Tables stand alone: their Markdown already is one
coherent unit.

The breakdown is deterministic given the paper's elements and sections, so
the index builder and the query-time tooling can rebuild the identical
passages independently.
"""

from __future__ import annotations

from dataclasses import dataclass

from paper_agent.domain import DocumentElement, Section


# Stage-1 content kinds that participate in passages. Stage-0 text blocks
# duplicate stage-1 paragraphs and would waste top-k slots; visuals carry
# no text.
PASSAGE_KINDS = frozenset({"paragraph", "heading", "table"})

# Soft upper bound for the element text of one passage (the breadcrumb
# header is added on top). Elements are never split, so a passage may
# overshoot when a single element is longer than the target.
_TARGET_PASSAGE_CHARS = 900


@dataclass(frozen=True)
class Passage:
    """One retrievable unit of paper content with section context."""

    key: str
    text: str
    element_ids: tuple[str, ...]
    section_id: str | None
    section_title: str | None
    breadcrumb: str | None
    page_number: int | None


def section_breadcrumbs(sections: tuple[Section, ...]) -> dict[str, str]:
    """Map section id to its "Chapter > Section > Subsection" title path."""
    breadcrumbs: dict[str, str] = {}
    stack: list[str] = []
    for section in sorted(sections, key=lambda item: item.order):
        level = min(max(section.level, 1), len(stack) + 1)
        stack = stack[: level - 1] + [section.title]
        breadcrumbs[section.id] = " > ".join(stack)
    return breadcrumbs


def _make_passage(
    members: list[DocumentElement],
    section: Section | None,
    breadcrumb: str | None,
) -> Passage:
    body = "\n\n".join(element.text for element in members)
    text = body if breadcrumb is None else f"{breadcrumb}\n\n{body}"
    page_number = next(
        (
            element.page_number
            for element in members
            if element.page_number is not None
        ),
        None,
    )
    return Passage(
        key=members[0].id,
        text=text,
        element_ids=tuple(element.id for element in members),
        section_id=None if section is None else section.id,
        section_title=None if section is None else section.title,
        breadcrumb=breadcrumb,
        page_number=page_number,
    )


def _passages_for_group(
    members: list[DocumentElement], section: Section | None, breadcrumb: str | None
) -> list[Passage]:
    # Element order is the parser's column-aware reading order; sorting by
    # it keeps two-column pages in the right sequence.
    members = sorted(
        members, key=lambda element: element.order or 0
    )
    passages: list[Passage] = []
    buffer: list[DocumentElement] = []

    def flush() -> None:
        nonlocal buffer
        if buffer:
            passages.append(_make_passage(buffer, section, breadcrumb))
            buffer = []

    for element in members:
        if element.kind == "table":
            flush()
            passages.append(_make_passage([element], section, breadcrumb))
            continue
        buffered_chars = sum(len(item.text) + 2 for item in buffer)
        if buffer and buffered_chars + len(element.text) > _TARGET_PASSAGE_CHARS:
            flush()
        buffer.append(element)
    flush()
    return passages


def build_passages(
    elements: tuple[DocumentElement, ...], sections: tuple[Section, ...]
) -> tuple[Passage, ...]:
    """Build the deterministic passages for one paper."""
    sections_by_id = {section.id: section for section in sections}
    breadcrumbs = section_breadcrumbs(sections)
    groups: dict[str | None, list[DocumentElement]] = {}
    for element in elements:
        if element.kind not in PASSAGE_KINDS:
            continue
        groups.setdefault(element.section_id, []).append(element)

    passages: list[Passage] = []
    # The sectionless group leads the document (title, abstract); after
    # that sections follow in document order. Sections missing from the
    # list keep their elements as a sectionless group as a fallback.
    group_keys: list[str | None] = [None]
    group_keys.extend(
        section.id for section in sorted(sections, key=lambda item: item.order)
    )
    group_keys.extend(
        key for key in groups if key is not None and key not in sections_by_id
    )
    for key in dict.fromkeys(group_keys):
        members = groups.get(key)
        if not members:
            continue
        section = None if key is None else sections_by_id.get(key)
        breadcrumb = None if key is None else breadcrumbs.get(key)
        passages.extend(_passages_for_group(members, section, breadcrumb))
    return tuple(passages)
