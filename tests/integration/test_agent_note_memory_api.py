import json
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import func, select

import paper_agent.services.reasoning_clients as reasoning_clients
from paper_agent.database import conversations
from paper_agent.app import create_app
from paper_agent.config import Settings
from paper_agent.models.vllm import VllmModelConfig, VllmToolCall, VllmToolTurn
from paper_agent.services.reasoning_clients import ENVIRONMENT_FALLBACK_PROFILE_ID


class FakeToolClient:
    def __init__(self) -> None:
        self.turns = 0

    def request_tool_turn(self, **_kwargs):
        self.turns += 1
        if self.turns == 1:
            return VllmToolTurn(
                content=None,
                tool_calls=(
                    VllmToolCall(
                        id="call-1",
                        name="search_paper",
                        arguments={"query": "sample body", "limit": 5},
                    ),
                ),
                reasoning_content="先检索正文，再回答。",
            )
        return VllmToolTurn(content=None, tool_calls=())

    def complete_markdown_messages(self, *, messages, **_kwargs):
        tool_result = json.loads(
            next(
                message["content"]
                for message in reversed(messages)
                if message["role"] == "tool"
            )
        )
        evidence_ids = tool_result["evidence_element_ids"]
        return f"The paper introduces its method. [[{evidence_ids[0]}]]"


def _configured_client(tmp_path: Path, monkeypatch) -> TestClient:
    settings = Settings(
        data_dir=tmp_path / "note-data",
        database_url=f"sqlite:///{tmp_path / 'note-data' / 'paper-agent.db'}",
        reasoning_model=VllmModelConfig(
            base_url="http://127.0.0.1:9/v1", model="test-reasoning-model"
        ),
    )
    monkeypatch.setattr(
        reasoning_clients,
        "VllmToolCallingClient",
        lambda _config, client=None: FakeToolClient(),
    )
    return TestClient(create_app(settings), raise_server_exceptions=False)


def _upload(client: TestClient, sample_pdf: Path) -> str:
    response = client.post(
        "/api/papers",
        files={"file": ("paper.pdf", sample_pdf.read_bytes(), "application/pdf")},
    )
    assert response.status_code == 201
    return response.json()["id"]


def test_agent_uses_relevant_notes_and_history_keeps_deleted_references(
    tmp_path: Path, sample_pdf: Path, monkeypatch
) -> None:
    client = _configured_client(tmp_path, monkeypatch)
    try:
        paper_id = _upload(client, sample_pdf)
        note = client.post(
            f"/api/papers/{paper_id}/notes",
            json={
                "body": "Introduction 的补充说明",
                "page_number": 1,
                "request_id": str(uuid4()),
            },
        ).json()

        response = client.post(
            f"/api/papers/{paper_id}/agent/messages",
            json={
                "content": "Introduction 的作用是什么？",
                "model_profile_id": ENVIRONMENT_FALLBACK_PROFILE_ID,
                "request_id": str(uuid4()),
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["note_references"] == [
            {
                "note_id": note["id"],
                "note_type": "manual",
                "page_number": 1,
                "available": True,
            }
        ]

        history = client.get(
            f"/api/papers/{paper_id}/agent/conversations/{payload['conversation_id']}"
        ).json()
        assistant_message = history["messages"][-1]
        assert assistant_message["note_references"][0]["note_id"] == note["id"]

        deleted = client.delete(f"/api/papers/{paper_id}/notes/{note['id']}")
        assert deleted.status_code == 204

        history_after_delete = client.get(
            f"/api/papers/{paper_id}/agent/conversations/{payload['conversation_id']}"
        ).json()
        reference = history_after_delete["messages"][-1]["note_references"][0]
        assert reference["note_id"] == note["id"]
        assert reference["available"] is False
    finally:
        client.close()


def test_agent_rejects_cross_paper_selection_element(
    tmp_path: Path, sample_pdf: Path, monkeypatch
) -> None:
    client = _configured_client(tmp_path, monkeypatch)
    try:
        paper_id = _upload(client, sample_pdf)
        other = _upload(client, sample_pdf)

        response = client.post(
            f"/api/papers/{paper_id}/agent/messages",
            json={
                "content": "Explain it.",
                "model_profile_id": ENVIRONMENT_FALLBACK_PROFILE_ID,
                "request_id": str(uuid4()),
                "selection": {
                    "quote": "selected text",
                    "page_number": 1,
                    "rects": [
                        {"order": 0, "x0": 0.1, "y0": 0.2, "x1": 0.8, "y1": 0.25}
                    ],
                    "element_id": other,
                },
            },
        )

        assert response.status_code == 422
        assert response.json() == {"detail": "Selected text is invalid."}
        engine = client.app.state.paper_repository.engine
        with engine.connect() as connection:
            count = connection.execute(
                select(func.count(conversations.c.id)).where(
                    conversations.c.paper_id == paper_id
                )
            ).scalar_one()
        assert count == 0
    finally:
        client.close()
