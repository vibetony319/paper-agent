from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass
import json
from pathlib import Path
from threading import Barrier
from typing import Callable
from uuid import uuid4

import pytest
from sqlalchemy import event, func, select

from paper_agent.database import conversation_messages, conversations, processing_runs
from paper_agent.domain import (
    AgentMessageRole,
    AgentMode,
    BoundingBox,
    Conversation,
    ConversationMessage,
    DocumentElement,
    GraphNode,
    GraphStage,
    Page,
    ProcessingStatus,
    Section,
)
from paper_agent.models import (
    VllmToolCall,
    VllmToolCallingError,
    VllmToolTurn,
)
from paper_agent.model_profiles import ModelSnapshot
from paper_agent.services.agent_runtime import (
    AgentQuestion,
    AgentRuntimePrerequisiteError,
    AgentRuntimeResponseError,
    AgentRuntimeUnavailableError,
    AgentTurn,
    PaperAgentRuntime,
)
from paper_agent.services.agent_tools import PaperToolRegistry
from paper_agent.services.citation_guard import CitationGuard
from paper_agent.storage import PaperRepository


CANONICAL_INSUFFICIENT_EVIDENCE = (
    "I could not find enough evidence in this paper to answer that reliably."
)


@pytest.fixture
def repository(tmp_path: Path) -> PaperRepository:
    return PaperRepository(f"sqlite:///{tmp_path / 'paper-agent.db'}")


@dataclass(frozen=True)
class PreparedPaper:
    id: str
    element_id: str


class FakeAgentClient:
    def __init__(
        self,
        *,
        turns: tuple[VllmToolTurn | Exception, ...] = (),
        final_payload: dict[str, object] | Exception | None = None,
        on_tool_turn: Callable[[], None] | None = None,
    ) -> None:
        self.remaining_turns = list(turns)
        self.final_payload = final_payload or _insufficient_payload()
        self.on_tool_turn = on_tool_turn
        self.tool_requests: list[dict[str, object]] = []
        self.final_requests: list[dict[str, object]] = []
        self.health_calls = 0
        self.health_error: Exception | None = None

    @property
    def tool_choices(self) -> list[object]:
        return [request["tool_choice"] for request in self.tool_requests]

    def request_tool_turn(
        self,
        *,
        messages: list[dict[str, object]],
        tools: tuple[dict[str, object], ...],
        tool_choice: str | dict[str, object],
    ) -> VllmToolTurn:
        if self.on_tool_turn is not None:
            self.on_tool_turn()
        self.tool_requests.append(
            {
                "messages": deepcopy(messages),
                "tools": deepcopy(tools),
                "tool_choice": deepcopy(tool_choice),
            }
        )
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
        self.final_requests.append(
            {
                "messages": deepcopy(messages),
                "schema_name": schema_name,
                "schema": deepcopy(schema),
            }
        )
        if isinstance(self.final_payload, Exception):
            raise self.final_payload
        return deepcopy(self.final_payload)

    def validate_tool_calling(self) -> None:
        self.health_calls += 1
        if self.health_error is not None:
            raise self.health_error


class ExplodingToolRegistry:
    def __init__(self, delegate: PaperToolRegistry) -> None:
        self.delegate = delegate

    def definitions(self) -> tuple[dict[str, object], ...]:
        return self.delegate.definitions()

    def execute(self, **_kwargs: object):
        raise RuntimeError("raw-tool-execution-secret")


class FailingDefinitionsToolRegistry:
    def definitions(self) -> tuple[dict[str, object], ...]:
        raise RuntimeError("raw-tool-definitions-secret")

    def execute(self, **_kwargs: object):
        raise AssertionError("tool execution must not run after definitions fail")


class FailingOnceGuard(CitationGuard):
    def __init__(self) -> None:
        self.remaining_failures = 1

    def validate(self, *args: object, **kwargs: object):
        if self.remaining_failures:
            self.remaining_failures -= 1
            raise RuntimeError("raw-guard-secret")
        return super().validate(*args, **kwargs)


def _paper_with_stage1_document(
    repository: PaperRepository, name: str = "paper"
) -> PreparedPaper:
    paper = repository.create_paper(
        original_filename=f"{name}.pdf", stored_filename=f"private-{name}.pdf"
    )
    repository.save_page(paper.id, Page(number=1, width=200, height=300))
    section = Section(
        id=f"{name}-section", title="Method", order=0, page_number=1
    )
    element = DocumentElement(
        id=f"{name}-element",
        kind="paragraph",
        text="The router sends tokens to experts.",
        page_number=1,
        bbox=BoundingBox(0.1, 0.2, 0.9, 0.3),
        section_id=section.id,
    )
    repository.save_stage1_document(paper.id, (section,), (element,))
    repository.record_processing_status(
        paper.id, ProcessingStatus.completed, stage="stage1"
    )
    return PreparedPaper(id=paper.id, element_id=element.id)


