import json
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

import paper_agent.services.reasoning_clients as reasoning_clients
from paper_agent.app import create_app
from paper_agent.config import Settings
from paper_agent.domain import GraphEdge, GraphNode, GraphStage, ProcessingStatus
from paper_agent.models.vllm import VllmModelConfig
from paper_agent.services.reasoning_clients import ENVIRONMENT_FALLBACK_PROFILE_ID


def _upload_paper(client: TestClient, sample_pdf: Path) -> dict:
    response = client.post(
        "/api/papers",
        files={"file": ("paper.pdf", sample_pdf.read_bytes(), "application/pdf")},
    )
    assert response.status_code == 201
    return response.json()


def _seed_completed_core_graph(client: TestClient, paper_id: str) -> tuple[str, str, str]:
    """Creates a complete same-paper graph without a model call."""
    repository = client.app.state.paper_ingestion_service.repository
    evidence = repository.get_located_graph_source_elements(paper_id)[0]
    nodes = (
        GraphNode(
            id="router",
            node_type="method",
            name="Router",
            summary="Routes tokens to specialists.",
            stage=GraphStage.core,
            evidence_element_ids=(evidence.id,),
        ),
        GraphNode(
            id="efficiency",
            node_type="claim",
            name="Efficiency",
            summary="Reduces routing cost.",
            stage=GraphStage.core,
            evidence_element_ids=(evidence.id,),
        ),
        GraphNode(
            id="benchmark",
            node_type="experiment",
            name="Benchmark",
            summary="Measures routing quality.",
            stage=GraphStage.core,
            evidence_element_ids=(evidence.id,),
        ),
    )
    edges = (
        GraphEdge(
            id="router-supports-efficiency",
            source_node_id="router",
            target_node_id="efficiency",
            relation_type="supports",
            stage=GraphStage.core,
            evidence_element_ids=(evidence.id,),
        ),
        GraphEdge(
            id="efficiency-tests-benchmark",
            source_node_id="efficiency",
            target_node_id="benchmark",
            relation_type="tests",
            stage=GraphStage.core,
            evidence_element_ids=(evidence.id,),
        ),
    )
    repository.replace_graph_stage_and_complete(paper_id, GraphStage.core, nodes, edges)
    return tuple(node.id for node in nodes)


def _configured_client(tmp_path: Path, monkeypatch) -> TestClient:
    settings = Settings(
        data_dir=tmp_path / "configured-data",
        database_url=f"sqlite:///{tmp_path / 'configured-data' / 'paper-agent.db'}",
        reasoning_model=VllmModelConfig(
            base_url="http://127.0.0.1:9/v1", model="test-reasoning-model"
        ),
    )
    monkeypatch.setattr(
        reasoning_clients,
        "VllmStructuredClient",
        lambda _config, client=None: _CannedStructuredClient(),
    )
    return TestClient(create_app(settings), raise_server_exceptions=False)


def _graph_request(
    profile_id: str = ENVIRONMENT_FALLBACK_PROFILE_ID,
    request_id: str | None = None,
) -> dict:
    return {
        "model_profile_id": profile_id,
        "request_id": request_id or str(uuid4()),
    }


class _CannedStructuredClient:
    def __init__(self, failing: list[bool] | None = None) -> None:
        self.failing = failing
        self.calls: list[dict] = []

    def generate_json(self, **request) -> dict:
        self.calls.append(request)
        if self.failing and self.failing[0]:
            raise RuntimeError("raw reply with api_key=top-secret")
        payload = json.loads(request["user_prompt"])
        if request["schema_name"] == "paper_graph_nodes":
            evidence_id = payload["source_elements"][0]["id"]
            return {
                "nodes": [
                    {
                        "local_id": "n1",
                        "node_type": "method",
                        "name": "Router",
                        "summary": "Routes tokens to specialists.",
                        "evidence_element_ids": [evidence_id],
                    }
                ]
            }
        return {"edges": []}


