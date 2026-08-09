"""Bounded, citation-gated orchestration for durable paper conversations."""

from dataclasses import dataclass
import json

from paper_agent.domain import (
    AgentMessageRole,
    AgentMode,
    Conversation,
    ConversationMessage,
    ProcessingStatus,
)
from paper_agent.models import VllmToolCall, VllmToolCallingClient, VllmToolTurn
from paper_agent.services.agent_tools import PaperToolRegistry, ToolExecution
from paper_agent.services.citation_guard import (
    CitationGuard,
    CitationGuardError,
    CitationValidatedAnswer,
)
from paper_agent.storage import PaperRepository


MAX_TOOL_TURNS = 6
HISTORY_MESSAGE_LIMIT = 6
_FINAL_SCHEMA_NAME = "paper_agent_final_answer"
_PREREQUISITE_ERROR = "Paper agent prerequisites are not complete."
_UNAVAILABLE_ERROR = "Reasoning model is not configured."
_RESPONSE_ERROR = "Reasoning model could not complete the request."


class AgentRuntimeUnavailableError(RuntimeError):
    """Raised when the configured reasoning service cannot be used."""


class AgentRuntimePrerequisiteError(RuntimeError):
    """Raised when paper or conversation prerequisites are not satisfied."""


class AgentRuntimeResponseError(RuntimeError):
    """Raised when model or tool processing cannot safely complete a turn."""


@dataclass(frozen=True)
class AgentQuestion:
    content: str
    mode: AgentMode
    conversation_id: str | None = None


@dataclass(frozen=True)
class AgentTurn:
    conversation: Conversation
    user_message: ConversationMessage
    assistant_message: ConversationMessage
    answer: CitationValidatedAnswer


class PaperAgentRuntime:
    def __init__(
        self,
        *,
        repository: PaperRepository,
        tools: PaperToolRegistry,
        client: VllmToolCallingClient | None,
        guard: CitationGuard,
    ) -> None:
        self.repository = repository
        self.tools = tools
        self.client = client
        self.guard = guard

    def ask(self, *, paper_id: str, question: AgentQuestion) -> AgentTurn:
        conversation, client = self._preflight(paper_id=paper_id, question=question)
        if conversation is None:
            conversation = self.repository.create_conversation(
                Conversation(paper_id=paper_id, mode=question.mode)
            )

        user_message = self.repository.append_conversation_message(
            ConversationMessage(
                conversation_id=conversation.id,
                paper_id=paper_id,
                role=AgentMessageRole.user,
                content=question.content,
            )
        )
        messages = self._model_history(
            paper_id=paper_id,
            conversation=conversation,
        )
        allowed_evidence_ids: set[str] = set()
        tool_definitions = self.tools.definitions()

        for tool_turn_index in range(MAX_TOOL_TURNS):
            try:
                turn = client.request_tool_turn(
                    messages=messages,
                    tools=tool_definitions,
                    tool_choice="required" if tool_turn_index == 0 else "auto",
                )
                if len(turn.tool_calls) > 1:
                    raise ValueError("multiple tool calls are not allowed")
                if not turn.tool_calls:
                    break
                call = turn.tool_calls[0]
                execution = self.tools.execute(
                    paper_id=paper_id,
                    name=call.name,
                    arguments=call.arguments,
                )
                tool_result_messages = _tool_result_messages(turn, call, execution)
            except Exception:
                raise AgentRuntimeResponseError(_RESPONSE_ERROR) from None

            allowed_evidence_ids.update(execution.evidence_element_ids)
            messages.extend(tool_result_messages)

        try:
            payload = client.generate_json_messages(
                messages=messages,
                schema_name=_FINAL_SCHEMA_NAME,
                schema=self.guard.output_schema(),
            )
        except Exception:
            raise AgentRuntimeResponseError(_RESPONSE_ERROR) from None

        allowed_evidence = frozenset(allowed_evidence_ids)
        try:
            answer = self.guard.validate(
                payload,
                mode=question.mode,
                allowed_evidence_ids=allowed_evidence,
            )
        except CitationGuardError:
            answer = _canonical_insufficient_answer(
                self.guard,
                mode=question.mode,
                allowed_evidence_ids=allowed_evidence,
            )

        assistant_message = self.repository.append_conversation_message(
            ConversationMessage(
                conversation_id=conversation.id,
                paper_id=paper_id,
                role=AgentMessageRole.assistant,
                content=answer.paper_answer,
                citation_element_ids=answer.citation_element_ids,
            )
        )
        return AgentTurn(
            conversation=conversation,
            user_message=user_message,
            assistant_message=assistant_message,
            answer=answer,
        )

    def validate_tool_calling(self) -> None:
        if self.client is None:
            raise AgentRuntimeUnavailableError(_UNAVAILABLE_ERROR)
        try:
            self.client.validate_tool_calling()
        except Exception:
            raise AgentRuntimeUnavailableError(_UNAVAILABLE_ERROR) from None

    def _preflight(
        self, *, paper_id: str, question: AgentQuestion
    ) -> tuple[Conversation | None, VllmToolCallingClient]:
        if self.repository.get_paper(paper_id) is None:
            raise AgentRuntimePrerequisiteError(_PREREQUISITE_ERROR)
        if (
            self.repository.get_latest_stage_status(paper_id, "stage1")
            is not ProcessingStatus.completed
        ):
            raise AgentRuntimePrerequisiteError(_PREREQUISITE_ERROR)

        conversation = None
        if question.conversation_id is not None:
            conversation = self.repository.get_conversation(
                paper_id, question.conversation_id
            )
            if conversation is None or conversation.mode is not question.mode:
                raise AgentRuntimePrerequisiteError(_PREREQUISITE_ERROR)

        if self.client is None:
            raise AgentRuntimeUnavailableError(_UNAVAILABLE_ERROR)
        return conversation, self.client

    def _model_history(
        self, *, paper_id: str, conversation: Conversation
    ) -> list[dict[str, object]]:
        durable_messages = self.repository.get_conversation_messages(
            paper_id, conversation.id
        )[-HISTORY_MESSAGE_LIMIT:]
        return [
            {"role": "system", "content": _system_prompt(conversation.mode)},
            *(
                {
                    "role": message.role.value,
                    "content": message.content,
                }
                for message in durable_messages
            ),
        ]


