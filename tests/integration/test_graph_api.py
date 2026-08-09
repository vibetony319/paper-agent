from pathlib import Path

from fastapi.testclient import TestClient

import paper_agent.app as app_module
from paper_agent.app import create_app
from paper_agent.config import Settings
from paper_agent.domain import GraphEdge, GraphNode, GraphStage, ProcessingStatus
from paper_agent.models.vllm import VllmModelConfig


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
    class LocalClient:
        def generate_json(self, **_kwargs) -> dict:
            raise AssertionError("graph construction should not call the local test client")

    monkeypatch.setattr(app_module, "VllmStructuredClient", lambda _config: LocalClient())
    return TestClient(create_app(settings), raise_server_exceptions=False)


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

    response = client.post(f"/api/papers/{paper_id}/graph/core")

    assert response.status_code == 503
    assert response.json() == {"detail": "Reasoning model is not configured."}


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
        assert client.post("/api/papers/missing/graph/core").status_code == 404
        assert client.post(f"/api/papers/{incomplete.id}/graph/core").status_code == 409
        assert client.post(f"/api/papers/{paper_id}/graph/deep").status_code == 409
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

        class FailingClient:
            def generate_json(self, **_kwargs) -> dict:
                raise RuntimeError("raw reply at C:\\secret\\model-output with api_key=top-secret")

        client.app.state.graph_construction_service.client = FailingClient()
        response = client.post(f"/api/papers/{paper_id}/graph/core")

        assert response.status_code == 500
        assert response.json() == {"detail": "The graph could not be constructed."}
        assert "raw reply" not in response.text
        assert "top-secret" not in response.text
        summary = client.get(f"/api/papers/{paper_id}")
        assert summary.json()["stage2_status"] == "failed"
        assert summary.json()["stage3_status"] is None
    finally:
        client.close()