def test_graph_queries_return_same_paper_evidence_and_traversal_results(
    client: TestClient, sample_pdf: Path
) -> None:
    """Breaks if graph reads omit evidence or traversal crosses graph boundaries."""
    uploaded = _upload_paper(client, sample_pdf)
    paper_id = uploaded["id"]
    router_id, efficiency_id, benchmark_id = _seed_completed_core_graph(client, paper_id)

    graph = client.get(f"/api/papers/{paper_id}/graph")
    node = client.get(f"/api/papers/{paper_id}/graph/nodes/{efficiency_id}")
    neighbors = client.get(
        f"/api/papers/{paper_id}/graph/nodes/{efficiency_id}/neighbors"
    )
    paths = client.get(
        f"/api/papers/{paper_id}/graph/paths",
        params={"source_id": router_id, "target_id": benchmark_id, "max_depth": 2},
    )
    subgraph = client.get(
        f"/api/papers/{paper_id}/graph/subgraph",
        params={"node_id": router_id, "depth": 1},
    )

    assert graph.status_code == 200
    assert graph.json()["nodes"][0]["evidence_element_ids"]
    assert graph.json()["edges"][0]["evidence_element_ids"]
    assert graph.json()["nodes"][0]["stage"] == "stage2"
    assert node.status_code == 200
    assert node.json()["id"] == efficiency_id
    assert neighbors.status_code == 200
    assert {item["id"] for item in neighbors.json()["nodes"]} == {
        router_id,
        efficiency_id,
        benchmark_id,
    }
    assert paths.status_code == 200
    assert paths.json() == {"paths": [[router_id, efficiency_id, benchmark_id]]}
    assert subgraph.status_code == 200
    assert {item["id"] for item in subgraph.json()["nodes"]} == {router_id, efficiency_id}
    assert str(client.app.state.settings.data_dir) not in graph.text
    assert "stored_filename" not in graph.text


def test_summary_reads_durable_graph_stage_statuses(client: TestClient, sample_pdf: Path) -> None:
    """Breaks if public summaries omit durable Stage 2 and Stage 3 state."""
    paper_id = _upload_paper(client, sample_pdf)["id"]
    _seed_completed_core_graph(client, paper_id)

    response = client.get(f"/api/papers/{paper_id}")

    assert response.status_code == 200
    assert response.json()["stage2_status"] == "completed"
    assert response.json()["stage3_status"] is None


def test_core_graph_build_without_vllm_configuration_is_safe_503(
    client: TestClient, sample_pdf: Path
) -> None:
    """Breaks if an optional reasoning service becomes a configuration leak."""
    paper_id = _upload_paper(client, sample_pdf)["id"]

    response = client.post(
        f"/api/papers/{paper_id}/graph/core", json=_graph_request()
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "Reasoning model is not configured."}
    assert (
        client.app.state.paper_ingestion_service.repository.get_stage_statuses(
            paper_id, "stage2"
        )
        == ()
    )


def test_incomplete_graph_build_without_vllm_is_409_without_processing_rows(
    client: TestClient, sample_pdf: Path
) -> None:
    """Breaks if missing model configuration masks graph prerequisite failures."""
    repository = client.app.state.paper_ingestion_service.repository
    stage1_incomplete = repository.create_paper(
        original_filename="incomplete.pdf",
        stored_filename="incomplete.pdf",
        status=ProcessingStatus.queued,
    )
    stage2_incomplete = _upload_paper(client, sample_pdf)

    core_response = client.post(
        f"/api/papers/{stage1_incomplete.id}/graph/core", json=_graph_request()
    )
    deep_response = client.post(
        f"/api/papers/{stage2_incomplete['id']}/graph/deep", json=_graph_request()
    )

    assert core_response.status_code == 409
    assert deep_response.status_code == 409
    assert repository.get_stage_statuses(stage1_incomplete.id, "stage2") == ()
    assert repository.get_stage_statuses(stage2_incomplete["id"], "stage3") == ()


