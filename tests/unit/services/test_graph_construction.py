import json

import pytest
from sqlalchemy import event
from sqlalchemy.exc import SQLAlchemyError

from paper_agent.domain import (
    BoundingBox,
    DocumentElement,
    GraphNode,
    GraphStage,
    Page,
    ProcessingStatus,
    Section,
)
from paper_agent.models.vllm import VllmResponseError
from paper_agent.model_profiles import ModelSnapshot
from paper_agent.services.graph_construction import (
    GraphBuildConflictError,
    GraphBuildPrerequisiteError,
    GraphBuildUnavailableError,
    GraphConstructionService,
)
from paper_agent.storage import PaperRepository


@pytest.fixture
def repository(tmp_path) -> PaperRepository:
    return PaperRepository(f"sqlite:///{tmp_path / 'paper-agent.db'}")


class FakeStructuredClient:
    def __init__(self, responses: list[dict | Exception | object]) -> None:
        self._responses = iter(responses)
        self.requests: list[dict] = []

    def generate_json(self, **request) -> dict:
        self.requests.append(request)
        response = next(self._responses)
        if isinstance(response, Exception):
            raise response
        if callable(response):
            return response(request)
        return response


def _node(local_id: str, node_type: str, name: str, evidence_ids: list[str]) -> dict:
    return {
        "local_id": local_id,
        "node_type": node_type,
        "name": name,
        "summary": f"{name} summary.",
        "evidence_element_ids": evidence_ids,
    }


def _snapshot(
    profile_id: str = "11111111-1111-4111-8111-111111111111",
) -> ModelSnapshot:
    return ModelSnapshot(
        profile_id=profile_id,
        display_name="本地 Qwen",
        base_url="http://127.0.0.1:8001/v1",
        model_name="Qwen3-32B",
        revision=1,
    )


def _edge_from_node_prompt(request: dict) -> dict:
    nodes = json.loads(request["user_prompt"])["nodes"]
    return {
        "edges": [
            {
                "source_node_id": nodes[0]["id"],
                "target_node_id": nodes[1]["id"],
                "relation_type": "supports",
                "evidence_element_ids": ["paragraph-1"],
            }
        ]
    }


def _paper_with_stage1_source(repository: PaperRepository):
    paper = repository.create_paper(
        original_filename="paper.pdf", stored_filename="paper.pdf"
    )
    repository.save_page(paper.id, Page(number=1, width=200, height=300))
    section = repository.save_section(paper.id, Section(title="Method", order=0))
    paragraph = repository.save_element(
        paper.id,
        DocumentElement(
            kind="paragraph",
            text="The Token Router sends tokens to experts.",
            page_number=1,
            bbox=BoundingBox(0, 0, 1, 0.2),
            section_id=section.id,
            id="paragraph-1",
        ),
    )
    repository.record_processing_status(
        paper.id, ProcessingStatus.completed, stage="stage1"
    )
    return paper, section, paragraph


def _paper_with_completed_core_graph(repository: PaperRepository):
    paper, section, paragraph = _paper_with_stage1_source(repository)
    core_node = GraphNode(
        id="core-node",
        node_type="method",
        name="Token Router",
        summary="Routes tokens to experts.",
        stage=GraphStage.core,
        evidence_element_ids=(paragraph.id,),
    )
    repository.replace_graph_stage(paper.id, GraphStage.core, (core_node,), ())
    repository.record_processing_status(
        paper.id, ProcessingStatus.completed, stage="stage2"
    )
    return paper, section, paragraph


