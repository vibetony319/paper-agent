from pathlib import Path
from uuid import UUID

import pytest

import paper_agent.services.ingestion as ingestion_module
from paper_agent.config import Settings
from paper_agent.domain import BoundingBox, DocumentElement, ProcessingStatus, Section
from paper_agent.parsers.base import PdfParseError
from paper_agent.parsers.markitdown_stage1 import MarkdownDocument, MarkdownParseError
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
    paragraph = next(
        element
        for element in document.elements
        if element.kind == "paragraph" and element.text == "Introduction"
    )
    assert paragraph.location_status == "located"
    assert paragraph.page_number == 1
    assert paragraph.bbox is not None
    assert (
        paragraph.bbox.x0,
        paragraph.bbox.y0,
        paragraph.bbox.x1,
        paragraph.bbox.y1,
    ) == pytest.approx((0.1, 0.040875, 0.387375, 0.116445))
    assert service.get_source_path(paper.id).read_bytes() == sample_pdf_bytes
    assert service.repository.get_paper(paper.id).source_published is True
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


def test_encrypted_pdf_is_a_durable_stage0_failure(
    service: PaperIngestionService, encrypted_pdf: Path
) -> None:
    """Breaks if a valid encrypted upload is rejected as an invalid API input."""
    paper = service.ingest(
        UploadPayload(
            filename="locked.pdf",
            content=encrypted_pdf.read_bytes(),
            media_type="application/pdf",
        )
    )

    assert paper.status == ProcessingStatus.failed
    assert paper.stage0_status == ProcessingStatus.failed
    assert paper.stage1_status == ProcessingStatus.queued
    assert paper.error == "The PDF could not be parsed."
    assert service.get_source_path(paper.id).is_file()