def test_graph_routes_map_unknown_resources_invalid_parameters_and_prerequisites(
    tmp_path: Path, sample_pdf: Path, monkeypatch
) -> None:
    """Breaks if graph route boundary errors use the wrong HTTP status."""
    client = _configured_client(tmp_path, monkeypatch)
    try:
        uploaded = _upload_paper(client, sample_pdf)
        paper_id = uploaded["id"]
        repository = client.app.state.paper_ingestion_service.repository
        incomplete = repository.create_paper(
            original_filename="incomplete.pdf",
            stored_filename="incomplete.pdf",
            status=ProcessingStatus.queued,
        )

        assert client.get("/api/papers/missing/graph").status_code == 404
        assert (
            client.post(
                "/api/papers/missing/graph/core", json=_graph_request()
            ).status_code
            == 404
        )
        assert (
            client.post(
                f"/api/papers/{incomplete.id}/graph/core", json=_graph_request()
            ).status_code
            == 409
        )
        assert (
            client.post(
                f"/api/papers/{paper_id}/graph/deep", json=_graph_request()
            ).status_code
            == 409
        )
        assert client.get(f"/api/papers/{paper_id}/graph/nodes/missing").status_code == 404
        assert (
            client.get(f"/api/papers/{paper_id}/graph/nodes/missing/neighbors").status_code
            == 404
        )
        assert (
            client.get(
                f"/api/papers/{paper_id}/graph/paths",
                params={"source_id": "missing", "target_id": "also-missing"},
            ).status_code
            == 404
        )
        assert (
            client.get(
                f"/api/papers/{paper_id}/graph/subgraph", params={"node_id": "missing"}
            ).status_code
            == 404
        )
        assert (
            client.get(
                f"/api/papers/{paper_id}/graph/paths", params={"source_id": "router"}
            ).status_code
            == 422
        )
        assert (
            client.get(
                f"/api/papers/{paper_id}/graph/paths",
                params={"source_id": "router", "target_id": "router", "max_depth": -1},
            ).status_code
            == 422
        )
        assert (
            client.get(
                f"/api/papers/{paper_id}/graph/subgraph", params={"node_id": "router", "depth": -1}
            ).status_code
            == 422
        )
    finally:
        client.close()


def test_graph_build_failure_is_persisted_and_returns_nonleaking_500(
    tmp_path: Path, sample_pdf: Path, monkeypatch
) -> None:
    """Breaks if a durable graph failure exposes model output, paths, or secrets."""
    client = _configured_client(tmp_path, monkeypatch)
    try:
        paper_id = _upload_paper(client, sample_pdf)["id"]
        failing = [True]
        monkeypatch.setattr(
            reasoning_clients,
            "VllmStructuredClient",
            lambda _config, client=None: _CannedStructuredClient(failing=failing),
        )
        response = client.post(
            f"/api/papers/{paper_id}/graph/core", json=_graph_request()
        )

        assert response.status_code == 500
        assert response.json() == {"detail": "The graph could not be constructed."}
        assert "raw reply" not in response.text
        assert "top-secret" not in response.text
        summary = client.get(f"/api/papers/{paper_id}")
        assert summary.json()["stage2_status"] == "failed"
        assert summary.json()["stage3_status"] is None
        assert summary.json()["stage2_model"]["profile_id"] == ENVIRONMENT_FALLBACK_PROFILE_ID
    finally:
        client.close()


def test_graph_build_uses_requested_profile_and_records_stage_model(
    tmp_path: Path, sample_pdf: Path, monkeypatch
) -> None:
    """Breaks if graph provenance is missing from the durable paper summary."""
    client = _configured_client(tmp_path, monkeypatch)
    try:
        paper_id = _upload_paper(client, sample_pdf)["id"]
        request_id = str(uuid4())

        response = client.post(
            f"/api/papers/{paper_id}/graph/core",
            json=_graph_request(request_id=request_id),
        )

        assert response.status_code == 200
        summary = client.get(f"/api/papers/{paper_id}").json()
        assert summary["stage2_model"]["profile_id"] == ENVIRONMENT_FALLBACK_PROFILE_ID
        assert summary["stage2_model"]["model_name"] == "test-reasoning-model"
        assert summary["stage3_model"] is None
    finally:
        client.close()


