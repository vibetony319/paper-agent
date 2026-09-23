from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
import json
from pathlib import Path
from threading import Event
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import delete, event, func, select

from paper_agent.database import (
    conversation_message_citations,
    conversation_messages,
    conversations,
    document_elements,
    model_profiles as model_profile_rows,
)
from paper_agent.domain import (
    AgentMessageRole,
    BoundingBox,
    Conversation,
    ConversationMessage,
    DocumentElement,
)
from paper_agent.models import VllmToolCall, VllmToolCallingError, VllmToolTurn
from paper_agent.model_profiles import ModelCapabilities, ModelProfile, ModelProfileChanges
from paper_agent.routes import agent as agent_routes
from paper_agent.services.agent_runtime import PaperAgentRuntime
from paper_agent.services.agent_tools import PaperToolRegistry
from paper_agent.services.model_profiles import ModelProfileService
from paper_agent.services.reasoning_clients import ReasoningClientResolutionError


DEFAULT_PROFILE_ID = "10000000-0000-0000-0000-000000000001"
SECOND_PROFILE_ID = "10000000-0000-0000-0000-000000000002"


_UNUSED_ANSWER = "[[status:insufficient_evidence]]\n\nunused raw model prose"


class FakeAgentClient:
    """Deterministic in-process substitute for the external reasoning service."""

    def __init__(
        self,
        *,
        turns: tuple[VllmToolTurn | Exception, ...] = (),
        final_answer: str | Exception = _UNUSED_ANSWER,
        stream_chunks: tuple[str, ...] | None = None,
    ) -> None:
        self.remaining_turns = list(turns)
        self.final_answer = final_answer
        self.stream_chunks = stream_chunks
        self.health_calls = 0
        self.health_error: Exception | None = None
        self.tool_requests = 0
        self.final_requests = 0

    def request_tool_turn(
        self,
        *,
        messages: list[dict[str, object]],
        tools: tuple[dict[str, object], ...],
        tool_choice: str | dict[str, object],
    ) -> VllmToolTurn:
        self.tool_requests += 1
        turn = (
            self.remaining_turns.pop(0)
            if self.remaining_turns
            else VllmToolTurn(content=None, tool_calls=())
        )
        if isinstance(turn, Exception):
            raise turn
        return turn

    def complete_markdown_messages(self, *, messages: list[dict[str, object]]) -> str:
        self.final_requests += 1
        if isinstance(self.final_answer, Exception):
            raise self.final_answer
        return self.final_answer

    def stream_final_answer(self, *, messages: list[dict[str, object]]):
        self.final_requests += 1
        if isinstance(self.final_answer, Exception):
            raise self.final_answer
        chunks = (
            [self.final_answer] if self.stream_chunks is None else self.stream_chunks
        )
        yield from chunks

    def validate_tool_calling(self) -> None:
        self.health_calls += 1
        if self.health_error is not None:
            raise self.health_error


@dataclass(frozen=True)
class UploadedPaper:
    id: str
    element_id: str


@dataclass(frozen=True)
class FakeResolvedClients:
    profile: ModelProfile
    snapshot: object
    tools: FakeAgentClient


class FakeReasoningProvider:
    def __init__(self, resolved: dict[str, FakeResolvedClients]) -> None:
        self.resolved = resolved
        self.resolve_calls: list[str] = []

    def resolve(self, profile_id: str) -> FakeResolvedClients:
        self.resolve_calls.append(profile_id)
        try:
            return self.resolved[profile_id]
        except KeyError:
            raise ReasoningClientResolutionError("raw-provider-profile-detail") from None

    @staticmethod
    def is_read_only_profile(_profile_id: str) -> bool:
        return False


class ExplodingReasoningProvider:
    @staticmethod
    def resolve(_profile_id: str):
        raise RuntimeError("raw-provider-resolution-secret")


class FakeDefaultModelService:
    def __init__(self, resolved: FakeResolvedClients | None) -> None:
        self.resolved = resolved

    def resolve_default_clients(self) -> FakeResolvedClients | None:
        return self.resolved

    def usage_lease(self, _profile_id: str):
        from contextlib import nullcontext

        return nullcontext()


class BlockingReasoningProvider(FakeReasoningProvider):
    def __init__(self, resolved: dict[str, FakeResolvedClients]) -> None:
        super().__init__(resolved)
        self.resolve_entered = Event()
        self.release_resolve = Event()

    def resolve(self, profile_id: str) -> FakeResolvedClients:
        self.resolve_calls.append(profile_id)
        resolved = self.resolved[profile_id]
        self.resolve_entered.set()
        if not self.release_resolve.wait(timeout=5):
            raise RuntimeError("test resolve timeout")
        return resolved


class RepositoryAwareReasoningProvider:
    def __init__(self, repository, clients: dict[str, FakeAgentClient]) -> None:
        self.repository = repository
        self.clients = clients
        self.resolve_calls: list[str] = []

    def resolve(self, profile_id: str) -> FakeResolvedClients:
        self.resolve_calls.append(profile_id)
        profile = self.repository.get(profile_id)
        if (
            profile is None
            or profile.deleted_at is not None
            or not profile.enabled
            or profile_id not in self.clients
        ):
            raise ReasoningClientResolutionError("unavailable test profile")
        return FakeResolvedClients(
            profile=profile,
            snapshot=profile.snapshot(),
            tools=self.clients[profile_id],
        )

    @staticmethod
    def is_read_only_profile(_profile_id: str) -> bool:
        return False


def _agent_payload(
    content: str = "Question",
    *,
    conversation_id: str | None = None,
    model_profile_id: str = DEFAULT_PROFILE_ID,
    request_id: str | None = None,
) -> dict[str, str]:
    payload = {
        "content": content,
        "model_profile_id": model_profile_id,
        "request_id": request_id or str(uuid4()),
    }
    if conversation_id is not None:
        payload["conversation_id"] = conversation_id
    return payload


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


