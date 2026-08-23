from dataclasses import replace
import os
from pathlib import Path
from uuid import uuid4

from sqlalchemy.exc import SQLAlchemyError

from paper_agent.config import Settings
from paper_agent.domain import DocumentElement, Note, PaperDocument, ProcessingStatus
from paper_agent.parsers.base import PdfParseError
from paper_agent.parsers.alignment import TextAligner
from paper_agent.parsers.markitdown_stage1 import (
    MarkdownParseError,
    MarkItDownStage1Parser,
)
from paper_agent.parsers.pymupdf_stage0 import PyMuPdfStage0Parser
from paper_agent.schemas import PaperSummary, UploadPayload
from paper_agent.storage import PageReferenceError, PaperRepository


SAFE_PROCESSING_ERROR_SUMMARIES = frozenset(
    {
        "The PDF source could not be stored.",
        "The PDF could not be parsed.",
        "The PDF could not be converted.",
    }
)
PUBLIC_PROCESSING_ERROR = "The paper could not be processed."


class InvalidUploadError(ValueError):
    """Raised only when an upload fails filename or signature preflight."""


class PaperIngestionService:
    def __init__(self, *, settings: Settings, repository: PaperRepository) -> None:
        self.settings = settings
        self.repository = repository
        self.stage0_parser = PyMuPdfStage0Parser()
        self.stage1_parser = MarkItDownStage1Parser()
        self.aligner = TextAligner()

    def ingest(self, upload: UploadPayload) -> PaperSummary:
        self._validate_upload(upload)

        paper_id = str(uuid4())
        stored_filename = f"{paper_id}.pdf"
        paper = self.repository.create_paper(
            paper_id=paper_id,
            original_filename=upload.filename,
            stored_filename=stored_filename,
        )
        self.repository.record_processing_status(paper.id, ProcessingStatus.queued)
        self.repository.record_processing_status(
            paper.id, ProcessingStatus.queued, stage="stage0"
        )
        self.repository.record_processing_status(
            paper.id, ProcessingStatus.queued, stage="stage1"
        )

        source_path = self.settings.data_dir / "papers" / stored_filename
        temporary_source_path = source_path.with_name(
            f".{paper_id}-{uuid4()}.tmp"
        )
        temporary_source_owned = False
        try:
            source_file = temporary_source_path.open("xb")
            temporary_source_owned = True
            with source_file:
                source_file.write(upload.content)
            os.link(temporary_source_path, source_path)
            self.repository.mark_source_published(paper.id)
        except OSError:
            if temporary_source_owned:
                try:
                    temporary_source_path.unlink()
                except OSError:
                    pass
            self.repository.record_processing_status(
                paper.id,
                ProcessingStatus.failed,
                stage="stage0",
                error_summary="The PDF source could not be stored.",
            )
            return self._finish(
                paper,
                ProcessingStatus.failed,
                error_summary="The PDF source could not be stored.",
            )

        try:
            temporary_source_path.unlink()
        except OSError:
            pass

        paper = self.repository.update_paper_status(paper.id, ProcessingStatus.running)
        self.repository.record_processing_status(paper.id, ProcessingStatus.running)
        self.repository.record_processing_status(
            paper.id, ProcessingStatus.running, stage="stage0"
        )

        try:
            stage0 = self.stage0_parser.parse(source_path)
        except PdfParseError:
            return self._finish_stage0_failure(paper)
        source_elements = tuple(
            DocumentElement(
                kind="text_block",
                text=block.text,
                page_number=block.page_number,
                bbox=block.bbox,
            )
            for block in stage0.text_blocks
        ) + tuple(
            DocumentElement(
                kind=visual.kind,
                text=visual.caption or "",
                page_number=visual.page_number,
                bbox=visual.bbox,
            )
            for visual in stage0.visual_elements
        )
        try:
            self.repository.save_stage0_document(
                paper.id, stage0.pages, source_elements
            )
        except (SQLAlchemyError, PageReferenceError):
            return self._finish_stage0_failure(paper)
        self.repository.record_processing_status(
            paper.id, ProcessingStatus.completed, stage="stage0"
        )

        self.repository.record_processing_status(
            paper.id, ProcessingStatus.running, stage="stage1"
        )

        try:
            stage1 = self._run_stage1(source_path)
        except MarkdownParseError:
            return self._finish_stage1_failure(paper)
        except Exception:
            self._finish_stage1_failure(paper)
            raise

        try:
            elements = tuple(
                replace(element, order=None)
                for element in self.aligner.align(stage1.paragraphs, stage0.text_blocks)
            )
        except ValueError:
            return self._finish_stage1_failure(paper)
        except Exception:
            self._finish_stage1_failure(paper)
            raise

        try:
            self.repository.save_stage1_document(paper.id, stage1.sections, elements)
        except (SQLAlchemyError, PageReferenceError):
            return self._finish_stage1_failure(paper)
        except Exception:
            self._finish_stage1_failure(paper)
            raise

        self.repository.record_processing_status(
            paper.id, ProcessingStatus.completed, stage="stage1"
        )
        return self._finish(paper, ProcessingStatus.completed)

    def get_document(self, paper_id: str) -> PaperDocument:
        document = self.repository.get_document(paper_id)
        if document is None:
            raise KeyError(paper_id)
        return document

    def get_summary(self, paper_id: str) -> PaperSummary:
        paper = self.repository.get_paper(paper_id)
        if paper is None:
            raise KeyError(paper_id)
        error_summary = self.repository.get_processing_error(paper_id)
        return PaperSummary.from_paper(
            paper,
            stage0_status=self.repository.get_latest_stage_status(paper_id, "stage0"),
            stage1_status=self.repository.get_latest_stage_status(paper_id, "stage1"),
            stage2_status=self.repository.get_latest_stage_status(paper_id, "stage2"),
            stage3_status=self.repository.get_latest_stage_status(paper_id, "stage3"),
            stage2_model=self.repository.get_latest_graph_model(paper_id, "stage2"),
            stage3_model=self.repository.get_latest_graph_model(paper_id, "stage3"),
            error=self._public_error_summary(error_summary),
        )

    def list_summaries(self) -> tuple[PaperSummary, ...]:
        return tuple(self.get_summary(paper.id) for paper in self.repository.list_papers())

    def get_source_path(self, paper_id: str) -> Path:
        paper = self.repository.get_paper(paper_id)
        if paper is None:
            raise KeyError(paper_id)
        if not paper.source_published:
            raise KeyError(paper_id)
        return self.settings.data_dir / "papers" / paper.stored_filename

    def render_page_png(self, paper_id: str, page_number: int) -> bytes:
        try:
            return self.stage0_parser.render_page_png(
                self.get_source_path(paper_id), page_number
            )
        except (PdfParseError, ValueError) as error:
            raise KeyError(paper_id) from error

    def create_note(
        self,
        paper_id: str,
        *,
        body: str,
        element_id: str | None = None,
        page_number: int | None = None,
    ) -> Note:
        document = self.get_document(paper_id)
        if element_id is not None and not any(
            element.id == element_id for element in document.elements
        ):
            raise ValueError("element target does not belong to paper")
        if page_number is not None and not any(
            page.number == page_number for page in document.pages
        ):
            raise ValueError("page target does not belong to paper")
        return self.repository.create_note(
            paper_id,
            Note(body=body, element_id=element_id, page_number=page_number),
        )

    def get_notes(self, paper_id: str) -> tuple[Note, ...]:
        if self.repository.get_paper(paper_id) is None:
            raise KeyError(paper_id)
        return self.repository.get_notes(paper_id)

    def _run_stage1(self, source_path: Path):
        return self.stage1_parser.parse(source_path)

    def _finish_stage0_failure(self, paper) -> PaperSummary:
        self.repository.record_processing_status(
            paper.id,
            ProcessingStatus.failed,
            stage="stage0",
            error_summary="The PDF could not be parsed.",
        )
        return self._finish(
            paper,
            ProcessingStatus.failed,
            error_summary="The PDF could not be parsed.",
        )

    def _finish_stage1_failure(self, paper) -> PaperSummary:
        self.repository.record_processing_status(
            paper.id,
            ProcessingStatus.failed,
            stage="stage1",
            error_summary="The PDF could not be converted.",
        )
        return self._finish(
            paper,
            ProcessingStatus.partial,
            error_summary="The PDF could not be converted.",
        )

    @staticmethod
    def _validate_upload(upload: UploadPayload) -> None:
        if not upload.filename.lower().endswith(".pdf") or not upload.content.startswith(
            b"%PDF-"
        ):
            raise InvalidUploadError("upload must be a valid PDF upload")

    @staticmethod
    def _public_error_summary(error_summary: str | None) -> str | None:
        if error_summary is None or error_summary in SAFE_PROCESSING_ERROR_SUMMARIES:
            return error_summary
        return PUBLIC_PROCESSING_ERROR

    def _finish(
        self,
        paper,
        status: ProcessingStatus,
        *,
        error_summary: str | None = None,
    ) -> PaperSummary:
        paper = self.repository.update_paper_status(paper.id, status)
        self.repository.record_processing_status(
            paper.id, status, error_summary=error_summary
        )
        return self.get_summary(paper.id)
