from pathlib import Path

import pytest

from paper_agent.domain import Paper
from paper_agent.services.paper_deletion import (
    PaperDeletionNotFoundError,
    PaperDeletionPathError,
    PaperDeletionService,
    managed_source_path,
)
from paper_agent.storage import PaperRepository


@pytest.fixture
def repository(tmp_path: Path) -> PaperRepository:
    return PaperRepository(f"sqlite:///{tmp_path / 'paper-agent.db'}")


@pytest.fixture
def dirs(tmp_path: Path) -> tuple[Path, Path]:
    papers_dir = tmp_path / "papers"
    trash_dir = tmp_path / ".trash"
    papers_dir.mkdir()
    trash_dir.mkdir()
    return papers_dir, trash_dir


@pytest.fixture
def service(repository: PaperRepository, dirs) -> PaperDeletionService:
    papers_dir, trash_dir = dirs
    return PaperDeletionService(
        repository, papers_dir=papers_dir, trash_dir=trash_dir
    )


@pytest.fixture
def paper(repository: PaperRepository) -> Paper:
    return repository.create_paper(
        original_filename="paper-a.pdf", stored_filename="paper-a.pdf"
    )


@pytest.fixture
def source_pdf(dirs, paper: Paper) -> Path:
    papers_dir, _ = dirs
    path = papers_dir / paper.stored_filename
    path.write_bytes(b"%PDF-1.4")
    return path


def test_delete_restores_source_when_database_transaction_fails(
    service: PaperDeletionService,
    repository: PaperRepository,
    paper: Paper,
    source_pdf: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        repository, "delete_paper_data", lambda _paper_id: (_ for _ in ()).throw(RuntimeError())
    )

    with pytest.raises(RuntimeError):
        service.delete(paper.id)

    assert source_pdf.exists()
    assert list(service.trash_dir.glob("paper-a-*.pdf")) == []
    assert list(service.trash_dir.glob("*.delete.json")) == []
    assert repository.get_paper(paper.id) is not None


def test_startup_recovery_restores_staged_file_when_paper_row_exists(
    service: PaperDeletionService, paper: Paper, source_pdf: Path
) -> None:
    journal = service._stage_for_test(paper.id, source_pdf)
    assert not source_pdf.exists()

    report = service.recover_pending()

    assert source_pdf.exists()
    assert report.restored == (journal.paper_id,)


def test_startup_recovery_cleans_trash_when_database_was_deleted(
    service: PaperDeletionService,
    repository: PaperRepository,
    paper: Paper,
    source_pdf: Path,
) -> None:
    journal = service._stage_for_test(paper.id, source_pdf)
    repository.delete_paper_data(paper.id)

    report = service.recover_pending()

    assert report.cleaned == (journal.paper_id,)
    assert not (service.trash_dir / journal.trash_name).exists()


def test_path_escape_is_rejected_without_touching_external_file(
    service: PaperDeletionService,
    repository: PaperRepository,
    paper: Paper,
    tmp_path: Path,
) -> None:
    external = tmp_path / "outside.pdf"
    external.write_bytes(b"%PDF-1.4")
    repository.force_stored_filename(paper.id, str(external))

    with pytest.raises(PaperDeletionPathError):
        service.delete(paper.id)

    assert external.exists()


def test_missing_paper_and_source_are_not_found(
    service: PaperDeletionService, paper: Paper
) -> None:
    with pytest.raises(PaperDeletionNotFoundError):
        service.delete(paper.id)
    with pytest.raises(PaperDeletionNotFoundError):
        service.delete("missing-paper")


def test_damaged_marker_is_preserved_and_reported(
    service: PaperDeletionService, paper: Paper
) -> None:
    marker = service.trash_dir / f"{paper.id}.delete.json"
    marker.write_text("{broken", encoding="utf-8")

    report = service.recover_pending()

    assert marker.exists()
    assert report.damaged_markers == (marker.name,)


def test_windows_file_lock_retries_then_raises_busy(
    service: PaperDeletionService,
    paper: Paper,
    source_pdf: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_replace = Path.replace
    attempts = 0

    def locked_replace(self, target):
        nonlocal attempts
        if not (self.parent == service.papers_dir and self.suffix == ".pdf"):
            return original_replace(self, target)
        attempts += 1
        if attempts < 3:
            raise PermissionError("file is locked")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", locked_replace)

    service.delete(paper.id)

    assert attempts == 3
    assert not source_pdf.exists()


def test_final_cleanup_failure_keeps_marker_for_recovery(
    service: PaperDeletionService,
    paper: Paper,
    source_pdf: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_unlink = Path.unlink

    def failing_unlink(self, *args, **kwargs):
        if self.name.endswith(".pdf") and self.parent == service.trash_dir:
            raise PermissionError("trash file is locked")
        return original_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", failing_unlink)

    service.delete(paper.id)

    assert list(service.trash_dir.glob("*.delete.json"))


def test_managed_source_path_resolves_only_managed_files(
    dirs,
) -> None:
    papers_dir, _ = dirs
    (papers_dir / "paper.pdf").write_bytes(b"%PDF-1.4")
    assert managed_source_path(papers_dir, "paper.pdf") == (
        papers_dir / "paper.pdf"
    ).resolve()
    with pytest.raises(PaperDeletionPathError):
        managed_source_path(papers_dir, "../paper.pdf")