def test_lists_paper_conversations_and_restores_background(client: TestClient, sample_pdf: Path):
    paper = _upload_pdf(client, sample_pdf)
    repository = client.app.state.paper_repository
    conversation = repository.create_conversation(Conversation(paper_id=paper.id))
    repository.append_conversation_message(ConversationMessage(
        conversation_id=conversation.id, paper_id=paper.id,
        role=AgentMessageRole.user, content="方法是什么？",
    ))
    repository.append_conversation_message(ConversationMessage(
        conversation_id=conversation.id, paper_id=paper.id,
        role=AgentMessageRole.assistant, content="使用路由方法。",
        background_explanation="路由用于选择专家。",
    ))

    listing = client.get(f"/api/papers/{paper.id}/agent/conversations")
    assert listing.status_code == 200
    assert listing.json() == [{"id": conversation.id, "first_question": "方法是什么？"}]
    other_paper = _upload_pdf(client, sample_pdf, "other.pdf")
    assert client.get(f"/api/papers/{other_paper.id}/agent/conversations").json() == []
    detail = client.get(f"/api/papers/{paper.id}/agent/conversations/{conversation.id}")
    assert detail.status_code == 200
    assert detail.json()["messages"][1]["background_explanation"] == "路由用于选择专家。"


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
        final_answer=(
            f"The method is introduced in the paper. [[{paper.element_id}]]"
        ),
    )


def _model_profile(
    profile_id: str,
    *,
    display_name: str = "Agent model",
    model_name: str = "agent-model",
    revision: int = 1,
    enabled: bool = True,
    structured_output: bool = True,
    tool_calling: bool = True,
) -> ModelProfile:
    return ModelProfile(
        id=profile_id,
        display_name=display_name,
        base_url="http://127.0.0.1:8000/v1",
        model_name=model_name,
        revision=revision,
        enabled=enabled,
        capabilities=ModelCapabilities(
            basic_chat=True,
            structured_output=structured_output,
            tool_calling=tool_calling,
        ),
    )


def _configure_fake_agent_models(
    app, clients: dict[str, FakeAgentClient], *, profiles: dict[str, ModelProfile] | None = None
) -> FakeReasoningProvider:
    configured: dict[str, FakeResolvedClients] = {}
    for profile_id, fake in clients.items():
        profile = (
            _model_profile(profile_id)
            if profiles is None
            else profiles[profile_id]
        )
        configured[profile_id] = FakeResolvedClients(
            profile=profile,
            snapshot=profile.snapshot(),
            tools=fake,
        )
    provider = FakeReasoningProvider(configured)
    app.state.reasoning_client_provider = provider
    app.state.model_profile_service = FakeDefaultModelService(
        next(iter(configured.values()), None)
    )
    # Substring-only tools keep these tests deterministic: the semantic
    # service would otherwise load a real embedding model on search_paper.
    app.state.paper_tool_registry = PaperToolRegistry(app.state.paper_repository)
    app.state.paper_agent_runtime = PaperAgentRuntime(
        repository=app.state.paper_repository,
        tools=app.state.paper_tool_registry,
        guard=app.state.citation_guard,
    )
    return provider


def _configure_fake_agent_runtime(app, fake: FakeAgentClient) -> FakeReasoningProvider:
    """Resolve one deterministic tools client without any external model connection."""
    return _configure_fake_agent_models(app, {DEFAULT_PROFILE_ID: fake})


def _chat_row_counts(repository) -> tuple[int, int]:
    with repository.engine.connect() as connection:
        return (
            connection.execute(
                select(func.count()).select_from(conversations)
            ).scalar_one(),
            connection.execute(
                select(func.count()).select_from(conversation_messages)
            ).scalar_one(),
        )


def test_agent_returns_locatable_same_paper_citations_without_paths(
    client: TestClient, uploaded_paper: UploadedPaper
) -> None:
    """Breaks if the route leaks storage details or returns unlocatable citations."""
    _configure_fake_agent_runtime(client.app, _grounded_tool_flow(uploaded_paper))

    statements: list[tuple[str, tuple[object, ...]]] = []

    def capture_statement(
        connection, cursor, statement, parameters, context, executemany
    ) -> None:
        statements.append((statement, tuple(parameters)))

    repository = client.app.state.paper_repository
    event.listen(repository.engine, "before_cursor_execute", capture_statement)
    try:
        response = client.post(
            f"/api/papers/{uploaded_paper.id}/agent/messages",
            json=_agent_payload("Explain the method."),
        )
    finally:
        event.remove(repository.engine, "before_cursor_execute", capture_statement)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "grounded"
    assert body["paper_answer"] == (
        f"The method is introduced in the paper. [[{uploaded_paper.element_id}]]"
    )
    assert body["background_explanation"] is None
    assert body["citations"][0]["id"] == uploaded_paper.element_id
    assert body["citations"][0]["kind"] == "text_block"
    assert body["citations"][0]["page_number"] == 1
    assert set(body["citations"][0]["bbox"]) == {"x0", "y0", "x1", "y1"}
    assert str(client.app.state.settings.data_dir) not in response.text
    assert "stored_filename" not in response.text
    assert "private tool planning" not in response.text
    citation_element_reads = [
        (statement, parameters)
        for statement, parameters in statements
        if "FROM document_elements" in statement
        and "document_elements.text" in statement
        and "document_elements.location_status" in statement
        and "document_elements.id IN" in statement
    ]
    assert citation_element_reads == []

    history = client.get(
        f"/api/papers/{uploaded_paper.id}/agent/conversations/{body['conversation_id']}"
    )

    assert history.status_code == 200
    assert history.json()["paper_id"] == uploaded_paper.id
    assert history.json()["messages"][-1]["id"] == body["message_id"]
    assert history.json()["messages"][-1]["citations"] == body["citations"]


def test_same_conversation_can_continue_with_a_different_model(
    client: TestClient, uploaded_paper: UploadedPaper
) -> None:
    """Breaks if a selected model is fixed to a conversation instead of a request turn."""
    first_profile = _model_profile(DEFAULT_PROFILE_ID)
    second_profile = _model_profile(
        SECOND_PROFILE_ID,
        display_name="Second model",
        model_name="second-model",
    )
    _configure_fake_agent_models(
        client.app,
        {
            DEFAULT_PROFILE_ID: _grounded_tool_flow(uploaded_paper),
            SECOND_PROFILE_ID: FakeAgentClient(),
        },
        profiles={
            DEFAULT_PROFILE_ID: first_profile,
            SECOND_PROFILE_ID: second_profile,
        },
    )

    first = client.post(
        f"/api/papers/{uploaded_paper.id}/agent/messages",
        json=_agent_payload("What is the method?"),
    ).json()
    second = client.post(
        f"/api/papers/{uploaded_paper.id}/agent/messages",
        json=_agent_payload(
            "Continue.",
            conversation_id=first["conversation_id"],
            model_profile_id=SECOND_PROFILE_ID,
        ),
    ).json()

    assert second["conversation_id"] == first["conversation_id"]
    assert first["model"]["profile_id"] == DEFAULT_PROFILE_ID
    assert second["model"]["profile_id"] == SECOND_PROFILE_ID
    history = client.get(
        f"/api/papers/{uploaded_paper.id}/agent/conversations/{first['conversation_id']}"
    ).json()
    assert [message["model"]["profile_id"] for message in history["messages"]] == [
        DEFAULT_PROFILE_ID,
        DEFAULT_PROFILE_ID,
        SECOND_PROFILE_ID,
        SECOND_PROFILE_ID,
    ]


