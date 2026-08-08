from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict

from paper_agent.domain import (
    DocumentElement,
    Note,
    Page,
    Paper,
    PaperDocument,
    ProcessingStatus,
    Section,
)


@dataclass(frozen=True)
class UploadPayload:
    filename: str
    content: bytes
    media_type: str | None


@dataclass(frozen=True)
class PaperSummary:
    id: str
    original_filename: str
    status: ProcessingStatus
    stage0_status: ProcessingStatus
    stage1_status: ProcessingStatus
    error: str | None = None

    @classmethod
    def from_paper(
        cls,
        paper: Paper,
        *,
        stage0_status: ProcessingStatus | None = None,
        stage1_status: ProcessingStatus | None = None,
        error: str | None = None,
    ) -> "PaperSummary":
        stage_statuses = {
            ProcessingStatus.queued: (ProcessingStatus.queued, ProcessingStatus.queued),
            ProcessingStatus.running: (ProcessingStatus.running, ProcessingStatus.queued),
            ProcessingStatus.completed: (
                ProcessingStatus.completed,
                ProcessingStatus.completed,
            ),
            ProcessingStatus.partial: (ProcessingStatus.completed, ProcessingStatus.failed),
            ProcessingStatus.failed: (ProcessingStatus.failed, ProcessingStatus.queued),
        }
        fallback_stage0_status, fallback_stage1_status = stage_statuses[paper.status]
        return cls(
            id=paper.id,
            original_filename=paper.original_filename,
            status=paper.status,
            stage0_status=fallback_stage0_status if stage0_status is None else stage0_status,
            stage1_status=fallback_stage1_status if stage1_status is None else stage1_status,
            error=error,
        )


class PaperSummaryResponse(BaseModel):
    id: str
    original_filename: str
    status: ProcessingStatus
    stage0_status: ProcessingStatus
    stage1_status: ProcessingStatus
    error: str | None = None

    @classmethod
    def from_summary(cls, summary: PaperSummary) -> "PaperSummaryResponse":
        return cls(
            id=summary.id,
            original_filename=summary.original_filename,
            status=summary.status,
            stage0_status=summary.stage0_status,
            stage1_status=summary.stage1_status,
            error=summary.error,
        )


class PaperResponse(BaseModel):
    id: str
    original_filename: str
    status: ProcessingStatus

    @classmethod
    def from_paper(cls, paper: Paper) -> "PaperResponse":
        return cls(
            id=paper.id,
            original_filename=paper.original_filename,
            status=paper.status,
        )


class BoundingBoxResponse(BaseModel):
    x0: float
    y0: float
    x1: float
    y1: float


class PageResponse(BaseModel):
    id: str
    number: int
    width: float
    height: float

    @classmethod
    def from_page(cls, page: Page) -> "PageResponse":
        return cls(id=page.id, number=page.number, width=page.width, height=page.height)


class SectionResponse(BaseModel):
    id: str
    title: str
    page_number: int | None
    order: int

    @classmethod
    def from_section(cls, section: Section) -> "SectionResponse":
        return cls(
            id=section.id,
            title=section.title,
            page_number=section.page_number,
            order=section.order,
        )


class ElementResponse(BaseModel):
    id: str
    kind: str
    text: str
    page_number: int | None
    bbox: BoundingBoxResponse | None
    section_id: str | None
    location_status: str
    order: int

    @classmethod
    def from_element(cls, element: DocumentElement) -> "ElementResponse":
        return cls(
            id=element.id,
            kind=element.kind,
            text=element.text,
            page_number=element.page_number,
            bbox=None
            if element.bbox is None
            else BoundingBoxResponse(
                x0=element.bbox.x0,
                y0=element.bbox.y0,
                x1=element.bbox.x1,
                y1=element.bbox.y1,
            ),
            section_id=element.section_id,
            location_status=element.location_status,
            order=element.order,
        )


class NoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: str
    element_id: str | None = None
    page_number: int | None = None


class NoteResponse(BaseModel):
    id: str
    body: str
    element_id: str | None
    page_number: int | None

    @classmethod
    def from_note(cls, note: Note) -> "NoteResponse":
        return cls(
            id=note.id,
            body=note.body,
            element_id=note.element_id,
            page_number=note.page_number,
        )


class PaperDocumentResponse(BaseModel):
    paper: PaperResponse
    pages: list[PageResponse]
    sections: list[SectionResponse]
    elements: list[ElementResponse]
    notes: list[NoteResponse]

    @classmethod
    def from_document(cls, document: PaperDocument) -> "PaperDocumentResponse":
        return cls(
            paper=PaperResponse.from_paper(document.paper),
            pages=[PageResponse.from_page(page) for page in document.pages],
            sections=[SectionResponse.from_section(section) for section in document.sections],
            elements=[ElementResponse.from_element(element) for element in document.elements],
            notes=[NoteResponse.from_note(note) for note in document.notes],
        )
