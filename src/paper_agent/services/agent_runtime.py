"""Bounded, citation-gated orchestration for durable paper conversations."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
import json
from threading import Lock

from paper_agent.domain import (
    AgentMessageRole,
    AgentMode,
    Conversation,
    ConversationMessage,
    ProcessingStatus,
)
from paper_agent.annotation_storage import PaperAnnotationRepository
from paper_agent.annotations import NoteType, TextAnchorDraft
from paper_agent.models import VllmToolCall, VllmToolCallingClient, VllmToolTurn
from paper_agent.model_profiles import ModelSnapshot
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
_REQUEST_CONFLICT_ERROR = (
    "Request state conflicts with its stored retry. Use a new request_id."
)
from paper_agent.services.note_memory import (
    NoteMemoryContext,
    NoteMemoryReference,
    NoteMemoryService,
)


class AgentRuntimeUnavailableError(RuntimeError):
    """Raised when the configured reasoning service cannot be used."""


class AgentRuntimePrerequisiteError(RuntimeError):
    """Raised when paper or conversation prerequisites are not satisfied."""


class AgentRuntimeResponseError(RuntimeError):
    """Raised when model or tool processing cannot safely complete a turn."""


class AgentRuntimeConflictError(RuntimeError):
    """Raised when a user-only retry conflicts with its immutable request state."""


class AgentRuntimeInvalidSelectionError(RuntimeError):
    """Raised when a supplied text selection cannot belong to the paper."""


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
    note_references: tuple[NoteMemoryReference, ...] = ()


@dataclass
class _RequestLockEntry:
    lock: Lock = field(default_factory=Lock)
    references: int = 0


class PaperAgentRuntime:
    def __init__(
        self,
        *,
        repository: PaperRepository,
        tools: PaperToolRegistry,
        guard: CitationGuard,
        annotation_repository: PaperAnnotationRepository | None = None,
        note_memory: NoteMemoryService | None = None,
    ) -> None:
        self.repository = repository
        self.tools = tools
        self.guard = guard
        self.annotation_repository = annotation_repository
        self.note_memory = note_memory
        self._request_locks: dict[tuple[str, str], _RequestLockEntry] = {}
        self._request_locks_guard = Lock()

    def ask(
        self,
        *,
        paper_id: str,
        question: AgentQuestion,
        client: VllmToolCallingClient,
        model_snapshot: ModelSnapshot,
        request_id: str,
        selection: TextAnchorDraft | None = None,
    ) -> AgentTurn:
        with self._request_lock(paper_id, request_id):
            existing = self.repository.get_agent_turn_by_request(
                paper_id, request_id
            )
            if existing is not None:
                return self.turn_from_messages(
                    paper_id=paper_id,
                    messages=existing,
                    note_references=self._note_references_for_message(
                        paper_id, existing[1]
                    ),
                )

            partial_user = self.repository.get_agent_user_message_by_request(
                paper_id, request_id
            )
            if partial_user is not None:
                self._require_matching_partial_retry(
                    partial_user,
                    question=question,
                    model_snapshot=model_snapshot,
                )
                conversation_id = partial_user.conversation_id
            else:
                conversation_id = question.conversation_id

            note_context = (
                self.note_memory.retrieve(
                    paper_id, question.content, selection
                )
                if self.note_memory is not None
                else NoteMemoryContext("", ())
            )
            selection_anchor = None
            if selection is not None and self.annotation_repository is not None:
                try:
                    selection_anchor = self.annotation_repository.create_anchor(
                        paper_id, selection
                    )
                except Exception as error:
                    raise AgentRuntimeInvalidSelectionError(
                        "Selected text is invalid."
                    ) from error

            conversation = self._preflight(
                paper_id=paper_id,
                mode=question.mode,
                conversation_id=conversation_id,
            )
            if client is None:
                raise AgentRuntimeUnavailableError(_UNAVAILABLE_ERROR)
            if conversation is None:
                conversation = self.repository.create_conversation(
                    Conversation(paper_id=paper_id, mode=question.mode)
                )

            if partial_user is None:
                user_message = self.repository.append_conversation_message(
                    ConversationMessage(
                        conversation_id=conversation.id,
                        paper_id=paper_id,
                        role=AgentMessageRole.user,
                        content=question.content,
                        model_profile_id=model_snapshot.profile_id,
                        model_snapshot=model_snapshot,
                        request_id=request_id,
                    )
                )
            else:
                user_message = partial_user

            if selection_anchor is not None:
                self.repository.link_message_anchor(
                    paper_id, user_message.id, selection_anchor.id
                )

            messages = self._model_history(
                paper_id=paper_id,
                conversation=conversation,
                current_user_message_id=user_message.id,
                selection=selection,
                note_context=note_context,
            )
            allowed_evidence_ids: set[str] = set()
            try:
                tool_definitions = self.tools.definitions()
            except Exception:
                raise AgentRuntimeResponseError(_RESPONSE_ERROR) from None

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
                    allowed_evidence_ids.update(execution.evidence_element_ids)
                    messages.extend(tool_result_messages)
                except Exception:
                    raise AgentRuntimeResponseError(_RESPONSE_ERROR) from None

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
            except Exception:
                raise AgentRuntimeResponseError(_RESPONSE_ERROR) from None

            assistant_message = self.repository.append_conversation_message(
                ConversationMessage(
                    conversation_id=conversation.id,
                    paper_id=paper_id,
                    role=AgentMessageRole.assistant,
                    content=answer.paper_answer,
                    citation_element_ids=answer.citation_element_ids,
                    model_profile_id=model_snapshot.profile_id,
                    model_snapshot=model_snapshot,
                    request_id=request_id,
                    background_explanation=answer.background_explanation,
                )
            )
            self.repository.link_message_notes(
                paper_id,
                assistant_message.id,
                tuple(
                    reference.note_id
                    for reference in note_context.references
                ),
            )
            return AgentTurn(
                conversation=conversation,
                user_message=user_message,
                assistant_message=assistant_message,
                answer=answer,
                note_references=note_context.references,
            )

    def turn_from_messages(
        self,
        *,
        paper_id: str,
        messages: tuple[ConversationMessage, ConversationMessage],
        note_references: tuple[NoteMemoryReference, ...] = (),
    ) -> AgentTurn:
        user_message, assistant_message = messages
        conversation = self.repository.get_conversation(
            paper_id, user_message.conversation_id
        )
        if conversation is None:
            raise AgentRuntimePrerequisiteError(_PREREQUISITE_ERROR)
        citations = assistant_message.citation_element_ids
        answer = CitationValidatedAnswer(
            status="grounded" if citations else "insufficient_evidence",
            paper_answer=assistant_message.content,
            citation_element_ids=citations,
            background_explanation=assistant_message.background_explanation,
        )
        return AgentTurn(
            conversation=conversation,
            user_message=user_message,
            assistant_message=assistant_message,
            answer=answer,
            note_references=note_references,
        )

    def validate_tool_calling(
        self, *, client: VllmToolCallingClient | None
    ) -> None:
        if client is None:
            raise AgentRuntimeUnavailableError(_UNAVAILABLE_ERROR)
        try:
            client.validate_tool_calling()
        except Exception:
            raise AgentRuntimeUnavailableError(_UNAVAILABLE_ERROR) from None

    def _preflight(
        self,
        *,
        paper_id: str,
        mode: AgentMode,
        conversation_id: str | None,
    ) -> Conversation | None:
        if self.repository.get_paper(paper_id) is None:
            raise AgentRuntimePrerequisiteError(_PREREQUISITE_ERROR)
        if (
            self.repository.get_latest_stage_status(paper_id, "stage1")
            is not ProcessingStatus.completed
        ):
            raise AgentRuntimePrerequisiteError(_PREREQUISITE_ERROR)

        conversation = None
        if conversation_id is not None:
            conversation = self.repository.get_conversation(
                paper_id, conversation_id
            )
            if conversation is None or conversation.mode is not mode:
                raise AgentRuntimePrerequisiteError(_PREREQUISITE_ERROR)
        return conversation

    @contextmanager
    def _request_lock(self, paper_id: str, request_id: str) -> Iterator[None]:
        key = (paper_id, request_id)
        with self._request_locks_guard:
            entry = self._request_locks.setdefault(key, _RequestLockEntry())
            entry.references += 1
        try:
            with entry.lock:
                yield
        finally:
            with self._request_locks_guard:
                entry.references -= 1
                if (
                    entry.references == 0
                    and self._request_locks.get(key) is entry
                ):
                    del self._request_locks[key]

    @staticmethod
    def _require_matching_partial_retry(
        user_message: ConversationMessage,
        *,
        question: AgentQuestion,
        model_snapshot: ModelSnapshot,
    ) -> None:
        if (
            user_message.content != question.content
            or (
                question.conversation_id is not None
                and question.conversation_id != user_message.conversation_id
            )
            or user_message.model_profile_id != model_snapshot.profile_id
            or user_message.model_snapshot != model_snapshot
        ):
            raise AgentRuntimeConflictError(_REQUEST_CONFLICT_ERROR)

    def _model_history(
        self,
        *,
        paper_id: str,
        conversation: Conversation,
        current_user_message_id: str,
        selection: TextAnchorDraft | None = None,
        note_context: NoteMemoryContext | None = None,
    ) -> list[dict[str, object]]:
        durable_messages = self.repository.get_conversation_messages(
            paper_id,
            conversation.id,
            limit=HISTORY_MESSAGE_LIMIT,
        )
        messages: list[dict[str, object]] = [
            {"role": "system", "content": _system_prompt(conversation.mode)},
        ]
        if note_context is not None and note_context.prompt_block:
            messages.append(
                {"role": "system", "content": note_context.prompt_block}
            )
        for message in durable_messages:
            content = message.content
            if message.id == current_user_message_id and selection is not None:
                content = (
                    f'<selected_text page="{selection.page_number}">'
                    f"{selection.quote}</selected_text>\n{message.content}"
                )
            messages.append({"role": message.role.value, "content": content})
        return messages

    def _note_references_for_message(
        self, paper_id: str, message: ConversationMessage
    ) -> tuple[NoteMemoryReference, ...]:
        note_ids = self.repository.get_message_note_ids(
            paper_id, (message.id,)
        ).get(message.id, ())
        if self.annotation_repository is None:
            return tuple(
                NoteMemoryReference(note_id, NoteType.manual, None, False)
                for note_id in note_ids
            )
        anchors = {
            anchor.id: anchor
            for anchor in self.annotation_repository.list_anchors(paper_id)
        }
        references: list[NoteMemoryReference] = []
        for note_id in note_ids:
            try:
                note = self.annotation_repository.get_note(paper_id, note_id)
                page_number = note.page_number
                if page_number is None and note.anchor_ids:
                    anchor = anchors.get(note.anchor_ids[0])
                    if anchor is not None:
                        page_number = anchor.page_number
                references.append(
                    NoteMemoryReference(
                        note.id, note.note_type, page_number, True
                    )
                )
            except Exception:
                references.append(
                    NoteMemoryReference(note_id, NoteType.manual, None, False)
                )
        return tuple(references)


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
