"""HTTP routes for paper text anchors, highlights, and notes."""

from typing import Callable, TypeVar
from uuid import UUID

from fastapi import APIRouter, Request, Response
from fastapi.responses import StreamingResponse
from fastapi import HTTPException

from paper_agent.annotation_storage import (
    AnnotationInputError,
    AnnotationNotFoundError,
    IdempotencyConflictError,
)
from paper_agent.schemas import (
    AnnotationBundleResponse,
    HighlightCreateRequest,
    HighlightResponse,
    NoteRequest,
    NoteResponse,
    NoteUpdateRequest,
    SelectionAssistRequest,
)
from paper_agent.services.model_profiles import ModelProfileNotFoundError
from paper_agent.services.paper_operations import PaperDeletingError
from paper_agent.services.reasoning_clients import (
    ReasoningClientProvider,
    ResolvedReasoningClients,
)
from paper_agent.services.selection_assists import (
    SelectionAssistAction,
    SelectionAssistEvent,
    SelectionAssistService,
    encode_sse,
)
from paper_agent.services.annotations import AnnotationService, draft_from_request


router = APIRouter(prefix="/api/papers", tags=["annotations"])
_Result = TypeVar("_Result")


class AnnotationHttpError(Exception):
    def __init__(self, status_code: int, code: str, detail: str) -> None:
        self.status_code = status_code
        self.code = code
        self.detail = detail


def _service(request: Request) -> AnnotationService:
    return request.app.state.annotation_service


def _selection_assist_service(request: Request) -> SelectionAssistService:
    return request.app.state.selection_assist_service


def _provider(request: Request) -> ReasoningClientProvider:
    return request.app.state.reasoning_client_provider


def _model_profile_service(request: Request):
    return request.app.state.model_profile_service


def _resolve_chat_model(
    provider: ReasoningClientProvider, profile_id: str
) -> ResolvedReasoningClients:
    try:
        resolved = provider.resolve(profile_id)
    except Exception:
        raise HTTPException(
            status_code=503, detail="Reasoning model is not configured."
        ) from None
    capabilities = resolved.profile.capabilities
    if not provider.is_read_only_profile(resolved.profile.id) and (
        not capabilities.basic_chat
    ):
        raise HTTPException(
            status_code=409,
            detail="Selected model does not support text generation.",
        )
    return resolved


def _safe_errors(action: Callable[[], _Result]) -> _Result:
    try:
        return action()
    except AnnotationNotFoundError:
        raise AnnotationHttpError(404, "annotation_not_found", "论文或批注不存在。")
    except IdempotencyConflictError:
        raise AnnotationHttpError(
            409,
            "idempotency_conflict",
            "该请求与已保存的结果不一致，请更换请求标识后重试。",
        )
    except (AnnotationInputError, ValueError):
        raise AnnotationHttpError(422, "validation_error", "批注请求无效。")
    except PaperDeletingError:
        raise AnnotationHttpError(
            409, "paper_busy", "论文正在删除，请稍后重试。"
        )
    except Exception:
        raise AnnotationHttpError(500, "annotation_error", "批注操作失败。")


@router.get(
    "/{paper_id}/annotations", response_model=AnnotationBundleResponse
)
def get_annotations(paper_id: str, request: Request) -> AnnotationBundleResponse:
    def load() -> AnnotationBundleResponse:
        service = _service(request)
        service.require_paper(paper_id)
        return AnnotationBundleResponse(
            highlights=[
                HighlightResponse.from_highlight(highlight)
                for highlight in service.list_highlights(paper_id)
            ],
            notes=[
                NoteResponse.from_note(note) for note in service.get_notes(paper_id)
            ],
        )

    return _safe_errors(load)


@router.post(
    "/{paper_id}/highlights",
    response_model=HighlightResponse,
    status_code=201,
)
def create_highlight(
    paper_id: str, payload: HighlightCreateRequest, request: Request
) -> HighlightResponse:
    def create() -> HighlightResponse:
        with request.app.state.paper_operation_coordinator.operation(paper_id):
            highlight = _service(request).create_highlight(
                paper_id,
                draft_from_request(payload),
                color=payload.color,
                request_id=str(payload.request_id),
            )
        return HighlightResponse.from_highlight(highlight)

    return _safe_errors(create)