def test_core_build_persists_deduplicated_nodes_edges_and_exact_evidence(
    repository: PaperRepository,
) -> None:
    """Breaks if construction trusts model IDs, loses evidence, or combines node and edge calls."""
    paper, section, paragraph = _paper_with_stage1_source(repository)
    client = FakeStructuredClient(
        [
            {
                "nodes": [
                    _node("n1", "method", "Token Router", [paragraph.id]),
                    _node("n2", "claim", "Routing improves coverage", [paragraph.id]),
                    _node("n3", "method", "token   router", [paragraph.id]),
                ]
            },
            _edge_from_node_prompt,
            {"edges": []},
        ]
    )

    graph = GraphConstructionService(repository=repository).build_core(
        paper.id,
        client=client,
        model_snapshot=_snapshot(),
        request_id="request-core",
    )

    assert {(node.node_type, node.name) for node in graph.nodes} == {
        ("method", "Token Router"),
        ("claim", "Routing improves coverage"),
    }
    assert all(node.evidence_element_ids == (paragraph.id,) for node in graph.nodes)
    assert [(edge.relation_type, edge.evidence_element_ids) for edge in graph.edges] == [
        ("supports", (paragraph.id,))
    ]
    assert all(node.id not in {"n1", "n2", "n3"} for node in graph.nodes)
    assert repository.get_latest_stage_status(paper.id, "stage2") == ProcessingStatus.completed
    assert repository.get_latest_stage_status(paper.id, "stage3") is None
    assert [request["schema_name"] for request in client.requests] == [
        "paper_graph_nodes",
        "paper_graph_edges",
        "paper_graph_cross_section_edges",
    ]
    node_prompt = client.requests[0]["user_prompt"]
    assert paragraph.id in node_prompt
    assert section.title in node_prompt
    assert paragraph.text in node_prompt
    assert "return only the supplied json schema" in client.requests[0]["system_prompt"].lower()


def test_build_prompts_limit_scope_and_cross_section_payloads_to_supplied_evidence(
    repository: PaperRepository,
) -> None:
    """Breaks if a prompt leaks fields outside the evidence and persistent-node contract."""
    paper, section, paragraph = _paper_with_stage1_source(repository)
    client = FakeStructuredClient(
        [
            {"nodes": [_node("n1", "method", "Token Router", [paragraph.id])]},
            {"edges": []},
            {"edges": []},
        ]
    )

    GraphConstructionService(repository=repository).build_core(
        paper.id,
        client=client,
        model_snapshot=_snapshot(),
        request_id="request-scope",
    )

    node_payload = json.loads(client.requests[0]["user_prompt"])
    assert node_payload == {
        "section": {"title": section.title},
        "source_elements": [
            {"id": paragraph.id, "kind": "paragraph", "text": paragraph.text}
        ],
    }

    edge_payload = json.loads(client.requests[1]["user_prompt"])
    assert set(edge_payload) == {"section", "nodes", "source_elements"}
    assert edge_payload["section"] == {"title": section.title}
    assert edge_payload["source_elements"] == node_payload["source_elements"]
    assert edge_payload["nodes"] == [
        {
            "id": edge_payload["nodes"][0]["id"],
            "name": "Token Router",
            "summary": "Token Router summary.",
        }
    ]

    cross_section_payload = json.loads(client.requests[2]["user_prompt"])
    assert set(cross_section_payload) == {"nodes", "source_elements"}
    assert cross_section_payload["nodes"] == edge_payload["nodes"]
    assert cross_section_payload["source_elements"] == [
        {"id": paragraph.id, "text": paragraph.text}
    ]


def test_core_build_uses_located_text_blocks_when_a_section_has_no_located_paragraphs(
    repository: PaperRepository,
) -> None:
    """Breaks if a section with unlocated prose loses its Stage 0 evidence fallback."""
    paper = repository.create_paper(
        original_filename="paper.pdf", stored_filename="paper.pdf"
    )
    repository.save_page(paper.id, Page(number=1, width=200, height=300))
    section = repository.save_section(paper.id, Section(title="Results", order=0))
    text_block = repository.save_element(
        paper.id,
        DocumentElement(
            id="text-block-1",
            kind="text_block",
            text="The model improves accuracy.",
            page_number=1,
            bbox=BoundingBox(0, 0, 1, 0.2),
        ),
    )
    repository.save_element(
        paper.id,
        DocumentElement.paragraph(
            "The model improves accuracy.",
            section_id=section.id,
            location_status="unlocated",
        ),
    )
    repository.record_processing_status(
        paper.id, ProcessingStatus.completed, stage="stage1"
    )
    client = FakeStructuredClient(
        [
            {"nodes": [_node("n1", "claim", "Accuracy improves", [text_block.id])]},
            {"edges": []},
            {"edges": []},
        ]
    )

    graph = GraphConstructionService(repository=repository).build_core(
        paper.id,
        client=client,
        model_snapshot=_snapshot(),
        request_id="request-blocks",
    )

    assert graph.nodes[0].evidence_element_ids == (text_block.id,)
    assert text_block.id in client.requests[0]["user_prompt"]
    assert section.title in client.requests[0]["user_prompt"]


