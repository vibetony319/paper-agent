from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from paper_agent.models import VllmToolCall, VllmToolCallingError, VllmToolTurn
from paper_agent.services.agent_runtime import PaperAgentRuntime


class FakeAgentClient:
    """Deterministic in-process substitute for the external reasoning service."""

    def __init__(
        self,
        *,
        turns: tuple[VllmToolTurn | Exception, ...] = (),
        final_payload: dict[str, object] | Exception | None = None,
    ) -> None:
        self.remaining_turns = list(turns)
        self.final_payload = final_payload or {
            "status": "insufficient_evidence",
            "paper_answer": "unused raw model prose",
            "citation_element_ids": [],
            "background_explanation": None,
        }
        self.health_calls = 0
        self.health_error: Exception | None = None

    def request_tool_turn(
        self,
        *,
        messages: list[dict[str, object]],
        tools: tuple[dict[str, object], ...],
        tool_choice: str | dict[str, object],
    ) -> VllmToolTurn:
        turn = (
            self.remaining_turns.pop(0)
            if self.remaining_turns
            else VllmToolTurn(content=None, tool_calls=())
        )
        if isinstance(turn, Exception):
            raise turn
        return turn

    def generate_json_messages(
        self,
        *,
        messages: list[dict[str, object]],
        schema_name: str,
        schema: dict[str, object],
    ) -> dict[str, object]:
        if isinstance(self.final_payload, Exception):
            raise self.final_payload
        return deepcopy(self.final_payload)

    def validate_tool_calling(self) -> None:
        self.health_calls += 1
        if self.health_error is not None:
            raise self.health_error


@dataclass(frozen=True)
class UploadedPaper:
    id: str
    element_id: str


def _upload_pdf(client: TestClient, sample_pdf: Path, filename: str = "paper.pdf") -> UploadedPaper:
    response = client.post(
        "/api/papers",
        files={"file": (filename, sample_pdf.read_bytes(), "application/pdf")},
    )
    assert response.status_code == 201
    paper_id = response.json()["id"]
    document = client.get(f"/api/papers/{paper_id}/document")
    assert document.status_code == 200
    return UploadedPaper(id=paper_id, element_id=document.json()["elements"][0]["id"])


@pytest.fixture
def uploaded_paper(client: TestClient, sample_pdf: Path) -> UploadedPaper:
    return _upload_pdf(client, sample_pdf)


@pytest.fixture
def stage1_incomplete_paper(client: TestClient):
    """Create a durable incomplete record without relying on a failed upload."""
    return client.app.state.paper_repository.create_paper(
        original_filename="incomplete.pdf",
        stored_filename="internal-incomplete.pdf",
    )


def _grounded_tool_flow(paper: UploadedPaper) -> FakeAgentClient:
    return FakeAgentClient(
        turns=(
            VllmToolTurn(
                content="private tool planning",
                tool_calls=(
                    VllmToolCall(
                        id="call-1",
                        name="read_element",
                        arguments={"element_id": paper.element_id},
                    ),
                ),
            ),
            VllmToolTurn(content=None, tool_calls=()),
        ),
        final_payload={
            "status": "grounded",
            "paper_answer": "The method is introduced in the paper.",
            "citation_element_ids": [paper.element_id],
            "background_explanation": None,
        },
    )


def _configure_fake_agent_runtime(app, fake: FakeAgentClient) -> None:
    """Replace the app-state runtime only; never connect an external model in tests."""
    app.state.paper_agent_runtime = PaperAgentRuntime(
        repository=app.state.paper_repository,
        tools=app.state.paper_tool_registry,
        client=fake,
        guard=app.state.citation_guard,
    )


