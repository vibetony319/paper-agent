from pathlib import Path

import pytest

from paper_agent.config import Settings
from paper_agent.domain import ProcessingStatus
from paper_agent.parsers.base import PdfParseError
from paper_agent.parsers.markitdown_stage1 import MarkdownParseError
from paper_agent.schemas import UploadPayload
from paper_agent.services.ingestion import PaperIngestionService
from paper_agent.storage import PaperRepository


@pytest.fixture
def sample_pdf_bytes(sample_pdf: Path) -> bytes:
    return sample_pdf.read_bytes()


@pytest.fixture
def service(tmp_path: Path) -> PaperIngestionService:
    settings = Settings(
        data_dir=tmp_path / "data",
        database_url=f"sqlite:///{tmp_path / 'data' / 'paper-agent.db'}",
    )
    return PaperIngestionService(
        settings=settings,
        repository=PaperRepository(settings.database_url),
    )


def test_ingestion_persists_source_geometry_and_document(
    service: PaperIngestionService, sample_pdf_bytes: bytes
) -> None:
    """Breaks if ingestion omits durable source geometry or Stage 1 content."""
    paper = service.ingest(
        UploadPayload(
            filename="paper.pdf",
            content=sample_pdf_bytes,
            media_type="application/pdf",
        )
    )

    assert paper.status == ProcessingStatus.completed
    assert paper.stage0_status == ProcessingStatus.completed
    assert paper.stage1_status == ProcessingStatus.completed
    assert "path" not in paper.__dataclass_fields__
    document = service.get_document(paper.id)
    assert document.pages[0].number == 1
    assert any(element.location_status == "located" for element in document.elements)
    assert service.get_source_path(paper.id).read_bytes() == sample_pdf_bytes
    assert service.repository.get_processing_statuses(paper.id) == (
        ProcessingStatus.queued,
        ProcessingStatus.running,
        ProcessingStatus.completed,
    )
    assert service.repository.get_stage_statuses(paper.id, "stage0") == (
        ProcessingStatus.queued,
        ProcessingStatus.running,
        ProcessingStatus.completed,
    )
    assert service.repository.get_stage_statuses(paper.id, "stage1") == (
        ProcessingStatus.queued,
        ProcessingStatus.running,
        ProcessingStatus.completed,
    )


@pytest.mark.parametrize(
    ("filename", "content"),
    [
        ("paper.txt", b"%PDF-1.7\nsource"),
        ("paper.pdf", b"not a PDF"),
    ],
)
def test_ingestion_rejects_invalid_upload_before_storing_source(
    service: PaperIngestionService, filename: str, content: bytes
) -> None:
    """Breaks if an untrusted upload can create a paper or source file."""
    with pytest.raises(ValueError, match="valid PDF upload"):
        service.ingest(
            UploadPayload(filename=filename, content=content, media_type=None)
        )

    assert tuple((service.settings.data_dir / "papers").iterdir()) == ()


def test_stage0_failure_is_durable_and_uses_a_safe_error_summary(
    service: PaperIngestionService,
    sample_pdf_bytes: bytes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Breaks if parser paths leak or Stage 0 failures escape without state."""
    monkeypatch.setattr(
        service.stage0_parser,
        "parse",
        lambda _path: (_ for _ in ()).throw(PdfParseError("C:/private/broken.pdf")),
    )

    paper = service.ingest(
        UploadPayload(
            filename="paper.pdf",
            content=sample_pdf_bytes,
            media_type="application/pdf",
        )
    )

    assert paper.status == ProcessingStatus.failed
    assert paper.stage0_status == ProcessingStatus.failed
    assert paper.stage1_status == ProcessingStatus.queued
    assert paper.error == "The PDF could not be parsed."
    assert "private" not in paper.error
    assert service.repository.get_processing_error(paper.id) == paper.error
    assert service.repository.get_stage_statuses(paper.id, "stage0") == (
        ProcessingStatus.queued,
        ProcessingStatus.running,
        ProcessingStatus.failed,
    )
    assert service.repository.get_stage_statuses(paper.id, "stage1") == (
        ProcessingStatus.queued,
    )


def test_stage1_failure_keeps_stage0_and_marks_paper_partial(
    service: PaperIngestionService,
    sample_pdf_bytes: bytes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Breaks if Stage 1 failure erases source records or reports completion."""
    def fail_stage1(*_args, **_kwargs):
        raise MarkdownParseError("converter failed")

    monkeypatch.setattr(service, "_run_stage1", fail_stage1)

    paper = service.ingest(
        UploadPayload(
            filename="paper.pdf",
            content=sample_pdf_bytes,
            media_type="application/pdf",
        )
    )

    assert paper.status == ProcessingStatus.partial
    assert paper.stage0_status == ProcessingStatus.completed
    assert paper.stage1_status == ProcessingStatus.failed
    assert paper.error == "The PDF could not be converted."
    assert service.get_document(paper.id).pages
    assert service.repository.get_processing_error(paper.id) == paper.error
    assert service.repository.get_stage_statuses(paper.id, "stage0") == (
        ProcessingStatus.queued,
        ProcessingStatus.running,
        ProcessingStatus.completed,
    )
    assert service.repository.get_stage_statuses(paper.id, "stage1") == (
        ProcessingStatus.queued,
        ProcessingStatus.running,
        ProcessingStatus.failed,
    )
