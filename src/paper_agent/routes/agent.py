from uuid import UUID

from fastapi import APIRouter, HTTPException, Request

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
)
from paper_agent.services.agent_runtime import (
    AgentQuestion,
    AgentRuntimeConflictError,
    AgentRuntimePrerequisiteError,
    AgentRuntimeResponseError,
    AgentRuntimeUnavailableError,
    AgentTurn,
    PaperAgentRuntime,
)
from paper_agent.services.model_profiles import (
    ModelProfileNotFoundError,
    ModelProfileService,
)
from paper_agent.services.reasoning_clients import (
    ReasoningClientProvider,
    ResolvedReasoningClients,
)
from paper_agent.storage import PaperRepository


router = APIRouter(prefix="/api", tags=["agent"])


def _runtime(request: Request) -> PaperAgentRuntime:
    return request.app.state.paper_agent_runtime


def _repository(request: Request) -> PaperRepository:
    return request.app.state.paper_repository


def _provider(request: Request) -> ReasoningClientProvider:
    return request.app.state.reasoning_client_provider


def _model_profile_service(request: Request) -> ModelProfileService:
    return request.app.state.model_profile_service


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
    if not provider.is_read_only_profile(resolved.profile.id) and not (
        capabilities.structured_output and capabilities.tool_calling
    ):
        raise HTTPException(
            status_code=409,
            detail="Selected model does not support Agent requests.",
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
            or conversation.mode is not payload.mode
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
                    mode=payload.mode,
                    conversation_id=(
                        None
                        if payload.conversation_id is None
                        else str(payload.conversation_id)
                    ),
                ),
                client=resolved.tools,
                model_snapshot=resolved.snapshot,
                request_id=request_id,
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
    except AgentRuntimeResponseError:
        raise HTTPException(
            status_code=502,
            detail="Reasoning model could not complete the request.",
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
    return ConversationResponse.from_conversation(
        conversation,
        [
            ConversationMessageResponse.from_message(
                message,
                []
                if message.role is AgentMessageRole.user
                else _citations(citation_elements, message),
            )
            for message in messages
        ],
    )


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
