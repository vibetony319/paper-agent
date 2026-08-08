import pytest

from paper_agent.domain import GraphStage
from paper_agent.services.graph_extraction import (
    EdgeCandidate,
    GraphExtractionError,
    NodeCandidate,
    deduplicate_nodes,
    edge_output_schema,
    node_output_schema,
    parse_edge_candidates,
    parse_node_candidates,
)


def _node(
    local_id: str,
    name: str,
    evidence_id: str,
    *,
    node_type: str = "component",
    summary: str = "Routes tokens.",
) -> NodeCandidate:
    return NodeCandidate(
        local_id=local_id,
        node_type=node_type,
        name=name,
        summary=summary,
        evidence_element_ids=(evidence_id,),
    )


def test_node_candidates_reject_hallucinated_evidence_ids() -> None:
    """Breaks if unsupported evidence can enter the graph candidate boundary."""
    payload = {
        "nodes": [
            {
                "local_id": "n1",
                "node_type": "method",
                "name": "Router",
                "summary": "Routes tokens.",
                "evidence_element_ids": ["not-in-prompt"],
            }
        ]
    }

    with pytest.raises(GraphExtractionError, match="evidence"):
        parse_node_candidates(
            payload,
            stage=GraphStage.core,
            allowed_evidence_ids=frozenset({"e1"}),
        )


@pytest.mark.parametrize(
    "payload",
    [
        {"nodes": [{"local_id": "n1", "node_type": "method", "name": "Router", "summary": "Routes tokens.", "evidence_element_ids": ["e1"], "page": 1}]},
        {"nodes": [{"local_id": " ", "node_type": "method", "name": "Router", "summary": "Routes tokens.", "evidence_element_ids": ["e1"]}]},
        {"nodes": [{"local_id": "n1", "node_type": "component", "name": "Router", "summary": "Routes tokens.", "evidence_element_ids": ["e1"]}]},
        {"nodes": [{"local_id": "n1", "node_type": "method", "name": "Router", "summary": "Routes tokens.", "evidence_element_ids": []}]},
    ],
)
def test_node_candidates_reject_any_invalid_candidate(payload: dict) -> None:
    """Breaks if invalid model claims are silently retained or normalized."""
    with pytest.raises(GraphExtractionError):
        parse_node_candidates(
            payload,
            stage=GraphStage.core,
            allowed_evidence_ids=frozenset({"e1"}),
        )


def test_edge_candidates_require_existing_endpoints_and_supported_evidence() -> None:
    """Breaks if an edge can cite an absent node or fabricated source element."""
    payload = {
        "edges": [
            {
                "source_node_id": "stored-node-1",
                "target_node_id": "missing-node",
                "relation_type": "uses",
                "evidence_element_ids": ["e2"],
            }
        ]
    }

    with pytest.raises(GraphExtractionError, match="node"):
        parse_edge_candidates(
            payload,
            stage=GraphStage.deep,
            allowed_node_ids=frozenset({"stored-node-1"}),
            allowed_evidence_ids=frozenset({"e1"}),
        )


def test_edge_candidates_reject_invalid_candidate_without_returning_valid_edges() -> None:
    """Breaks if malformed edges permit a partial graph response."""
    payload = {
        "edges": [
            {
                "source_node_id": "stored-node-1",
                "target_node_id": "stored-node-2",
                "relation_type": "uses",
                "evidence_element_ids": ["e1"],
            },
            {
                "source_node_id": "stored-node-2",
                "target_node_id": "stored-node-1",
                "relation_type": "invented_relation",
                "evidence_element_ids": ["e1"],
            },
        ]
    }

    with pytest.raises(GraphExtractionError, match="relation"):
        parse_edge_candidates(
            payload,
            stage=GraphStage.deep,
            allowed_node_ids=frozenset({"stored-node-1", "stored-node-2"}),
            allowed_evidence_ids=frozenset({"e1"}),
        )


