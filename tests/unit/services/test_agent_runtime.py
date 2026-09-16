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

from paper_agent.annotation_storage import PaperAnnotationRepository
from paper_agent.annotations import TextAnchorDraft, TextAnchorRect
from paper_agent.database import conversation_messages, conversations, processing_runs
from paper_agent.domain import (
    AgentMessageRole,
    BoundingBox,
    Conversation,
    ConversationMessage,
    DocumentElement,
    Page,
    ProcessingStatus,
    Note,
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
    AgentStreamEvent,
    AgentTurn,
    PaperAgentRuntime,
    _COMPACTION_INSTRUCTION,
)
from paper_agent.services.agent_tools import PaperToolRegistry
from paper_agent.services.context_budget import ContextBudget
from paper_agent.services.citation_guard import CitationGuard
from paper_agent.services.note_memory import NoteMemoryService
from paper_agent.storage import PaperRepository


@pytest.fixture
def repository(tmp_path: Path) -> PaperRepository:
    return PaperRepository(f"sqlite:///{tmp_path / 'paper-agent.db'}")


@dataclass(frozen=True)
class PreparedPaper:
    id: str
    element_id: str


_INSUFFICIENT_MARKDOWN = "[[status:insufficient_evidence]]\n\nraw model refusal"


class FakeAgentClient:
    def __init__(
        self,
        *,
        turns: tuple[VllmToolTurn | Exception, ...] = (),
        final_answer: str = _INSUFFICIENT_MARKDOWN,
        stream_chunks: tuple[str, ...] | None = None,
        final_error: Exception | None = None,
        stream_error_at: int | None = None,
        on_tool_turn: Callable[[], None] | None = None,
        summary_answer: str | None = None,
        summary_error: Exception | None = None,
    ) -> None:
        self.remaining_turns = list(turns)
        self.final_answer = final_answer
        self.stream_chunks = None if stream_chunks is None else list(stream_chunks)
        self.final_error = final_error
        self.stream_error_at = stream_error_at
        self.on_tool_turn = on_tool_turn
        self.summary_answer = summary_answer
        self.summary_error = summary_error
        self.summary_requests: list[dict[str, object]] = []
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

    def complete_markdown_messages(self, *, messages: list[dict[str, object]]) -> str:
        if messages and messages[0].get("content") == _COMPACTION_INSTRUCTION:
            self.summary_requests.append({"messages": deepcopy(messages)})
            if self.summary_error is not None:
                raise self.summary_error
            if self.summary_answer is not None:
                return self.summary_answer
        self.final_requests.append({"messages": deepcopy(messages)})
        if self.final_error is not None:
            raise self.final_error
        return self.final_answer

    def stream_final_answer(self, *, messages: list[dict[str, object]]):
        self.final_requests.append({"messages": deepcopy(messages)})
        if self.final_error is not None:
            raise self.final_error
        chunks = (
            [self.final_answer] if self.stream_chunks is None else self.stream_chunks
        )
        for index, chunk in enumerate(chunks):
            if self.stream_error_at == index:
                raise VllmToolCallingError("raw-stream-secret")
            yield chunk

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

    def parse_markdown_answer(self, *args: object, **kwargs: object):
        if self.remaining_failures:
            self.remaining_failures -= 1
            raise RuntimeError("raw-guard-secret")
        return super().parse_markdown_answer(*args, **kwargs)


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

    def ask(
        self,
        *,
        paper_id: str,
        question: AgentQuestion,
        context_budget: ContextBudget | None = None,
    ) -> AgentTurn:
        return self.runtime.ask(
            paper_id=paper_id,
            question=question,
            client=self.client,
            model_snapshot=_model_snapshot(),
            request_id=str(uuid4()),
            context_budget=context_budget,
        )

    def stream_ask(
        self,
        *,
        paper_id: str,
        question: AgentQuestion,
        request_id: str | None = None,
        context_budget: ContextBudget | None = None,
    ) -> list[AgentStreamEvent]:
        return list(
            self.runtime.stream_ask(
                paper_id=paper_id,
                question=question,
                client=self.client,
                model_snapshot=_model_snapshot(),
                request_id=request_id or str(uuid4()),
                context_budget=context_budget,
            )
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


def _grounded_markdown(
    *,
    citations: list[str],
    paper_answer: str = "The paper uses a router.",
    background: str | None = None,
) -> str:
    markers = "".join(f" [[{element_id}]]" for element_id in citations)
    answer = f"{paper_answer}{markers}"
    return answer if background is None else f"{answer}\n\n---\n\n{background}"


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


# ~108 estimated tokens per message; six of these push a small profile over
# its compaction threshold without slowing the tests down.
_HISTORY_CHUNK = "context filler " * 30


def _conversation_with_history(
    repository: PaperRepository, paper_id: str, *, count: int = 6
) -> Conversation:
    conversation = repository.create_conversation(Conversation(paper_id=paper_id))
    for index in range(count):
        role = AgentMessageRole.user if index % 2 == 0 else AgentMessageRole.assistant
        repository.append_conversation_message(
            ConversationMessage(
                conversation_id=conversation.id,
                paper_id=paper_id,
                role=role,
                content=f"durable-{index} {_HISTORY_CHUNK}",
            )
        )
    return conversation


def test_runtime_persists_user_before_model_then_only_the_final_answer(repository):
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
        final_answer=_grounded_markdown(citations=[paper.element_id]),
        on_tool_turn=lambda: observed_during_model.append(
            _all_durable_rows(repository)
        ),
    )
    before_processing_rows = _chat_row_counts(repository)[2]

    turn = _runtime(repository, client).ask(
        paper_id=paper.id,
        question=AgentQuestion(
            content="How does routing work?"
        ),
    )

    assert observed_during_model == [
        [("user", "How does routing work?")],
        [("user", "How does routing work?")],
    ]
    assert turn.answer.status == "grounded"
    assert turn.assistant_message.content == (
        f"The paper uses a router. [[{paper.element_id}]]"
    )
    assert turn.assistant_message.citation_element_ids == (paper.element_id,)
    assert repository.get_conversation_messages(paper.id, turn.conversation.id) == (
        turn.user_message,
        turn.assistant_message,
    )
    assert client.tool_choices == ["auto", "auto"]
    assert set(client.final_requests[0]) == {"messages"}
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
        question=AgentQuestion(content="First"),
        client=FakeAgentClient(),
        model_snapshot=first_snapshot,
        request_id=first_request_id,
    )
    second = runtime.ask(
        paper_id=paper.id,
        question=AgentQuestion(
            content="Second",
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
        question=AgentQuestion(content="Original"),
        client=first_client,
        model_snapshot=_model_snapshot(),
        request_id=request_id,
    )
    replay_client = FakeAgentClient(
        turns=(AssertionError("duplicate model call"),),
    )

    replay = runtime.ask(
        paper_id=paper.id,
        question=AgentQuestion(content="Conflicting duplicate"),
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
            question=AgentQuestion(content="Retry me"),
            client=failing_client,
            model_snapshot=snapshot,
            request_id=request_id,
        )
    user = repository.get_agent_user_message_by_request(paper.id, request_id)
    assert user is not None

    recovered = runtime.ask(
        paper_id=paper.id,
        question=AgentQuestion(content="Retry me"),
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
            question=AgentQuestion(content="Retry me"),
            client=FakeAgentClient(turns=(VllmToolCallingError("failed"),)),
            model_snapshot=first_snapshot,
            request_id=request_id,
        )
    retry_client = FakeAgentClient()

    with pytest.raises(RuntimeError, match="new request_id"):
        runtime.ask(
            paper_id=paper.id,
            question=AgentQuestion(content="Retry me"),
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
            question=AgentQuestion(content="Concurrent"),
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
                    content=f"Failure {index}"
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
            question=AgentQuestion(content="Guard retry"),
            client=FakeAgentClient(),
            model_snapshot=snapshot,
            request_id=request_id,
        )
    assert "raw-guard-secret" not in str(caught.value)
    assert caught.value.__cause__ is None

    recovered = runtime.ask(
        paper_id=paper.id,
        question=AgentQuestion(content="Guard retry"),
        client=FakeAgentClient(),
        model_snapshot=snapshot,
        request_id=request_id,
    )

    assert recovered.user_message.request_id == request_id
    assert len(repository.get_conversation_messages(paper.id, recovered.conversation.id)) == 2


def test_chinese_overview_reads_source_before_generating_answer(repository):
    paper = _paper_with_stage1_document(repository)
    client = FakeAgentClient(final_answer=_grounded_markdown(citations=[paper.element_id]))
    turn = _runtime(repository, client).ask(paper_id=paper.id,
        question=AgentQuestion(content='这篇论文讲了什么'))
    assert turn.answer.status == 'grounded'
    messages = client.final_requests[0]['messages']
    assert any(
        message['role'] == 'system' and paper.element_id in message['content']
        for message in messages
    )
    # Pre-read excerpts must not be replayed as a tool turn the model never made.
    assert not any(message['role'] == 'tool' for message in messages)
    assert not any(message.get('tool_calls') for message in messages)


def test_tool_turn_replays_the_providers_reasoning_content(repository):
    """Thinking-mode providers reject a replayed tool turn without its reasoning."""
    paper = _paper_with_stage1_document(repository)
    client = FakeAgentClient(
        turns=(
            VllmToolTurn(
                content=None,
                tool_calls=(
                    _tool_call("search_paper", {"query": "router", "limit": 5}),
                ),
                reasoning_content="I should search the paper first.",
            ),
            VllmToolTurn(content=None, tool_calls=()),
        ),
        final_answer=_grounded_markdown(citations=[paper.element_id]),
    )

    _runtime(repository, client).ask(
        paper_id=paper.id,
        question=AgentQuestion(content="Explain it."),
    )

    replayed = client.final_requests[0]["messages"][2]
    assert replayed["role"] == "assistant"
    assert replayed["reasoning_content"] == "I should search the paper first."


def test_tool_turn_omits_reasoning_content_when_the_provider_sends_none(repository):
    """Providers without thinking mode must not receive an invented field."""
    paper = _paper_with_stage1_document(repository)
    client = FakeAgentClient(
        turns=(
            _tool_turn("search_paper", {"query": "router", "limit": 5}),
            VllmToolTurn(content=None, tool_calls=()),
        ),
        final_answer=_grounded_markdown(citations=[paper.element_id]),
    )

    _runtime(repository, client).ask(
        paper_id=paper.id,
        question=AgentQuestion(content="Explain it."),
    )

    replayed = client.final_requests[0]["messages"][2]
    assert replayed["role"] == "assistant"
    assert "reasoning_content" not in replayed


def test_runtime_sends_openai_tool_result_messages_with_returned_evidence_ids(repository):
    """Breaks if the final model cannot distinguish source IDs from navigation data."""
    paper = _paper_with_stage1_document(repository)
    client = FakeAgentClient(
        turns=(
            _tool_turn("search_paper", {"query": "router", "limit": 5}),
            VllmToolTurn(content=None, tool_calls=()),
        ),
        final_answer=_grounded_markdown(citations=[paper.element_id]),
    )

    _runtime(repository, client).ask(
        paper_id=paper.id,
        question=AgentQuestion(content="Explain it."),
    )

    final_messages = client.final_requests[0]["messages"]
    assistant_tool_call = final_messages[-3]
    tool_result = final_messages[-2]
    assert final_messages[-1]["role"] == "system"
    assert assistant_tool_call["role"] == "assistant"
    assert assistant_tool_call["tool_calls"][0]["function"]["name"] == "search_paper"
    assert tool_result["role"] == "tool"
    assert tool_result["tool_call_id"] == "call-1"
    result_payload = json.loads(tool_result["content"])
    assert result_payload["evidence_element_ids"] == [paper.element_id]
    assert result_payload["content"]["elements"][0]["id"] == paper.element_id


def test_final_answer_request_is_closed_with_a_wrap_up_instruction_after_tools(
    repository,
):
    """Breaks if the final request ends on tool results and tool-trained models keep tool-calling."""
    paper = _paper_with_stage1_document(repository)
    client = FakeAgentClient(
        turns=(
            _tool_turn("search_paper", {"query": "router", "limit": 5}),
            VllmToolTurn(content=None, tool_calls=()),
        ),
        final_answer=_grounded_markdown(citations=[paper.element_id]),
    )

    _runtime(repository, client).ask(
        paper_id=paper.id,
        question=AgentQuestion(content="Explain it."),
    )

    final_messages = client.final_requests[0]["messages"]
    assert final_messages[-1]["role"] == "system"
    assert "write the final answer now" in final_messages[-1]["content"]
    assert "Markdown" in final_messages[-1]["content"]
    assert final_messages[-2]["role"] == "tool"
    assert final_messages[-3]["role"] == "assistant"


def test_wrap_up_instruction_follows_budget_exhausted_tool_results(repository):
    """Breaks if the budget-exhausted final request ends on bare tool results."""
    paper = _paper_with_stage1_document(repository)
    turns = tuple(
        _tool_turn(
            "search_paper",
            {"query": "router", "limit": 5},
            call_id=f"call-{index}",
        )
        for index in range(6)
    )
    client = FakeAgentClient(
        turns=turns,
        final_answer=_grounded_markdown(citations=[paper.element_id]),
    )

    _runtime(repository, client).ask(
        paper_id=paper.id,
        question=AgentQuestion(content="Keep searching."),
    )

    final_messages = client.final_requests[0]["messages"]
    assert final_messages[-1]["role"] == "system"
    assert final_messages[-2]["role"] == "tool"


def test_final_answer_request_keeps_user_ending_when_no_tool_ran(repository):
    """The wrap-up instruction must not be appended without preceding tool results."""
    paper = _paper_with_stage1_document(repository)
    client = FakeAgentClient(
        final_answer=_grounded_markdown(citations=[paper.element_id])
    )

    _runtime(repository, client).ask(
        paper_id=paper.id,
        question=AgentQuestion(content="Question"),
    )

    final_messages = client.final_requests[0]["messages"]
    assert final_messages[-1] == {"role": "user", "content": "Question"}


def test_agent_injects_relevant_notes_and_returns_note_references(repository):
    """Breaks if note memory never reaches model context or response references."""
    paper = _paper_with_stage1_document(repository)
    annotation_repository = PaperAnnotationRepository(engine=repository.engine)
    note = annotation_repository.create_note(
        paper.id, Note(body="路由负载均衡损失"), request_id="note-a"
    )
    client = FakeAgentClient(
        turns=(
            _tool_turn("search_paper", {"query": "router", "limit": 5}),
            VllmToolTurn(content=None, tool_calls=()),
        ),
        final_answer=_grounded_markdown(citations=[paper.element_id]),
    )
    runtime = PaperAgentRuntime(
        repository=repository,
        tools=PaperToolRegistry(repository),
        guard=CitationGuard(),
        annotation_repository=annotation_repository,
        note_memory=NoteMemoryService(annotation_repository),
    )

    turn = runtime.ask(
        paper_id=paper.id,
        question=AgentQuestion(content="负载如何均衡？"),
        client=client,
        model_snapshot=_model_snapshot(),
        request_id="agent-a",
    )

    system_messages = [
        message["content"]
        for message in client.tool_requests[0]["messages"]
        if message["role"] == "system"
    ]
    assert any(f"note:{note.id}" in content for content in system_messages)
    assert turn.note_references[0].note_id == note.id
    assert turn.assistant_message.citation_element_ids
    assert repository.get_message_note_ids(
        paper.id, (turn.assistant_message.id,)
    )[turn.assistant_message.id] == (note.id,)


def test_selection_is_wrapped_in_input_and_persisted_as_message_anchor(repository):
    """Breaks if selection context mutates durable content or loses its anchor."""
    paper = _paper_with_stage1_document(repository)
    annotation_repository = PaperAnnotationRepository(engine=repository.engine)
    client = FakeAgentClient(
        turns=(
            _tool_turn("search_paper", {"query": "router", "limit": 5}),
            VllmToolTurn(content=None, tool_calls=()),
        ),
        final_answer=_grounded_markdown(citations=[paper.element_id]),
    )
    runtime = PaperAgentRuntime(
        repository=repository,
        tools=PaperToolRegistry(repository),
        guard=CitationGuard(),
        annotation_repository=annotation_repository,
    )
    selection = TextAnchorDraft(
        quote="router sends",
        page_number=1,
        rects=(TextAnchorRect(0, 0.1, 0.2, 0.8, 0.3),),
    )

    turn = runtime.ask(
        paper_id=paper.id,
        question=AgentQuestion(content="Explain it."),
        client=client,
        model_snapshot=_model_snapshot(),
        request_id="agent-selection",
        selection=selection,
    )

    final_messages = client.final_requests[0]["messages"]
    assert any(
        '<selected_text page="1">router sends</selected_text>' in message["content"]
        for message in final_messages
        if message["role"] == "user"
    )
    assert turn.user_message.content == "Explain it."
    anchor_ids = repository.get_message_anchor_ids(
        paper.id, (turn.user_message.id,)
    )[turn.user_message.id]
    assert anchor_ids


def test_notes_are_injected_without_changing_the_model_answer_contract(repository):
    """Notes remain context and do not become persisted citation links."""
    paper = _paper_with_stage1_document(repository)
    annotation_repository = PaperAnnotationRepository(engine=repository.engine)
    annotation_repository.create_note(
        paper.id, Note(body="路由负载均衡损失"), request_id="note-guard"
    )
    runtime = PaperAgentRuntime(
        repository=repository,
        tools=PaperToolRegistry(repository),
        guard=CitationGuard(),
        annotation_repository=annotation_repository,
        note_memory=NoteMemoryService(annotation_repository),
    )
    client = FakeAgentClient()

    turn = runtime.ask(
        paper_id=paper.id,
        question=AgentQuestion(content="负载如何均衡？"),
        client=client,
        model_snapshot=_model_snapshot(),
        request_id="agent-note-guard",
    )

    assert turn.answer.status == "insufficient_evidence"
    assert turn.note_references


def test_runtime_checks_paper_before_client_without_creating_chat_or_processing_rows(
    repository,
):
    """Breaks if an unknown paper creates state or is masked by missing configuration."""
    before = _chat_row_counts(repository)

    with pytest.raises(AgentRuntimePrerequisiteError):
        _runtime(repository, None).ask(
            paper_id="missing-paper",
            question=AgentQuestion(content="Question"),
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
                question=AgentQuestion(content="Question"),
            )

    assert _chat_row_counts(repository) == before


def test_runtime_rejects_a_conversation_owned_by_another_paper_without_writes(repository):
    """Breaks if a supplied conversation can cross the active-paper boundary."""
    first = _paper_with_stage1_document(repository, "first")
    second = _paper_with_stage1_document(repository, "second")
    other_conversation = repository.create_conversation(
        Conversation(paper_id=second.id)
    )
    before = _chat_row_counts(repository)

    with pytest.raises(AgentRuntimePrerequisiteError):
        _runtime(repository, FakeAgentClient()).ask(
            paper_id=first.id,
            question=AgentQuestion(
                content="Question",
                conversation_id=other_conversation.id,
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
            question=AgentQuestion(content="Question"),
        )

    assert _chat_row_counts(repository) == before


def test_runtime_builds_model_history_from_only_the_latest_six_durable_messages(
    repository,
):
    """Breaks if history is unbounded, unstable, or duplicates the current user turn."""
    paper = _paper_with_stage1_document(repository)
    conversation = repository.create_conversation(
        Conversation(paper_id=paper.id)
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


def test_system_prompt_describes_the_markdown_answer_contract(repository):
    """The model is told how to mark citations and background context."""
    paper = _paper_with_stage1_document(repository)
    client = FakeAgentClient()

    _runtime(repository, client).ask(
        paper_id=paper.id,
        question=AgentQuestion(content="Question"),
    )

    system_prompt = client.tool_requests[0]["messages"][0]["content"]
    assert "answer directly and naturally" in system_prompt.casefold()
    assert "Answer in Markdown" in system_prompt
    assert "Cite paper locations inline as [[element_id]]" in system_prompt
    assert "[[status:insufficient_evidence]]" in system_prompt
    assert "Do not return JSON" in system_prompt


def test_runtime_keeps_background_bearing_answer(repository):
    """Background prose is shown separately instead of replacing the answer."""
    paper = _paper_with_stage1_document(repository)
    client = FakeAgentClient(
        turns=(
            _tool_turn("search_paper", {"query": "router", "limit": 5}),
            VllmToolTurn(content=None, tool_calls=()),
        ),
        final_answer=_grounded_markdown(
            citations=[paper.element_id], background="General routing background."
        ),
    )

    turn = _runtime(repository, client).ask(
        paper_id=paper.id,
        question=AgentQuestion(content="Explain routing."),
    )

    assert turn.answer.status == "grounded"
    assert turn.assistant_message.content == (
        f"The paper uses a router. [[{paper.element_id}]]"
    )
    durable = repository.get_conversation_messages(paper.id, turn.conversation.id)
    assert durable[-1].background_explanation == "General routing background."
    assert durable[-1].content == turn.assistant_message.content


def test_runtime_keeps_answer_when_model_reuses_an_existing_paper_citation(repository):
    """An answer is not replaced just because its citation was not tool-returned."""
    paper = _paper_with_stage1_document(repository)
    conversation = repository.create_conversation(
        Conversation(paper_id=paper.id)
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
        final_answer=_grounded_markdown(
            citations=[paper.element_id], paper_answer="Unsupported reuse."
        )
    )

    turn = _runtime(repository, client).ask(
        paper_id=paper.id,
        question=AgentQuestion(
            content="Can I reuse that citation?",
            conversation_id=conversation.id,
        ),
    )

    assert turn.answer.status == "grounded"
    assert turn.assistant_message.content == (
        f"Unsupported reuse. [[{paper.element_id}]]"
    )
    assert turn.assistant_message.citation_element_ids == (paper.element_id,)


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
        final_answer=_grounded_markdown(citations=[paper.element_id]),
    )

    turn = _runtime(repository, client).ask(
        paper_id=paper.id,
        question=AgentQuestion(content="Keep searching."),
    )

    assert turn.answer.status == "grounded"
    assert client.tool_choices == ["auto", "auto", "auto", "auto", "auto", "auto"]
    assert len(client.remaining_turns) == 1
    assert len(client.final_requests) == 1


def test_runtime_rejects_duplicate_tool_ids_without_assistant_write(
    repository,
):
    """Ambiguous tool IDs must never be sent back as distinct tool results."""
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
                        call_id="call-1",
                    ),
                ),
            ),
        )
    )

    with pytest.raises(AgentRuntimeResponseError) as error:
        _runtime(repository, client).ask(
            paper_id=paper.id,
            question=AgentQuestion(content="Question"),
        )

    assert str(error.value) == "Reasoning model could not complete the request."
    assert "multi-call" not in str(error.value)
    assert _all_durable_rows(repository) == [("user", "Question")]


