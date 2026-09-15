import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from paper_agent.annotation_storage import PaperAnnotationRepository
from paper_agent.domain import AgentMessageRole, ConversationMessage, DocumentElement
from paper_agent.model_profiles import ModelSnapshot
from paper_agent.schemas import (
    AgentHealthResponse,
    AgentMessageRequest,
    AgentMessageResponse,
    CitationResponse,
    ConversationMessageResponse,
    ConversationResponse,
    ModelSnapshotResponse,
    NoteReferenceResponse,
)
from paper_agent.services.agent_runtime import (
    AgentQuestion,
    AgentRuntimeConflictError,
    AgentRuntimeInvalidSelectionError,
    AgentRuntimePrerequisiteError,
    AgentRuntimeResponseError,
    AgentRuntimeUnavailableError,
    AgentStreamEvent,
    AgentTurn,
    PaperAgentRuntime,
)
from paper_agent.services.model_profiles import (
    ModelProfileNotFoundError,
    ModelProfileService,
)
from paper_agent.services.paper_operations import PaperDeletingError
from paper_agent.services.annotations import draft_from_request
from paper_agent.services.reasoning_clients import (
    ReasoningClientProvider,
    ResolvedReasoningClients,
)
from paper_agent.services.sse import sse_frame
from paper_agent.storage import PaperRepository


router = APIRouter(prefix="/api", tags=["agent"])
logger = logging.getLogger(__name__)


def _runtime(request: Request) -> PaperAgentRuntime:
    return request.app.state.paper_agent_runtime


def _repository(request: Request) -> PaperRepository:
    return request.app.state.paper_repository


def _provider(request: Request) -> ReasoningClientProvider:
    return request.app.state.reasoning_client_provider


def _model_profile_service(request: Request) -> ModelProfileService:
    return request.app.state.model_profile_service


def _annotation_repository(request: Request) -> PaperAnnotationRepository:
    return request.app.state.annotation_repository


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="Paper agent resource not found.")


def _require_paper(repository: PaperRepository, paper_id: str) -> None:
    if repository.get_paper(paper_id) is None:
        raise _not_found()


def _require_conversation(
    repository: PaperRepository, paper_id: str, conversation_id: str
):
    conversation = repository.get_conversation(paper_id, conversation_id)
    if conversation is None:
        raise _not_found()
    return conversation


def _request_conflict() -> HTTPException:
    return HTTPException(
        status_code=409,
        detail="Request state conflicts with its stored retry. Use a new request_id.",
    )


def _resolve_agent_model(
    provider: ReasoningClientProvider,
    profile_id: str,
    *,
    expected_snapshot: ModelSnapshot | None = None,
) -> ResolvedReasoningClients:
    try:
        resolved = provider.resolve(profile_id)
    except Exception:
        if expected_snapshot is not None:
            raise _request_conflict() from None
        raise HTTPException(
            status_code=503,
            detail="Reasoning model is not configured.",
        ) from None
    if expected_snapshot is not None and resolved.snapshot != expected_snapshot:
        raise _request_conflict()
    capabilities = resolved.profile.capabilities
    # The Markdown answer needs no structured response format, so tool calling
    # is the only capability the agent turn actually depends on.
    if not provider.is_read_only_profile(resolved.profile.id) and not capabilities.tool_calling:
        raise HTTPException(
            status_code=409,
            detail="当前模型的工具调用检测未通过，请在模型设置中重新测试；仍失败时检查服务接口兼容性。",
        )
    return resolved


def _citations(
    elements: dict[str, DocumentElement], message: ConversationMessage
) -> list[CitationResponse]:
    try:
        citations: list[CitationResponse] = []
        for ordinal, element_id in enumerate(message.citation_element_ids):
            snapshot = (
                message.citation_snapshots[ordinal]
                if ordinal < len(message.citation_snapshots)
                else None
            )
            citations.append(
                CitationResponse.from_snapshot(snapshot)
                if snapshot is not None
                else CitationResponse.from_element(elements[element_id])
            )
        return citations
    except (KeyError, ValueError) as error:
        raise HTTPException(
            status_code=502,
            detail="Reasoning model could not complete the request.",
        ) from error


