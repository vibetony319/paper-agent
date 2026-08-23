import pytest

from paper_agent.annotation_storage import (
    AnnotationInputError,
    IdempotencyConflictError,
    PaperAnnotationRepository,
)
from paper_agent.annotations import NoteType, TextAnchorDraft, TextAnchorRect
from paper_agent.domain import BoundingBox, DocumentElement, Note, Page, Paper
from paper_agent.model_profiles import ModelSnapshot
from paper_agent.storage import PaperRepository


@pytest.fixture
def repository(tmp_path) -> PaperRepository:
    return PaperRepository(f"sqlite:///{tmp_path / 'paper-agent.db'}")


@pytest.fixture
def annotations(repository: PaperRepository) -> PaperAnnotationRepository:
    return PaperAnnotationRepository(engine=repository.engine)


@pytest.fixture
def prepared_paper(repository: PaperRepository) -> Paper:
    paper = repository.create_paper(
        original_filename="paper.pdf", stored_filename="paper.pdf"
    )
    repository.save_page(paper.id, Page(number=1, width=600, height=800))
    repository.save_element(
        paper.id,
        DocumentElement(
            id="element-1",
            kind="paragraph",
            text="The Token Router sends tokens to experts.",
            page_number=1,
            bbox=BoundingBox(0.1, 0.2, 0.7, 0.24),
        ),
    )
    return paper


def _draft(
    page: int = 1,
    quote: str = "routing tokens",
    rect_count: int = 2,
    element_id: str | None = "element-1",
) -> TextAnchorDraft:
    return TextAnchorDraft(
        quote=quote,
        page_number=page,
        element_id=element_id,
        rects=tuple(
            TextAnchorRect(
                order=index,
                x0=0.1,
                y0=0.2 + index * 0.05,
                x1=0.5,
                y1=0.24 + index * 0.05,
            )
            for index in range(rect_count)
        ),
    )


def _manual_note(body: str = "手写笔记") -> Note:
    return Note(body=body)


def _snapshot() -> ModelSnapshot:
    return ModelSnapshot(
        profile_id="11111111-1111-4111-8111-111111111111",
        display_name="本地 Qwen",
        base_url="http://127.0.0.1:8001/v1",
        model_name="Qwen3-32B",
        revision=1,
    )


def _generated_note() -> Note:
    return Note(
        body="这是模型生成的解释。",
        note_type=NoteType.explanation,
        model_profile_id="11111111-1111-4111-8111-111111111111",
        model_snapshot=_snapshot(),
        ai_generated=True,
    )


def test_highlight_round_trip_preserves_rect_order(
    annotations: PaperAnnotationRepository, prepared_paper: Paper
) -> None:
    highlight = annotations.create_highlight(
        prepared_paper.id,
        _draft(page=1, quote="routing tokens", rect_count=2),
        color="yellow",
        request_id="highlight-request-a",
    )

    loaded = annotations.list_highlights(prepared_paper.id)

    assert loaded == (highlight,)
    assert [rect.order for rect in loaded[0].anchor.rects] == [0, 1]


