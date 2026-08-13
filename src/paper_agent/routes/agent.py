from uuid import UUID

from fastapi import APIRouter, HTTPException, Request

from paper_agent.domain import AgentMessageRole, ConversationMessage, DocumentElement
from paper_agent.schemas import (
    AgentHealthResponse,
    AgentMessageRequest,
    AgentMessageResponse,
    CitationResponse,
    ConversationMessageResponse,
    ConversationResponse,
)
from paper_agent.services.agent_runtime import (
    AgentQuestion,
    AgentRuntimePrerequisiteError,
    AgentRuntimeResponseError,
    AgentRuntimeUnavailableError,
    PaperAgentRuntime,
)
from paper_agent.storage import PaperRepository


router = APIRouter(prefix="/api", tags=["agent"])


def _runtime(request: Request) -> PaperAgentRuntime:
    return request.app.state.paper_agent_runtime


def _repository(request: Request) -> PaperRepository:
    return request.app.state.paper_repository


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


def _citations(
    elements: dict[str, DocumentElement], citation_element_ids: tuple[str, ...]
) -> list[CitationResponse]:
    try:
        return [
            CitationResponse.from_element(elements[element_id])
            for element_id in citation_element_ids
        ]
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
            for element_id in message.citation_element_ids
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
    if payload.conversation_id is not None:
        _require_conversation(repository, paper_id_text, str(payload.conversation_id))
    try:
        turn = _runtime(request).ask(
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
        )
    except AgentRuntimePrerequisiteError as error:
        raise HTTPException(
            status_code=409, detail="Paper agent prerequisites are not complete."
        ) from error
    except AgentRuntimeUnavailableError as error:
        raise HTTPException(
            status_code=503, detail="Reasoning model is not configured."
        ) from error
    except AgentRuntimeResponseError as error:
        raise HTTPException(
            status_code=502,
            detail="Reasoning model could not complete the request.",
        ) from error
    citation_elements = _citation_elements(
        repository, paper_id_text, (turn.assistant_message,)
    )
    return AgentMessageResponse(
        conversation_id=turn.conversation.id,
        message_id=turn.assistant_message.id,
        status=turn.answer.status,
        paper_answer=turn.answer.paper_answer,
        background_explanation=turn.answer.background_explanation,
        citations=_citations(
            citation_elements,
            turn.assistant_message.citation_element_ids,
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
                else _citations(citation_elements, message.citation_element_ids),
            )
            for message in messages
        ],
    )


@router.post("/agent/health", response_model=AgentHealthResponse)
def validate_agent_health(request: Request) -> AgentHealthResponse:
    try:
        _runtime(request).validate_tool_calling()
    except AgentRuntimeUnavailableError as error:
        raise HTTPException(
            status_code=503,
            detail="Reasoning model tool calling is unavailable.",
        ) from error
    return AgentHealthResponse()