def test_complete_request_duplicate_replays_stored_citations_before_provider_resolution(
    client: TestClient, uploaded_paper: UploadedPaper
) -> None:
    """Breaks if a complete duplicate resolves or calls a model instead of replaying storage."""
    fake = _grounded_tool_flow(uploaded_paper)
    provider = _configure_fake_agent_runtime(client.app, fake)
    request_id = "20000000-0000-0000-0000-000000000011"
    first = client.post(
        f"/api/papers/{uploaded_paper.id}/agent/messages",
        json=_agent_payload("Original", request_id=request_id),
    )
    assert first.status_code == 200
    provider.resolved.clear()

    replay = client.post(
        f"/api/papers/{uploaded_paper.id}/agent/messages",
        json=_agent_payload(
            "Conflicting duplicate",
            model_profile_id=SECOND_PROFILE_ID,
            request_id=request_id,
        ),
    )

    assert replay.status_code == 200
    assert replay.json() == first.json()
    assert provider.resolve_calls == [DEFAULT_PROFILE_ID]
    assert fake.tool_requests == 2
    assert fake.final_requests == 1


def test_complete_duplicate_replays_background_citation_order_and_original_geometry(
    client: TestClient, uploaded_paper: UploadedPaper
) -> None:
    """Breaks if a complete replay reconstructs any response field from mutable rows."""
    repository = client.app.state.paper_repository
    second = repository.save_element(
        uploaded_paper.id,
        DocumentElement.paragraph(
            "Second exact replay source",
            page_number=1,
            bbox=BoundingBox(0.11, 0.22, 0.77, 0.33),
        ),
    )
    fake = FakeAgentClient(
        turns=(
            VllmToolTurn(
                content=None,
                tool_calls=(
                    VllmToolCall(
                        id="call-first",
                        name="read_element",
                        arguments={"element_id": uploaded_paper.element_id},
                    ),
                ),
            ),
            VllmToolTurn(
                content=None,
                tool_calls=(
                    VllmToolCall(
                        id="call-second",
                        name="read_element",
                        arguments={"element_id": second.id},
                    ),
                ),
            ),
            VllmToolTurn(content=None, tool_calls=()),
        ),
        final_answer=(
            f"Ordered answer. [[{second.id}]] [[{uploaded_paper.element_id}]]"
        ),
    )
    provider = _configure_fake_agent_runtime(client.app, fake)
    request_id = "20000000-0000-0000-0000-000000000030"
    payload = _agent_payload(
        "Explain with context.",
        request_id=request_id,
    )

    first = client.post(
        f"/api/papers/{uploaded_paper.id}/agent/messages", json=payload
    )
    assert first.status_code == 200
    with repository.engine.begin() as connection:
        connection.execute(
            document_elements.update()
            .where(document_elements.c.id == second.id)
            .values(kind="changed", bbox_x0=0.0, bbox_y0=0.0, bbox_x1=0.1, bbox_y1=0.1)
        )
    provider.resolved.clear()

    replay = client.post(
        f"/api/papers/{uploaded_paper.id}/agent/messages",
        json={**payload, "content": "Conflicting duplicate"},
    )

    assert replay.status_code == 200
    assert replay.json() == first.json()
    assert first.json()["background_explanation"] is None
    assert [citation["id"] for citation in first.json()["citations"]] == [
        second.id,
        uploaded_paper.element_id,
    ]
    assert first.json()["citations"][0]["kind"] == second.kind
    assert first.json()["citations"][0]["bbox"] == {
        "x0": 0.11,
        "y0": 0.22,
        "x1": 0.77,
        "y1": 0.33,
    }
    assert provider.resolve_calls == [DEFAULT_PROFILE_ID]
    assert fake.final_requests == 1