def _system_prompt(mode: AgentMode) -> str:
    prompt = (
        "Use the provided paper and graph tools to answer the user's question. "
        "Graph data is for navigation only; graph node and edge IDs are not citations. "
        "Only source-element IDs returned by tools during this request may appear in "
        "citation_element_ids. If returned evidence does not support a paper claim, "
        "use status insufficient_evidence. "
    )
    if mode is AgentMode.external_knowledge:
        return (
            prompt
            + "External background belongs only in the separate background_explanation "
            "field and must not be mixed into paper_answer."
        )
    return prompt + "In paper-only mode, background_explanation must be null."


def _tool_result_messages(
    turn: VllmToolTurn,
    call: VllmToolCall,
    execution: ToolExecution,
) -> tuple[dict[str, object], dict[str, object]]:
    assistant_message: dict[str, object] = {
        "role": "assistant",
        "content": turn.content,
        "tool_calls": [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(
                        call.arguments,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                },
            }
        ],
    }
    tool_message: dict[str, object] = {
        "role": "tool",
        "tool_call_id": call.id,
        "name": execution.name,
        "content": json.dumps(
            {
                "content": execution.content,
                "evidence_element_ids": execution.evidence_element_ids,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ),
    }
    return assistant_message, tool_message


def _canonical_insufficient_answer(
    guard: CitationGuard,
    *,
    mode: AgentMode,
    allowed_evidence_ids: frozenset[str],
) -> CitationValidatedAnswer:
    return guard.validate(
        {
            "status": "insufficient_evidence",
            "paper_answer": "",
            "citation_element_ids": [],
            "background_explanation": None,
        },
        mode=mode,
        allowed_evidence_ids=allowed_evidence_ids,
    )
