"""Crash-recoverable permanent deletion of papers and their source PDFs."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import time
from uuid import uuid4

from paper_agent.storage import PaperRepository


_MARKER_SUFFIX = ".delete.json"
_RETRY_DELAYS = (0.05, 0.15, 0.35)


class PaperDeletionNotFoundError(RuntimeError):
    pass


class PaperDeletionPathError(RuntimeError):
    pass


class PaperDeletionBusyError(RuntimeError):
    pass


def managed_source_path(papers_dir: Path, stored_filename: str) -> Path:
    if Path(stored_filename).name != stored_filename:
        raise PaperDeletionPathError("stored filename is not a basename")
    root = papers_dir.resolve(strict=True)
    unresolved = root / stored_filename
    if unresolved.is_symlink():
        raise PaperDeletionPathError("paper source cannot be a symbolic link")
    try:
        candidate = unresolved.resolve(strict=True)
    except FileNotFoundError as error:
        raise PaperDeletionNotFoundError("paper source was not found") from error
    if not candidate.is_relative_to(root):
        raise PaperDeletionPathError("paper source escaped managed storage")
    return candidate


@dataclass(frozen=True)
class DeletionJournal:
    paper_id: str
    original_name: str
    trash_name: str
    state: str
    created_at: str


@dataclass(frozen=True)
class DeletionRecoveryReport:
    restored: tuple[str, ...] = ()
    cleaned: tuple[str, ...] = ()
    damaged_markers: tuple[str, ...] = ()


class PaperDeletionService:
    def __init__(self, repository: PaperRepository, *, papers_dir: Path, trash_dir: Path) -> None:
        self.repository = repository
        self.papers_dir = papers_dir
        self.trash_dir = trash_dir
        self.papers_dir.mkdir(parents=True, exist_ok=True)
        self.trash_dir.mkdir(parents=True, exist_ok=True)

    def delete(self, paper_id: str) -> None:
        paper = self.repository.get_paper(paper_id)
        if paper is None:
            raise PaperDeletionNotFoundError("paper was not found")
        source = managed_source_path(self.papers_dir, paper.stored_filename)
        trash_name = f"{paper.id}-{uuid4().hex}.pdf"
        marker_path = self.trash_dir / f"{paper.id}{_MARKER_SUFFIX}"
        journal = DeletionJournal(
            paper_id=paper.id,
            original_name=source.name,
            trash_name=trash_name,
            state="prepared",
            created_at=datetime.now(UTC).isoformat(),
        )
        self._write_marker(marker_path, journal)
        trash_path = self.trash_dir / trash_name
        try:
            self._replace_with_retries(source, trash_path)
            self._write_marker(
                marker_path, replace(journal, state="staged")
            )
            self.repository.delete_paper_data(paper_id)
        except Exception:
            if trash_path.exists():
                try:
                    self._replace_with_retries(trash_path, source)
                    marker_path.unlink(missing_ok=True)
                except Exception:
                    # Recovery will retry the restore from the durable marker.
                    pass
            else:
                marker_path.unlink(missing_ok=True)
            raise

        # The database commit is irreversible here. Cleanup failures must keep
        # the staged journal for recovery, never restore an orphan source PDF.
        self._write_marker(
            marker_path, replace(journal, state="cleanup_pending")
        )
        try:
            trash_path.unlink()
        except OSError:
            return
        marker_path.unlink(missing_ok=True)

    def recover_pending(self) -> DeletionRecoveryReport:
        restored: list[str] = []
        cleaned: list[str] = []
        damaged: list[str] = []
        for marker_path in sorted(self.trash_dir.glob(f"*{_MARKER_SUFFIX}")):
            try:
                journal = self._read_marker(marker_path)
                if journal is None:
                    damaged.append(marker_path.name)
                    continue
                if not self._journal_names_are_safe(journal):
                    damaged.append(marker_path.name)
                    continue
                paper_exists = self.repository.get_paper(journal.paper_id) is not None
                trash_path = self.trash_dir / journal.trash_name
                source_path = self.papers_dir / journal.original_name
                if paper_exists:
                    if trash_path.exists():
                        self._replace_with_retries(trash_path, source_path)
                    marker_path.unlink(missing_ok=True)
                    restored.append(journal.paper_id)
                else:
                    trash_path.unlink(missing_ok=True)
                    marker_path.unlink(missing_ok=True)
                    cleaned.append(journal.paper_id)
            except Exception:
                damaged.append(marker_path.name)
        return DeletionRecoveryReport(
            restored=tuple(restored),
            cleaned=tuple(cleaned),
            damaged_markers=tuple(damaged),
        )

    def _stage_for_test(self, paper_id: str, source_pdf: Path) -> DeletionJournal:
        trash_name = f"{paper_id}-{uuid4().hex}.pdf"
        journal = DeletionJournal(
            paper_id=paper_id,
            original_name=source_pdf.name,
            trash_name=trash_name,
            state="staged",
            created_at=datetime.now(UTC).isoformat(),
        )
        self._write_marker(self.trash_dir / f"{paper_id}{_MARKER_SUFFIX}", journal)
        source_pdf.replace(self.trash_dir / trash_name)
        return journal

    def _write_marker(self, path: Path, journal: DeletionJournal) -> None:
        temporary = path.with_suffix(f"{path.suffix}.tmp")
        temporary.write_text(
            json.dumps(
                {
                    "paper_id": journal.paper_id,
                    "original_name": journal.original_name,
                    "trash_name": journal.trash_name,
                    "state": journal.state,
                    "created_at": journal.created_at,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        temporary.replace(path)

    @staticmethod
    def _read_marker(path: Path) -> DeletionJournal | None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            journal = DeletionJournal(
                paper_id=payload["paper_id"],
                original_name=payload["original_name"],
                trash_name=payload["trash_name"],
                state=payload["state"],
                created_at=payload["created_at"],
            )
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None
        if journal.state not in {"prepared", "staged", "cleanup_pending"}:
            return None
        return journal

    @staticmethod
    def _journal_names_are_safe(journal: DeletionJournal) -> bool:
        return (
            Path(journal.original_name).name == journal.original_name
            and Path(journal.trash_name).name == journal.trash_name
            and bool(journal.original_name)
            and bool(journal.trash_name)
        )

    @staticmethod
    def _replace_with_retries(source: Path, target: Path) -> None:
        last_error: OSError | None = None
        for delay in _RETRY_DELAYS:
            try:
                source.replace(target)
                return
            except OSError as error:
                last_error = error
                time.sleep(delay)
        if last_error is not None:
            raise PaperDeletionBusyError("paper source is busy") from last_error
