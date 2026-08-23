"""HTTP routes for paper text anchors, highlights, and notes."""

from typing import Callable, TypeVar
from uuid import UUID

from fastapi import APIRouter, Request, Response

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
        deleted = _service(request).delete_note(paper_id, str(note_id))
        if not deleted:
            raise AnnotationNotFoundError("note was not found")

    _safe_errors(remove)
    return Response(status_code=204)
