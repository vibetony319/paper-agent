"""Compaction behavior of PaperAgentRuntime under a configured ContextBudget."""

from pathlib import Path

import pytest

from paper_agent.domain import DocumentElement, BoundingBox
from paper_agent.models import VllmToolCallingError, VllmToolTurn
from paper_agent.services.agent_runtime import AgentQuestion, _COMPACTION_INSTRUCTION
from paper_agent.services.context_budget import ContextBudget
from paper_agent.storage import PaperRepository

from test_agent_runtime import (
    _HISTORY_CHUNK,
    _all_durable_rows,
    _conversation_with_history,
    _grounded_markdown,
    _paper_with_stage1_document,
    _runtime,
    _tool_turn,
    FakeAgentClient,
)


@pytest.fixture
def repository(tmp_path: Path) -> PaperRepository:
    return PaperRepository(f"sqlite:///{tmp_path / 'paper-agent.db'}")


_SUMMARY_TEXT = "已确认论文主题与证据元素 ID。"


def _summary_message(text: str = _SUMMARY_TEXT) -> dict[str, object]:
    return {
        "role": "system",
        "content": "以下是此前对话历史的压缩摘要，用于延续上下文：\n" + text,
    }


def test_context_compaction_folds_history_into_a_summary_request(repository):
    """A request over the compaction threshold folds history into a summary."""
    paper = _paper_with_stage1_document(repository)
    conversation = _conversation_with_history(repository, paper.id, count=7)
    client = FakeAgentClient(
        final_answer=_grounded_markdown(citations=[paper.element_id]),
        summary_answer=_SUMMARY_TEXT,
    )

    turn = _runtime(repository, client).ask(
        paper_id=paper.id,
        question=AgentQuestion(
            content="Summarize the paper.", conversation_id=conversation.id
        ),
        context_budget=ContextBudget(context_length=2_000, max_output_tokens=200),
    )

    assert turn.answer.status == "grounded"
    # The summary call sees only the foldable history, framed by the
    # compaction instruction rather than the answer-contract system prompt.
    summary_messages = client.summary_requests[0]["messages"]
    assert summary_messages[0] == {"role": "system", "content": _COMPACTION_INSTRUCTION}
    assert [message["content"] for message in summary_messages[1:]] == [
        f"durable-{index} {_HISTORY_CHUNK}" for index in range(2, 7)
    ]
    # The final answer request carries the summary plus the current user
    # turn instead of the folded history.
    final_messages = client.final_requests[-1]["messages"]
    assert _summary_message() in final_messages
    assert not any("durable-2" in str(message) for message in final_messages)
    assert {"role": "user", "content": "Summarize the paper."} in final_messages
    # Compaction is transient: the seeded history survives untouched and
    # the summary is never persisted.
    durable = _all_durable_rows(repository)
    assert durable[-2:] == [
        ("user", "Summarize the paper."),
        ("assistant", turn.assistant_message.content),
    ]
    assert not any("压缩摘要" in content for _, content in durable)


def test_context_compaction_leaves_requests_under_the_threshold_alone(repository):
    paper = _paper_with_stage1_document(repository)
    conversation = _conversation_with_history(repository, paper.id, count=7)
    client = FakeAgentClient(
        final_answer=_grounded_markdown(citations=[paper.element_id])
    )

    _runtime(repository, client).ask(
        paper_id=paper.id,
        question=AgentQuestion(
            content="Summarize the paper.", conversation_id=conversation.id
        ),
        context_budget=ContextBudget(
            context_length=1_000_000, max_output_tokens=4_096
        ),
    )

    assert client.summary_requests == []
    final_messages = client.final_requests[-1]["messages"]
    assert {"role": "user", "content": f"durable-2 {_HISTORY_CHUNK}"} in final_messages


def test_unconfigured_context_budget_keeps_the_full_history(repository):
    """A profile without context_length must never compact or truncate."""
    paper = _paper_with_stage1_document(repository)
    conversation = _conversation_with_history(repository, paper.id, count=7)
    client = FakeAgentClient(
        final_answer=_grounded_markdown(citations=[paper.element_id])
    )

    _runtime(repository, client).ask(
        paper_id=paper.id,
        question=AgentQuestion(
            content="Summarize the paper.", conversation_id=conversation.id
        ),
    )

    assert client.summary_requests == []
    final_messages = client.final_requests[-1]["messages"]
    assert {"role": "user", "content": f"durable-2 {_HISTORY_CHUNK}"} in final_messages