def test_generated_note_and_anchor_are_atomic(
    annotations: PaperAnnotationRepository,
    prepared_paper: Paper,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_insert_anchor(*_args, **_kwargs):
        raise RuntimeError("anchor insert failed")

    monkeypatch.setattr(annotations, "_insert_anchor", fail_insert_anchor)

    with pytest.raises(RuntimeError, match="anchor insert failed"):
        annotations.create_note(
            prepared_paper.id,
            _generated_note(),
            anchor_draft=_draft(page=1),
            request_id="assist-a",
        )

    assert annotations.get_notes(prepared_paper.id) == ()
    assert annotations.list_anchors(prepared_paper.id) == ()


def test_annotation_rejects_element_from_another_paper(
    annotations: PaperAnnotationRepository,
    prepared_paper: Paper,
    repository: PaperRepository,
) -> None:
    other = repository.create_paper(
        original_filename="other.pdf", stored_filename="other.pdf"
    )
    repository.save_page(other.id, Page(number=1, width=100, height=100))
    repository.save_element(
        other.id,
        DocumentElement(
            id="other-element",
            kind="paragraph",
            text="other paper text",
            page_number=1,
            bbox=BoundingBox(0.1, 0.2, 0.7, 0.24),
        ),
    )

    with pytest.raises(AnnotationInputError, match="element"):
        annotations.create_highlight(
            prepared_paper.id,
            _draft(element_id="other-element"),
            request_id="cross-paper-element",
        )


def test_annotation_rejects_unknown_page_and_invalid_color(
    annotations: PaperAnnotationRepository, prepared_paper: Paper
) -> None:
    with pytest.raises(AnnotationInputError, match="page"):
        annotations.create_highlight(
            prepared_paper.id, _draft(page=99, element_id=None)
        )

    with pytest.raises(AnnotationInputError, match="yellow"):
        annotations.create_highlight(
            prepared_paper.id, _draft(), color="green"
        )


def test_duplicate_requests_return_same_object_and_reject_conflicting_fields(
    annotations: PaperAnnotationRepository, prepared_paper: Paper
) -> None:
    first = annotations.create_highlight(
        prepared_paper.id,
        _draft(quote="routing tokens"),
        request_id="highlight-idempotent",
    )
    repeated = annotations.create_highlight(
        prepared_paper.id,
        _draft(quote="routing tokens"),
        request_id="highlight-idempotent",
    )
    assert repeated.id == first.id

    with pytest.raises(IdempotencyConflictError):
        annotations.create_highlight(
            prepared_paper.id,
            _draft(quote="different quote"),
            request_id="highlight-idempotent",
        )

    note = annotations.create_note(
        prepared_paper.id,
        _manual_note("稳定笔记"),
        request_id="note-idempotent",
    )
    repeated_note = annotations.create_note(
        prepared_paper.id,
        _manual_note("稳定笔记"),
        request_id="note-idempotent",
    )
    assert repeated_note.id == note.id

    with pytest.raises(IdempotencyConflictError):
        annotations.create_note(
            prepared_paper.id,
            _manual_note("另一个正文"),
            request_id="note-idempotent",
        )


def test_delete_highlight_keeps_note_anchor(
    annotations: PaperAnnotationRepository, prepared_paper: Paper
) -> None:
    draft = _draft(quote="routing tokens")
    highlight = annotations.create_highlight(
        prepared_paper.id, draft, request_id="highlight-delete"
    )
    note = annotations.create_note(
        prepared_paper.id,
        _manual_note("锚点笔记"),
        anchor_draft=draft,
        request_id="note-with-anchor",
    )

    assert annotations.delete_highlight(prepared_paper.id, highlight.id) is True
    assert annotations.list_highlights(prepared_paper.id) == ()
    assert annotations.get_note(prepared_paper.id, note.id).anchor_ids


def test_delete_note_keeps_highlight(
    annotations: PaperAnnotationRepository, prepared_paper: Paper
) -> None:
    draft = _draft(quote="routing tokens")
    highlight = annotations.create_highlight(
        prepared_paper.id, draft, request_id="highlight-keep"
    )
    note = annotations.create_note(
        prepared_paper.id,
        _manual_note("要删除的笔记"),
        anchor_draft=draft,
        request_id="note-delete",
    )

    assert annotations.delete_note(prepared_paper.id, note.id) is True
    assert annotations.list_highlights(prepared_paper.id) == (highlight,)


def test_note_update_uses_updated_at_optimistic_guard(
    annotations: PaperAnnotationRepository, prepared_paper: Paper
) -> None:
    note = annotations.create_note(
        prepared_paper.id, _manual_note("旧正文"), request_id="note-edit"
    )

    with pytest.raises(IdempotencyConflictError):
        annotations.update_note(
            prepared_paper.id, note.id, "冲突正文", expected_updated_at=None
        )

    updated = annotations.update_note(
        prepared_paper.id,
        note.id,
        "新正文",
        expected_updated_at=note.updated_at,
    )
    assert updated.body == "新正文"
    assert updated.user_edited is True