def test_deep_build_failure_keeps_completed_core_graph_and_marks_only_stage3_failed(
    repository: PaperRepository,
) -> None:
    """Breaks if a Stage 3 model error clears a completed core graph or its status."""
    paper, _, _ = _paper_with_completed_core_graph(repository)
    client = FakeStructuredClient([VllmResponseError("unavailable")])

    with pytest.raises(VllmResponseError):
        GraphConstructionService(repository=repository).build_deep(
            paper.id,
            client=client,
            model_snapshot=_snapshot(),
            request_id="request-deep",
        )

    assert repository.get_latest_stage_status(paper.id, "stage2") == ProcessingStatus.completed
    assert repository.get_latest_stage_status(paper.id, "stage3") == ProcessingStatus.failed
    assert repository.get_graph(paper.id).nodes[0].id == "core-node"
    assert repository.get_paper(paper.id).status == ProcessingStatus.partial


def test_final_completion_failure_rolls_back_core_rebuild_and_preserves_prior_graph(
    repository: PaperRepository,
) -> None:
    """Breaks if final completion can commit a replacement graph before it fails."""
    paper, _, paragraph = _paper_with_completed_core_graph(repository)
    client = FakeStructuredClient(
        [
            {"nodes": [_node("n1", "claim", "Replacement claim", [paragraph.id])]},
            {"edges": []},
            {"edges": []},
        ]
    )
    failed = False

    def fail_completed_processing_run(
        connection, cursor, statement, parameters, context, executemany
    ) -> None:
        nonlocal failed
        if (
            not failed
            and "INSERT INTO processing_runs" in statement
            and "completed" in parameters
        ):
            failed = True
            raise SQLAlchemyError("final completion write failed")

    event.listen(repository.engine, "before_cursor_execute", fail_completed_processing_run)
    try:
        with pytest.raises(SQLAlchemyError, match="final completion write failed"):
            GraphConstructionService(repository=repository).build_core(
                paper.id,
                client=client,
                model_snapshot=_snapshot(),
                request_id="request-rollback",
            )
    finally:
        event.remove(
            repository.engine, "before_cursor_execute", fail_completed_processing_run
        )

    assert failed
    assert [node.id for node in repository.get_graph(paper.id).nodes] == ["core-node"]
    assert repository.get_latest_stage_status(paper.id, "stage2") == ProcessingStatus.failed
    assert repository.get_paper(paper.id).status == ProcessingStatus.partial


def test_successful_core_retry_recovers_aggregate_status_and_error(
    repository: PaperRepository,
) -> None:
    """Breaks if a completed retry remains publicly partial after an earlier failure."""
    paper, _, paragraph = _paper_with_stage1_source(repository)

    with pytest.raises(VllmResponseError):
        GraphConstructionService(repository=repository).build_core(
            paper.id,
            client=FakeStructuredClient([VllmResponseError("unavailable")]),
            model_snapshot=_snapshot(),
            request_id="request-retry",
        )

    GraphConstructionService(
        repository=repository,
    ).build_core(
        paper.id,
        client=FakeStructuredClient(
            [
                {"nodes": [_node("n1", "method", "Token Router", [paragraph.id])]},
                {"edges": []},
                {"edges": []},
            ]
        ),
        model_snapshot=_snapshot(),
        request_id="request-retry",
    )

    assert repository.get_latest_stage_status(paper.id, "stage2") == ProcessingStatus.completed
    assert repository.get_paper(paper.id).status == ProcessingStatus.completed
    assert repository.get_processing_error(paper.id) is None


def test_successful_core_rebuild_invalidates_completed_deep_stage(
    repository: PaperRepository,
) -> None:
    """Breaks if a rebuilt core graph leaves removed deep data marked completed."""
    paper, _, paragraph = _paper_with_completed_core_graph(repository)
    repository.replace_graph_stage(
        paper.id,
        GraphStage.deep,
        (
            GraphNode(
                id="deep-node",
                node_type="component",
                name="Expert selector",
                summary="Selects an expert for each token.",
                stage=GraphStage.deep,
                evidence_element_ids=(paragraph.id,),
            ),
        ),
        (),
    )
    repository.record_processing_status(
        paper.id, ProcessingStatus.completed, stage="stage3"
    )

    graph = GraphConstructionService(repository=repository).build_core(
        paper.id,
        client=FakeStructuredClient(
            [
                {"nodes": [_node("n1", "claim", "Coverage improves", [paragraph.id])]},
                {"edges": []},
                {"edges": []},
            ]
        ),
        model_snapshot=_snapshot(),
        request_id="request-rebuild",
    )

    assert {node.id for node in graph.nodes} != {"core-node", "deep-node"}
    assert all(node.stage is GraphStage.core for node in graph.nodes)
    assert repository.get_latest_stage_status(paper.id, "stage3") == ProcessingStatus.queued