def test_completed_graph_request_replays_without_resolving_the_model(
    tmp_path: Path, sample_pdf: Path, monkeypatch
) -> None:
    """Breaks if a repeated graph request runs another extraction call."""
    client = _configured_client(tmp_path, monkeypatch)
    try:
        paper_id = _upload_paper(client, sample_pdf)["id"]
        payload = _graph_request()
        first = client.post(f"/api/papers/{paper_id}/graph/core", json=payload)
        assert first.status_code == 200
        before = len(
            client.app.state.reasoning_client_provider
            .resolve(ENVIRONMENT_FALLBACK_PROFILE_ID)
            .structured.calls
        )

        replay = client.post(f"/api/papers/{paper_id}/graph/core", json=payload)

        assert replay.status_code == 200
        assert replay.json() == first.json()
        after = len(
            client.app.state.reasoning_client_provider
            .resolve(ENVIRONMENT_FALLBACK_PROFILE_ID)
            .structured.calls
        )
        assert after == before
    finally:
        client.close()


def test_running_graph_request_conflicts_without_processing_rows(
    tmp_path: Path, sample_pdf: Path, monkeypatch
) -> None:
    """Breaks if a running graph request can be started twice."""
    client = _configured_client(tmp_path, monkeypatch)
    try:
        paper_id = _upload_paper(client, sample_pdf)["id"]
        request_id = str(uuid4())
        repository = client.app.state.paper_repository
        repository.record_processing_status(
            paper_id,
            ProcessingStatus.running,
            stage="stage2",
            request_id=request_id,
        )

        response = client.post(
            f"/api/papers/{paper_id}/graph/core",
            json=_graph_request(request_id=request_id),
        )

        assert response.status_code == 409
        assert len(repository.get_stage_statuses(paper_id, "stage2")) == 1
    finally:
        client.close()


def test_failed_graph_request_retries_under_the_same_id(
    tmp_path: Path, sample_pdf: Path, monkeypatch
) -> None:
    """Breaks if a failed graph request cannot recover with the same request ID."""
    client = _configured_client(tmp_path, monkeypatch)
    try:
        paper_id = _upload_paper(client, sample_pdf)["id"]
        failing = [True]
        monkeypatch.setattr(
            reasoning_clients,
            "VllmStructuredClient",
            lambda _config, client=None: _CannedStructuredClient(failing=failing),
        )
        payload = _graph_request()
        failed = client.post(f"/api/papers/{paper_id}/graph/core", json=payload)
        assert failed.status_code == 500

        failing[0] = False
        retried = client.post(f"/api/papers/{paper_id}/graph/core", json=payload)

        assert retried.status_code == 200
        summary = client.get(f"/api/papers/{paper_id}").json()
        assert summary["stage2_status"] == "completed"
        assert summary["stage2_model"]["profile_id"] == ENVIRONMENT_FALLBACK_PROFILE_ID
    finally:
        client.close()


def test_graph_rejects_insufficient_structured_capability_before_queued_row(
    tmp_path: Path, sample_pdf: Path, monkeypatch
) -> None:
    """Breaks if graph starts before the selected model can emit structured JSON."""
    client = _configured_client(tmp_path, monkeypatch)
    try:
        paper_id = _upload_paper(client, sample_pdf)["id"]
        profile = client.post(
            "/api/model-profiles",
            json={
                "display_name": "Chat only",
                "base_url": "http://127.0.0.1:8002/v1",
                "model_name": "chat-model",
                "api_key": "profile-secret",
            },
        ).json()

        response = client.post(
            f"/api/papers/{paper_id}/graph/core",
            json=_graph_request(profile_id=profile["id"]),
        )

        assert response.status_code == 409
        assert "profile-secret" not in response.text
        repository = client.app.state.paper_repository
        assert repository.get_stage_statuses(paper_id, "stage2") == ()
    finally:
        client.close()
