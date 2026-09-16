from pathlib import Path

import pytest

from paper_agent.domain import (
    BoundingBox,
    DocumentElement,
    Page,
    Section,
)
from paper_agent.storage import PaperRepository


@pytest.fixture
def repository(tmp_path: Path) -> PaperRepository:
    return PaperRepository(f"sqlite:///{tmp_path / 'paper-agent.db'}")


def _paper_with_document(repository: PaperRepository, name: str):
    paper = repository.create_paper(
        original_filename=f"{name}.pdf",
        stored_filename=f"private-{name}.pdf",
    )
    repository.save_page(paper.id, Page(number=1, width=200, height=300))
    section = repository.save_section(
        paper.id, Section(id=f"{name}-section", title="Method", order=0, page_number=1)
    )
    located = repository.save_element(
        paper.id,
        DocumentElement(
            id=f"{name}-located",
            kind="paragraph",
            text="The Cafe\u0301 router sends tokens to experts.",
            page_number=1,
            bbox=BoundingBox(0, 0, 1, 0.2),
            section_id=section.id,
        ),
    )
    unlocated = repository.save_element(
        paper.id,
        DocumentElement.paragraph(
            "The router has semantic-only notes.",
            section_id=section.id,
            location_status="unlocated",
        ),
    )
    return paper, section, located, unlocated


def test_definitions_expose_only_strict_openai_function_schemas(repository):
    """Breaks if a model can call an unbounded tool or send undeclared fields."""
    from paper_agent.services.agent_tools import PaperToolRegistry

    definitions = PaperToolRegistry(repository).definitions()

    assert [definition["function"]["name"] for definition in definitions] == [
        "search_paper",
        "read_element",
        "read_section",
    ]
    assert all(definition["type"] == "function" for definition in definitions)
    assert all(definition["function"]["strict"] is True for definition in definitions)
    assert all(
        definition["function"]["parameters"]["additionalProperties"] is False
        for definition in definitions
    )


def test_read_element_rejects_cross_paper_ids_and_returns_only_located_evidence(repository):
    """Breaks if an answer can cite an element outside its active paper."""
    from paper_agent.services.agent_tools import AgentToolError, PaperToolRegistry

    first, _, first_element, _ = _paper_with_document(repository, "first")
    _, _, second_element, _ = _paper_with_document(repository, "second")
    tools = PaperToolRegistry(repository)

    with pytest.raises(AgentToolError, match="element"):
        tools.execute(
            paper_id=first.id,
            name="read_element",
            arguments={"element_id": second_element.id},
        )

    result = tools.execute(
        paper_id=first.id,
        name="read_element",
        arguments={"element_id": first_element.id},
    )

    assert result.evidence_element_ids == (first_element.id,)
    assert result.content["element"]["id"] == first_element.id
    assert "private-first.pdf" not in repr(result.content)
    assert "path" not in repr(result.content).lower()


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("search_paper", {"query": "router", "page": 7}),
        ("search_paper", {"query": "   "}),
        ("search_paper", {"query": "router", "limit": 0}),
        ("search_paper", {"query": "router", "limit": 11}),
    ],
)
def test_execution_rejects_unknown_blank_and_out_of_range_arguments(
    repository, name: str, arguments: dict[str, object]
):
    """Breaks if malformed tool calls reach repository reads with weakened bounds."""
    from paper_agent.services.agent_tools import AgentToolError, PaperToolRegistry

    paper, _, _, _ = _paper_with_document(repository, "reject")

    with pytest.raises(AgentToolError, match="arguments"):
        PaperToolRegistry(repository).execute(
            paper_id=paper.id, name=name, arguments=arguments
        )


def test_search_paper_uses_unicode_normalized_substrings_and_filters_unlocated_evidence(
    repository,
):
    """Breaks if search becomes semantic retrieval or cites unlocated prose."""
    from paper_agent.services.agent_tools import PaperToolRegistry

    paper, _, located, unlocated = _paper_with_document(repository, "search")

    result = PaperToolRegistry(repository).execute(
        paper_id=paper.id,
        name="search_paper",
        arguments={"query": "Caf\u00e9", "limit": 5},
    )
    unlocated_result = PaperToolRegistry(repository).execute(
        paper_id=paper.id,
        name="search_paper",
        arguments={"query": "semantic-only", "limit": 5},
    )

    assert [element["id"] for element in result.content["elements"]] == [located.id]
    assert result.evidence_element_ids == (located.id,)
    assert [element["id"] for element in unlocated_result.content["elements"]] == [
        unlocated.id
    ]
    assert unlocated_result.evidence_element_ids == ()


class _StubSearch:
    def __init__(self, hits, error=None):
        self.hits = hits
        self.error = error
        self.queries: list[tuple[str, str, int]] = []

    def search(self, paper_id, query, *, limit):
        self.queries.append((paper_id, query, limit))
        if self.error is not None:
            raise self.error
        return self.hits


def test_search_paper_merges_semantic_hits_after_substring_matches(repository):
    from paper_agent.services.agent_tools import PaperToolRegistry

    """Breaks if semantic hits replace or shadow deterministic substring matches."""
    from paper_agent.services.paper_search import SearchHit

    paper, _, located, unlocated = _paper_with_document(repository, "merge")
    other_paper, _, other_element, _ = _paper_with_document(repository, "other")
    stub = _StubSearch(
        (
            SearchHit(element_id=unlocated.id, score=0.8),
            SearchHit(element_id=other_element.id, score=0.7),
        )
    )
    tools = PaperToolRegistry(repository, search=stub)

    result = tools.execute(
        paper_id=paper.id,
        name="search_paper",
        arguments={"query": "tokens", "limit": 5},
    )

    # Substring match first, semantic hits appended, cross-paper IDs dropped.
    assert [element["id"] for element in result.content["elements"]] == [
        located.id,
        unlocated.id,
    ]
    modes = [element["search_mode"] for element in result.content["elements"]]
    assert modes == ["substring", "semantic"]
    assert result.content["elements"][0]["search_score"] == 1.0
    assert result.content["elements"][1]["search_score"] == 0.8
    # Only located elements become citable evidence.
    assert result.evidence_element_ids == (located.id,)
    assert stub.queries == [(paper.id, "tokens", 10)]


def test_search_paper_truncates_to_the_requested_limit(repository):
    from paper_agent.services.agent_tools import PaperToolRegistry

    from paper_agent.services.paper_search import SearchHit

    paper, _, located, unlocated = _paper_with_document(repository, "truncate")
    stub = _StubSearch(
        (
            SearchHit(element_id=located.id, score=0.9),
            SearchHit(element_id=unlocated.id, score=0.8),
        )
    )
    tools = PaperToolRegistry(repository, search=stub)

    result = tools.execute(
        paper_id=paper.id,
        name="search_paper",
        arguments={"query": "router", "limit": 1},
    )

    assert [element["id"] for element in result.content["elements"]] == [located.id]


def test_search_paper_survives_semantic_service_failure(repository):
    from paper_agent.services.agent_tools import PaperToolRegistry

    paper, _, located, _ = _paper_with_document(repository, "failing")
    stub = _StubSearch((), error=RuntimeError("embedding backend down"))
    tools = PaperToolRegistry(repository, search=stub)

    result = tools.execute(
        paper_id=paper.id,
        name="search_paper",
        arguments={"query": "tokens", "limit": 5},
    )

    assert [element["id"] for element in result.content["elements"]] == [located.id]
    assert result.content["elements"][0]["search_mode"] == "substring"