def test_temporary_source_write_failure_is_durable_and_removes_partial_artifact(
    service: PaperIngestionService,
    sample_pdf_bytes: bytes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Breaks if partial upload data is published or leaks local write details."""
    original_open = Path.open

    class PartialWriteFile:
        def __init__(self, source_file) -> None:
            self.source_file = source_file

        def __enter__(self):
            self.source_file.__enter__()
            return self

        def __exit__(self, *args) -> None:
            self.source_file.__exit__(*args)

        def write(self, _content: bytes) -> int:
            self.source_file.write(b"partial PDF data")
            raise OSError("C:/private/source-write-failure.pdf")

    def write_partial_temporary_source_then_fail(path: Path, *args, **kwargs):
        source_file = original_open(path, *args, **kwargs)
        if path.suffix == ".tmp":
            return PartialWriteFile(source_file)
        return source_file

    monkeypatch.setattr(Path, "open", write_partial_temporary_source_then_fail)

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
    assert paper.error == "The PDF source could not be stored."
    assert "private" not in paper.error
    assert service.repository.get_processing_error(paper.id) == paper.error
    assert service.repository.get_processing_statuses(paper.id) == (
        ProcessingStatus.queued,
        ProcessingStatus.failed,
    )
    assert service.repository.get_stage_statuses(paper.id, "stage0") == (
        ProcessingStatus.queued,
        ProcessingStatus.failed,
    )
    assert service.repository.get_stage_statuses(paper.id, "stage1") == (
        ProcessingStatus.queued,
    )
    assert tuple((service.settings.data_dir / "papers").iterdir()) == ()


def test_temporary_create_collision_does_not_delete_an_unowned_file(
    service: PaperIngestionService,
    sample_pdf_bytes: bytes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Breaks if cleanup removes a file the failed upload never owned."""
    papers_dir = service.settings.data_dir / "papers"
    unrelated_file = papers_dir / "unrelated-upload.tmp"
    unrelated_file.write_bytes(b"other upload")
    original_open = Path.open

    def reject_temporary_create(path: Path, *args, **kwargs):
        mode = args[0] if args else kwargs.get("mode", "r")
        if path.suffix == ".tmp" and "x" in mode:
            raise FileExistsError("C:/private/temporary-file-collision.tmp")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", reject_temporary_create)

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
    assert paper.error == "The PDF source could not be stored."
    assert "private" not in paper.error
    assert service.repository.get_processing_error(paper.id) == paper.error
    assert unrelated_file.read_bytes() == b"other upload"
    with pytest.raises(KeyError):
        service.get_source_path(paper.id)
    assert not (papers_dir / f"{paper.id}.pdf").exists()


def test_final_source_collision_preserves_existing_pdf_and_fails_safely(
    service: PaperIngestionService,
    sample_pdf_bytes: bytes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Breaks if publishing a temporary source overwrites an existing final PDF."""
    paper_id = UUID("00000000-0000-0000-0000-000000000101")
    temporary_id = UUID("00000000-0000-0000-0000-000000000102")
    uuid_values = iter((paper_id, temporary_id))
    monkeypatch.setattr(ingestion_module, "uuid4", lambda: next(uuid_values))
    papers_dir = service.settings.data_dir / "papers"
    source_path = papers_dir / f"{paper_id}.pdf"
    temporary_source_path = papers_dir / f".{paper_id}-{temporary_id}.tmp"
    source_path.write_bytes(b"existing final PDF")

    paper = service.ingest(
        UploadPayload(
            filename="paper.pdf",
            content=sample_pdf_bytes,
            media_type="application/pdf",
        )
    )

    assert paper.id == str(paper_id)
    assert paper.status == ProcessingStatus.failed
    assert paper.stage0_status == ProcessingStatus.failed
    assert paper.stage1_status == ProcessingStatus.queued
    assert paper.error == "The PDF source could not be stored."
    assert source_path.read_bytes() == b"existing final PDF"
    assert not temporary_source_path.exists()
    assert service.repository.get_paper(paper.id).source_published is False


def test_temporary_source_collision_preserves_unowned_file_and_fails_safely(
    service: PaperIngestionService,
    sample_pdf_bytes: bytes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Breaks if a temporary-name collision replaces or deletes an unowned file."""
    paper_id = UUID("00000000-0000-0000-0000-000000000201")
    temporary_id = UUID("00000000-0000-0000-0000-000000000202")
    uuid_values = iter((paper_id, temporary_id))
    monkeypatch.setattr(ingestion_module, "uuid4", lambda: next(uuid_values))
    papers_dir = service.settings.data_dir / "papers"
    source_path = papers_dir / f"{paper_id}.pdf"
    temporary_source_path = papers_dir / f".{paper_id}-{temporary_id}.tmp"
    temporary_source_path.write_bytes(b"unowned temporary PDF")

    paper = service.ingest(
        UploadPayload(
            filename="paper.pdf",
            content=sample_pdf_bytes,
            media_type="application/pdf",
        )
    )

    assert paper.id == str(paper_id)
    assert paper.status == ProcessingStatus.failed
    assert paper.stage0_status == ProcessingStatus.failed
    assert paper.stage1_status == ProcessingStatus.queued
    assert paper.error == "The PDF source could not be stored."
    assert temporary_source_path.read_bytes() == b"unowned temporary PDF"
    assert not source_path.exists()


def test_temporary_cleanup_failure_after_publish_keeps_paper_successful(
    service: PaperIngestionService,
    sample_pdf_bytes: bytes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Breaks if cleanup of a published temporary link reverses a valid ingest."""
    paper_id = UUID("00000000-0000-0000-0000-000000000301")
    temporary_id = UUID("00000000-0000-0000-0000-000000000302")
    uuid_values = iter((paper_id, temporary_id))
    monkeypatch.setattr(ingestion_module, "uuid4", lambda: next(uuid_values))
    papers_dir = service.settings.data_dir / "papers"
    source_path = papers_dir / f"{paper_id}.pdf"
    temporary_source_path = papers_dir / f".{paper_id}-{temporary_id}.tmp"
    original_unlink = Path.unlink

    def fail_temporary_cleanup(path: Path, *args, **kwargs) -> None:
        if path == temporary_source_path:
            raise OSError("C:/private/temporary-cleanup-failure.tmp")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_temporary_cleanup)

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
    assert source_path.read_bytes() == sample_pdf_bytes
    assert temporary_source_path.read_bytes() == sample_pdf_bytes
    assert source_path.samefile(temporary_source_path)


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


def test_stage1_unexpected_value_error_propagates(
    service: PaperIngestionService,
    sample_pdf_bytes: bytes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Breaks if a Stage 1 implementation defect is hidden as a conversion failure."""
    paper_id = UUID("00000000-0000-0000-0000-000000000501")
    temporary_id = UUID("00000000-0000-0000-0000-000000000502")
    uuid_values = iter((paper_id, temporary_id))
    monkeypatch.setattr(ingestion_module, "uuid4", lambda: next(uuid_values))

    def fail_stage1(*_args, **_kwargs):
        raise ValueError("unexpected stage1 defect")

    monkeypatch.setattr(service, "_run_stage1", fail_stage1)

    with pytest.raises(ValueError, match="unexpected stage1 defect"):
        service.ingest(
            UploadPayload(
                filename="paper.pdf",
                content=sample_pdf_bytes,
                media_type="application/pdf",
            )
        )

    paper = service.get_summary(str(paper_id))
    assert paper.status == ProcessingStatus.partial
    assert paper.stage0_status == ProcessingStatus.completed
    assert paper.stage1_status == ProcessingStatus.failed
    assert paper.error == "The PDF could not be converted."


def test_stage1_alignment_value_error_keeps_stage0_and_marks_paper_partial(
    service: PaperIngestionService,
    sample_pdf_bytes: bytes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Breaks if an alignment domain failure escapes the Stage 1 failure boundary."""
    monkeypatch.setattr(
        service.aligner,
        "align",
        lambda *_args: (_ for _ in ()).throw(ValueError("invalid alignment")),
    )

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


def test_stage1_persistence_failure_rolls_back_sections_and_paragraphs(
    service: PaperIngestionService,
    sample_pdf_bytes: bytes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Breaks if a mid-transaction Stage 1 write leaves semantic records behind."""
    section = Section(title="Introduction", order=0)
    duplicate_id = "duplicate-stage1-paragraph"
    elements = (
        DocumentElement(
            kind="paragraph",
            text="First paragraph",
            page_number=1,
            bbox=BoundingBox(0.1, 0.1, 0.5, 0.2),
            section_id=section.id,
            id=duplicate_id,
        ),
        DocumentElement(
            kind="paragraph",
            text="Second paragraph",
            page_number=1,
            bbox=BoundingBox(0.1, 0.2, 0.5, 0.3),
            section_id=section.id,
            id=duplicate_id,
        ),
    )
    monkeypatch.setattr(
        service,
        "_run_stage1",
        lambda _path: MarkdownDocument(sections=(section,), paragraphs=()),
    )
    monkeypatch.setattr(service.aligner, "align", lambda *_args: elements)

    paper = service.ingest(
        UploadPayload(
            filename="paper.pdf",
            content=sample_pdf_bytes,
            media_type="application/pdf",
        )
    )

    document = service.get_document(paper.id)
    assert paper.status == ProcessingStatus.partial
    assert paper.stage0_status == ProcessingStatus.completed
    assert paper.stage1_status == ProcessingStatus.failed
    assert paper.error == "The PDF could not be converted."
    assert document.pages
    assert any(element.kind == "text_block" for element in document.elements)
    assert document.sections == ()
    assert not [element for element in document.elements if element.kind == "paragraph"]


def test_stage0_second_element_persistence_failure_is_atomic_and_durable(
    service: PaperIngestionService,
    visual_pdf: Path,
) -> None:
    """Breaks if an SQLite Stage 0 write failure leaves source rows or running state."""
    with service.repository.engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TRIGGER fail_stage0_drawing
            BEFORE INSERT ON document_elements
            WHEN NEW.kind = 'drawing'
            BEGIN
                SELECT RAISE(FAIL, 'stage0 drawing insert failed');
            END;
            """
        )

    source = visual_pdf.read_bytes()
    paper = service.ingest(
        UploadPayload(
            filename="visuals.pdf",
            content=source,
            media_type="application/pdf",
        )
    )

    assert paper.status == ProcessingStatus.failed
    assert paper.stage0_status == ProcessingStatus.failed
    assert paper.stage1_status == ProcessingStatus.queued
    assert paper.error == "The PDF could not be parsed."
    assert service.repository.get_pages(paper.id) == ()
    assert service.repository.get_elements(paper.id) == ()
    assert service.get_source_path(paper.id).read_bytes() == source
    assert service.repository.get_paper(paper.id).source_published is True
