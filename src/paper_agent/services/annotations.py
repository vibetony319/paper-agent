"""Application rules for paper text anchors, highlights, and notes."""

from paper_agent.annotation_storage import (
    AnnotationNotFoundError,
    PaperAnnotationRepository,
)
from paper_agent.annotations import (
    Highlight,
    NoteType,
    TextAnchorDraft,
    TextAnchorRect,
)
from paper_agent.domain import Note
from paper_agent.schemas import TextAnchorDraftRequest


def draft_from_request(payload: TextAnchorDraftRequest) -> TextAnchorDraft:
    return TextAnchorDraft(
        quote=payload.quote,
        page_number=payload.page_number,
        element_id=(
            None if payload.element_id is None else str(payload.element_id)
        ),
        rects=tuple(
            TextAnchorRect(
                order=rect.order,
                x0=rect.x0,
                y0=rect.y0,
                x1=rect.x1,
                y1=rect.y1,
            )
            for rect in payload.rects
        ),
    )


class AnnotationService:
    def __init__(self, repository: PaperAnnotationRepository) -> None:
        self.repository = repository

    def require_paper(self, paper_id: str) -> None:
        if not self.repository.paper_exists(paper_id):
            raise AnnotationNotFoundError("paper was not found")

    def create_highlight(
        self,
        paper_id: str,
        draft: TextAnchorDraft,
        color: str,
        request_id: str,
    ) -> Highlight:
        return self.repository.create_highlight(
            paper_id, draft, color=color, request_id=request_id
        )

    def list_highlights(self, paper_id: str) -> tuple[Highlight, ...]:
        return self.repository.list_highlights(paper_id)

    def delete_highlight(self, paper_id: str, highlight_id: str) -> bool:
        return self.repository.delete_highlight(paper_id, highlight_id)

    def create_note(
        self,
        paper_id: str,
        *,
        body: str,
        element_id: str | None,
        page_number: int | None,
        anchor_draft: TextAnchorDraft | None,
        request_id: str | None,
    ) -> Note:
        return self.repository.create_note(
            paper_id,
            Note(
                body=body,
                element_id=element_id,
                page_number=page_number,
                note_type=NoteType.manual,
            ),
            anchor_draft=anchor_draft,
            request_id=request_id,
        )

    def update_note(
        self,
        paper_id: str,
        note_id: str,
        *,
        body: str,
        expected_updated_at,
    ) -> Note:
        return self.repository.update_note(
            paper_id, note_id, body, expected_updated_at
        )

    def delete_note(self, paper_id: str, note_id: str) -> bool:
        return self.repository.delete_note(paper_id, note_id)

    def get_notes(self, paper_id: str) -> tuple[Note, ...]:
        return self.repository.get_notes(paper_id)