@router.delete(
    "/{paper_id}/highlights/{highlight_id}",
    status_code=204,
)
def delete_highlight(
    paper_id: str, highlight_id: str, request: Request
) -> Response:
    def remove() -> None:
        with request.app.state.paper_operation_coordinator.operation(paper_id):
            deleted = _service(request).delete_highlight(paper_id, highlight_id)
            if not deleted:
                raise AnnotationNotFoundError("highlight was not found")

    _safe_errors(remove)
    return Response(status_code=204)


@router.post(
    "/{paper_id}/notes", response_model=NoteResponse, status_code=201
)
def create_note(
    paper_id: str, payload: NoteRequest, request: Request
) -> NoteResponse:
    def create() -> NoteResponse:
        with request.app.state.paper_operation_coordinator.operation(paper_id):
            note = _service(request).create_note(
                paper_id,
                body=payload.body,
                element_id=payload.element_id,
                page_number=payload.page_number,
                anchor_draft=(
                    None if payload.anchor is None else draft_from_request(payload.anchor)
                ),
                request_id=(
                    None if payload.request_id is None else str(payload.request_id)
                ),
            )
        return NoteResponse.from_note(note)

    return _safe_errors(create)


@router.patch("/{paper_id}/notes/{note_id}", response_model=NoteResponse)
def update_note(
    paper_id: str,
    note_id: UUID,
    payload: NoteUpdateRequest,
    request: Request,
) -> NoteResponse:
    def update() -> NoteResponse:
        with request.app.state.paper_operation_coordinator.operation(paper_id):
            note = _service(request).update_note(
                paper_id,
                str(note_id),
                body=payload.body,
                expected_updated_at=payload.expected_updated_at,
            )
        return NoteResponse.from_note(note)

    return _safe_errors(update)


@router.delete("/{paper_id}/notes/{note_id}", status_code=204)
def delete_note(paper_id: str, note_id: UUID, request: Request) -> Response:
    def remove() -> None:
        with request.app.state.paper_operation_coordinator.operation(paper_id):
            deleted = _service(request).delete_note(paper_id, str(note_id))
            if not deleted:
                raise AnnotationNotFoundError("note was not found")

    _safe_errors(remove)
    return Response(status_code=204)


@router.post("/{paper_id}/selection-assists")
def create_selection_assist(
    paper_id: str, payload: SelectionAssistRequest, request: Request
) -> StreamingResponse:
    annotation_service = _service(request)
    annotation_service.require_paper(paper_id)
    try:
        draft = draft_from_request(payload)
    except ValueError as error:
        raise HTTPException(status_code=422, detail="批注请求无效。") from error

    request_id = str(payload.request_id)
    profile_id = str(payload.model_profile_id)
    assist_service = _selection_assist_service(request)
    existing = assist_service.repository.get_selection_assist(paper_id, request_id)
    headers = {
        "Cache-Control": "no-store",
        "X-Accel-Buffering": "no",
    }
    if existing is not None and existing["status"] == "completed":
        return StreamingResponse(
            (encode_sse(event) for event in assist_service.replay(paper_id, request_id)),
            media_type="text/event-stream",
            headers=headers,
        )

    resolved = _resolve_chat_model(_provider(request), profile_id)
    action = SelectionAssistAction(payload.action)

    def event_stream():
        try:
            with request.app.state.paper_operation_coordinator.operation(paper_id):
                with _model_profile_service(request).usage_lease(profile_id):
                    for event in assist_service.stream(
                        paper_id=paper_id,
                        draft=draft,
                        action=action,
                        client=resolved.chat,
                        model_snapshot=resolved.snapshot,
                        request_id=request_id,
                    ):
                        yield encode_sse(event)
        except PaperDeletingError:
            yield encode_sse(
                SelectionAssistEvent(
                    "error",
                    {"code": "paper_busy", "detail": "论文正在删除。"},
                )
            )
        except ModelProfileNotFoundError:
            yield encode_sse(
                SelectionAssistEvent(
                    "error",
                    {"code": "assist_failed", "detail": "模型档案不可用。"},
                )
            )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers=headers,
    )
