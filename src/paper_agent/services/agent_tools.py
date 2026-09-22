"""Strict, deterministic paper tools for the paper agent."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar
from unicodedata import normalize

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from paper_agent.domain import DocumentElement, Section
from paper_agent.services.passages import (
    Passage,
    build_passages,
    section_breadcrumbs,
)
from paper_agent.storage import PaperRepository

if TYPE_CHECKING:
    from paper_agent.services.paper_search import SemanticPaperSearchService


class AgentToolError(ValueError):
    """Raised when a paper-agent tool request is invalid or unavailable."""


@dataclass(frozen=True)
class ToolExecution:
    name: str
    content: dict[str, object]
    evidence_element_ids: tuple[str, ...]


# A whole chapter is readable in one read_section call; beyond this the
# output is truncated with a note so the model can drill into subsections.
_MAX_SECTION_CHARS = 28_000


class _ToolArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    @field_validator("*", mode="after")
    @classmethod
    def _reject_blank_strings(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
            if not value:
                raise ValueError("must not be blank")
        return value


class _SearchPaperArguments(_ToolArguments):
    query: str
    limit: int = Field(default=5, ge=1, le=10)


class _ReadElementArguments(_ToolArguments):
    element_id: str


class _ReadSectionArguments(_ToolArguments):
    section_id: str
    include_subsections: bool = True


class _ListSectionsArguments(_ToolArguments):
    pass


def _public_element(
    element: DocumentElement,
    section_title: str | None = None,
    breadcrumb: str | None = None,
) -> dict[str, object]:
    return {
        "id": element.id,
        "kind": element.kind,
        "text": element.text,
        "page_number": element.page_number,
        "bbox": None
        if element.bbox is None
        else {
            "x0": element.bbox.x0,
            "y0": element.bbox.y0,
            "x1": element.bbox.x1,
            "y1": element.bbox.y1,
        },
        "section_id": element.section_id,
        "section_title": section_title,
        "section_breadcrumb": breadcrumb,
        "location_status": element.location_status,
        "order": element.order,
    }


def _public_section(
    section: Section,
    breadcrumb: str | None = None,
    element_count: int | None = None,
) -> dict[str, object]:
    return {
        "id": section.id,
        "title": section.title,
        "level": section.level,
        "page_number": section.page_number,
        "order": section.order,
        "breadcrumb": breadcrumb,
        "element_count": element_count,
    }


def _public_passage(
    passage: Passage, search_mode: str | None = None, search_score: float | None = None
) -> dict[str, object]:
    payload: dict[str, object] = {
        "text": passage.text,
        "element_ids": list(passage.element_ids),
        "section_id": passage.section_id,
        "section_title": passage.section_title,
        "breadcrumb": passage.breadcrumb,
        "page_number": passage.page_number,
    }
    if search_mode is not None:
        payload["search_mode"] = search_mode
        payload["search_score"] = search_score
    return payload


def _section_subtree(
    root: Section, sections: tuple[Section, ...]
) -> list[Section]:
    """Return the section and every nested subsection after it in order."""
    ordered = sorted(sections, key=lambda item: item.order)
    try:
        root_index = next(
            index for index, section in enumerate(ordered) if section.id == root.id
        )
    except StopIteration:
        return [root]
    subtree = [root]
    for section in ordered[root_index + 1 :]:
        if section.level <= root.level:
            break
        subtree.append(section)
    return subtree


class PaperToolRegistry:
    _tool_models: ClassVar[dict[str, type[_ToolArguments]]] = {
        "search_paper": _SearchPaperArguments,
        "read_element": _ReadElementArguments,
        "read_section": _ReadSectionArguments,
        "list_sections": _ListSectionsArguments,
    }
    _descriptions: ClassVar[dict[str, str]] = {
        "search_paper": (
            "Find paper passages by semantic similarity or normalized text "
            "substring. Each result is a section-context passage (breadcrumb, "
            "text, element ids, page). Semantic matches surface related "
            "passages even when the query shares no literal wording."
        ),
        "read_element": "Read one document element from the active paper.",
        "read_section": (
            "Read one section's document elements. include_subsections "
            "(default true) also reads every subsection below it, so one "
            "call returns a whole chapter; large output is truncated with a "
            "note to drill into a specific subsection."
        ),
        "list_sections": (
            "List the paper's table of contents: every section with id, "
            "title, level, page, breadcrumb, and element count. Use this "
            "first for chapter, section, or structure questions (e.g. 'what "
            "does chapter 3 cover') to find the right section id."
        ),
    }

    def __init__(
        self,
        repository: PaperRepository,
        *,
        search: SemanticPaperSearchService | None = None,
    ) -> None:
        self.repository = repository
        self.search = search
        self._handlers = {
            "search_paper": self._search_paper,
            "read_element": self._read_element,
            "read_section": self._read_section,
            "list_sections": self._list_sections,
        }

    def definitions(self) -> tuple[dict[str, object], ...]:
        return tuple(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": self._descriptions[name],
                    "strict": True,
                    "parameters": model.model_json_schema(),
                },
            }
            for name, model in self._tool_models.items()
        )

    def execute(
        self, *, paper_id: str, name: str, arguments: dict[str, object]
    ) -> ToolExecution:
        handler = self._handlers.get(name)
        model = self._tool_models.get(name)
        if handler is None or model is None:
            raise AgentToolError("requested paper tool is unavailable")
        if not isinstance(arguments, dict):
            raise AgentToolError("invalid tool arguments")
        if self.repository.get_document(paper_id) is None:
            raise AgentToolError("active paper is unavailable")
        try:
            parsed = model.model_validate(arguments)
        except ValidationError as error:
            raise AgentToolError("invalid tool arguments") from None
        return handler(paper_id, parsed)

    def _paper_context(self, paper_id: str) -> tuple[
        tuple[DocumentElement, ...], tuple[Section, ...]
    ]:
        return (
            self.repository.get_elements(paper_id),
            self.repository.get_sections(paper_id),
        )

    def _search_paper(self, paper_id: str, arguments: _ToolArguments) -> ToolExecution:
        query = _normalized_search_text(arguments.query)
        elements, sections = self._paper_context(paper_id)
        passages = build_passages(elements, sections)
        matches: list[tuple[Passage, str, float]] = [
            (passage, "substring", 1.0)
            for passage in passages
            if query in _normalized_search_text(passage.text)
        ]
        matched_keys = {passage.key for passage, _, _ in matches}
        for hit in self._semantic_hits(paper_id, arguments):
            if len(matches) >= arguments.limit:
                break
            if hit.passage_key in matched_keys:
                continue
            passage = next(
                (
                    candidate
                    for candidate in passages
                    if candidate.key == hit.passage_key
                ),
                None,
            )
            if passage is None:
                continue
            matches.append((passage, "semantic", hit.score))
            matched_keys.add(passage.key)
        matches = matches[: arguments.limit]
        return ToolExecution(
            name="search_paper",
            content={
                "results": [
                    _public_passage(passage, mode, score)
                    for passage, mode, score in matches
                ]
            },
            evidence_element_ids=self._located_evidence_ids(
                paper_id,
                tuple(
                    element
                    for passage, _, _ in matches
                    for element in (
                        candidate
                        for candidate in elements
                        if candidate.id in passage.element_ids
                    )
                ),
            ),
        )

    def _semantic_hits(self, paper_id: str, arguments: _SearchPaperArguments):
        if self.search is None:
            return ()
        try:
            return self.search.search(
                paper_id, arguments.query, limit=arguments.limit * 2
            )
        except Exception:
            # Semantic retrieval is an enhancement: any failure keeps the
            # deterministic substring results intact.
            return ()

    def _read_element(self, paper_id: str, arguments: _ToolArguments) -> ToolExecution:
        elements, sections = self._paper_context(paper_id)
        element = next(
            (candidate for candidate in elements if candidate.id == arguments.element_id),
            None,
        )
        if element is None:
            raise AgentToolError("requested element is unavailable")
        section_title, breadcrumb = self._section_context(
            sections, element.section_id
        )
        return ToolExecution(
            name="read_element",
            content={
                "element": _public_element(element, section_title, breadcrumb)
            },
            evidence_element_ids=self._located_evidence_ids(paper_id, (element,)),
        )

    def _read_section(self, paper_id: str, arguments: _ToolArguments) -> ToolExecution:
        elements, sections = self._paper_context(paper_id)
        section = next(
            (
                candidate
                for candidate in sections
                if candidate.id == arguments.section_id
            ),
            None,
        )
        if section is None:
            raise AgentToolError("requested section is unavailable")
        if arguments.include_subsections:
            subtree = _section_subtree(section, sections)
        else:
            subtree = [section]
        subtree_ids = {candidate.id for candidate in subtree}
        breadcrumbs = section_breadcrumbs(sections)

        def _section_of(element: DocumentElement) -> Section | None:
            if element.section_id is None:
                return None
            return next(
                (
                    candidate
                    for candidate in sections
                    if candidate.id == element.section_id
                ),
                None,
            )

        selected = [
            element
            for element in elements
            if element.section_id in subtree_ids
        ]
        selected.sort(key=lambda element: element.order or 0)

        included: list[DocumentElement] = []
        total_chars = 0
        truncated = False
        for element in selected:
            if total_chars + len(element.text) > _MAX_SECTION_CHARS and included:
                truncated = True
                break
            included.append(element)
            total_chars += len(element.text)

        def _count(section_id: str) -> int:
            return sum(1 for element in elements if element.section_id == section_id)

        def _element_payload(element: DocumentElement) -> dict[str, object]:
            owner = _section_of(element)
            return _public_element(
                element,
                None if owner is None else owner.title,
                None if owner is None else breadcrumbs.get(owner.id),
            )

        content: dict[str, object] = {
            "section": _public_section(
                section,
                breadcrumbs.get(section.id),
                _count(section.id),
            ),
            "subsections": [
                _public_section(
                    subsection,
                    breadcrumbs.get(subsection.id),
                    _count(subsection.id),
                )
                for subsection in subtree[1:]
            ],
            "elements": [_element_payload(element) for element in included],
            "total_chars": total_chars,
            "truncated": truncated,
        }
        if truncated:
            content["note"] = (
                "Output truncated to keep the section readable; call "
                "read_section on one of the subsections above to continue."
            )
        return ToolExecution(
            name="read_section",
            content=content,
            evidence_element_ids=self._located_evidence_ids(paper_id, tuple(included)),
        )

    def _list_sections(self, paper_id: str, arguments: _ToolArguments) -> ToolExecution:
        elements, sections = self._paper_context(paper_id)
        breadcrumbs = section_breadcrumbs(sections)
        counts = Counter(
            element.section_id for element in elements if element.section_id
        )
        return ToolExecution(
            name="list_sections",
            content={
                "sections": [
                    _public_section(
                        section,
                        breadcrumbs.get(section.id),
                        counts.get(section.id, 0),
                    )
                    for section in sections
                ]
            },
            evidence_element_ids=(),
        )

    @staticmethod
    def _section_context(
        sections: tuple[Section, ...], section_id: str | None
    ) -> tuple[str | None, str | None]:
        if section_id is None:
            return None, None
        section = next(
            (candidate for candidate in sections if candidate.id == section_id),
            None,
        )
        if section is None:
            return None, None
        return section.title, section_breadcrumbs(sections).get(section.id)

    def _located_evidence_ids(
        self, paper_id: str, elements: tuple[DocumentElement, ...]
    ) -> tuple[str, ...]:
        return tuple(
            element.id for element in elements if element.location_status == "located"
        )


def _normalized_search_text(value: str) -> str:
    return normalize("NFKC", value).casefold()
