import pytest

from paper_agent.domain import (
    AgentMessageRole,
    AgentMode,
    BoundingBox,
    Conversation,
    ConversationMessage,
    DocumentElement,
    GraphEdge,
    GraphNode,
    GraphStage,
    ProcessingStatus,
)


def test_conversation_records_expose_durable_agent_values():
    """Breaks if persisted conversation records lose their stable defaults or enum values."""
    conversation = Conversation(paper_id="paper-1", mode=AgentMode.paper_only)
    message = ConversationMessage(
        conversation_id=conversation.id,
        paper_id=conversation.paper_id,
        role=AgentMessageRole.user,
        content="What does the paper claim?",
    )

    assert [mode.value for mode in AgentMode] == ["paper_only", "external_knowledge"]
    assert [role.value for role in AgentMessageRole] == ["user", "assistant"]
    assert message.citation_element_ids == ()
    assert message.sequence is None


def test_bounding_box_is_normalized_and_clamped():
    """Breaks if page-coordinate conversion leaks out-of-bounds geometry."""
    bbox = BoundingBox.from_page_rect((-10, 20, 70, 120), 100, 100)

    assert bbox == BoundingBox(x0=0.0, y0=0.2, x1=0.7, y1=1.0)


def test_unlocated_element_cannot_have_page_or_geometry():
    """Breaks if semantic-only content can falsely claim a source location."""
    with pytest.raises(ValueError, match="unlocated"):
        DocumentElement.paragraph(
            text="semantic only", location_status="unlocated", page_number=1
        )

    with pytest.raises(ValueError, match="unlocated"):
        DocumentElement.paragraph(
            text="semantic only",
            location_status="unlocated",
            bbox=BoundingBox(0, 0, 1, 0.1),
        )


def test_located_element_requires_a_complete_source_location():
    """Breaks if a purportedly located element can be persisted half-located."""
    with pytest.raises(ValueError, match="located"):
        DocumentElement.paragraph(text="missing geometry", page_number=1)


def test_processing_status_exposes_persisted_state_values():
    """Breaks if persisted processing states drift from the stable contract."""
    assert [status.value for status in ProcessingStatus] == [
        "queued",
        "running",
        "completed",
        "partial",
        "failed",
    ]


def test_graph_records_require_allowed_types_and_source_evidence():
    """Breaks if graph values can be created without grounded evidence or valid vocabulary."""
    with pytest.raises(ValueError, match="evidence"):
        GraphNode(
            node_type="method",
            name="Router",
            summary="Routes tokens.",
            stage=GraphStage.core,
            evidence_element_ids=(),
        )

    with pytest.raises(ValueError, match="relation"):
        GraphEdge(
            source_node_id="a",
            target_node_id="b",
            relation_type="improves",
            stage=GraphStage.core,
            evidence_element_ids=("element-1",),
        )


def test_graph_nodes_enforce_stage_vocabulary_and_nonempty_trimmed_values():
    """Breaks if invalid model output can create ambiguous graph nodes."""
    with pytest.raises(ValueError, match="node type"):
        GraphNode(
            node_type="component",
            name="Router",
            summary="Routes tokens.",
            stage=GraphStage.core,
            evidence_element_ids=("element-1",),
        )

    with pytest.raises(ValueError, match="name"):
        GraphNode(
            node_type="method",
            name=" Router ",
            summary="Routes tokens.",
            stage=GraphStage.core,
            evidence_element_ids=("element-1",),
        )

    with pytest.raises(ValueError, match="summary"):
        GraphNode(
            node_type="method",
            name="Router",
            summary=" ",
            stage=GraphStage.core,
            evidence_element_ids=("element-1",),
        )


def test_graph_records_reject_duplicate_or_empty_evidence_and_self_edges():
    """Breaks if evidence can be duplicated, blank, or edge endpoints collapse."""
    with pytest.raises(ValueError, match="evidence"):
        GraphNode(
            node_type="method",
            name="Router",
            summary="Routes tokens.",
            stage=GraphStage.core,
            evidence_element_ids=("element-1", "element-1"),
        )

    with pytest.raises(ValueError, match="evidence"):
        GraphEdge(
            source_node_id="a",
            target_node_id="b",
            relation_type="uses",
            stage=GraphStage.core,
            evidence_element_ids=(" ",),
        )

    with pytest.raises(ValueError, match="different"):
        GraphEdge(
            source_node_id="node-1",
            target_node_id="node-1",
            relation_type="uses",
            stage=GraphStage.core,
            evidence_element_ids=("element-1",),
        )


@pytest.mark.parametrize("evidence_element_ids", ("abc", ["element-1"]))
def test_graph_nodes_require_tuple_evidence_ids(evidence_element_ids):
    """Breaks if a string or list can pass through graph-node persistence."""
    with pytest.raises(ValueError, match="evidence.*tuple"):
        GraphNode(
            node_type="method",
            name="Router",
            summary="Routes tokens.",
            stage=GraphStage.core,
            evidence_element_ids=evidence_element_ids,
        )


@pytest.mark.parametrize("evidence_element_ids", ("abc", ["element-1"]))
def test_graph_edges_require_tuple_evidence_ids(evidence_element_ids):
    """Breaks if a string or list can pass through graph-edge persistence."""
    with pytest.raises(ValueError, match="evidence.*tuple"):
        GraphEdge(
            source_node_id="node-a",
            target_node_id="node-b",
            relation_type="uses",
            stage=GraphStage.core,
            evidence_element_ids=evidence_element_ids,
        )