def test_complete_replay_falls_back_safely_for_legacy_or_corrupt_citation_snapshot(
    client: TestClient, uploaded_paper: UploadedPaper
) -> None:
    """Breaks if nullable legacy data fails or corrupt snapshot JSON reaches the response."""
    fake = _grounded_tool_flow(uploaded_paper)
    provider = _configure_fake_agent_runtime(client.app, fake)
    request_id = "20000000-0000-0000-0000-000000000032"
    payload = _agent_payload("Legacy replay", request_id=request_id)
    first = client.post(
        f"/api/papers/{uploaded_paper.id}/agent/messages", json=payload
    )
    assert first.status_code == 200
    provider.resolved.clear()
    repository = client.app.state.paper_repository
    with repository.engine.begin() as connection:
        connection.execute(
            conversation_message_citations.update()
            .where(
                conversation_message_citations.c.message_id
                == first.json()["message_id"]
            )
            .values(ordinal=None, citation_snapshot_json=None)
        )

    legacy = client.post(
        f"/api/papers/{uploaded_paper.id}/agent/messages", json=payload
    )
    assert legacy.status_code == 200
    assert legacy.json() == first.json()

    corrupt_payload = json.dumps(
        {
            "id": uploaded_paper.element_id,
            "kind": first.json()["citations"][0]["kind"],
            "page_number": first.json()["citations"][0]["page_number"],
            "bbox": first.json()["citations"][0]["bbox"],
            "api_key": "must-not-escape",
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    with repository.engine.begin() as connection:
        connection.execute(
            conversation_message_citations.update()
            .where(
                conversation_message_citations.c.message_id
                == first.json()["message_id"]
            )
            .values(citation_snapshot_json=corrupt_payload)
        )

    corrupt = client.post(
        f"/api/papers/{uploaded_paper.id}/agent/messages", json=payload
    )
    assert corrupt.status_code == 200
    assert corrupt.json() == first.json()
    assert "must-not-escape" not in corrupt.text
    assert provider.resolve_calls == [DEFAULT_PROFILE_ID]


def test_complete_replay_and_history_hide_valid_but_mismatched_pair_provenance(
    client: TestClient, uploaded_paper: UploadedPaper
) -> None:
    """Breaks if a valid foreign snapshot creates false provenance on a stored pair."""
    provider = _configure_fake_agent_runtime(client.app, FakeAgentClient())
    request_id = "20000000-0000-0000-0000-000000000031"
    first = client.post(
        f"/api/papers/{uploaded_paper.id}/agent/messages",
        json=_agent_payload("Question", request_id=request_id),
    )
    assert first.status_code == 200
    conversation_id = first.json()["conversation_id"]
    other_profile = _model_profile(
        SECOND_PROFILE_ID,
        display_name="Other",
        model_name="other-model",
    )
    other_snapshot_json = json.dumps(
        {
            "profile_id": other_profile.id,
            "display_name": other_profile.display_name,
            "base_url": other_profile.base_url,
            "model_name": other_profile.model_name,
            "revision": other_profile.revision,
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    with client.app.state.paper_repository.engine.begin() as connection:
        connection.execute(
            conversation_messages.update()
            .where(conversation_messages.c.request_id == request_id)
            .where(conversation_messages.c.role == AgentMessageRole.assistant.value)
            .values(
                model_profile_id=SECOND_PROFILE_ID,
                model_snapshot_json=other_snapshot_json,
            )
        )
    provider.resolved.clear()

    replay = client.post(
        f"/api/papers/{uploaded_paper.id}/agent/messages",
        json=_agent_payload("Duplicate", request_id=request_id),
    )
    history = client.get(
        f"/api/papers/{uploaded_paper.id}/agent/conversations/{conversation_id}"
    )

    assert replay.status_code == 200
    assert replay.json()["model"] is None
    assert history.status_code == 200
    assert [message["model"] for message in history.json()["messages"]] == [
        None,
        None,
    ]
    assert provider.resolve_calls == [DEFAULT_PROFILE_ID]


def test_agent_runs_without_saved_capability_results(
    client: TestClient, uploaded_paper: UploadedPaper
) -> None:
    """An untested profile is attempted directly, without a capability gate."""
    profile = _model_profile(DEFAULT_PROFILE_ID, tool_calling=False)
    _configure_fake_agent_models(
        client.app,
        {DEFAULT_PROFILE_ID: _grounded_tool_flow(uploaded_paper)},
        profiles={DEFAULT_PROFILE_ID: profile},
    )

    response = client.post(
        f"/api/papers/{uploaded_paper.id}/agent/messages",
        json=_agent_payload(),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "grounded"


def test_agent_accepts_a_model_without_structured_output(
    client: TestClient, uploaded_paper: UploadedPaper
) -> None:
    """The Markdown answer needs tool calling, not a structured response format."""
    profile = _model_profile(DEFAULT_PROFILE_ID, structured_output=False)
    _configure_fake_agent_models(
        client.app,
        {DEFAULT_PROFILE_ID: _grounded_tool_flow(uploaded_paper)},
        profiles={DEFAULT_PROFILE_ID: profile},
    )

    response = client.post(
        f"/api/papers/{uploaded_paper.id}/agent/messages",
        json=_agent_payload("Explain the method."),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "grounded"
    assert response.json()["citations"]


def test_agent_profile_lease_blocks_delete_while_allowing_edit_after_snapshot(
    client: TestClient, uploaded_paper: UploadedPaper
) -> None:
    """Breaks if request start can race deletion or monopolizes profile edits."""
    profile = client.app.state.model_profile_repository.create(
        _model_profile(DEFAULT_PROFILE_ID)
    )
    fake = FakeAgentClient()
    resolved = FakeResolvedClients(
        profile=profile,
        snapshot=profile.snapshot(),
        tools=fake,
    )
    provider = BlockingReasoningProvider({profile.id: resolved})
    client.app.state.reasoning_client_provider = provider
    client.app.state.model_profile_service = ModelProfileService(
        client.app.state.model_profile_repository,
        provider,
        client.app.state.model_secret_store,
    )
    client.app.state.paper_agent_runtime = PaperAgentRuntime(
        repository=client.app.state.paper_repository,
        tools=client.app.state.paper_tool_registry,
        guard=client.app.state.citation_guard,
    )

    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(
            client.post,
            f"/api/papers/{uploaded_paper.id}/agent/messages",
            json=_agent_payload("Frozen request"),
        )
        assert provider.resolve_entered.wait(timeout=5)
        blocked_delete = client.delete(
            f"/api/model-profiles/{profile.id}",
            headers={"If-Match": str(profile.revision)},
        )
        edited = client.patch(
            f"/api/model-profiles/{profile.id}",
            headers={"If-Match": str(profile.revision)},
            json={"display_name": "Edited after snapshot"},
        )
        provider.release_resolve.set()
        response = pending.result(timeout=5)

    assert blocked_delete.status_code == 409
    assert blocked_delete.json()["code"] == "profile_in_use"
    assert edited.status_code == 200
    assert response.status_code == 200
    assert response.json()["model"]["display_name"] == profile.display_name
    assert response.json()["model"]["revision"] == profile.revision
    assert client.delete(
        f"/api/model-profiles/{profile.id}",
        headers={"If-Match": str(edited.json()["revision"])},
    ).status_code == 204


def test_agent_rejects_missing_disabled_and_deleted_profiles_before_chat_write(
    client: TestClient, uploaded_paper: UploadedPaper
) -> None:
    """Breaks if unusable selected profiles create durable conversation state."""
    repository = client.app.state.model_profile_repository
    disabled_id = "10000000-0000-0000-0000-000000000003"
    deleted_id = "10000000-0000-0000-0000-000000000004"
    repository.create(_model_profile(disabled_id, enabled=False))
    repository.create(_model_profile(deleted_id))
    repository.soft_delete(deleted_id, expected_revision=1)
    before = _chat_row_counts(client.app.state.paper_repository)

    statuses = [
        client.post(
            f"/api/papers/{uploaded_paper.id}/agent/messages",
            json=_agent_payload(model_profile_id=profile_id),
        ).status_code
        for profile_id in (
            "10000000-0000-0000-0000-000000000099",
            disabled_id,
            deleted_id,
        )
    ]

    assert statuses == [503, 503, 503]
    assert _chat_row_counts(client.app.state.paper_repository) == before


def test_agent_sanitizes_unexpected_provider_resolution_failure_before_chat_write(
    client: TestClient, uploaded_paper: UploadedPaper
) -> None:
    """Breaks if an unexpected provider exception or its chain crosses the HTTP boundary."""
    client.app.state.reasoning_client_provider = ExplodingReasoningProvider()
    safe_client = TestClient(client.app, raise_server_exceptions=False)
    before = _chat_row_counts(client.app.state.paper_repository)

    response = safe_client.post(
        f"/api/papers/{uploaded_paper.id}/agent/messages",
        json=_agent_payload(),
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "Reasoning model is not configured."}
    assert "raw-provider-resolution-secret" not in response.text
    assert _chat_row_counts(client.app.state.paper_repository) == before


def test_partial_retry_requires_the_original_unchanged_model_snapshot(
    client: TestClient, uploaded_paper: UploadedPaper
) -> None:
    """Breaks if a user-only retry silently switches to a revised profile snapshot."""
    request_id = "20000000-0000-0000-0000-000000000012"
    first_profile = _model_profile(DEFAULT_PROFILE_ID)
    _configure_fake_agent_models(
        client.app,
        {
            DEFAULT_PROFILE_ID: FakeAgentClient(
                turns=(VllmToolCallingError("raw-model-secret"),)
            )
        },
        profiles={DEFAULT_PROFILE_ID: first_profile},
    )
    payload = _agent_payload("Retry", request_id=request_id)
    failed = client.post(
        f"/api/papers/{uploaded_paper.id}/agent/messages", json=payload
    )
    assert failed.status_code == 502

    retry_client = FakeAgentClient()
    revised_profile = _model_profile(DEFAULT_PROFILE_ID, revision=2)
    _configure_fake_agent_models(
        client.app,
        {DEFAULT_PROFILE_ID: retry_client},
        profiles={DEFAULT_PROFILE_ID: revised_profile},
    )
    conflict = client.post(
        f"/api/papers/{uploaded_paper.id}/agent/messages", json=payload
    )

    assert conflict.status_code == 409
    assert "new request_id" in conflict.json()["detail"]
    assert retry_client.tool_requests == 0
    assert retry_client.final_requests == 0
    user = client.app.state.paper_repository.get_agent_user_message_by_request(
        uploaded_paper.id, request_id
    )
    assert user is not None


@pytest.mark.parametrize(
    "profile_state",
    ("missing", "deleted", "disabled", "unresolvable"),
)
def test_partial_retry_maps_unusable_original_profile_to_stable_conflict(
    client: TestClient,
    uploaded_paper: UploadedPaper,
    profile_state: str,
) -> None:
    """Breaks if durable partial recovery becomes a generic model 503 or adds a user."""
    profile_repository = client.app.state.model_profile_repository
    profile = profile_repository.create(_model_profile(DEFAULT_PROFILE_ID))
    failing = FakeAgentClient(
        turns=(VllmToolCallingError("first attempt failed"),)
    )
    provider = RepositoryAwareReasoningProvider(
        profile_repository,
        {profile.id: failing},
    )
    service = ModelProfileService(
        profile_repository,
        provider,
        client.app.state.model_secret_store,
    )
    client.app.state.reasoning_client_provider = provider
    client.app.state.model_profile_service = service
    client.app.state.paper_agent_runtime = PaperAgentRuntime(
        repository=client.app.state.paper_repository,
        tools=client.app.state.paper_tool_registry,
        guard=client.app.state.citation_guard,
    )
    request_id = str(uuid4())
    payload = _agent_payload("Retry durable partial", request_id=request_id)
    failed = client.post(
        f"/api/papers/{uploaded_paper.id}/agent/messages", json=payload
    )
    assert failed.status_code == 502
    original_user = client.app.state.paper_repository.get_agent_user_message_by_request(
        uploaded_paper.id, request_id
    )
    assert original_user is not None

    retry_client = FakeAgentClient()
    provider.clients[profile.id] = retry_client
    if profile_state == "missing":
        with profile_repository.engine.begin() as connection:
            connection.execute(
                delete(model_profile_rows).where(model_profile_rows.c.id == profile.id)
            )
    elif profile_state == "deleted":
        service.delete_profile(profile.id, expected_revision=profile.revision)
    elif profile_state == "disabled":
        service.update_profile(
            profile.id,
            expected_revision=profile.revision,
            changes=ModelProfileChanges(enabled=False),
        )
    else:
        provider.clients.clear()

    retry = client.post(
        f"/api/papers/{uploaded_paper.id}/agent/messages", json=payload
    )

    assert retry.status_code == 409
    assert retry.json() == {
        "detail": "Request state conflicts with its stored retry. Use a new request_id."
    }
    assert retry_client.tool_requests == 0
    assert retry_client.final_requests == 0
    stored_user = client.app.state.paper_repository.get_agent_user_message_by_request(
        uploaded_paper.id, request_id
    )
    assert stored_user == original_user
    assert _chat_row_counts(client.app.state.paper_repository)[1] == 1


@pytest.mark.parametrize(
    "conflict",
    ("content", "conversation", "model"),
)
def test_partial_retry_rejects_rebinding_original_request_state(
    client: TestClient,
    uploaded_paper: UploadedPaper,
    conflict: str,
) -> None:
    """Breaks if user-only recovery rebinds content, conversation, or model."""
    request_id = "20000000-0000-0000-0000-000000000013"
    _configure_fake_agent_runtime(
        client.app,
        FakeAgentClient(turns=(VllmToolCallingError("first attempt failed"),)),
    )
    original = _agent_payload("Original", request_id=request_id)
    assert client.post(
        f"/api/papers/{uploaded_paper.id}/agent/messages", json=original
    ).status_code == 502
    other_conversation = client.app.state.paper_repository.create_conversation(
        Conversation(paper_id=uploaded_paper.id)
    )
    retry_client = FakeAgentClient()
    _configure_fake_agent_models(
        client.app,
        {
            DEFAULT_PROFILE_ID: retry_client,
            SECOND_PROFILE_ID: FakeAgentClient(),
        },
    )
    retry = dict(original)
    if conflict == "content":
        retry["content"] = "Changed"
    elif conflict == "conversation":
        retry["conversation_id"] = other_conversation.id
    else:
        retry["model_profile_id"] = SECOND_PROFILE_ID

    response = client.post(
        f"/api/papers/{uploaded_paper.id}/agent/messages", json=retry
    )

    assert response.status_code == 409
    assert "new request_id" in response.json()["detail"]
    assert retry_client.tool_requests == 0
    assert retry_client.final_requests == 0


def test_conversation_history_exposes_legacy_model_as_null(
    client: TestClient, uploaded_paper: UploadedPaper
) -> None:
    """Breaks if pre-provenance rows become unreadable through the conversation API."""
    repository = client.app.state.paper_repository
    conversation = repository.create_conversation(
        Conversation(paper_id=uploaded_paper.id)
    )
    repository.append_conversation_message(
        ConversationMessage(
            conversation_id=conversation.id,
            paper_id=uploaded_paper.id,
            role=AgentMessageRole.user,
            content="Legacy question",
        )
    )

    history = client.get(
        f"/api/papers/{uploaded_paper.id}/agent/conversations/{conversation.id}"
    )

    assert history.status_code == 200
    assert history.json()["messages"][0]["model"] is None


def test_agent_maps_preflight_and_configuration_failures_without_chat_writes(
    client: TestClient, uploaded_paper: UploadedPaper, stage1_incomplete_paper
) -> None:
    """Breaks if a failed preflight is a 5xx or creates a conversation before validation."""
    _configure_fake_agent_runtime(client.app, FakeAgentClient())
    before = _chat_row_counts(client.app.state.paper_repository)

    assert client.post(
        f"/api/papers/{stage1_incomplete_paper.id}/agent/messages",
        json=_agent_payload(),
    ).status_code == 409
    assert _chat_row_counts(client.app.state.paper_repository) == before

    client.app.state.reasoning_client_provider = FakeReasoningProvider({})
    assert client.post(
        f"/api/papers/{uploaded_paper.id}/agent/messages",
        json=_agent_payload(),
    ).status_code == 503
    assert _chat_row_counts(client.app.state.paper_repository) == before


def test_conversation_get_batches_unique_cited_elements_once(
    client: TestClient, uploaded_paper: UploadedPaper
) -> None:
    """Breaks if each returned message materializes the paper's full element set."""
    repository = client.app.state.paper_repository
    conversation = repository.create_conversation(
        Conversation(paper_id=uploaded_paper.id)
    )
    messages = (
        ConversationMessage(
            conversation_id=conversation.id,
            paper_id=uploaded_paper.id,
            role=AgentMessageRole.user,
            content="First question",
        ),
        ConversationMessage(
            conversation_id=conversation.id,
            paper_id=uploaded_paper.id,
            role=AgentMessageRole.assistant,
            content="First answer",
            citation_element_ids=(uploaded_paper.element_id,),
        ),
        ConversationMessage(
            conversation_id=conversation.id,
            paper_id=uploaded_paper.id,
            role=AgentMessageRole.user,
            content="Second question",
        ),
        ConversationMessage(
            conversation_id=conversation.id,
            paper_id=uploaded_paper.id,
            role=AgentMessageRole.assistant,
            content="Second answer",
            citation_element_ids=(uploaded_paper.element_id,),
        ),
    )
    for message in messages:
        repository.append_conversation_message(message)
    with repository.engine.begin() as connection:
        connection.execute(
            conversation_message_citations.update().values(
                ordinal=None,
                citation_snapshot_json=None,
            )
        )

    statements: list[tuple[str, tuple[object, ...]]] = []

    def capture_statement(
        connection, cursor, statement, parameters, context, executemany
    ) -> None:
        statements.append((statement, tuple(parameters)))

    event.listen(repository.engine, "before_cursor_execute", capture_statement)
    try:
        response = client.get(
            f"/api/papers/{uploaded_paper.id}/agent/conversations/{conversation.id}"
        )
    finally:
        event.remove(repository.engine, "before_cursor_execute", capture_statement)

    assert response.status_code == 200
    assert [
        [citation["id"] for citation in message["citations"]]
        for message in response.json()["messages"]
    ] == [
        [],
        [uploaded_paper.element_id],
        [],
        [uploaded_paper.element_id],
    ]
    element_reads = [
        (statement, parameters)
        for statement, parameters in statements
        if "FROM document_elements" in statement
    ]
    assert len(element_reads) == 1
    statement, parameters = element_reads[0]
    assert "document_elements.location_status" in statement
    assert "document_elements.id IN" in statement
    assert parameters.count(uploaded_paper.element_id) == 1


def test_conversation_get_does_not_lookup_elements_for_user_only_messages(
    client: TestClient, uploaded_paper: UploadedPaper
) -> None:
    """Breaks if citation DTO construction performs element reads for user messages."""
    repository = client.app.state.paper_repository
    conversation = repository.create_conversation(
        Conversation(paper_id=uploaded_paper.id)
    )
    for content in ("First question", "Second question"):
        repository.append_conversation_message(
            ConversationMessage(
                conversation_id=conversation.id,
                paper_id=uploaded_paper.id,
                role=AgentMessageRole.user,
                content=content,
            )
        )

    statements: list[str] = []

    def capture_statement(
        connection, cursor, statement, parameters, context, executemany
    ) -> None:
        statements.append(statement)

    event.listen(repository.engine, "before_cursor_execute", capture_statement)
    try:
        response = client.get(
            f"/api/papers/{uploaded_paper.id}/agent/conversations/{conversation.id}"
        )
    finally:
        event.remove(repository.engine, "before_cursor_execute", capture_statement)

    assert response.status_code == 200
    assert [message["citations"] for message in response.json()["messages"]] == [
        [],
        [],
    ]
    assert not [
        statement for statement in statements if "FROM document_elements" in statement
    ]


def test_agent_message_response_openapi_constrains_status_values(
    client: TestClient,
) -> None:
    """Breaks if the public response contract permits arbitrary status strings."""
    status_schema = client.get("/openapi.json").json()["components"]["schemas"][
        "AgentMessageResponse"
    ]["properties"]["status"]

    assert status_schema["enum"] == ["grounded", "insufficient_evidence"]


def test_agent_maps_resource_validation_and_model_failures_to_safe_boundaries(
    client: TestClient, uploaded_paper: UploadedPaper, sample_pdf: Path
) -> None:
    """Breaks if resource isolation, invalid fields, or provider failures escape the contract."""
    first_client = _grounded_tool_flow(uploaded_paper)
    _configure_fake_agent_runtime(client.app, first_client)
    created = client.post(
        f"/api/papers/{uploaded_paper.id}/agent/messages",
        json=_agent_payload(),
    )
    assert created.status_code == 200
    conversation_id = created.json()["conversation_id"]
    other_paper = _upload_pdf(client, sample_pdf, "other.pdf")

    assert client.post(
        "/api/papers/00000000-0000-0000-0000-000000000999/agent/messages",
        json=_agent_payload(),
    ).status_code == 404
    assert client.get(
        f"/api/papers/{uploaded_paper.id}/agent/conversations/00000000-0000-0000-0000-000000000999"
    ).status_code == 404
    assert client.get(
        f"/api/papers/{other_paper.id}/agent/conversations/{conversation_id}"
    ).status_code == 404
    assert client.post(
        f"/api/papers/{other_paper.id}/agent/messages",
        json=_agent_payload(conversation_id=conversation_id),
    ).status_code == 404

    for payload in (
        _agent_payload(""),
        _agent_payload(conversation_id="not-a-uuid"),
        {**_agent_payload(), "unexpected": "field"},
        {**_agent_payload(), "mode": "paper_only"},
    ):
        assert client.post(
            f"/api/papers/{uploaded_paper.id}/agent/messages", json=payload
        ).status_code == 422
    assert client.post(
        "/api/papers/not-a-uuid/agent/messages",
        json=_agent_payload(),
    ).status_code == 422

    _configure_fake_agent_runtime(
        client.app,
        FakeAgentClient(turns=(VllmToolCallingError("raw model endpoint secret"),)),
    )
    failure = client.post(
        f"/api/papers/{uploaded_paper.id}/agent/messages",
        json=_agent_payload(),
    )
    assert failure.status_code == 502
    assert "raw model endpoint secret" not in failure.text


def test_agent_returns_model_answer_without_citation_guard_replacement(
    client: TestClient, uploaded_paper: UploadedPaper
) -> None:
    """A model answer is returned even when no tool evidence was collected."""
    _configure_fake_agent_runtime(
        client.app,
        FakeAgentClient(
            final_answer=(
                f"raw unsupported claim [[{uploaded_paper.element_id}]]"
            )
        ),
    )

    response = client.post(
        f"/api/papers/{uploaded_paper.id}/agent/messages",
        json=_agent_payload(),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "grounded"
    assert len(response.json()["citations"]) == 1
    assert "raw unsupported claim" in response.text


def test_agent_health_is_explicit_and_maps_unavailable_or_failed_validation_to_503(
    client: TestClient,
) -> None:
    """Breaks if agent health validates during startup or leaks provider failure details."""
    unavailable = client.post("/api/agent/health")
    assert unavailable.status_code == 503
    assert unavailable.json() == {
        "detail": "Reasoning model tool calling is unavailable."
    }

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
    assert response.json() == {
        "detail": "Reasoning model tool calling is unavailable."
    }
    assert "raw health endpoint secret" not in response.text


def _stream_events(response) -> list[tuple[str, dict[str, object]]]:
    """Parse an SSE body into (event, payload) pairs."""
    events: list[tuple[str, dict[str, object]]] = []
    for frame in response.text.split("\n\n"):
        if not frame.strip():
            continue
        name = None
        data = None
        for line in frame.splitlines():
            if line.startswith("event:"):
                name = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data = line[len("data:"):].strip()
        assert name is not None and data is not None, frame
        events.append((name, json.loads(data)))
    return events


def test_agent_stream_emits_deltas_then_the_persisted_message(
    client: TestClient, uploaded_paper: UploadedPaper
) -> None:
    """Breaks if a streamed answer is not durable, duplicated, or misordered."""
    fake = _grounded_tool_flow(uploaded_paper)
    fake.stream_chunks = None
    _configure_fake_agent_runtime(client.app, fake)
    request_id = "20000000-0000-0000-0000-000000000040"

    response = client.post(
        f"/api/papers/{uploaded_paper.id}/agent/messages/stream",
        json=_agent_payload("Explain the method.", request_id=request_id),
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _stream_events(response)
    names = [name for name, _ in events]
    assert [name for name in names if name != "step"] == [
        "started",
        "delta",
        "completed",
    ]
    assert events[0][1]["request_id"] == request_id
    streamed = events[names.index("delta")][1]["text"]
    message = events[names.index("completed")][1]["message"]
    assert message["paper_answer"] == streamed
    assert message["paper_answer"].endswith(f"[[{uploaded_paper.element_id}]]")
    assert message["status"] == "grounded"
    assert message["citations"][0]["id"] == uploaded_paper.element_id

    history = client.get(
        f"/api/papers/{uploaded_paper.id}/agent/conversations/{message['conversation_id']}"
    ).json()
    assert [item["role"] for item in history["messages"]] == ["user", "assistant"]
    assert history["messages"][1]["content"] == message["paper_answer"]
    assert fake.final_requests == 1


def test_agent_stream_emits_execution_steps(
    client: TestClient, uploaded_paper: UploadedPaper
) -> None:
    """Breaks if the execution path is missing from the event stream."""
    fake = _grounded_tool_flow(uploaded_paper)
    fake.stream_chunks = None
    _configure_fake_agent_runtime(client.app, fake)

    response = client.post(
        f"/api/papers/{uploaded_paper.id}/agent/messages/stream",
        json=_agent_payload(
            "Explain the method.",
            request_id="20000000-0000-0000-0000-000000000042",
        ),
    )

    assert response.status_code == 200
    steps = [data for name, data in _stream_events(response) if name == "step"]
    assert [step["kind"] for step in steps] == [
        "notes",
        "round",
        "reasoning",
        "tool_call",
        "tool_result",
        "round",
        "final_answer",
    ]
    assert steps[0]["count"] == 0
    assert steps[2]["text"] == "private tool planning"
    assert steps[3]["tool_name"] == "read_element"
    assert steps[3]["arguments"] == {"element_id": uploaded_paper.element_id}
    assert steps[4]["evidence_count"] == 1
    assert steps[5]["round"] == 2


def test_agent_stream_replays_a_duplicate_request_without_calling_the_model(
    client: TestClient, uploaded_paper: UploadedPaper
) -> None:
    """Breaks if a retried stream regenerates an already stored answer."""
    fake = _grounded_tool_flow(uploaded_paper)
    _configure_fake_agent_runtime(client.app, fake)
    request_id = "20000000-0000-0000-0000-000000000041"
    payload = _agent_payload("Explain the method.", request_id=request_id)

    first = _stream_events(
        client.post(
            f"/api/papers/{uploaded_paper.id}/agent/messages/stream", json=payload
        )
    )
    final_requests_after_first = fake.final_requests

    replay = _stream_events(
        client.post(
            f"/api/papers/{uploaded_paper.id}/agent/messages/stream", json=payload
        )
    )

    assert [name for name, _ in replay] == ["started", "completed"]
    assert replay[-1][1]["message"]["message_id"] == first[-1][1]["message"]["message_id"]
    assert fake.final_requests == final_requests_after_first
    assert _chat_row_counts(client.app.state.paper_repository) == (1, 2)


def test_agent_stream_reports_a_provider_failure_as_an_error_event(
    client: TestClient, uploaded_paper: UploadedPaper
) -> None:
    """Breaks if a provider failure leaks raw text or leaves an assistant row."""
    _configure_fake_agent_runtime(
        client.app,
        FakeAgentClient(final_answer=VllmToolCallingError("raw-stream-provider-secret")),
    )

    response = client.post(
        f"/api/papers/{uploaded_paper.id}/agent/messages/stream",
        json=_agent_payload("Explain the method."),
    )

    assert response.status_code == 200
    events = _stream_events(response)
    assert [name for name, _ in events if name != "step"] == ["started", "error"]
    assert events[-1][1]["code"] == "agent_failed"
    assert "raw-stream-provider-secret" not in response.text
    assert _chat_row_counts(client.app.state.paper_repository) == (1, 1)


def test_agent_stream_reports_an_unresolvable_citation_as_an_error_event(
    client: TestClient, uploaded_paper: UploadedPaper, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Breaks if a citation lookup failure truncates the stream with no event."""
    fake = _grounded_tool_flow(uploaded_paper)
    fake.stream_chunks = None
    _configure_fake_agent_runtime(client.app, fake)

    def fail_citation_response(*args: object, **kwargs: object) -> None:
        raise HTTPException(
            status_code=502, detail="Reasoning model could not complete the request."
        )

    monkeypatch.setattr(agent_routes, "_agent_message_response", fail_citation_response)

    response = client.post(
        f"/api/papers/{uploaded_paper.id}/agent/messages/stream",
        json=_agent_payload("Explain the method."),
    )

    assert response.status_code == 200
    events = _stream_events(response)
    assert [name for name, _ in events if name != "step"] == [
        "started",
        "delta",
        "error",
    ]
    assert events[-1][1]["code"] == "answer_unavailable"
    assert "Reasoning model could not complete the request." not in response.text


def test_agent_stream_keeps_request_errors_outside_the_event_stream(
    client: TestClient, uploaded_paper: UploadedPaper, stage1_incomplete_paper
) -> None:
    """Breaks if validation failures are reported as stream errors instead of JSON."""
    _configure_fake_agent_runtime(
        client.app,
        FakeAgentClient(final_answer=VllmToolCallingError("unused")),
    )

    missing_paper = client.post(
        f"/api/papers/{uuid4()}/agent/messages/stream", json=_agent_payload()
    )
    unknown_profile = client.post(
        f"/api/papers/{uploaded_paper.id}/agent/messages/stream",
        json=_agent_payload(model_profile_id=str(uuid4())),
    )
    incomplete = client.post(
        f"/api/papers/{stage1_incomplete_paper.id}/agent/messages/stream",
        json=_agent_payload(),
    )

    assert missing_paper.status_code == 404
    assert unknown_profile.status_code == 503
    assert incomplete.status_code == 200
    events = _stream_events(incomplete)
    assert [name for name, _ in events] == ["started", "error"]
    assert events[-1][1]["code"] == "agent_not_ready"


def test_agent_stream_completed_event_carries_context_usage(
    client: TestClient, uploaded_paper: UploadedPaper
) -> None:
    """The completed frame reports the next request's occupancy estimate."""
    _configure_fake_agent_runtime(client.app, _grounded_tool_flow(uploaded_paper))

    response = client.post(
        f"/api/papers/{uploaded_paper.id}/agent/messages/stream",
        json=_agent_payload("Explain the method."),
    )

    events = _stream_events(response)
    completed = events[-1][1]
    usage = completed["context_usage"]
    assert usage["used_tokens"] > 0
    # The fake profile sets no context length, so the limit fields stay unset.
    assert usage["context_length"] is None
    assert usage["effective_limit"] is None
    assert usage["percent"] is None


def test_context_usage_endpoint_reports_next_request_estimate(
    client: TestClient, uploaded_paper: UploadedPaper
) -> None:
    """GET estimates the next request against the selected profile."""
    limited = replace(
        _model_profile(DEFAULT_PROFILE_ID),
        context_length=8192,
        max_output_tokens=1024,
    )
    _configure_fake_agent_models(
        client.app,
        {DEFAULT_PROFILE_ID: _grounded_tool_flow(uploaded_paper)},
        profiles={DEFAULT_PROFILE_ID: limited},
    )
    asked = client.post(
        f"/api/papers/{uploaded_paper.id}/agent/messages",
        json=_agent_payload("Explain the method."),
    )
    assert asked.status_code == 200
    body = asked.json()
    assert body["context_usage"]["context_length"] == 8192
    assert body["context_usage"]["effective_limit"] == 8192 - 1024
    assert body["context_usage"]["percent"] is not None

    usage = client.get(
        f"/api/papers/{uploaded_paper.id}/agent/conversations"
        f"/{body['conversation_id']}/context-usage",
        params={"model_profile_id": DEFAULT_PROFILE_ID},
    )
    assert usage.status_code == 200
    payload = usage.json()
    assert payload == body["context_usage"]
    assert payload["context_length"] == 8192
    assert payload["compaction_threshold"] == int((8192 - 1024) * 0.75)
    assert payload["used_tokens"] > 0


def test_context_usage_endpoint_rejects_unknown_conversation_and_profile(
    client: TestClient, uploaded_paper: UploadedPaper
) -> None:
    _configure_fake_agent_runtime(client.app, _grounded_tool_flow(uploaded_paper))
    asked = client.post(
        f"/api/papers/{uploaded_paper.id}/agent/messages",
        json=_agent_payload("Explain the method."),
    )
    conversation_id = asked.json()["conversation_id"]

    unknown_conversation = client.get(
        f"/api/papers/{uploaded_paper.id}/agent/conversations"
        f"/{uuid4()}/context-usage",
        params={"model_profile_id": DEFAULT_PROFILE_ID},
    )
    unknown_profile = client.get(
        f"/api/papers/{uploaded_paper.id}/agent/conversations"
        f"/{conversation_id}/context-usage",
        params={"model_profile_id": str(uuid4())},
    )

    assert unknown_conversation.status_code == 404
    assert unknown_profile.status_code == 503
