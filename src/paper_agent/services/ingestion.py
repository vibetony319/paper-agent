from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from paper_agent.config import Settings
from paper_agent.domain import DocumentElement, PaperDocument, ProcessingStatus
from paper_agent.parsers.base import PdfParseError
from paper_agent.parsers.alignment import TextAligner
from paper_agent.parsers.markitdown_stage1 import (
    MarkdownParseError,
    MarkItDownStage1Parser,
)
from paper_agent.parsers.pymupdf_stage0 import PyMuPdfStage0Parser
from paper_agent.schemas import PaperSummary, UploadPayload
from paper_agent.storage import PaperRepository


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
        with source_path.open("xb") as source_file:
            source_file.write(upload.content)

        paper = self.repository.update_paper_status(paper.id, ProcessingStatus.running)
        self.repository.record_processing_status(paper.id, ProcessingStatus.running)
        self.repository.record_processing_status(
            paper.id, ProcessingStatus.running, stage="stage0"
        )

        try:
            stage0 = self.stage0_parser.parse(source_path)
        except PdfParseError:
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
        for page in stage0.pages:
            self.repository.save_page(paper.id, page)
        for block in stage0.text_blocks:
            self.repository.save_element(
                paper.id,
                DocumentElement(
                    kind="text_block",
                    text=block.text,
                    page_number=block.page_number,
                    bbox=block.bbox,
                ),
            )
        for visual in stage0.visual_elements:
            self.repository.save_element(
                paper.id,
                DocumentElement(
                    kind=visual.kind,
                    text=visual.caption or "",
                    page_number=visual.page_number,
                    bbox=visual.bbox,
                ),
            )
        self.repository.record_processing_status(
            paper.id, ProcessingStatus.completed, stage="stage0"
        )

        self.repository.record_processing_status(
            paper.id, ProcessingStatus.running, stage="stage1"
        )

        try:
            stage1 = self._run_stage1(source_path)
        except MarkdownParseError:
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
        for section in stage1.sections:
            self.repository.save_section(paper.id, section)
        for element in self.aligner.align(stage1.paragraphs, stage0.text_blocks):
            self.repository.save_element(paper.id, replace(element, order=None))

        self.repository.record_processing_status(
            paper.id, ProcessingStatus.completed, stage="stage1"
        )
        return self._finish(paper, ProcessingStatus.completed)

    def get_document(self, paper_id: str) -> PaperDocument:
        document = self.repository.get_document(paper_id)
        if document is None:
            raise KeyError(paper_id)
        return document

    def get_source_path(self, paper_id: str) -> Path:
        paper = self.repository.get_paper(paper_id)
        if paper is None:
            raise KeyError(paper_id)
        return self.settings.data_dir / "papers" / paper.stored_filename

    def _run_stage1(self, source_path: Path):
        return self.stage1_parser.parse(source_path)

    @staticmethod
    def _validate_upload(upload: UploadPayload) -> None:
        if not upload.filename.lower().endswith(".pdf") or not upload.content.startswith(
            b"%PDF-"
        ):
            raise ValueError("upload must be a valid PDF upload")

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
        return PaperSummary.from_paper(paper, error=error_summary)