def test_successful_core_rebuild_invalidates_completed_empty_deep_stage(
    repository: PaperRepository,
) -> None:
    """Breaks if empty but completed deep output survives a changed core input."""
    paper, _, paragraph = _paper_with_completed_core_graph(repository)
    repository.replace_graph_stage(paper.id, GraphStage.deep, (), ())
    repository.record_processing_status(
        paper.id, ProcessingStatus.completed, stage="stage3"
    )

    GraphConstructionService(repository=repository).build_core(
        paper.id,
        client=FakeStructuredClient(
            [
                {"nodes": [_node("n1", "claim", "Coverage improves", [paragraph.id])]},
                {"edges": []},
                {"edges": []},
            ]
        ),
        model_snapshot=_snapshot(),
        request_id="request-empty-rebuild",
    )

    assert repository.get_latest_stage_status(paper.id, "stage3") == ProcessingStatus.queued


@pytest.mark.parametrize("method_name", ("build_core", "build_deep"))
def test_build_requires_completed_prior_stage_without_starting_processing(
    repository: PaperRepository, method_name: str
) -> None:
    """Breaks if graph extraction starts before its durable input stage is complete."""
    paper = repository.create_paper(
        original_filename="paper.pdf", stored_filename="paper.pdf"
    )
    service = GraphConstructionService(repository=repository)

    with pytest.raises(GraphBuildPrerequisiteError):
        getattr(service, method_name)(
            paper.id,
            client=FakeStructuredClient([]),
            model_snapshot=_snapshot(),
            request_id="request-prerequisite",
        )

    stage = "stage2" if method_name == "build_core" else "stage3"
    assert repository.get_latest_stage_status(paper.id, stage) is None


def test_build_rejects_missing_reasoning_client_without_mutating_status(
    repository: PaperRepository,
) -> None:
    """Breaks if unavailable optional reasoning configuration creates a misleading run."""
    paper, _, _ = _paper_with_stage1_source(repository)

    with pytest.raises(GraphBuildUnavailableError):
        GraphConstructionService(repository=repository).build_core(
            paper.id,
            client=None,
            model_snapshot=_snapshot(),
            request_id="request-unavailable",
        )

    assert repository.get_latest_stage_status(paper.id, "stage2") is None


def test_unexpected_build_error_records_safe_failed_partial_state(
    repository: PaperRepository,
) -> None:
    """Breaks if an implementation defect escapes with a running graph stage left behind."""
    paper, _, _ = _paper_with_stage1_source(repository)
    client = FakeStructuredClient([RuntimeError("private implementation failure")])

    with pytest.raises(RuntimeError, match="private implementation failure"):
        GraphConstructionService(repository=repository).build_core(
            paper.id,
            client=client,
            model_snapshot=_snapshot(),
            request_id="request-unexpected",
        )

    assert repository.get_stage_statuses(paper.id, "stage2") == (
        ProcessingStatus.queued,
        ProcessingStatus.running,
        ProcessingStatus.failed,
    )
    assert repository.get_paper(paper.id).status == ProcessingStatus.partial


