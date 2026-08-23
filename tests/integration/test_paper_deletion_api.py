from pathlib import Path
from threading import Event, Thread
from uuid import uuid4

from fastapi.testclient import TestClient

import paper_agent.services.reasoning_clients as reasoning_clients
from paper_agent.app import create_app
from paper_agent.config import Settings
from paper_agent.models.vllm import VllmModelConfig
from paper_agent.services.reasoning_clients import ENVIRONMENT_FALLBACK_PROFILE_ID


class BlockingChatClient:
    def __init__(self, started: Event, release: Event) -> None:
        self.started = started
        self.release = release

    def stream_text(self, messages):
        self.started.set()
        yield "部分"
        assert self.release.wait(timeout=5)
        yield "结果"


def _upload(client: TestClient, sample_pdf: Path) -> str:
    response = client.post(
        "/api/papers",
        files={"file": ("paper.pdf", sample_pdf.read_bytes(), "application/pdf")},
    )
    assert response.status_code == 201
    return response.json()["id"]


def _configured_client(tmp_path: Path, monkeypatch) -> TestClient:
    settings = Settings(
        data_dir=tmp_path / "delete-data",
        database_url=f"sqlite:///{tmp_path / 'delete-data' / 'paper-agent.db'}",
        reasoning_model=VllmModelConfig(
            base_url="http://127.0.0.1:9/v1", model="test-reasoning-model"
        ),
    )
    return TestClient(create_app(settings), raise_server_exceptions=False)


def _assist_payload(paper_id: str) -> dict:
    return {
        "quote": "selected text",
        "page_number": 1,
        "rects": [
            {"order": 0, "x0": 0.1, "y0": 0.2, "x1": 0.8, "y1": 0.25}
        ],
        "action": "explain",
        "model_profile_id": ENVIRONMENT_FALLBACK_PROFILE_ID,
        "request_id": str(uuid4()),
    }


def test_delete_api_removes_source_and_all_related_data(
    client: TestClient, sample_pdf: Path
) -> None:
    paper_id = _upload(client, sample_pdf)
    settings = client.app.state.settings
    stored_filename = client.app.state.paper_repository.get_paper(
        paper_id
    ).stored_filename
    client.post(
        "/api/model-profiles",
        json={
            "display_name": "本地模型",
            "base_url": "http://127.0.0.1:8001/v1",
            "model_name": "kept-model",
            "api_key": "profile-secret",
        },
    )

    response = client.request(
        "DELETE",
        f"/api/papers/{paper_id}",
        json={"confirmation": paper_id},
    )

    assert response.status_code == 204
    assert client.get(f"/api/papers/{paper_id}").status_code == 404
    assert client.get(f"/api/papers/{paper_id}/source").status_code == 404
    assert not (settings.papers_dir / stored_filename).exists()
    assert client.get("/api/model-profiles").status_code == 200


def test_delete_confirmation_mismatch_and_missing_paper(
    client: TestClient, sample_pdf: Path
) -> None:
    paper_id = _upload(client, sample_pdf)

    mismatch = client.request(
        "DELETE",
        f"/api/papers/{paper_id}",
        json={"confirmation": str(uuid4())},
    )
    missing = client.request(
        "DELETE",
        "/api/papers/00000000-0000-0000-0000-000000000000",
        json={"confirmation": "00000000-0000-0000-0000-000000000000"},
    )

    assert mismatch.status_code == 422
    assert mismatch.json()["code"] == "DELETE_CONFIRMATION_MISMATCH"
    assert client.get(f"/api/papers/{paper_id}").status_code == 200
    assert missing.status_code == 404
    assert missing.json()["code"] == "PAPER_NOT_FOUND"


def test_delete_returns_conflict_while_selection_stream_is_active(
    tmp_path: Path, sample_pdf: Path, monkeypatch
) -> None:
    client = _configured_client(tmp_path, monkeypatch)
    try:
        paper_id = _upload(client, sample_pdf)
        started = Event()
        release = Event()
        monkeypatch.setattr(
            reasoning_clients,
            "VllmChatClient",
            lambda _config, client=None: BlockingChatClient(started, release),
        )
        payload = _assist_payload(paper_id)

        def consume():
            try:
                with client.stream(
                    "POST",
                    f"/api/papers/{paper_id}/selection-assists",
                    json=payload,
                ) as response:
                    list(response.iter_lines())
            finally:
                release.set()

        worker = Thread(target=consume)
        worker.start()
        try:
            assert started.wait(timeout=3)
            response = client.request(
                "DELETE",
                f"/api/papers/{paper_id}",
                json={"confirmation": paper_id},
            )
            assert response.status_code == 409
            assert response.json()["code"] == "PAPER_BUSY"
        finally:
            release.set()
            worker.join(timeout=5)
    finally:
        client.close()


def test_startup_recovery_runs_before_routes_are_registered(
    tmp_path: Path, monkeypatch
) -> None:
    data_dir = tmp_path / "recover-data"
    settings = Settings(
        data_dir=data_dir,
        database_url=f"sqlite:///{data_dir / 'paper-agent.db'}",
    )
    from paper_agent.storage import PaperRepository

    repository = PaperRepository(settings.database_url)
    paper = repository.create_paper(
        original_filename="recover.pdf", stored_filename="recover.pdf"
    )
    source = settings.papers_dir / "recover.pdf"
    source.write_bytes(b"%PDF-1.4")
    from paper_agent.services.paper_deletion import PaperDeletionService

    service = PaperDeletionService(
        repository,
        papers_dir=settings.papers_dir,
        trash_dir=settings.trash_dir,
    )
    service._stage_for_test(paper.id, source)
    assert not source.exists()

    client = TestClient(create_app(settings), raise_server_exceptions=False)
    try:
        assert source.exists()
        assert list(settings.trash_dir.glob("*.delete.json")) == []
    finally:
        client.close()
