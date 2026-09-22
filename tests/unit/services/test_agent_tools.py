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
    """One paper with two sections so passages stay distinct.

    The Method section holds a located and an unlocated paragraph (one
    passage); the Evaluation section holds one located paragraph mentioning
    the router (a second passage).
    """
    paper = repository.create_paper(
        original_filename=f"{name}.pdf",
        stored_filename=f"private-{name}.pdf",
    )
    repository.save_page(paper.id, Page(number=1, width=200, height=300))
    method = repository.save_section(
        paper.id, Section(id=f"{name}-section", title="Method", order=0, page_number=1)
    )
    evaluation = repository.save_section(
        paper.id,
        Section(id=f"{name}-evaluation", title="Evaluation", order=1, page_number=1),
    )
    located = repository.save_element(
        paper.id,
        DocumentElement(
            id=f"{name}-located",
            kind="paragraph",
            text="The Cafe\u0301 router sends tokens to experts.",
            page_number=1,
            bbox=BoundingBox(0, 0, 1, 0.2),
            section_id=method.id,
        ),
    )
    unlocated = repository.save_element(
        paper.id,
        DocumentElement.paragraph(
            "The router has semantic-only notes.",
            section_id=method.id,
            location_status="unlocated",
        ),
    )
    accuracy = repository.save_element(
        paper.id,
        DocumentElement(
            id=f"{name}-accuracy",
            kind="paragraph",
            text="The router improves accuracy.",
            page_number=1,
            bbox=BoundingBox(0, 0.3, 1, 0.5),
            section_id=evaluation.id,
        ),
    )
    return paper, method, evaluation, located, unlocated, accuracy