def _model_snapshot(
    *,
    profile_id: str = "10000000-0000-0000-0000-000000000001",
    display_name: str = "Agent model",
    model_name: str = "agent-model",
    revision: int = 1,
) -> ModelSnapshot:
    return ModelSnapshot(
        profile_id=profile_id,
        display_name=display_name,
        base_url="http://127.0.0.1:8000/v1",
        model_name=model_name,
        revision=revision,
    )


class RuntimeHarness:
    def __init__(
        self, runtime: PaperAgentRuntime, client: FakeAgentClient | None
    ) -> None:
        self.runtime = runtime
        self.client = client

    def ask(self, *, paper_id: str, question: AgentQuestion) -> AgentTurn:
        return self.runtime.ask(
            paper_id=paper_id,
            question=question,
            client=self.client,
            model_snapshot=_model_snapshot(),
            request_id=str(uuid4()),
        )

    def validate_tool_calling(self) -> None:
        self.runtime.validate_tool_calling(client=self.client)


def _runtime_core(
    repository: PaperRepository,
    *,
    tools: object | None = None,
    guard: CitationGuard | None = None,
) -> PaperAgentRuntime:
    return PaperAgentRuntime(
        repository=repository,
        tools=tools or PaperToolRegistry(repository),
        guard=guard or CitationGuard(),
    )


def _runtime(
    repository: PaperRepository,
    client: FakeAgentClient | None,
    *,
    tools: object | None = None,
) -> RuntimeHarness:
    return RuntimeHarness(_runtime_core(repository, tools=tools), client)


def _tool_call(
    name: str, arguments: dict[str, object], *, call_id: str = "call-1"
) -> VllmToolCall:
    return VllmToolCall(id=call_id, name=name, arguments=arguments)


def _tool_turn(
    name: str,
    arguments: dict[str, object],
    *,
    call_id: str = "call-1",
    content: str | None = None,
) -> VllmToolTurn:
    return VllmToolTurn(
        content=content,
        tool_calls=(_tool_call(name, arguments, call_id=call_id),),
    )


def _grounded_payload(
    *,
    citations: list[str],
    paper_answer: str = "The paper uses a router.",
    background: str | None = None,
) -> dict[str, object]:
    return {
        "status": "grounded",
        "paper_answer": paper_answer,
        "citation_element_ids": citations,
        "background_explanation": background,
    }


def _insufficient_payload() -> dict[str, object]:
    return {
        "status": "insufficient_evidence",
        "paper_answer": "raw model refusal",
        "citation_element_ids": [],
        "background_explanation": None,
    }


def _chat_row_counts(repository: PaperRepository) -> tuple[int, int, int]:
    with repository.engine.connect() as connection:
        return (
            connection.execute(
                select(func.count()).select_from(conversations)
            ).scalar_one(),
            connection.execute(
                select(func.count()).select_from(conversation_messages)
            ).scalar_one(),
            connection.execute(
                select(func.count()).select_from(processing_runs)
            ).scalar_one(),
        )


def _all_durable_rows(repository: PaperRepository) -> list[tuple[str, str]]:
    with repository.engine.connect() as connection:
        rows = connection.execute(
            select(conversation_messages.c.role, conversation_messages.c.content)
            .order_by(conversation_messages.c.sequence)
        )
        return [(row.role, row.content) for row in rows]