def test_agent_returns_locatable_same_paper_citations_without_paths(
    client: TestClient, uploaded_paper: UploadedPaper
) -> None:
    """Breaks if the route leaks storage details or returns unlocatable citations."""
    _configure_fake_agent_runtime(client.app, _grounded_tool_flow(uploaded_paper))

    response = client.post(
        f"/api/papers/{uploaded_paper.id}/agent/messages",
        json={"content": "Explain the method.", "mode": "paper_only"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "grounded"
    assert body["paper_answer"] == "The method is introduced in the paper."
    assert body["background_explanation"] is None
    assert body["citations"][0]["id"] == uploaded_paper.element_id
    assert body["citations"][0]["kind"] == "text_block"
    assert body["citations"][0]["page_number"] == 1
    assert set(body["citations"][0]["bbox"]) == {"x0", "y0", "x1", "y1"}
    assert str(client.app.state.settings.data_dir) not in response.text
    assert "stored_filename" not in response.text
    assert "private tool planning" not in response.text

    history = client.get(
        f"/api/papers/{uploaded_paper.id}/agent/conversations/{body['conversation_id']}"
    )

    assert history.status_code == 200
    assert history.json()["paper_id"] == uploaded_paper.id
    assert history.json()["mode"] == "paper_only"
    assert history.json()["messages"][-1]["id"] == body["message_id"]
    assert history.json()["messages"][-1]["citations"] == body["citations"]


def test_agent_maps_preflight_and_configuration_failures_without_chat_writes(
    client: TestClient, uploaded_paper: UploadedPaper, stage1_incomplete_paper
) -> None:
    """Breaks if a failed preflight is a 5xx or creates a conversation before validation."""
    assert client.post(
        f"/api/papers/{stage1_incomplete_paper.id}/agent/messages",
        json={"content": "Question", "mode": "paper_only"},
    ).status_code == 409
    assert client.post(
        f"/api/papers/{uploaded_paper.id}/agent/messages",
        json={"content": "Question", "mode": "paper_only"},
    ).status_code == 503
    assert client.app.state.paper_repository.get_conversation_messages(
        uploaded_paper.id, "missing-conversation"
    ) == ()


def test_agent_maps_resource_validation_and_model_failures_to_safe_boundaries(
    client: TestClient, uploaded_paper: UploadedPaper, sample_pdf: Path
) -> None:
    """Breaks if resource isolation, invalid fields, or provider failures escape the contract."""
    first_client = _grounded_tool_flow(uploaded_paper)
    _configure_fake_agent_runtime(client.app, first_client)
    created = client.post(
        f"/api/papers/{uploaded_paper.id}/agent/messages",
        json={"content": "Question", "mode": "paper_only"},
    )
    assert created.status_code == 200
    conversation_id = created.json()["conversation_id"]
    other_paper = _upload_pdf(client, sample_pdf, "other.pdf")

    assert client.post(
        "/api/papers/00000000-0000-0000-0000-000000000999/agent/messages",
        json={"content": "Question", "mode": "paper_only"},
    ).status_code == 404
    assert client.get(
        f"/api/papers/{uploaded_paper.id}/agent/conversations/00000000-0000-0000-0000-000000000999"
    ).status_code == 404
    assert client.get(
        f"/api/papers/{other_paper.id}/agent/conversations/{conversation_id}"
    ).status_code == 404
    assert client.post(
        f"/api/papers/{other_paper.id}/agent/messages",
        json={
            "content": "Question",
            "mode": "paper_only",
            "conversation_id": conversation_id,
        },
    ).status_code == 404
    assert client.post(
        f"/api/papers/{uploaded_paper.id}/agent/messages",
        json={
            "content": "Question",
            "mode": "external_knowledge",
            "conversation_id": conversation_id,
        },
    ).status_code == 409

    for payload in (
        {"content": "", "mode": "paper_only"},
        {"content": "Question", "mode": "unsupported"},
        {"content": "Question", "mode": "paper_only", "conversation_id": "not-a-uuid"},
        {"content": "Question", "mode": "paper_only", "unexpected": "field"},
    ):
        assert client.post(
            f"/api/papers/{uploaded_paper.id}/agent/messages", json=payload
        ).status_code == 422
    assert client.post(
        "/api/papers/not-a-uuid/agent/messages",
        json={"content": "Question", "mode": "paper_only"},
    ).status_code == 422

    _configure_fake_agent_runtime(
        client.app,
        FakeAgentClient(turns=(VllmToolCallingError("raw model endpoint secret"),)),
    )
    failure = client.post(
        f"/api/papers/{uploaded_paper.id}/agent/messages",
        json={"content": "Question", "mode": "paper_only"},
    )
    assert failure.status_code == 502
    assert "raw model endpoint secret" not in failure.text


def test_agent_keeps_citation_guard_fallback_as_a_successful_safe_answer(
    client: TestClient, uploaded_paper: UploadedPaper
) -> None:
    """Breaks if an unsupported final model claim becomes an error or escapes unchanged."""
    _configure_fake_agent_runtime(
        client.app,
        FakeAgentClient(
            final_payload={
                "status": "grounded",
                "paper_answer": "raw unsupported claim",
                "citation_element_ids": [uploaded_paper.element_id],
                "background_explanation": None,
            }
        ),
    )

    response = client.post(
        f"/api/papers/{uploaded_paper.id}/agent/messages",
        json={"content": "Question", "mode": "paper_only"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "insufficient_evidence"
    assert response.json()["citations"] == []
    assert "raw unsupported claim" not in response.text


def test_agent_health_is_explicit_and_maps_unavailable_or_failed_validation_to_503(
    client: TestClient,
) -> None:
    """Breaks if agent health validates during startup or leaks provider failure details."""
    assert client.post("/api/agent/health").status_code == 503

    healthy = FakeAgentClient()
    _configure_fake_agent_runtime(client.app, healthy)
    assert healthy.health_calls == 0
    assert client.post("/api/agent/health").json() == {"status": "ok"}
    assert healthy.health_calls == 1

    failing = FakeAgentClient()
    failing.health_error = VllmToolCallingError("raw health endpoint secret")
    _configure_fake_agent_runtime(client.app, failing)
    response = client.post("/api/agent/health")
    assert response.status_code == 503
    assert "raw health endpoint secret" not in response.text