def test_definitions_expose_only_strict_openai_function_schemas(repository):
    """Breaks if a model can call an unbounded tool or send undeclared fields."""
    from paper_agent.services.agent_tools import PaperToolRegistry

    definitions = PaperToolRegistry(repository).definitions()

    assert [definition["function"]["name"] for definition in definitions] == [
        "search_paper",
        "read_element",
        "read_section",
        "list_sections",
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

    first, _, _, first_element, _, _ = _paper_with_document(repository, "first")
    _, _, _, second_element, _, _ = _paper_with_document(repository, "second")
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

    paper, _, _, _, _, _ = _paper_with_document(repository, "reject")

    with pytest.raises(AgentToolError, match="arguments"):
        PaperToolRegistry(repository).execute(
            paper_id=paper.id, name=name, arguments=arguments
        )


def test_search_paper_matches_passages_and_cites_only_located_elements(repository):
    """Breaks if search loses section context or cites unlocated prose."""
    from paper_agent.services.agent_tools import PaperToolRegistry

    paper, _, _, located, unlocated, _ = _paper_with_document(repository, "search")

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

    # Both paragraphs share one passage: the hit carries the section context
    # and the whole merged text.
    assert len(result.content["results"]) == 1
    passage = result.content["results"][0]
    assert passage["element_ids"] == [located.id, unlocated.id]
    assert passage["section_title"] == "Method"
    assert passage["breadcrumb"] == "Method"
    assert passage["search_mode"] == "substring"
    assert passage["search_score"] == 1.0
    # Only located elements become citable evidence.
    assert result.evidence_element_ids == (located.id,)
    # Unlocated-only prose is still retrieved, anchored by its located sibling.
    assert unlocated_result.content["results"][0]["element_ids"] == [
        located.id,
        unlocated.id,
    ]
    assert unlocated_result.evidence_element_ids == (located.id,)


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

    paper, _, _, located, unlocated, accuracy = _paper_with_document(repository, "merge")
    other_paper, _, _, other_element, _, _ = _paper_with_document(repository, "other")
    stub = _StubSearch(
        (
            SearchHit(passage_key=accuracy.id, score=0.8),
            SearchHit(passage_key=other_element.id, score=0.7),
        )
    )
    tools = PaperToolRegistry(repository, search=stub)

    result = tools.execute(
        paper_id=paper.id,
        name="search_paper",
        arguments={"query": "tokens", "limit": 5},
    )

    # Substring match first, semantic hits appended, cross-paper keys dropped.
    assert [result["element_ids"] for result in result.content["results"]] == [
        [located.id, unlocated.id],
        [accuracy.id],
    ]
    modes = [result["search_mode"] for result in result.content["results"]]
    assert modes == ["substring", "semantic"]
    assert result.content["results"][0]["search_score"] == 1.0
    assert result.content["results"][1]["search_score"] == 0.8
    # Only located elements become citable evidence.
    assert result.evidence_element_ids == (located.id, accuracy.id)
    assert stub.queries == [(paper.id, "tokens", 10)]


def test_search_paper_truncates_to_the_requested_limit(repository):
    from paper_agent.services.agent_tools import PaperToolRegistry

    paper, _, _, located, unlocated, _ = _paper_with_document(repository, "truncate")
    tools = PaperToolRegistry(repository)

    result = tools.execute(
        paper_id=paper.id,
        name="search_paper",
        arguments={"query": "router", "limit": 1},
    )

    # Both passages match "router" by substring; document order wins the cut.
    assert [result["element_ids"] for result in result.content["results"]] == [
        [located.id, unlocated.id]
    ]


def test_search_paper_survives_semantic_service_failure(repository):
    from paper_agent.services.agent_tools import PaperToolRegistry

    paper, _, _, located, unlocated, _ = _paper_with_document(repository, "failing")
    stub = _StubSearch((), error=RuntimeError("embedding backend down"))
    tools = PaperToolRegistry(repository, search=stub)

    result = tools.execute(
        paper_id=paper.id,
        name="search_paper",
        arguments={"query": "tokens", "limit": 5},
    )

    assert [result["element_ids"] for result in result.content["results"]] == [
        [located.id, unlocated.id]
    ]
    assert result.content["results"][0]["search_mode"] == "substring"


def test_list_sections_returns_the_table_of_contents(repository):
    """Breaks if the agent cannot discover chapters before reading them."""
    from paper_agent.services.agent_tools import PaperToolRegistry

    paper, method, evaluation, _, _, _ = _paper_with_document(repository, "toc")
    tools = PaperToolRegistry(repository)

    result = tools.execute(paper_id=paper.id, name="list_sections", arguments={})

    assert [section["id"] for section in result.content["sections"]] == [
        method.id,
        evaluation.id,
    ]
    assert [section["title"] for section in result.content["sections"]] == [
        "Method",
        "Evaluation",
    ]
    assert [section["element_count"] for section in result.content["sections"]] == [2, 1]
    assert result.content["sections"][0]["breadcrumb"] == "Method"
    assert result.content["sections"][1]["breadcrumb"] == "Evaluation"
    assert result.evidence_element_ids == ()


def test_list_sections_reports_nested_breadcrumbs(repository):
    """Breaks if the table of contents loses the chapter > subsection path."""
    from paper_agent.services.agent_tools import PaperToolRegistry

    paper = repository.create_paper(
        original_filename="nested.pdf", stored_filename="private-nested.pdf"
    )
    repository.save_page(paper.id, Page(number=1, width=200, height=300))
    repository.save_section(
        paper.id,
        Section(id="ch3", title="3 General Infrastructures", order=0, page_number=1),
    )
    repository.save_section(
        paper.id,
        Section(id="s31", title="3.1 Training", order=1, page_number=1, level=2),
    )
    repository.save_section(
        paper.id,
        Section(id="ch4", title="4 Experiments", order=2, page_number=1),
    )

    result = PaperToolRegistry(repository).execute(
        paper_id=paper.id, name="list_sections", arguments={}
    )

    breadcrumbs = [section["breadcrumb"] for section in result.content["sections"]]
    assert breadcrumbs == [
        "3 General Infrastructures",
        "3 General Infrastructures > 3.1 Training",
        "4 Experiments",
    ]
    levels = [section["level"] for section in result.content["sections"]]
    assert levels == [1, 2, 1]


def test_read_section_includes_subsections_in_document_order(repository):
    """Breaks if reading a chapter stops at its own elements."""
    from paper_agent.services.agent_tools import PaperToolRegistry

    paper = repository.create_paper(
        original_filename="chapter.pdf", stored_filename="private-chapter.pdf"
    )
    repository.save_page(paper.id, Page(number=1, width=200, height=300))
    chapter = repository.save_section(
        paper.id,
        Section(id="ch3", title="3 General Infrastructures", order=0, page_number=1),
    )
    subsection = repository.save_section(
        paper.id,
        Section(id="s31", title="3.1 Training", order=1, page_number=1, level=2),
    )
    followup = repository.save_section(
        paper.id,
        Section(id="ch4", title="4 Experiments", order=2, page_number=1),
    )
    heading = repository.save_element(
        paper.id,
        DocumentElement(
            id="ch3-heading",
            kind="heading",
            text="3 General Infrastructures",
            page_number=1,
            bbox=BoundingBox(0, 0, 1, 0.1),
            section_id=chapter.id,
        ),
    )
    body = repository.save_element(
        paper.id,
        DocumentElement(
            id="ch3-body",
            kind="paragraph",
            text="The infrastructure spans training and serving.",
            page_number=1,
            bbox=BoundingBox(0, 0.15, 1, 0.3),
            section_id=chapter.id,
        ),
    )
    sub_body = repository.save_element(
        paper.id,
        DocumentElement(
            id="s31-body",
            kind="paragraph",
            text="Training uses a balanced router.",
            page_number=1,
            bbox=BoundingBox(0, 0.35, 1, 0.5),
            section_id=subsection.id,
        ),
    )
    repository.save_element(
        paper.id,
        DocumentElement(
            id="ch4-body",
            kind="paragraph",
            text="Experiments follow.",
            page_number=1,
            bbox=BoundingBox(0, 0.55, 1, 0.7),
            section_id=followup.id,
        ),
    )
    tools = PaperToolRegistry(repository)

    result = tools.execute(
        paper_id=paper.id,
        name="read_section",
        arguments={"section_id": chapter.id},
    )

    assert [section["id"] for section in result.content["subsections"]] == [subsection.id]
    assert [element["id"] for element in result.content["elements"]] == [
        heading.id,
        body.id,
        sub_body.id,
    ]
    # Each element carries its owning section's breadcrumb.
    assert result.content["elements"][2]["section_breadcrumb"] == (
        "3 General Infrastructures > 3.1 Training"
    )
    assert result.content["truncated"] is False
    assert result.evidence_element_ids == (heading.id, body.id, sub_body.id)

    without_subsections = tools.execute(
        paper_id=paper.id,
        name="read_section",
        arguments={"section_id": chapter.id, "include_subsections": False},
    )
    assert [element["id"] for element in without_subsections.content["elements"]] == [
        heading.id,
        body.id,
    ]


def test_read_section_truncates_oversized_chapters(repository):
    """Breaks if a huge chapter floods the context window without a note."""
    from paper_agent.services.agent_tools import PaperToolRegistry

    paper = repository.create_paper(
        original_filename="big.pdf", stored_filename="private-big.pdf"
    )
    repository.save_page(paper.id, Page(number=1, width=200, height=300))
    chapter = repository.save_section(
        paper.id,
        Section(id="big", title="Appendix", order=0, page_number=1),
    )
    first = repository.save_element(
        paper.id,
        DocumentElement(
            id="big-first",
            kind="paragraph",
            text="x" * 27_500,
            page_number=1,
            bbox=BoundingBox(0, 0, 1, 0.1),
            section_id=chapter.id,
        ),
    )
    repository.save_element(
        paper.id,
        DocumentElement(
            id="big-second",
            kind="paragraph",
            text="y" * 1_000,
            page_number=1,
            bbox=BoundingBox(0, 0.15, 1, 0.3),
            section_id=chapter.id,
        ),
    )

    result = PaperToolRegistry(repository).execute(
        paper_id=paper.id, name="read_section", arguments={"section_id": chapter.id}
    )

    assert [element["id"] for element in result.content["elements"]] == [first.id]
    assert result.content["truncated"] is True
    assert "read_section" in result.content["note"]
    assert result.evidence_element_ids == (first.id,)