def _citation_elements(
    repository: PaperRepository,
    paper_id: str,
    messages: tuple[ConversationMessage, ...],
) -> dict[str, DocumentElement]:
    citation_element_ids = tuple(
        dict.fromkeys(
            element_id
            for message in messages
            if message.role is AgentMessageRole.assistant
            for ordinal, element_id in enumerate(message.citation_element_ids)
            if ordinal >= len(message.citation_snapshots)
            or message.citation_snapshots[ordinal] is None
        )
    )
    if not citation_element_ids:
        return {}

    elements = {
        element.id: element
        for element in repository.get_located_elements_by_ids(
            paper_id, citation_element_ids
        )
    }
    if set(elements) != set(citation_element_ids):
        raise HTTPException(
            status_code=502,
            detail="Reasoning model could not complete the request.",
        )
    return elements


@router.post(
    "/papers/{paper_id}/agent/messages", response_model=AgentMessageResponse
)
def ask_paper_agent(
    paper_id: UUID, payload: AgentMessageRequest, request: Request
) -> AgentMessageResponse:
    paper_id_text = str(paper_id)
    repository = _repository(request)
    _require_paper(repository, paper_id_text)
    runtime = _runtime(request)
    request_id = str(payload.request_id)
    try:
        selection = (
            None
            if payload.selection is None
            else draft_from_request(payload.selection)
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail="Selected text is invalid.") from error
    try:
        with request.app.state.paper_operation_coordinator.operation(
            paper_id_text
        ):
            return _run_agent_request(
                repository,
                runtime,
                request,
                paper_id_text,
                payload,
                selection,
                request_id,
            )
    except PaperDeletingError:
        raise HTTPException(
            status_code=409, detail="Paper deletion is active."
        ) from None


def _run_agent_request(
    repository: PaperRepository,
    runtime: PaperAgentRuntime,
    request: Request,
    paper_id_text: str,
    payload: AgentMessageRequest,
    selection,
    request_id: str,
) -> AgentMessageResponse:
    existing = repository.get_agent_turn_by_request(paper_id_text, request_id)
    if existing is not None:
        return _agent_message_response(
            repository,
            paper_id_text,
            runtime.turn_from_messages(paper_id=paper_id_text, messages=existing),
        )

    partial_user = repository.get_agent_user_message_by_request(
        paper_id_text, request_id
    )
    if partial_user is None:
        if payload.conversation_id is not None:
            _require_conversation(
                repository, paper_id_text, str(payload.conversation_id)
            )
        profile_id = str(payload.model_profile_id)
    else:
        conversation = repository.get_conversation(
            paper_id_text, partial_user.conversation_id
        )
        if (
            partial_user.model_profile_id is None
            or partial_user.model_snapshot is None
            or str(payload.model_profile_id) != partial_user.model_profile_id
            or payload.content != partial_user.content
            or (
                payload.conversation_id is not None
                and str(payload.conversation_id) != partial_user.conversation_id
            )
            or conversation is None
        ):
            raise _request_conflict()
        profile_id = partial_user.model_profile_id

    try:
        with _model_profile_service(request).usage_lease(profile_id):
            resolved = _resolve_agent_model(
                _provider(request),
                profile_id,
                expected_snapshot=(
                    None if partial_user is None else partial_user.model_snapshot
                ),
            )
            turn = runtime.ask(
                paper_id=paper_id_text,
                question=AgentQuestion(
                    content=payload.content,
                    conversation_id=(
                        None
                        if payload.conversation_id is None
                        else str(payload.conversation_id)
                    ),
                ),
                client=resolved.tools,
                model_snapshot=resolved.snapshot,
                request_id=request_id,
                selection=selection,
            )
    except ModelProfileNotFoundError:
        if partial_user is not None:
            raise _request_conflict() from None
        raise HTTPException(
            status_code=503,
            detail="Reasoning model is not configured.",
        ) from None
    except AgentRuntimePrerequisiteError:
        raise HTTPException(
            status_code=409, detail="Paper agent prerequisites are not complete."
        ) from None
    except AgentRuntimeUnavailableError:
        raise HTTPException(
            status_code=503, detail="Reasoning model is not configured."
        ) from None
    except AgentRuntimeConflictError:
        raise _request_conflict() from None
    except AgentRuntimeInvalidSelectionError:
        raise HTTPException(
            status_code=422, detail="Selected text is invalid."
        ) from None
    except AgentRuntimeResponseError:
        raise HTTPException(
            status_code=502,
            detail="模型未能完成有效回答，可能是工具调用或返回格式不符合要求，请重试或在模型设置中重新测试。",
        ) from None
    return _agent_message_response(repository, paper_id_text, turn)