def test_runtime_reports_a_rejected_tool_argument_to_the_model(repository):
    """A model argument the tools reject must come back as feedback, not a 502."""
    paper = _paper_with_stage1_document(repository)
    client = FakeAgentClient(
        turns=(
            _tool_turn("read_section", {"section_id": "missing-section"}),
            VllmToolTurn(content=None, tool_calls=()),
        ),
        final_answer=_grounded_markdown(citations=[paper.element_id]),
    )

    turn = _runtime(repository, client).ask(
        paper_id=paper.id,
        question=AgentQuestion(content="Explain the section."),
    )

    assert turn.answer.status == "grounded"
    assert len(client.tool_requests) == 2
    followed_up = client.tool_requests[1]["messages"]
    rejected = followed_up[-1]
    assert rejected["role"] == "tool"
    assert rejected["tool_call_id"] == "call-1"
    payload = json.loads(rejected["content"])
    assert payload["content"] == {"error": "requested section is unavailable"}
    assert payload["evidence_element_ids"] == []
    assert _all_durable_rows(repository) == [
        ("user", "Explain the section."),
        ("assistant", turn.assistant_message.content),
    ]


def test_runtime_caps_a_wide_tool_batch_and_still_answers(repository):
    """A provider that batches more calls than the budget must not fail the turn."""
    paper = _paper_with_stage1_document(repository)
    client = FakeAgentClient(turns=(VllmToolTurn(content=None, tool_calls=tuple(
        _tool_call('search_paper', {'query':'router','limit':5}, call_id=str(i))
        for i in range(7)
    )),), final_answer=_grounded_markdown(citations=[paper.element_id]))

    turn = _runtime(repository, client).ask(paper_id=paper.id,
        question=AgentQuestion(content='Question'))

    assert turn.answer.status == 'grounded'
    assert len(client.tool_requests) == 1
    messages = client.final_requests[0]['messages']
    executed = [
        call['id'] for message in messages if message.get('tool_calls')
        for call in message['tool_calls']
    ]
    assert executed == [str(index) for index in range(6)]
    assert [message['tool_call_id'] for message in messages if message['role'] == 'tool'] == executed
    assert messages[-1]['role'] == 'system'


