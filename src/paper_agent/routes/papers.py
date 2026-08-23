from fastapi import APIRouter, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse
from uuid import UUID

from paper_agent.schemas import (
    NoteResponse,
    PaperDeleteRequest,
    PaperDocumentResponse,
    PaperSummaryResponse,
    UploadPayload,
)
from paper_agent.services.ingestion import InvalidUploadError, PaperIngestionService
from paper_agent.services.paper_deletion import (
    PaperDeletionBusyError,
    PaperDeletionNotFoundError,
    PaperDeletionPathError,
)
from paper_agent.services.paper_operations import PaperBusyError


router = APIRouter(prefix="/api/papers", tags=["papers"])


def _service(request: Request) -> PaperIngestionService:
    return request.app.state.paper_ingestion_service


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="Paper resource not found.")


class PaperDeletionHttpError(Exception):
    def __init__(self, status_code: int, code: str, detail: str) -> None:
        self.status_code = status_code
        self.code = code
        self.detail = detail


_DELETION_STATUS = {
    "PAPER_NOT_FOUND": 404,
    "PAPER_BUSY": 409,
    "DELETE_CONFIRMATION_MISMATCH": 422,
    "DELETE_RECOVERY_REQUIRED": 500,
}


def _deletion_error(code: str, detail: str) -> PaperDeletionHttpError:
    return PaperDeletionHttpError(_DELETION_STATUS[code], code, detail)


@router.post("", response_model=PaperSummaryResponse, status_code=201)
async def upload_paper(request: Request, file: UploadFile = File(...)) -> PaperSummaryResponse:
    try:
        summary = _service(request).ingest(
            UploadPayload(
                filename=file.filename or "",
                content=await file.read(),
                media_type=file.content_type,
            )
        )
    except InvalidUploadError as error:
        raise HTTPException(status_code=422, detail="Invalid PDF upload.") from error
    return PaperSummaryResponse.from_summary(summary)


@router.get("", response_model=list[PaperSummaryResponse])
def list_papers(request: Request) -> list[PaperSummaryResponse]:
    return [
        PaperSummaryResponse.from_summary(summary)
        for summary in _service(request).list_summaries()
    ]


@router.get("/{paper_id}", response_model=PaperSummaryResponse)
def get_paper(paper_id: str, request: Request) -> PaperSummaryResponse:
    try:
        return PaperSummaryResponse.from_summary(_service(request).get_summary(paper_id))
    except KeyError as error:
        raise _not_found() from error


@router.get("/{paper_id}/document", response_model=PaperDocumentResponse)
def get_document(paper_id: str, request: Request) -> PaperDocumentResponse:
    try:
        return PaperDocumentResponse.from_document(_service(request).get_document(paper_id))
    except KeyError as error:
        raise _not_found() from error


@router.get("/{paper_id}/source")
def get_source(paper_id: str, request: Request) -> FileResponse:
    try:
        source_path = _service(request).get_source_path(paper_id)
        if not source_path.is_file():
            raise KeyError(paper_id)
        return FileResponse(
            source_path, media_type="application/pdf"
        )
    except KeyError as error:
        raise _not_found() from error


@router.get("/{paper_id}/pages/{page_number}/image")
def get_page_image(paper_id: str, page_number: str, request: Request) -> Response:
    try:
        parsed_page_number = int(page_number)
    except ValueError as error:
        raise _not_found() from error
    try:
        return Response(
            content=_service(request).render_page_png(paper_id, parsed_page_number),
            media_type="image/png",
        )
    except KeyError as error:
        raise _not_found() from error


@router.get("/{paper_id}/notes", response_model=list[NoteResponse])
def get_notes(paper_id: str, request: Request) -> list[NoteResponse]:
    try:
        return [
            NoteResponse.from_note(note) for note in _service(request).get_notes(paper_id)
        ]
    except KeyError as error:
        raise _not_found() from error


@router.delete("/{paper_id}", status_code=204)
def delete_paper(
    paper_id: UUID, payload: PaperDeleteRequest, request: Request
) -> Response:
    paper_id_text = str(paper_id)
    if str(payload.confirmation) != paper_id_text:
        raise _deletion_error(
            "DELETE_CONFIRMATION_MISMATCH", "删除确认与论文不匹配。"
        )
    try:
        with request.app.state.paper_operation_coordinator.deletion(paper_id_text):
            request.app.state.paper_deletion_service.delete(paper_id_text)
    except PaperBusyError:
        raise _deletion_error("PAPER_BUSY", "论文正在处理中，请稍后重试。") from None
    except PaperDeletionBusyError:
        raise _deletion_error("PAPER_BUSY", "论文正在处理中，请稍后重试。") from None
    except PaperDeletionNotFoundError:
        raise _deletion_error("PAPER_NOT_FOUND", "论文不存在。") from None
    except PaperDeletionPathError:
        raise _deletion_error(
            "DELETE_RECOVERY_REQUIRED", "删除失败，需要启动恢复。"
        ) from None
    return Response(status_code=204)