def test_runtime_persists_user_before_model_then_only_the_guarded_answer(repository):
    """Breaks if raw model/tool text is stored or the user is not durable first."""
    paper = _paper_with_stage1_document(repository)
    observed_during_model: list[list[tuple[str, str]]] = []
    client = FakeAgentClient(
        turns=(
            _tool_turn(
                "search_paper",
                {"query": "router", "limit": 5},
                content="raw tool-planning prose",
            ),
            VllmToolTurn(content="raw unguarded draft", tool_calls=()),
        ),
        final_payload=_grounded_payload(citations=[paper.element_id]),
        on_tool_turn=lambda: observed_during_model.append(
            _all_durable_rows(repository)
        ),
    )
    before_processing_rows = _chat_row_counts(repository)[2]

    turn = _runtime(repository, client).ask(
        paper_id=paper.id,
        question=AgentQuestion(
            content="How does routing work?", mode=AgentMode.paper_only
        ),
    )

    assert observed_during_model == [
        [("user", "How does routing work?")],
        [("user", "How does routing work?")],
    ]
    assert turn.answer.status == "grounded"
    assert turn.assistant_message.content == "The paper uses a router."
    assert turn.assistant_message.citation_element_ids == (paper.element_id,)
    assert repository.get_conversation_messages(paper.id, turn.conversation.id) == (
        turn.user_message,
        turn.assistant_message,
    )
    assert client.tool_choices == ["required", "auto"]
    assert client.final_requests[0]["schema"] == CitationGuard().output_schema()
    assert _chat_row_counts(repository)[2] == before_processing_rows
    assert "raw tool-planning prose" not in repr(
        repository.get_conversation_messages(paper.id, turn.conversation.id)
    )
    assert "raw unguarded draft" not in repr(
        repository.get_conversation_messages(paper.id, turn.conversation.id)
    )


def test_runtime_switches_models_in_one_conversation_and_persists_turn_audit_parity(
    repository,
):
    """Breaks if model selection mutates conversation identity or differs within one turn."""
    paper = _paper_with_stage1_document(repository)
    runtime = _runtime_core(repository)
    first_snapshot = _model_snapshot()
    second_snapshot = _model_snapshot(
        profile_id="10000000-0000-0000-0000-000000000002",
        display_name="Second model",
        model_name="second-model",
    )
    first_request_id = "20000000-0000-0000-0000-000000000001"
    second_request_id = "20000000-0000-0000-0000-000000000002"

    first = runtime.ask(
        paper_id=paper.id,
        question=AgentQuestion(content="First", mode=AgentMode.paper_only),
        client=FakeAgentClient(),
        model_snapshot=first_snapshot,
        request_id=first_request_id,
    )
    second = runtime.ask(
        paper_id=paper.id,
        question=AgentQuestion(
            content="Second",
            mode=AgentMode.paper_only,
            conversation_id=first.conversation.id,
        ),
        client=FakeAgentClient(),
        model_snapshot=second_snapshot,
        request_id=second_request_id,
    )

    assert second.conversation.id == first.conversation.id
    messages = repository.get_conversation_messages(paper.id, first.conversation.id)
    assert [message.model_snapshot for message in messages] == [
        first_snapshot,
        first_snapshot,
        second_snapshot,
        second_snapshot,
    ]
    assert [message.request_id for message in messages] == [
        first_request_id,
        first_request_id,
        second_request_id,
        second_request_id,
    ]


def test_runtime_replays_complete_request_without_another_model_call(repository):
    """Breaks if a complete idempotency hit validates new payload or invokes its model."""
    paper = _paper_with_stage1_document(repository)
    runtime = _runtime_core(repository)
    request_id = "20000000-0000-0000-0000-000000000003"
    first_client = FakeAgentClient()
    first = runtime.ask(
        paper_id=paper.id,
        question=AgentQuestion(content="Original", mode=AgentMode.paper_only),
        client=first_client,
        model_snapshot=_model_snapshot(),
        request_id=request_id,
    )
    replay_client = FakeAgentClient(
        turns=(AssertionError("duplicate model call"),),
    )

    replay = runtime.ask(
        paper_id=paper.id,
        question=AgentQuestion(content="Conflicting duplicate", mode=AgentMode.paper_only),
        client=replay_client,
        model_snapshot=_model_snapshot(
            profile_id="10000000-0000-0000-0000-000000000009"
        ),
        request_id=request_id,
    )

    assert replay.conversation == first.conversation
    assert replay.user_message == first.user_message
    assert replay.assistant_message == first.assistant_message
    assert replay_client.tool_requests == []
    assert replay_client.final_requests == []
    assert len(repository.get_conversation_messages(paper.id, first.conversation.id)) == 2


