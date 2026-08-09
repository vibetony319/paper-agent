from uuid import UUID

from fastapi import APIRouter, HTTPException, Request

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
    repository: PaperRepository, paper_id: str, citation_element_ids: tuple[str, ...]
) -> list[CitationResponse]:
    elements = {element.id: element for element in repository.get_elements(paper_id)}
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
    return AgentMessageResponse(
        conversation_id=turn.conversation.id,
        message_id=turn.assistant_message.id,
        status=turn.answer.status,
        paper_answer=turn.answer.paper_answer,
        background_explanation=turn.answer.background_explanation,
        citations=_citations(
            repository,
            paper_id_text,
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
    return ConversationResponse.from_conversation(
        conversation,
        [
            ConversationMessageResponse.from_message(
                message,
                _citations(repository, paper_id_text, message.citation_element_ids),
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
            status_code=503, detail="Reasoning model is not configured."
        ) from error
    return AgentHealthResponse()