def _agent_message_response(
    repository: PaperRepository,
    paper_id: str,
    turn: AgentTurn,
) -> AgentMessageResponse:
    citation_elements = _citation_elements(
        repository, paper_id, (turn.assistant_message,)
    )
    return AgentMessageResponse(
        conversation_id=turn.conversation.id,
        message_id=turn.assistant_message.id,
        status=turn.answer.status,
        paper_answer=turn.answer.paper_answer,
        background_explanation=turn.answer.background_explanation,
        citations=_citations(
            citation_elements,
            turn.assistant_message,
        ),
        model=(
            None
            if turn.assistant_message.model_snapshot is None
            else ModelSnapshotResponse.from_snapshot(
                turn.assistant_message.model_snapshot
            )
        ),
        note_references=[
            NoteReferenceResponse.from_reference(reference)
            for reference in turn.note_references
        ],
    )


_STREAM_ERROR_DETAILS: dict[str, str] = {
    "agent_not_ready": "论文尚未完成解析，暂时无法提问。",
    "model_unavailable": "模型档案不可用。",
    "request_conflict": "该请求已有不同的处理状态，请使用新的请求重试。",
    "invalid_selection": "选区无效。",
    "answer_too_long": "模型输出过长，请缩小问题范围后重试。",
    "answer_unavailable": "回答已生成，但引用信息无法解析，请重试。",
    "paper_busy": "论文正在删除。",
    "agent_failed": "模型未能完成有效回答，请重试或在模型设置中重新测试。",
}


def _stream_error_payload(code: str | None) -> dict[str, object]:
    resolved = code if code in _STREAM_ERROR_DETAILS else "agent_failed"
    return {"code": resolved, "detail": _STREAM_ERROR_DETAILS[resolved]}


@router.post("/papers/{paper_id}/agent/messages/stream")
def stream_paper_agent(
    paper_id: UUID, payload: AgentMessageRequest, request: Request
) -> StreamingResponse:
    """Stream one answered turn as it is generated.

    Request validation fails before the stream opens so the client still sees a
    normal JSON error; failures after that arrive as an ``error`` event.
    """
    paper_id_text = str(paper_id)
    repository = _repository(request)
    _require_paper(repository, paper_id_text)
    try:
        selection = (
            None
            if payload.selection is None
            else draft_from_request(payload.selection)
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail="Selected text is invalid.") from error

    request_id = str(payload.request_id)
    profile_id = str(payload.model_profile_id)
    if (
        repository.get_agent_user_message_by_request(paper_id_text, request_id) is None
        and payload.conversation_id is not None
    ):
        _require_conversation(repository, paper_id_text, str(payload.conversation_id))
    resolved = _resolve_agent_model(_provider(request), profile_id)
    headers = {"Cache-Control": "no-store", "X-Accel-Buffering": "no"}

    def event_stream():
        try:
            yield from agent_events()
        except PaperDeletingError:
            yield sse_frame("error", _stream_error_payload("paper_busy"))
        except ModelProfileNotFoundError:
            yield sse_frame("error", _stream_error_payload("model_unavailable"))
        except HTTPException:
            # Only the completed branch can raise here: its citation lookup
            # needs elements that are no longer located. Answering with an error
            # frame keeps the connection readable instead of truncating it.
            logger.warning(
                "agent stream could not resolve citations for paper %s request %s",
                paper_id_text,
                request_id,
            )
            yield sse_frame("error", _stream_error_payload("answer_unavailable"))
        except Exception:
            # The response has already started, so an escaping exception would
            # close the stream without telling the client what happened.
            logger.exception(
                "agent stream failed for paper %s request %s",
                paper_id_text,
                request_id,
            )
            yield sse_frame("error", _stream_error_payload("agent_failed"))

    def agent_events():
        with request.app.state.paper_operation_coordinator.operation(
            paper_id_text
        ):
            with _model_profile_service(request).usage_lease(profile_id):
                for event in _runtime(request).stream_ask(
                    paper_id=paper_id_text,
                    question=AgentQuestion(
                        content=payload.content,
                        conversation_id=(
                            None
                            if payload.conversation_id is None
                            else str(payload.conversation_id)
                        ),
                    ),
                    client=resolved.tools,
                    model_snapshot=resolved.snapshot,
                    request_id=request_id,
                    selection=selection,
                ):
                    if event.event == "completed" and event.turn is not None:
                        yield sse_frame(
                            "completed",
                            {
                                "message": _agent_message_response(
                                    repository, paper_id_text, event.turn
                                ).model_dump(mode="json")
                            },
                        )
                    elif event.event == "error":
                        logger.warning(
                            "agent stream reported %s for paper %s request %s",
                            event.code,
                            paper_id_text,
                            request_id,
                        )
                        yield sse_frame(
                            "error", _stream_error_payload(event.code)
                        )
                    elif event.event == "delta":
                        yield sse_frame("delta", {"text": event.text})
                    else:
                        yield sse_frame("started", {"request_id": request_id})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers=headers,
    )