def test_runtime_retries_user_only_request_without_appending_a_second_user(repository):
    """Breaks if retry duplicates user state instead of completing the original request."""
    paper = _paper_with_stage1_document(repository)
    runtime = _runtime_core(repository)
    request_id = "20000000-0000-0000-0000-000000000004"
    snapshot = _model_snapshot()
    failing_client = FakeAgentClient(
        turns=(VllmToolCallingError("raw-first-attempt-secret"),)
    )
    with pytest.raises(AgentRuntimeResponseError):
        runtime.ask(
            paper_id=paper.id,
            question=AgentQuestion(content="Retry me", mode=AgentMode.paper_only),
            client=failing_client,
            model_snapshot=snapshot,
            request_id=request_id,
        )
    user = repository.get_agent_user_message_by_request(paper.id, request_id)
    assert user is not None

    recovered = runtime.ask(
        paper_id=paper.id,
        question=AgentQuestion(content="Retry me", mode=AgentMode.paper_only),
        client=FakeAgentClient(),
        model_snapshot=snapshot,
        request_id=request_id,
    )

    assert recovered.user_message == user
    assert repository.get_agent_turn_by_request(paper.id, request_id) == (
        recovered.user_message,
        recovered.assistant_message,
    )
    assert len(repository.get_conversation_messages(paper.id, user.conversation_id)) == 2


def test_runtime_rejects_partial_retry_when_current_snapshot_changed(repository):
    """Breaks if a failed request silently resumes against a revised model profile."""
    paper = _paper_with_stage1_document(repository)
    runtime = _runtime_core(repository)
    request_id = "20000000-0000-0000-0000-000000000005"
    first_snapshot = _model_snapshot()
    with pytest.raises(AgentRuntimeResponseError):
        runtime.ask(
            paper_id=paper.id,
            question=AgentQuestion(content="Retry me", mode=AgentMode.paper_only),
            client=FakeAgentClient(turns=(VllmToolCallingError("failed"),)),
            model_snapshot=first_snapshot,
            request_id=request_id,
        )
    retry_client = FakeAgentClient()

    with pytest.raises(RuntimeError, match="new request_id"):
        runtime.ask(
            paper_id=paper.id,
            question=AgentQuestion(content="Retry me", mode=AgentMode.paper_only),
            client=retry_client,
            model_snapshot=_model_snapshot(revision=2),
            request_id=request_id,
        )

    assert retry_client.tool_requests == []
    assert repository.get_agent_user_message_by_request(paper.id, request_id) is not None