def test_processing_status_write_error_records_safe_failed_partial_state(
    repository: PaperRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Breaks if a durable status-write error leaves a graph stage running."""
    paper, _, _ = _paper_with_stage1_source(repository)
    record_status = repository.record_processing_status

    def fail_when_marked_running(
        paper_id: str,
        status: ProcessingStatus,
        *,
        stage: str | None = None,
        error_summary: str | None = None,
        **kwargs,
    ) -> None:
        if status is ProcessingStatus.running:
            raise SQLAlchemyError("status write failed")
        record_status(
            paper_id, status, stage=stage, error_summary=error_summary, **kwargs
        )

    monkeypatch.setattr(repository, "record_processing_status", fail_when_marked_running)

    with pytest.raises(SQLAlchemyError, match="status write failed"):
        GraphConstructionService(repository=repository).build_core(
            paper.id,
            client=FakeStructuredClient([]),
            model_snapshot=_snapshot(),
            request_id="request-status-write",
        )

    assert repository.get_latest_stage_status(paper.id, "stage2") == ProcessingStatus.failed
    assert repository.get_paper(paper.id).status == ProcessingStatus.partial


def test_build_records_request_provenance_and_latest_graph_model(
    repository: PaperRepository,
) -> None:
    """Breaks if a successful graph build loses its request model provenance."""
    paper, _, paragraph = _paper_with_stage1_source(repository)
    snapshot = _snapshot(profile_id="22222222-2222-4222-8222-222222222222")
    client = FakeStructuredClient(
        [
            {"nodes": [_node("n1", "method", "Token Router", [paragraph.id])]},
            {"edges": []},
            {"edges": []},
        ]
    )

    GraphConstructionService(repository=repository).build_core(
        paper.id,
        client=client,
        model_snapshot=snapshot,
        request_id="request-provenance",
    )

    assert repository.get_graph_build_status(
        paper.id, "stage2", "request-provenance"
    ) == ProcessingStatus.completed
    assert repository.get_latest_graph_model(paper.id, "stage2") == snapshot


def test_completed_duplicate_request_replays_without_calling_the_model(
    repository: PaperRepository,
) -> None:
    """Breaks if a completed request re-runs extraction instead of replaying."""
    paper, _, paragraph = _paper_with_stage1_source(repository)
    client = FakeStructuredClient(
        [
            {"nodes": [_node("n1", "method", "Token Router", [paragraph.id])]},
            {"edges": []},
            {"edges": []},
        ]
    )
    GraphConstructionService(repository=repository).build_core(
        paper.id,
        client=client,
        model_snapshot=_snapshot(),
        request_id="request-replay",
    )

    replay = GraphConstructionService(repository=repository).build_core(
        paper.id,
        client=FakeStructuredClient([]),
        model_snapshot=_snapshot(),
        request_id="request-replay",
    )

    assert [node.name for node in replay.nodes] == ["Token Router"]
    assert client.requests[-1]["schema_name"] == "paper_graph_cross_section_edges"


def test_running_duplicate_request_conflicts_without_another_build(
    repository: PaperRepository,
) -> None:
    """Breaks if a running request can start a second graph extraction."""
    paper, _, _ = _paper_with_stage1_source(repository)
    repository.record_processing_status(
        paper.id,
        ProcessingStatus.running,
        stage="stage2",
        request_id="request-running",
        model_profile_id="11111111-1111-4111-8111-111111111111",
        model_snapshot=_snapshot(),
    )

    with pytest.raises(GraphBuildConflictError):
        GraphConstructionService(repository=repository).build_core(
            paper.id,
            client=FakeStructuredClient([]),
            model_snapshot=_snapshot(),
            request_id="request-running",
        )

    assert repository.get_graph_build_status(
        paper.id, "stage2", "request-running"
    ) == ProcessingStatus.running


def test_failed_duplicate_retry_reuses_key_and_keeps_failure_snapshot(
    repository: PaperRepository,
) -> None:
    """Breaks if a failed request cannot be retried under the same request ID."""
    paper, _, paragraph = _paper_with_stage1_source(repository)
    failed_snapshot = _snapshot(profile_id="33333333-3333-4333-8333-333333333333")
    with pytest.raises(VllmResponseError):
        GraphConstructionService(repository=repository).build_core(
            paper.id,
            client=FakeStructuredClient([VllmResponseError("unavailable")]),
            model_snapshot=failed_snapshot,
            request_id="request-failed-retry",
        )
    assert repository.get_latest_graph_model(paper.id, "stage2") == failed_snapshot

    GraphConstructionService(repository=repository).build_core(
        paper.id,
        client=FakeStructuredClient(
            [
                {"nodes": [_node("n1", "method", "Token Router", [paragraph.id])]},
                {"edges": []},
                {"edges": []},
            ]
        ),
        model_snapshot=_snapshot(profile_id="44444444-4444-4444-8444-444444444444"),
        request_id="request-failed-retry",
    )

    assert repository.get_latest_stage_status(paper.id, "stage2") == ProcessingStatus.completed
    assert repository.get_latest_graph_model(
        paper.id, "stage2"
    ) == _snapshot(profile_id="44444444-4444-4444-8444-444444444444")
