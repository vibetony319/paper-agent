"""Bounded orchestration for durable paper conversations."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
import json
import logging
from threading import Lock
from typing import Literal

from paper_agent.domain import (
    AgentMessageRole,
    Conversation,
    ConversationMessage,
    ProcessingStatus,
)
from paper_agent.annotation_storage import PaperAnnotationRepository
from paper_agent.annotations import NoteType, TextAnchorDraft
from paper_agent.models import VllmToolCall, VllmToolCallingClient, VllmToolTurn
from paper_agent.model_profiles import ModelSnapshot
from paper_agent.services.agent_tools import AgentToolError, PaperToolRegistry
from paper_agent.services.answer_format import ANSWER_FORMAT_INSTRUCTIONS
from paper_agent.services.citation_guard import (
    CitationGuard,
    CitationGuardError,
    CitationValidatedAnswer,
)
from paper_agent.storage import PaperRepository


MAX_TOOL_TURNS = 6
HISTORY_MESSAGE_LIMIT = 6
MAX_ANSWER_CHARS = 64_000
# Some tool-trained models keep emitting tool-call text when the final answer
# request follows tool results directly, which fails the answer parse.
_FINAL_ANSWER_INSTRUCTION = (
    "Tool calling is complete. Based on the evidence above, write the final "
    "answer now; do not call any more tools. " + ANSWER_FORMAT_INSTRUCTIONS
)
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
    conversation_id: str | None = None


@dataclass(frozen=True)
class AgentTurn:
    conversation: Conversation
    user_message: ConversationMessage
    assistant_message: ConversationMessage
    answer: CitationValidatedAnswer
    note_references: tuple[NoteMemoryReference, ...] = ()


@dataclass(frozen=True)
class AgentStreamEvent:
    """One step of a streamed answer.

    Errors carry a stable ``code`` so the transport can decide the user-facing
    wording instead of leaking provider text.
    """

    event: Literal["started", "delta", "completed", "error"]
    text: str = ""
    turn: AgentTurn | None = None
    code: str | None = None


@dataclass(frozen=True)
class _PreparedTurn:
    """A user turn that is persisted and ready for its final answer."""

    conversation: Conversation
    user_message: ConversationMessage
    messages: list[dict[str, object]]
    allowed_evidence_ids: set[str]
    note_context: NoteMemoryContext
    model_snapshot: ModelSnapshot
    request_id: str


@dataclass
class _RequestLockEntry:
    lock: Lock = field(default_factory=Lock)
    references: int = 0


logger = logging.getLogger(__name__)


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
            replay = self._replay_turn(paper_id, request_id)
            if replay is not None:
                return replay

            prepared = self._prepare_turn(
                paper_id=paper_id,
                question=question,
                client=client,
                model_snapshot=model_snapshot,
                request_id=request_id,
                selection=selection,
            )
            try:
                text = client.complete_markdown_messages(
                    messages=prepared.messages
                )
            except Exception:
                raise AgentRuntimeResponseError(_RESPONSE_ERROR) from None
            return self._finalize(prepared, text)

    def stream_ask(
        self,
        *,
        paper_id: str,
        question: AgentQuestion,
        client: VllmToolCallingClient,
        model_snapshot: ModelSnapshot,
        request_id: str,
        selection: TextAnchorDraft | None = None,
    ) -> Iterator[AgentStreamEvent]:
        """Yield the answer as it streams, then persist it exactly once.

        Nothing is stored until the whole answer arrives, so a stream that
        fails or is abandoned never leaves a half-written assistant message.
        """
        with self._request_lock(paper_id, request_id):
            try:
                replay = self._replay_turn(paper_id, request_id)
            except Exception:
                yield AgentStreamEvent("error", code="agent_failed")
                return
            if replay is not None:
                yield AgentStreamEvent("started")
                yield AgentStreamEvent("completed", turn=replay)
                return

            yield AgentStreamEvent("started")
            try:
                prepared = self._prepare_turn(
                    paper_id=paper_id,
                    question=question,
                    client=client,
                    model_snapshot=model_snapshot,
                    request_id=request_id,
                    selection=selection,
                )
            except AgentRuntimeConflictError:
                yield AgentStreamEvent("error", code="request_conflict")
                return
            except AgentRuntimeInvalidSelectionError:
                yield AgentStreamEvent("error", code="invalid_selection")
                return
            except AgentRuntimePrerequisiteError:
                yield AgentStreamEvent("error", code="agent_not_ready")
                return
            except AgentRuntimeUnavailableError:
                yield AgentStreamEvent("error", code="model_unavailable")
                return
            except Exception as error:
                logger.warning(
                    "agent turn preparation failed with %s", type(error).__name__
                )
                yield AgentStreamEvent("error", code="agent_failed")
                return

            parts: list[str] = []
            generated_chars = 0
            try:
                for chunk in client.stream_final_answer(
                    messages=prepared.messages
                ):
                    generated_chars += len(chunk)
                    if generated_chars > MAX_ANSWER_CHARS:
                        yield AgentStreamEvent("error", code="answer_too_long")
                        return
                    parts.append(chunk)
                    yield AgentStreamEvent("delta", text=chunk)
            except Exception as error:
                # Only the exception type is logged: provider text must not
                # reach the client, and a truncated stream is otherwise silent.
                logger.warning(
                    "answer stream broke after %d chars with %s",
                    generated_chars,
                    type(error).__name__,
                )
                yield AgentStreamEvent("error", code="agent_failed")
                return

            try:
                turn = self._finalize(prepared, "".join(parts))
            except Exception as error:
                logger.warning(
                    "finalizing the streamed answer failed with %s",
                    type(error).__name__,
                )
                yield AgentStreamEvent("error", code="agent_failed")
                return
            yield AgentStreamEvent("completed", turn=turn)

    def _replay_turn(self, paper_id: str, request_id: str) -> AgentTurn | None:
        """Return the stored turn for an already answered request, if any."""
        existing = self.repository.get_agent_turn_by_request(paper_id, request_id)
        if existing is None:
            return None
        return self.turn_from_messages(
            paper_id=paper_id,
            messages=existing,
            note_references=self._note_references_for_message(
                paper_id, existing[1]
            ),
        )

    def _prepare_turn(
        self,
        *,
        paper_id: str,
        question: AgentQuestion,
        client: VllmToolCallingClient,
        model_snapshot: ModelSnapshot,
        request_id: str,
        selection: TextAnchorDraft | None,
    ) -> _PreparedTurn:
        """Persist the user turn, run the tool loop, and collect the evidence."""
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
            self.note_memory.retrieve(paper_id, question.content, selection)
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
            conversation_id=conversation_id,
        )
        if client is None:
            raise AgentRuntimeUnavailableError(_UNAVAILABLE_ERROR)
        if conversation is None:
            conversation = self.repository.create_conversation(
                Conversation(paper_id=paper_id)
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

        executed_calls = 0
        # Overview questions often contain no literal terms from an English
        # paper. Seed actual, located source excerpts rather than searching
        # the Chinese question verbatim or weakening citation validation.
        overview = any(term in question.content.lower() for term in (
            '讲了什么', '主要内容', '概括', '总结', 'summarize', 'overview',
        ))
        if overview:
            elements = [element for element in self.repository.get_elements(paper_id)
                        if element.text.strip() and element.page_number is not None and element.bbox is not None]
            candidates = elements[:3] + elements[-1:]
            seen = set()
            seeded_elements: list[dict[str, object]] = []
            for element in candidates:
                if element.id in seen:
                    continue
                seen.add(element.id)
                execution = self.tools.execute(
                    paper_id=paper_id, name='read_element', arguments={'element_id': element.id}
                )
                allowed_evidence_ids.update(execution.evidence_element_ids)
                executed_calls += 1
                seeded_elements.append(execution.content['element'])
            if seeded_elements:
                messages.append(
                    {'role': 'system', 'content': _seeded_evidence_message(seeded_elements, allowed_evidence_ids)}
                )
                messages.append({'role':'system', 'content':'请用中文概括上方论文证据中的研究问题、方法和结论。只引用已给出的元素 ID；片段不足时说明范围，不要编造。'})
        for _ in range(0 if overview and allowed_evidence_ids else MAX_TOOL_TURNS):
            try:
                turn = client.request_tool_turn(
                    messages=messages,
                    tools=tool_definitions,
                    tool_choice="auto",
                )
                if not turn.tool_calls:
                    break
                if len({call.id for call in turn.tool_calls}) != len(turn.tool_calls):
                    raise ValueError("duplicate tool call IDs")
                # Providers may ignore parallel_tool_calls=False and batch several
                # calls at once. Keep the hard ceiling but answer from the evidence
                # already gathered instead of failing the whole turn.
                calls = turn.tool_calls[: MAX_TOOL_TURNS - executed_calls]
                assistant_message = None
                tool_messages = []
                for call in calls:
                    try:
                        execution = self.tools.execute(
                            paper_id=paper_id, name=call.name, arguments=call.arguments,
                        )
                    except AgentToolError as error:
                        # A rejected argument (for example an unknown section ID)
                        # is feedback for the model, not a failed turn.
                        content: dict[str, object] = {"error": str(error)}
                        evidence_ids: tuple[str, ...] = ()
                    else:
                        content = execution.content
                        evidence_ids = execution.evidence_element_ids
                        allowed_evidence_ids.update(evidence_ids)
                    assistant, tool_message = _tool_result_messages(
                        turn, call, content, evidence_ids
                    )
                    if assistant_message is None:
                        assistant_message = assistant
                    else:
                        assistant_message['tool_calls'].extend(assistant['tool_calls'])
                    tool_messages.append(tool_message)
                    executed_calls += 1
                if assistant_message is not None:
                    messages.extend([assistant_message, *tool_messages])
                if executed_calls >= MAX_TOOL_TURNS or len(calls) < len(turn.tool_calls):
                    break
            except Exception:
                raise AgentRuntimeResponseError(_RESPONSE_ERROR) from None

        if messages[-1].get("role") == "tool":
            messages.append(
                {"role": "system", "content": _FINAL_ANSWER_INSTRUCTION}
            )
        return _PreparedTurn(
            conversation=conversation,
            user_message=user_message,
            messages=messages,
            allowed_evidence_ids=allowed_evidence_ids,
            note_context=note_context,
            model_snapshot=model_snapshot,
            request_id=request_id,
        )

    def _finalize(self, prepared: _PreparedTurn, text: str) -> AgentTurn:
        """Parse the Markdown answer, persist it, and link its note memory."""
        try:
            answer = self.guard.parse_markdown_answer(text)
        except CitationGuardError:
            raise AgentRuntimeResponseError(_RESPONSE_ERROR) from None
        except Exception:
            raise AgentRuntimeResponseError(_RESPONSE_ERROR) from None

        # Citation links are optional presentation data.  Keep the model
        # prose even when it cites an old conversation ID,
        # or an element that no longer has a located geometry.  Only drop
        # IDs that the repository cannot persist; never replace the answer
        # with an evidence-refusal message.
        candidate_citation_ids = tuple(dict.fromkeys(answer.citation_element_ids))
        if set(candidate_citation_ids).issubset(prepared.allowed_evidence_ids):
            persistable_citation_ids = candidate_citation_ids
        else:
            persistable_citation_ids = _persistable_citation_ids(
                self.repository,
                prepared.conversation.paper_id,
                candidate_citation_ids,
            )
        answer = replace(
            answer, citation_element_ids=persistable_citation_ids
        )

        assistant_message = self.repository.append_conversation_message(
            ConversationMessage(
                conversation_id=prepared.conversation.id,
                paper_id=prepared.conversation.paper_id,
                role=AgentMessageRole.assistant,
                content=answer.paper_answer,
                citation_element_ids=answer.citation_element_ids,
                model_profile_id=prepared.model_snapshot.profile_id,
                model_snapshot=prepared.model_snapshot,
                request_id=prepared.request_id,
                background_explanation=answer.background_explanation,
            )
        )
        self.repository.link_message_notes(
            prepared.conversation.paper_id,
            assistant_message.id,
            tuple(
                reference.note_id
                for reference in prepared.note_context.references
            ),
        )
        return AgentTurn(
            conversation=prepared.conversation,
            user_message=prepared.user_message,
            assistant_message=assistant_message,
            answer=answer,
            note_references=prepared.note_context.references,
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
            if conversation is None:
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
            {"role": "system", "content": _system_prompt()},
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


def _system_prompt() -> str:
    return (
        "Use the provided paper tools when they help answer the user's "
        "question. Answer directly and naturally, including for general questions "
        "and short greetings. "
        "Tool evidence element IDs identify paper locations. "
        + ANSWER_FORMAT_INSTRUCTIONS
    )


def _seeded_evidence_message(
    elements: list[dict[str, object]], allowed_evidence_ids: set[str]
) -> str:
    """Present pre-read excerpts as evidence without faking a model tool turn.

    Thinking-mode providers reject replayed tool-call messages that the model
    never produced, so the seeded reads travel as plain evidence text.
    """
    return json.dumps(
        {
            "content": {"elements": elements},
            "evidence_element_ids": list(dict.fromkeys(allowed_evidence_ids)),
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _tool_result_messages(
    turn: VllmToolTurn,
    call: VllmToolCall,
    content: dict[str, object],
    evidence_element_ids: tuple[str, ...],
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
    if turn.reasoning_content is not None:
        assistant_message["reasoning_content"] = turn.reasoning_content
    tool_message: dict[str, object] = {
        "role": "tool",
        "tool_call_id": call.id,
        "name": call.name,
        "content": json.dumps(
            {
                "content": content,
                "evidence_element_ids": evidence_element_ids,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ),
    }
    return assistant_message, tool_message


def _persistable_citation_ids(
    repository: PaperRepository,
    paper_id: str,
    citation_ids: tuple[str, ...],
) -> tuple[str, ...]:
    """Keep only IDs that can be rendered and stored as optional links."""
    unique_ids = tuple(dict.fromkeys(citation_ids))
    located_ids = {
        element.id
        for element in repository.get_located_elements_by_ids(paper_id, unique_ids)
    }
    return tuple(element_id for element_id in unique_ids if element_id in located_ids)