def test_failed_compaction_summary_degrades_to_dropping_oldest_exchanges(repository):
    """A failed summary must hard-truncate instead of failing the turn."""
    paper = _paper_with_stage1_document(repository)
    conversation = _conversation_with_history(repository, paper.id, count=7)
    client = FakeAgentClient(
        final_answer=_grounded_markdown(citations=[paper.element_id]),
        summary_error=VllmToolCallingError("raw-summary-secret"),
    )

    turn = _runtime(repository, client).ask(
        paper_id=paper.id,
        question=AgentQuestion(
            content="Summarize the paper.", conversation_id=conversation.id
        ),
        context_budget=ContextBudget(context_length=2_000, max_output_tokens=200),
    )

    assert turn.answer.status == "grounded"
    assert client.summary_requests
    final_messages = client.final_requests[-1]["messages"]
    # The oldest exchanges are dropped to fit under the hard limit while
    # the newest history survives without any summary.
    assert not any(
        f"durable-{index} " in str(message)
        for index in range(2, 6)
        for message in final_messages
    )
    assert {"role": "user", "content": f"durable-6 {_HISTORY_CHUNK}"} in final_messages
    assert {"role": "user", "content": "Summarize the paper."} in final_messages
    assert not any("压缩摘要" in str(message) for message in final_messages)
    durable = _all_durable_rows(repository)
    assert durable[-2:] == [
        ("user", "Summarize the paper."),
        ("assistant", turn.assistant_message.content),
    ]
    assert not any("压缩摘要" in content for _, content in durable)


def test_mid_tool_loop_compaction_preserves_the_trailing_tool_pair(repository):
    """Compaction inside the tool loop keeps the assistant(tool_calls)+tool pair."""
    paper = _paper_with_stage1_document(repository)
    repository.save_stage1_document(
        paper.id,
        (),
        (
            DocumentElement(
                id="large-element",
                kind="paragraph",
                text="router token filler " * 300,
                page_number=1,
                bbox=BoundingBox(0.1, 0.2, 0.9, 0.3),
                section_id=None,
            ),
        ),
    )
    conversation = _conversation_with_history(repository, paper.id, count=7)
    client = FakeAgentClient(
        turns=(
            _tool_turn("search_paper", {"query": "router", "limit": 5}),
            VllmToolTurn(content=None, tool_calls=()),
        ),
        final_answer=_grounded_markdown(citations=["large-element"]),
        summary_answer="已确认路由证据。",
    )

    turn = _runtime(repository, client).ask(
        paper_id=paper.id,
        question=AgentQuestion(
            content="Explain routing.", conversation_id=conversation.id
        ),
        context_budget=ContextBudget(context_length=4_400, max_output_tokens=400),
    )

    assert turn.answer.status == "grounded"
    # The summary folds only the durable history, never the current turn's
    # tool exchange.
    summary_messages = client.summary_requests[0]["messages"]
    assert [message["content"] for message in summary_messages[1:]] == [
        f"durable-{index} {_HISTORY_CHUNK}" for index in range(2, 7)
    ]
    compacted = client.tool_requests[1]["messages"]
    assert not any("durable-2" in str(message) for message in compacted)
    assert _summary_message("已确认路由证据。") in compacted
    assert {"role": "user", "content": "Explain routing."} in compacted
    # The OpenAI API pairing rule survives: the tool result is still
    # preceded by the assistant message that requested it.
    assert compacted[-1]["role"] == "tool"
    assert compacted[-1]["tool_call_id"] == "call-1"
    assert compacted[-2]["role"] == "assistant"
    assert compacted[-2]["tool_calls"][0]["function"]["name"] == "search_paper"


def test_stream_ask_compacts_before_streaming_the_final_answer(repository):
    paper = _paper_with_stage1_document(repository)
    conversation = _conversation_with_history(repository, paper.id, count=7)
    answer = _grounded_markdown(citations=[paper.element_id])
    client = FakeAgentClient(
        final_answer=answer,
        stream_chunks=(answer[:12], answer[12:]),
        summary_answer=_SUMMARY_TEXT,
    )

    events = _runtime(repository, client).stream_ask(
        paper_id=paper.id,
        question=AgentQuestion(
            content="Summarize the paper.", conversation_id=conversation.id
        ),
        context_budget=ContextBudget(context_length=2_000, max_output_tokens=200),
    )

    assert [event.event for event in events] == [
        "started",
        "delta",
        "delta",
        "completed",
    ]
    streamed_messages = client.final_requests[0]["messages"]
    assert not any("durable-2" in str(message) for message in streamed_messages)
    assert _summary_message() in streamed_messages