def test_runtime_serializes_concurrent_same_request_before_any_model_call(repository):
    """Breaks if concurrent duplicates can both append or call the provider model."""
    paper = _paper_with_stage1_document(repository)
    runtime = _runtime_core(repository)
    client = FakeAgentClient()
    request_id = "20000000-0000-0000-0000-000000000006"
    barrier = Barrier(2)

    def ask_once() -> AgentTurn:
        barrier.wait()
        return runtime.ask(
            paper_id=paper.id,
            question=AgentQuestion(content="Concurrent", mode=AgentMode.paper_only),
            client=client,
            model_snapshot=_model_snapshot(),
            request_id=request_id,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        turns = tuple(executor.map(lambda _index: ask_once(), range(2)))

    assert turns[0].assistant_message.id == turns[1].assistant_message.id
    assert len(client.tool_requests) == 1
    assert len(client.final_requests) == 1
    assert repository.get_agent_turn_by_request(paper.id, request_id) is not None
    assert runtime._request_locks == {}


def test_runtime_releases_request_lock_entries_after_many_unique_failures(repository):
    """Breaks if arbitrary client request IDs permanently grow the process lock registry."""
    paper = _paper_with_stage1_document(repository)
    runtime = _runtime_core(repository)

    for index in range(40):
        with pytest.raises(AgentRuntimeResponseError):
            runtime.ask(
                paper_id=paper.id,
                question=AgentQuestion(
                    content=f"Failure {index}", mode=AgentMode.paper_only
                ),
                client=FakeAgentClient(
                    turns=(VllmToolCallingError("expected failure"),)
                ),
                model_snapshot=_model_snapshot(),
                request_id=str(uuid4()),
            )

    assert runtime._request_locks == {}


def test_runtime_retries_after_unexpected_guard_failure_without_leaking_or_duplication(
    repository,
):
    """Breaks if a guard exception leaks details or makes its user-only turn unrecoverable."""
    paper = _paper_with_stage1_document(repository)
    guard = FailingOnceGuard()
    runtime = _runtime_core(repository, guard=guard)
    request_id = "20000000-0000-0000-0000-000000000007"
    snapshot = _model_snapshot()
    with pytest.raises(AgentRuntimeResponseError) as caught:
        runtime.ask(
            paper_id=paper.id,
            question=AgentQuestion(content="Guard retry", mode=AgentMode.paper_only),
            client=FakeAgentClient(),
            model_snapshot=snapshot,
            request_id=request_id,
        )
    assert "raw-guard-secret" not in str(caught.value)
    assert caught.value.__cause__ is None

    recovered = runtime.ask(
        paper_id=paper.id,
        question=AgentQuestion(content="Guard retry", mode=AgentMode.paper_only),
        client=FakeAgentClient(),
        model_snapshot=snapshot,
        request_id=request_id,
    )

    assert recovered.user_message.request_id == request_id
    assert len(repository.get_conversation_messages(paper.id, recovered.conversation.id)) == 2


def test_runtime_sends_openai_tool_result_messages_with_returned_evidence_ids(repository):
    """Breaks if the final model cannot distinguish source IDs from navigation data."""
    paper = _paper_with_stage1_document(repository)
    client = FakeAgentClient(
        turns=(
            _tool_turn("search_paper", {"query": "router", "limit": 5}),
            VllmToolTurn(content=None, tool_calls=()),
        ),
        final_payload=_grounded_payload(citations=[paper.element_id]),
    )

    _runtime(repository, client).ask(
        paper_id=paper.id,
        question=AgentQuestion(content="Explain it.", mode=AgentMode.paper_only),
    )

    final_messages = client.final_requests[0]["messages"]
    assistant_tool_call = final_messages[-2]
    tool_result = final_messages[-1]
    assert assistant_tool_call["role"] == "assistant"
    assert assistant_tool_call["tool_calls"][0]["function"]["name"] == "search_paper"
    assert tool_result["role"] == "tool"
    assert tool_result["tool_call_id"] == "call-1"
    result_payload = json.loads(tool_result["content"])
    assert result_payload["evidence_element_ids"] == [paper.element_id]
    assert result_payload["content"]["elements"][0]["id"] == paper.element_id


def test_runtime_checks_paper_before_client_without_creating_chat_or_processing_rows(
    repository,
):
    """Breaks if an unknown paper creates state or is masked by missing configuration."""
    before = _chat_row_counts(repository)

    with pytest.raises(AgentRuntimePrerequisiteError):
        _runtime(repository, None).ask(
            paper_id="missing-paper",
            question=AgentQuestion(content="Question", mode=AgentMode.paper_only),
        )

    assert _chat_row_counts(repository) == before


def test_runtime_requires_latest_stage1_status_completed_before_any_chat_write(
    repository,
):
    """Breaks if missing or superseded Stage 1 completion is accepted."""
    missing_stage = repository.create_paper(
        original_filename="missing.pdf", stored_filename="missing.pdf"
    )
    superseded = _paper_with_stage1_document(repository, "superseded")
    repository.record_processing_status(
        superseded.id, ProcessingStatus.failed, stage="stage1"
    )
    before = _chat_row_counts(repository)

    for paper_id in (missing_stage.id, superseded.id):
        with pytest.raises(AgentRuntimePrerequisiteError):
            _runtime(repository, FakeAgentClient()).ask(
                paper_id=paper_id,
                question=AgentQuestion(content="Question", mode=AgentMode.paper_only),
            )

    assert _chat_row_counts(repository) == before


def test_runtime_rejects_a_conversation_owned_by_another_paper_without_writes(repository):
    """Breaks if a supplied conversation can cross the active-paper boundary."""
    first = _paper_with_stage1_document(repository, "first")
    second = _paper_with_stage1_document(repository, "second")
    other_conversation = repository.create_conversation(
        Conversation(paper_id=second.id, mode=AgentMode.paper_only)
    )
    before = _chat_row_counts(repository)

    with pytest.raises(AgentRuntimePrerequisiteError):
        _runtime(repository, FakeAgentClient()).ask(
            paper_id=first.id,
            question=AgentQuestion(
                content="Question",
                mode=AgentMode.paper_only,
                conversation_id=other_conversation.id,
            ),
        )

    assert _chat_row_counts(repository) == before


def test_runtime_rejects_a_conversation_mode_change_without_writes(repository):
    """Breaks if one durable conversation can mix paper-only and external modes."""
    paper = _paper_with_stage1_document(repository)
    conversation = repository.create_conversation(
        Conversation(paper_id=paper.id, mode=AgentMode.external_knowledge)
    )
    before = _chat_row_counts(repository)

    with pytest.raises(AgentRuntimePrerequisiteError):
        _runtime(repository, FakeAgentClient()).ask(
            paper_id=paper.id,
            question=AgentQuestion(
                content="Question",
                mode=AgentMode.paper_only,
                conversation_id=conversation.id,
            ),
        )

    assert _chat_row_counts(repository) == before


def test_runtime_requires_a_configured_client_before_creating_a_conversation(repository):
    """Breaks if unavailable reasoning configuration leaves an empty chat behind."""
    paper = _paper_with_stage1_document(repository)
    before = _chat_row_counts(repository)

    with pytest.raises(AgentRuntimeUnavailableError):
        _runtime(repository, None).ask(
            paper_id=paper.id,
            question=AgentQuestion(content="Question", mode=AgentMode.paper_only),
        )

    assert _chat_row_counts(repository) == before


def test_runtime_builds_model_history_from_only_the_latest_six_durable_messages(
    repository,
):
    """Breaks if history is unbounded, unstable, or duplicates the current user turn."""
    paper = _paper_with_stage1_document(repository)
    conversation = repository.create_conversation(
        Conversation(paper_id=paper.id, mode=AgentMode.paper_only)
    )
    for index in range(7):
        role = AgentMessageRole.user if index % 2 == 0 else AgentMessageRole.assistant
        repository.append_conversation_message(
            ConversationMessage(
                conversation_id=conversation.id,
                paper_id=paper.id,
                role=role,
                content=f"durable-{index}",
            )
        )
    client = FakeAgentClient()

    statements: list[str] = []

    def capture_statement(
        connection, cursor, statement, parameters, context, executemany
    ) -> None:
        statements.append(statement)

    event.listen(repository.engine, "before_cursor_execute", capture_statement)
    try:
        _runtime(repository, client).ask(
            paper_id=paper.id,
            question=AgentQuestion(
                content="current-question",
                mode=AgentMode.paper_only,
                conversation_id=conversation.id,
            ),
        )
    finally:
        event.remove(repository.engine, "before_cursor_execute", capture_statement)

    model_history = client.tool_requests[0]["messages"][1:]
    assert model_history == [
        {"role": "user", "content": "durable-2"},
        {"role": "assistant", "content": "durable-3"},
        {"role": "user", "content": "durable-4"},
        {"role": "assistant", "content": "durable-5"},
        {"role": "user", "content": "durable-6"},
        {"role": "user", "content": "current-question"},
    ]
    history_reads = [
        statement
        for statement in statements
        if "conversation_messages.content" in statement
        and "FROM conversation_messages" in statement
        and "FROM conversation_message_citations" not in statement
        and "LIMIT" in statement.upper()
    ]
    assert len(history_reads) == 1
    assert "LIMIT" in history_reads[0].upper()


def test_external_mode_prompt_and_result_keep_background_separate(repository):
    """Breaks if graph claims or external background can be mixed into paper prose."""
    paper = _paper_with_stage1_document(repository)
    client = FakeAgentClient(
        turns=(
            _tool_turn("search_paper", {"query": "router", "limit": 5}),
            VllmToolTurn(content=None, tool_calls=()),
        ),
        final_payload=_grounded_payload(
            citations=[paper.element_id], background="General routing background."
        ),
    )

    turn = _runtime(repository, client).ask(
        paper_id=paper.id,
        question=AgentQuestion(
            content="Explain routing.", mode=AgentMode.external_knowledge
        ),
    )

    system_prompt = client.tool_requests[0]["messages"][0]["content"].casefold()
    assert "graph data is for navigation only" in system_prompt
    assert "source-element ids returned by tools during this request" in system_prompt
    assert "insufficient_evidence" in system_prompt
    assert "background_explanation" in system_prompt
    assert "separate" in system_prompt
    assert turn.answer.background_explanation == "General routing background."
    assert turn.assistant_message.content == "The paper uses a router."
    durable = repository.get_conversation_messages(paper.id, turn.conversation.id)
    assert durable[-1].content == "The paper uses a router."
    assert durable[-1].background_explanation == "General routing background."
    assert "General routing background" not in durable[-1].content


def test_runtime_does_not_authorize_citations_from_earlier_conversation_turns(repository):
    """Breaks if durable historical citations leak into the current request allow-list."""
    paper = _paper_with_stage1_document(repository)
    conversation = repository.create_conversation(
        Conversation(paper_id=paper.id, mode=AgentMode.paper_only)
    )
    repository.append_conversation_message(
        ConversationMessage(
            conversation_id=conversation.id,
            paper_id=paper.id,
            role=AgentMessageRole.assistant,
            content="Earlier grounded answer.",
            citation_element_ids=(paper.element_id,),
        )
    )
    client = FakeAgentClient(
        final_payload=_grounded_payload(
            citations=[paper.element_id], paper_answer="Unsupported reuse."
        )
    )

    turn = _runtime(repository, client).ask(
        paper_id=paper.id,
        question=AgentQuestion(
            content="Can I reuse that citation?",
            mode=AgentMode.paper_only,
            conversation_id=conversation.id,
        ),
    )

    assert turn.answer.status == "insufficient_evidence"
    assert turn.assistant_message.content == CANONICAL_INSUFFICIENT_EVIDENCE
    assert turn.assistant_message.citation_element_ids == ()
    assert "Unsupported reuse" not in turn.assistant_message.content


def test_runtime_treats_graph_ids_as_navigation_not_answer_citations(repository):
    """Breaks if a returned graph node ID is accepted in place of source evidence."""
    paper = _paper_with_stage1_document(repository)
    repository.replace_graph_stage(
        paper.id,
        GraphStage.core,
        (
            GraphNode(
                id="router-node",
                node_type="method",
                name="Router",
                summary="Routes tokens.",
                stage=GraphStage.core,
                evidence_element_ids=(paper.element_id,),
            ),
        ),
        (),
    )
    client = FakeAgentClient(
        turns=(
            _tool_turn("search_graph", {"query": "router", "limit": 5}),
            VllmToolTurn(content=None, tool_calls=()),
        ),
        final_payload=_grounded_payload(
            citations=["router-node"], paper_answer="Node IDs are citations."
        ),
    )

    turn = _runtime(repository, client).ask(
        paper_id=paper.id,
        question=AgentQuestion(content="What is Router?", mode=AgentMode.paper_only),
    )

    assert turn.answer.status == "insufficient_evidence"
    assert turn.assistant_message.citation_element_ids == ()
    assert turn.assistant_message.content == CANONICAL_INSUFFICIENT_EVIDENCE


def test_runtime_stops_after_six_single_tool_turns(repository):
    """Breaks if a model can drive an unbounded tool loop."""
    paper = _paper_with_stage1_document(repository)
    turns = tuple(
        _tool_turn(
            "search_paper",
            {"query": "router", "limit": 5},
            call_id=f"call-{index}",
        )
        for index in range(7)
    )
    client = FakeAgentClient(
        turns=turns,
        final_payload=_grounded_payload(citations=[paper.element_id]),
    )

    turn = _runtime(repository, client).ask(
        paper_id=paper.id,
        question=AgentQuestion(content="Keep searching.", mode=AgentMode.paper_only),
    )

    assert turn.answer.status == "grounded"
    assert client.tool_choices == ["required", "auto", "auto", "auto", "auto", "auto"]
    assert len(client.remaining_turns) == 1
    assert len(client.final_requests) == 1


def test_runtime_rejects_multiple_tool_calls_in_one_turn_without_assistant_write(
    repository,
):
    """Breaks if a nonparallel runtime executes more than one call from one model turn."""
    paper = _paper_with_stage1_document(repository)
    client = FakeAgentClient(
        turns=(
            VllmToolTurn(
                content="raw multi-call prose",
                tool_calls=(
                    _tool_call("search_paper", {"query": "router", "limit": 5}),
                    _tool_call(
                        "search_paper",
                        {"query": "secret second query", "limit": 5},
                        call_id="call-2",
                    ),
                ),
            ),
        )
    )

    with pytest.raises(AgentRuntimeResponseError) as error:
        _runtime(repository, client).ask(
            paper_id=paper.id,
            question=AgentQuestion(content="Question", mode=AgentMode.paper_only),
        )

    assert str(error.value) == "Reasoning model could not complete the request."
    assert "multi-call" not in str(error.value)
    assert _all_durable_rows(repository) == [("user", "Question")]


def test_runtime_converts_malformed_final_contract_to_canonical_persisted_answer(
    repository,
):
    """Breaks if malformed final model prose reaches persistence or the caller."""
    paper = _paper_with_stage1_document(repository)
    malformed = _grounded_payload(
        citations=[paper.element_id], paper_answer="raw malformed claim"
    )
    malformed["unexpected"] = "raw final secret"
    client = FakeAgentClient(final_payload=malformed)

    turn = _runtime(repository, client).ask(
        paper_id=paper.id,
        question=AgentQuestion(content="Question", mode=AgentMode.paper_only),
    )

    assert turn.answer.status == "insufficient_evidence"
    assert turn.assistant_message.content == CANONICAL_INSUFFICIENT_EVIDENCE
    assert turn.assistant_message.citation_element_ids == ()
    assert "raw malformed claim" not in repr(
        repository.get_conversation_messages(paper.id, turn.conversation.id)
    )
    assert "raw final secret" not in repr(
        repository.get_conversation_messages(paper.id, turn.conversation.id)
    )


def test_runtime_sanitizes_tool_execution_failure_after_persisting_only_the_user(
    repository,
):
    """Breaks if a tool exception or raw result is exposed or stored."""
    paper = _paper_with_stage1_document(repository)
    client = FakeAgentClient(
        turns=(
            _tool_turn("search_paper", {"query": "router", "limit": 5}),
        )
    )
    tools = ExplodingToolRegistry(PaperToolRegistry(repository))

    with pytest.raises(AgentRuntimeResponseError) as error:
        _runtime(repository, client, tools=tools).ask(
            paper_id=paper.id,
            question=AgentQuestion(content="Question", mode=AgentMode.paper_only),
        )

    assert str(error.value) == "Reasoning model could not complete the request."
    assert "raw-tool-execution-secret" not in str(error.value)
    assert error.value.__cause__ is None
    assert _all_durable_rows(repository) == [("user", "Question")]


def test_runtime_sanitizes_tool_definition_failure_after_persisting_only_the_user(
    repository,
):
    """Breaks if tool-schema construction leaks raw errors after user persistence."""
    paper = _paper_with_stage1_document(repository)

    with pytest.raises(AgentRuntimeResponseError) as error:
        _runtime(repository, FakeAgentClient(), tools=FailingDefinitionsToolRegistry()).ask(
            paper_id=paper.id,
            question=AgentQuestion(content="Question", mode=AgentMode.paper_only),
        )

    assert str(error.value) == "Reasoning model could not complete the request."
    assert "raw-tool-definitions-secret" not in str(error.value)
    assert error.value.__cause__ is None
    assert _all_durable_rows(repository) == [("user", "Question")]


def test_runtime_sanitizes_tool_turn_transport_failure_after_user_persistence(repository):
    """Breaks if a vLLM tool-turn failure leaks content or writes an assistant reply."""
    paper = _paper_with_stage1_document(repository)
    client = FakeAgentClient(
        turns=(VllmToolCallingError("raw-provider-tool-secret"),)
    )

    with pytest.raises(AgentRuntimeResponseError) as error:
        _runtime(repository, client).ask(
            paper_id=paper.id,
            question=AgentQuestion(content="Question", mode=AgentMode.paper_only),
        )

    assert str(error.value) == "Reasoning model could not complete the request."
    assert "raw-provider-tool-secret" not in str(error.value)
    assert error.value.__cause__ is None
    assert _all_durable_rows(repository) == [("user", "Question")]


def test_runtime_sanitizes_final_transport_failure_after_user_persistence(repository):
    """Breaks if a vLLM final-JSON failure leaks content or writes an assistant reply."""
    paper = _paper_with_stage1_document(repository)
    client = FakeAgentClient(
        final_payload=VllmToolCallingError("raw-provider-final-secret")
    )

    with pytest.raises(AgentRuntimeResponseError) as error:
        _runtime(repository, client).ask(
            paper_id=paper.id,
            question=AgentQuestion(content="Question", mode=AgentMode.paper_only),
        )

    assert str(error.value) == "Reasoning model could not complete the request."
    assert "raw-provider-final-secret" not in str(error.value)
    assert error.value.__cause__ is None
    assert _all_durable_rows(repository) == [("user", "Question")]


def test_validate_tool_calling_requires_configuration_without_any_write(repository):
    """Breaks if explicit health validation runs without a configured client."""
    before = _chat_row_counts(repository)

    with pytest.raises(AgentRuntimeUnavailableError):
        _runtime(repository, None).validate_tool_calling()

    assert _chat_row_counts(repository) == before


def test_validate_tool_calling_sanitizes_failure_and_delegates_success(repository):
    """Breaks if health validation leaks provider text or skips the direct client."""
    failing = FakeAgentClient()
    failing.health_error = VllmToolCallingError("raw-health-secret")

    with pytest.raises(AgentRuntimeUnavailableError) as error:
        _runtime(repository, failing).validate_tool_calling()

    assert "raw-health-secret" not in str(error.value)
    assert error.value.__cause__ is None
    assert failing.health_calls == 1

    healthy = FakeAgentClient()
    _runtime(repository, healthy).validate_tool_calling()
    assert healthy.health_calls == 1