def test_runtime_executes_batched_read_tools_sequentially_with_matching_results(repository):
    paper = _paper_with_stage1_document(repository)
    client = FakeAgentClient(turns=(VllmToolTurn(content=None, tool_calls=(
        _tool_call('search_paper', {'query':'router','limit':5}, call_id='one'),
        _tool_call('search_paper', {'query':'router','limit':5}, call_id='two'),
    )),), final_answer=_grounded_markdown(citations=[paper.element_id]))
    turn = _runtime(repository, client).ask(paper_id=paper.id,
        question=AgentQuestion(content='Explain'))
    assert turn.answer.status == 'grounded'
    messages = client.final_requests[0]['messages']
    assert [c['id'] for c in messages[-4]['tool_calls']] == ['one','two']
    assert [m['tool_call_id'] for m in messages[-3:-1]] == ['one','two']
    assert messages[-1]['role'] == 'system'


def test_runtime_rejects_malformed_final_contract_without_persisting_model_content(
    repository,
):
    """Only malformed transport structure remains a service error."""
    paper = _paper_with_stage1_document(repository)
    client = FakeAgentClient(final_answer="   \n\n   ")

    with pytest.raises(AgentRuntimeResponseError):
        _runtime(repository, client).ask(
            paper_id=paper.id,
            question=AgentQuestion(content="Question"),
        )

    assert _all_durable_rows(repository) == [("user", "Question")]


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
            question=AgentQuestion(content="Question"),
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
            question=AgentQuestion(content="Question"),
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
            question=AgentQuestion(content="Question"),
        )

    assert str(error.value) == "Reasoning model could not complete the request."
    assert "raw-provider-tool-secret" not in str(error.value)
    assert error.value.__cause__ is None
    assert _all_durable_rows(repository) == [("user", "Question")]


