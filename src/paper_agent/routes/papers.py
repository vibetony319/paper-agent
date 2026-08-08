from fastapi import APIRouter, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse

from paper_agent.schemas import (
    NoteRequest,
    NoteResponse,
    PaperDocumentResponse,
    PaperSummaryResponse,
    UploadPayload,
)
from paper_agent.services.ingestion import PaperIngestionService


router = APIRouter(prefix="/api/papers", tags=["papers"])


def _service(request: Request) -> PaperIngestionService:
    return request.app.state.paper_ingestion_service


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="Paper resource not found.")


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
    except ValueError as error:
        raise HTTPException(status_code=422, detail="Invalid PDF upload.") from error
    return PaperSummaryResponse.from_summary(summary)


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
def get_page_image(paper_id: str, page_number: int, request: Request) -> Response:
    try:
        return Response(
            content=_service(request).render_page_png(paper_id, page_number),
            media_type="image/png",
        )
    except KeyError as error:
        raise _not_found() from error


@router.post("/{paper_id}/notes", response_model=NoteResponse, status_code=201)
def create_note(
    paper_id: str, payload: NoteRequest, request: Request
) -> NoteResponse:
    try:
        note = _service(request).create_note(
            paper_id,
            body=payload.body,
            element_id=payload.element_id,
            page_number=payload.page_number,
        )
    except KeyError as error:
        raise _not_found() from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail="Invalid note target.") from error
    return NoteResponse.from_note(note)


@router.get("/{paper_id}/notes", response_model=list[NoteResponse])
def get_notes(paper_id: str, request: Request) -> list[NoteResponse]:
    try:
        return [
            NoteResponse.from_note(note) for note in _service(request).get_notes(paper_id)
        ]
    except KeyError as error:
        raise _not_found() from error