@router.get(
    "/papers/{paper_id}/agent/conversations/{conversation_id}",
    response_model=ConversationResponse,
)
def get_conversation(
    paper_id: UUID, conversation_id: UUID, request: Request
) -> ConversationResponse:
    paper_id_text = str(paper_id)
    repository = _repository(request)
    _require_paper(repository, paper_id_text)
    conversation = _require_conversation(
        repository, paper_id_text, str(conversation_id)
    )
    messages = repository.get_conversation_messages(paper_id_text, conversation.id)
    citation_elements = _citation_elements(repository, paper_id_text, messages)
    note_references = _note_references_for_messages(
        repository, _annotation_repository(request), paper_id_text, messages
    )
    return ConversationResponse.from_conversation(
        conversation,
        [
            ConversationMessageResponse.from_message(
                message,
                []
                if message.role is AgentMessageRole.user
                else _citations(citation_elements, message),
                note_references[message.id],
            )
            for message in messages
        ],
    )


def _note_references_for_messages(
    repository: PaperRepository,
    annotation_repository: PaperAnnotationRepository,
    paper_id: str,
    messages: tuple[ConversationMessage, ...],
) -> dict[str, list[NoteReferenceResponse]]:
    assistant_ids = tuple(
        message.id
        for message in messages
        if message.role is AgentMessageRole.assistant
    )
    note_ids_by_message = repository.get_message_note_ids(
        paper_id, assistant_ids
    )
    anchors = {
        anchor.id: anchor
        for anchor in annotation_repository.list_anchors(paper_id)
    }
    result: dict[str, list[NoteReferenceResponse]] = {}
    for message in messages:
        if message.role is AgentMessageRole.user:
            result[message.id] = []
            continue
        references: list[NoteReferenceResponse] = []
        for note_id in note_ids_by_message.get(message.id, ()):
            try:
                note = annotation_repository.get_note(paper_id, note_id)
                page_number = note.page_number
                if page_number is None and note.anchor_ids:
                    anchor = anchors.get(note.anchor_ids[0])
                    if anchor is not None:
                        page_number = anchor.page_number
                references.append(
                    NoteReferenceResponse(
                        note_id=note.id,
                        note_type=note.note_type.value,
                        page_number=page_number,
                        available=True,
                    )
                )
            except Exception:
                references.append(
                    NoteReferenceResponse(
                        note_id=note_id,
                        note_type=None,
                        page_number=None,
                        available=False,
                    )
                )
        result[message.id] = references
    return result


@router.post("/agent/health", response_model=AgentHealthResponse)
def validate_agent_health(request: Request) -> AgentHealthResponse:
    try:
        clients = _model_profile_service(request).resolve_default_clients()
        _runtime(request).validate_tool_calling(client=None if clients is None else clients.tools)
    except Exception:
        raise HTTPException(
            status_code=503,
            detail="Reasoning model tool calling is unavailable.",
        ) from None
    return AgentHealthResponse()