def test_runtime_sanitizes_final_transport_failure_after_user_persistence(repository):
    """Breaks if a vLLM final-JSON failure leaks content or writes an assistant reply."""
    paper = _paper_with_stage1_document(repository)
    client = FakeAgentClient(
        final_answer=VllmToolCallingError("raw-provider-final-secret")
    )

    with pytest.raises(AgentRuntimeResponseError) as error:
        _runtime(repository, client).ask(
            paper_id=paper.id,
            question=AgentQuestion(content="Question"),
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


def test_stream_ask_yields_deltas_then_persists_exactly_one_answer(repository):
    """Breaks if a streamed answer is stored twice or loses its citation links."""
    paper = _paper_with_stage1_document(repository)
    answer = _grounded_markdown(citations=[paper.element_id])
    client = FakeAgentClient(
        turns=(
            _tool_turn("search_paper", {"query": "router", "limit": 5}),
            VllmToolTurn(content=None, tool_calls=()),
        ),
        stream_chunks=(answer[:12], answer[12:]),
    )

    events = _runtime(repository, client).stream_ask(
        paper_id=paper.id,
        question=AgentQuestion(content="Explain routing."),
    )

    assert [event.event for event in events] == [
        "started",
        "delta",
        "delta",
        "completed",
    ]
    assert "".join(event.text for event in events if event.event == "delta") == answer
    turn = events[-1].turn
    assert turn is not None
    assert turn.assistant_message.citation_element_ids == (paper.element_id,)
    assert turn.assistant_message.content == answer
    assert repository.get_conversation_messages(paper.id, turn.conversation.id) == (
        turn.user_message,
        turn.assistant_message,
    )
    assert len(client.final_requests) == 1


def test_stream_ask_persists_nothing_when_the_stream_breaks_mid_answer(repository):
    """Breaks if an interrupted stream leaves a half-written assistant message."""
    paper = _paper_with_stage1_document(repository)
    client = FakeAgentClient(
        stream_chunks=("partial answer", "never-sent"),
        stream_error_at=1,
    )
    request_id = "30000000-0000-0000-0000-000000000001"

    events = _runtime(repository, client).stream_ask(
        paper_id=paper.id,
        question=AgentQuestion(content="Explain routing."),
        request_id=request_id,
    )

    assert [event.event for event in events] == ["started", "delta", "error"]
    assert events[-1].code == "agent_failed"
    assert _all_durable_rows(repository) == [("user", "Explain routing.")]

    recovered = _runtime(repository, FakeAgentClient()).stream_ask(
        paper_id=paper.id,
        question=AgentQuestion(content="Explain routing."),
        request_id=request_id,
    )

    assert recovered[-1].event == "completed"
    assert len(_all_durable_rows(repository)) == 2


def test_stream_ask_does_not_expose_raw_provider_text_on_failure(repository):
    """Breaks if a provider message reaches the client through a stream error."""
    paper = _paper_with_stage1_document(repository)
    client = FakeAgentClient(final_error=VllmToolCallingError("raw-provider-secret"))

    events = _runtime(repository, client).stream_ask(
        paper_id=paper.id,
        question=AgentQuestion(content="Explain routing."),
    )

    assert [event.event for event in events] == ["started", "error"]
    assert events[-1].code == "agent_failed"
    assert "raw-provider-secret" not in repr(events)
    assert _all_durable_rows(repository) == [("user", "Explain routing.")]


def test_stream_ask_replays_a_completed_request_without_calling_the_model(repository):
    """Breaks if a duplicate stream re-runs the model instead of replaying the turn."""
    paper = _paper_with_stage1_document(repository)
    request_id = "30000000-0000-0000-0000-000000000002"
    first = _runtime(repository, FakeAgentClient()).ask(
        paper_id=paper.id,
        question=AgentQuestion(content="Explain routing."),
    )
    replay_client = FakeAgentClient(
        turns=(AssertionError("duplicate model call"),),
        final_error=AssertionError("duplicate final answer call"),
    )

    events = list(
        _runtime_core(repository).stream_ask(
            paper_id=paper.id,
            question=AgentQuestion(content="Conflicting duplicate"),
            client=replay_client,
            model_snapshot=_model_snapshot(
                profile_id="10000000-0000-0000-0000-000000000009"
            ),
            request_id=first.user_message.request_id,
        )
    )

    assert [event.event for event in events] == ["started", "completed"]
    assert events[-1].turn is not None
    assert events[-1].turn.assistant_message == first.assistant_message
    assert replay_client.tool_requests == []
    assert replay_client.final_requests == []
    assert len(repository.get_conversation_messages(paper.id, first.conversation.id)) == 2


def test_stream_ask_reports_prerequisites_as_a_stable_error_code(repository):
    """Breaks if an unknown paper is reported as a provider failure."""
    before = _chat_row_counts(repository)

    events = _runtime(repository, FakeAgentClient()).stream_ask(
        paper_id="missing-paper",
        question=AgentQuestion(content="Question"),
    )

    assert [event.event for event in events] == ["started", "error"]
    assert events[-1].code == "agent_not_ready"
    assert _chat_row_counts(repository) == before


def test_stream_ask_reports_a_missing_client_without_creating_chat_rows(repository):
    """Breaks if an unconfigured model leaves conversation state behind."""
    paper = _paper_with_stage1_document(repository)
    before = _chat_row_counts(repository)

    events = _runtime(repository, None).stream_ask(
        paper_id=paper.id,
        question=AgentQuestion(content="Question"),
    )

    assert [event.event for event in events] == ["started", "error"]
    assert events[-1].code == "model_unavailable"
    assert _chat_row_counts(repository) == before


def test_stream_ask_stops_an_over_long_answer_without_persisting_it(repository):
    """Breaks if a runaway generation is streamed and stored without a bound."""
    paper = _paper_with_stage1_document(repository)
    client = FakeAgentClient(stream_chunks=("x" * 64_001,))

    events = _runtime(repository, client).stream_ask(
        paper_id=paper.id,
        question=AgentQuestion(content="Question"),
    )

    assert [event.event for event in events] == ["started", "error"]
    assert events[-1].code == "answer_too_long"
    assert _all_durable_rows(repository) == [("user", "Question")]


def test_stream_ask_keeps_the_directive_status_and_background_split(repository):
    """Breaks if streamed answers bypass the Markdown answer contract."""
    paper = _paper_with_stage1_document(repository)
    answer = (
        "[[status:insufficient_evidence]]\n\n"
        "论文没有给出该结论。\n\n---\n\n上下文说明。"
    )
    events = _runtime(repository, FakeAgentClient(final_answer=answer)).stream_ask(
        paper_id=paper.id,
        question=AgentQuestion(content="Question"),
    )

    turn = events[-1].turn
    assert turn is not None
    assert turn.answer.status == "insufficient_evidence"
    assert turn.answer.background_explanation == "上下文说明。"
    assert turn.assistant_message.content == "论文没有给出该结论。"
    assert turn.assistant_message.background_explanation == "上下文说明。"
