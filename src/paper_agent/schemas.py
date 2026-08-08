from dataclasses import dataclass

from paper_agent.domain import Paper, ProcessingStatus


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
        cls, paper: Paper, *, error: str | None = None
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
        stage0_status, stage1_status = stage_statuses[paper.status]
        return cls(
            id=paper.id,
            original_filename=paper.original_filename,
            status=paper.status,
            stage0_status=stage0_status,
            stage1_status=stage1_status,
            error=error,
        )