def test_deduplicator_merges_evidence_for_casefolded_same_type_names() -> None:
    """Breaks if case, Unicode, or repeated whitespace bypasses node merging."""
    nodes = deduplicate_nodes(
        (
            _node("n1", "Token Router", "e1"),
            _node("n2", "token   router", "e2"),
            _node("n3", "TOKEN ROUTER", "e1"),
        ),
        GraphStage.deep,
    )

    assert len(nodes) == 1
    assert nodes[0].name == "Token Router"
    assert nodes[0].summary == "Routes tokens."
    assert nodes[0].evidence_element_ids == ("e1", "e2")


def test_deduplicator_normalizes_unicode_and_preserves_distinct_node_types() -> None:
    """Breaks if equivalent Unicode names split or node types are conflated."""
    nodes = deduplicate_nodes(
        (
            _node("n1", "Caf\u00e9", "e1", node_type="concept"),
            _node("n2", "Cafe\u0301", "e2", node_type="concept"),
            _node("n3", "Caf\u00e9", "e3", node_type="component"),
        ),
        GraphStage.deep,
    )

    assert len(nodes) == 2
    assert nodes[0].evidence_element_ids == ("e1", "e2")
    assert nodes[1].evidence_element_ids == ("e3",)


def test_output_schemas_match_the_strict_parser_boundary() -> None:
    """Breaks if model output schemas allow fields the parser rejects."""
    node_schema = node_output_schema()
    edge_schema = edge_output_schema()
    node = node_schema["properties"]["nodes"]["items"]
    edge = edge_schema["properties"]["edges"]["items"]

    assert node_schema["additionalProperties"] is False
    assert node_schema["required"] == ["nodes"]
    assert node["additionalProperties"] is False
    assert edge_schema["additionalProperties"] is False
    assert edge_schema["required"] == ["edges"]
    assert edge["additionalProperties"] is False
    for candidate_schema in (node, edge):
        evidence_schema = candidate_schema["properties"]["evidence_element_ids"]
        assert evidence_schema["minItems"] == 1
        assert evidence_schema["uniqueItems"] is True
        assert evidence_schema["items"] == {
            "minLength": 1,
            "pattern": r"^\S(?:.*\S)?$",
            "type": "string",
        }

    for field_name in ("local_id", "name", "summary"):
        assert node["properties"][field_name]["pattern"] == r"^\S(?:.*\S)?$"
    for field_name in ("source_node_id", "target_node_id"):
        assert edge["properties"][field_name]["pattern"] == r"^\S(?:.*\S)?$"

    assert node["properties"]["node_type"]["enum"] == [
        "ablation",
        "claim",
        "component",
        "concept",
        "contribution",
        "dataset",
        "experiment",
        "limitation",
        "method",
        "metric",
        "problem",
        "result",
    ]
    assert edge["properties"]["relation_type"]["enum"] == [
        "addresses",
        "compares_with",
        "contradicts",
        "defines",
        "evaluated_on",
        "illustrates",
        "measured_by",
        "part_of",
        "produces",
        "related_to",
        "supports",
        "tests",
        "uses",
    ]


def test_payload_validation_errors_are_safe_and_do_not_expose_model_values() -> None:
    """Breaks if invalid model data leaks through Pydantic error details."""
    sensitive_value = "sensitive-model-output-1c1c4d31"
    payload = {
        "nodes": [
            {
                "local_id": "n1",
                "node_type": "method",
                "name": "Router",
                "summary": "Routes tokens.",
                "evidence_element_ids": ["e1"],
                "untrusted_detail": sensitive_value,
            }
        ]
    }

    with pytest.raises(GraphExtractionError) as captured:
        parse_node_candidates(
            payload,
            stage=GraphStage.core,
            allowed_evidence_ids=frozenset({"e1"}),
        )

    assert str(captured.value) == "invalid graph candidate response"
    assert sensitive_value not in str(captured.value)
    assert captured.value.__cause__ is None
