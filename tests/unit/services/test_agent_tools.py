from pathlib import Path

import pytest

from paper_agent.domain import (
    BoundingBox,
    DocumentElement,
    GraphEdge,
    GraphNode,
    GraphStage,
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


def _paper_with_graph(repository: PaperRepository):
    paper, section, located, unlocated = _paper_with_document(repository, "graph")
    nodes = (
        GraphNode(
            id="router-node",
            node_type="method",
            name="Caf\u00e9 Router",
            summary="Routes tokens to experts.",
            stage=GraphStage.core,
            evidence_element_ids=(located.id,),
        ),
        GraphNode(
            id="coverage-node",
            node_type="claim",
            name="Coverage",
            summary="The router improves expert coverage.",
            stage=GraphStage.core,
            evidence_element_ids=(located.id,),
        ),
    )
    edges = (
        GraphEdge(
            id="router-supports-coverage",
            source_node_id=nodes[0].id,
            target_node_id=nodes[1].id,
            relation_type="supports",
            stage=GraphStage.core,
            evidence_element_ids=(located.id,),
        ),
    )
    repository.replace_graph_stage(paper.id, GraphStage.core, nodes, edges)
    return paper, section, located, unlocated, nodes, edges


def test_definitions_expose_only_strict_openai_function_schemas(repository):
    """Breaks if a model can call an unbounded tool or send undeclared fields."""
    from paper_agent.services.agent_tools import PaperToolRegistry

    definitions = PaperToolRegistry(repository).definitions()

    assert [definition["function"]["name"] for definition in definitions] == [
        "search_paper",
        "read_element",
        "read_section",
        "search_graph",
        "inspect_node",
        "expand_graph",
        "find_graph_paths",
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
        ("expand_graph", {"node_id": "router-node", "depth": 4}),
        (
            "find_graph_paths",
            {"source_node_id": "router-node", "target_node_id": "coverage-node", "max_depth": -1},
        ),
    ],
)
def test_execution_rejects_unknown_blank_and_out_of_range_arguments(
    repository, name: str, arguments: dict[str, object]
):
    """Breaks if malformed tool calls reach repository reads with weakened bounds."""
    from paper_agent.services.agent_tools import AgentToolError, PaperToolRegistry

    paper, _, _, _, _, _ = _paper_with_graph(repository)

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


def test_section_and_graph_tools_return_public_navigation_with_source_evidence(repository):
    """Breaks if graph identifiers are treated as citations or graph reads leak private paper fields."""
    from paper_agent.services.agent_tools import PaperToolRegistry

    paper, section, located, _, nodes, edges = _paper_with_graph(repository)
    tools = PaperToolRegistry(repository)

    section_result = tools.execute(
        paper_id=paper.id,
        name="read_section",
        arguments={"section_id": section.id},
    )
    graph_result = tools.execute(
        paper_id=paper.id,
        name="search_graph",
        arguments={"query": "cafe\u0301", "limit": 5},
    )
    node_result = tools.execute(
        paper_id=paper.id,
        name="inspect_node",
        arguments={"node_id": nodes[0].id},
    )
    subgraph_result = tools.execute(
        paper_id=paper.id,
        name="expand_graph",
        arguments={"node_id": nodes[0].id, "depth": 1},
    )
    paths_result = tools.execute(
        paper_id=paper.id,
        name="find_graph_paths",
        arguments={
            "source_node_id": nodes[0].id,
            "target_node_id": nodes[1].id,
            "max_depth": 3,
        },
    )

    assert section_result.evidence_element_ids == (located.id,)
    assert [node["id"] for node in graph_result.content["nodes"]] == [nodes[0].id]
    assert node_result.evidence_element_ids == (located.id,)
    assert [node["id"] for node in subgraph_result.content["nodes"]] == [
        "coverage-node",
        "router-node",
    ]
    assert [edge["id"] for edge in subgraph_result.content["edges"]] == [edges[0].id]
    assert paths_result.content["paths"] == [[nodes[0].id, nodes[1].id]]
    assert paths_result.evidence_element_ids == (located.id,)
    assert set(paths_result.evidence_element_ids).isdisjoint(
        {nodes[0].id, nodes[1].id, edges[0].id}
    )
    assert "private-graph.pdf" not in repr(
        (section_result.content, graph_result.content, node_result.content, subgraph_result.content)
    )
