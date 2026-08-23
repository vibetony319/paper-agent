from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

import paper_agent.services.reasoning_clients as reasoning_clients
from paper_agent.config import Settings
from paper_agent.models.vllm import VllmModelConfig
from paper_agent.services.reasoning_clients import ENVIRONMENT_FALLBACK_PROFILE_ID
from paper_agent.app import create_app


class FakeChatClient:
    def __init__(self, chunks: list[str] | None = None) -> None:
        self.chunks = chunks or ["这是", "流式解释。"]

    def stream_text(self, messages):
        yield from self.chunks


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
        "VllmChatClient",
        lambda _config, client=None: FakeChatClient(),
    )
    return TestClient(create_app(settings), raise_server_exceptions=False)


def _upload(client: TestClient, sample_pdf: Path) -> str:
    response = client.post(
        "/api/papers",
        files={"file": ("paper.pdf", sample_pdf.read_bytes(), "application/pdf")},
    )
    assert response.status_code == 201
    return response.json()["id"]


def _payload(request_id: str | None = None, profile_id: str | None = None) -> dict:
    return {
        "quote": "selected text",
        "page_number": 1,
        "rects": [
            {"order": 0, "x0": 0.1, "y0": 0.2, "x1": 0.8, "y1": 0.25}
        ],
        "action": "explain",
        "model_profile_id": profile_id or ENVIRONMENT_FALLBACK_PROFILE_ID,
        "request_id": request_id or str(uuid4()),
    }


def _sse_events(body: str) -> list[tuple[str, str]]:
    events: list[tuple[str, str]] = []
    for frame in body.split("\n\n"):
        lines = frame.strip().splitlines()
        if not lines or not lines[0].startswith("event:"):
            continue
        event = lines[0].removeprefix("event:").strip()
        data = next(
            (line.removeprefix("data:").strip() for line in lines if line.startswith("data:")),
            "",
        )
        events.append((event, data))
    return events


def test_explanation_streams_and_persists_one_generated_note(
    tmp_path: Path, sample_pdf: Path, monkeypatch
) -> None:
    client = _configured_client(tmp_path, monkeypatch)
    try:
        paper_id = _upload(client, sample_pdf)

        response = client.post(
            f"/api/papers/{paper_id}/selection-assists", json=_payload()
        )

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        events = _sse_events(response.text)
        assert [event for event, _ in events] == [
            "started",
            "delta",
            "delta",
            "completed",
        ]
        notes = client.get(f"/api/papers/{paper_id}/annotations").json()["notes"]
        assert len(notes) == 1
        assert notes[0]["note_type"] == "explanation"
        assert notes[0]["ai_generated"] is True
    finally:
        client.close()


def test_selection_assist_without_model_is_safe_503(
    client: TestClient, sample_pdf: Path
) -> None:
    paper_id = _upload(client, sample_pdf)

    response = client.post(
        f"/api/papers/{paper_id}/selection-assists",
        json=_payload(profile_id=ENVIRONMENT_FALLBACK_PROFILE_ID),
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "Reasoning model is not configured."}


def test_selection_assist_rejects_missing_basic_chat_capability(
    tmp_path: Path, sample_pdf: Path, monkeypatch
) -> None:
    client = _configured_client(tmp_path, monkeypatch)
    try:
        paper_id = _upload(client, sample_pdf)
        profile = client.post(
            "/api/model-profiles",
            json={
                "display_name": "聊天档案",
                "base_url": "http://127.0.0.1:8002/v1",
                "model_name": "chat-model",
                "api_key": "profile-secret",
            },
        ).json()

        response = client.post(
            f"/api/papers/{paper_id}/selection-assists",
            json=_payload(profile_id=profile["id"]),
        )

        assert response.status_code == 409
        assert "profile-secret" not in response.text
    finally:
        client.close()


def test_completed_assist_replays_without_restarting_the_stream(
    tmp_path: Path, sample_pdf: Path, monkeypatch
) -> None:
    client = _configured_client(tmp_path, monkeypatch)
    try:
        paper_id = _upload(client, sample_pdf)
        payload = _payload()

        first = client.post(
            f"/api/papers/{paper_id}/selection-assists", json=payload
        )
        replay = client.post(
            f"/api/papers/{paper_id}/selection-assists", json=payload
        )

        assert first.status_code == 200
        assert replay.status_code == 200
        assert [event for event, _ in _sse_events(replay.text)] == ["completed"]
        assert len(client.get(f"/api/papers/{paper_id}/annotations").json()["notes"]) == 1
    finally:
        client.close()
