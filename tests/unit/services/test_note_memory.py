from datetime import UTC, datetime

from paper_agent.annotations import NoteType, TextAnchorDraft, TextAnchorRect
from paper_agent.domain import Note
from paper_agent.services.note_memory import NoteMemoryService, searchable_terms


class FakeNotesRepository:
    def __init__(self, notes_by_paper=None) -> None:
        self.notes_by_paper = notes_by_paper or {}

    def get_notes(self, paper_id: str) -> tuple[Note, ...]:
        return tuple(self.notes_by_paper.get(paper_id, ()))

    def list_anchors(self, paper_id: str) -> tuple:
        return ()

    def replace_notes(self, paper_id: str, notes: tuple[Note, ...]) -> None:
        self.notes_by_paper[paper_id] = notes


def _note(
    note_id: str,
    body: str,
    note_type: NoteType = NoteType.manual,
    *,
    page_number: int | None = None,
    user_edited: bool = False,
    updated_at: datetime | None = None,
) -> Note:
    return Note(
        id=note_id,
        body=body,
        note_type=note_type,
        page_number=page_number,
        user_edited=user_edited,
        updated_at=updated_at,
    )


def _selection(page: int = 2, quote: str = "负载均衡") -> TextAnchorDraft:
    return TextAnchorDraft(
        quote=quote,
        page_number=page,
        rects=(TextAnchorRect(0, 0.1, 0.2, 0.7, 0.25),),
    )


def test_searchable_terms_handles_chinese_bigrams_and_latin_tokens() -> None:
    terms = searchable_terms("负载均衡 loss_router")

    assert "负载" in terms
    assert "均衡" in terms
    assert "loss_router" in terms


def test_retrieval_matches_chinese_bigrams_and_prefers_manual_note() -> None:
    repository = FakeNotesRepository()
    service = NoteMemoryService(repository)
    manual = _note("manual-note", "路由负载均衡损失")
    generated = _note(
        "generated-note", "路由负载均衡损失", NoteType.explanation
    )
    repository.replace_notes("paper-a", (generated, manual))

    context = service.retrieve("paper-a", "负载是怎么均衡的？", selection=None)

    assert [reference.note_id for reference in context.references[:2]] == [
        manual.id,
        generated.id,
    ]
    assert "不可信笔记参考" in context.prompt_block
    assert "AI解释" in context.prompt_block
    assert "不能替代论文原文证据" in context.prompt_block


def test_retrieval_never_exceeds_character_budget() -> None:
    repository = FakeNotesRepository()
    service = NoteMemoryService(repository)
    repository.replace_notes(
        "paper-a", tuple(_note(f"note-{index}", "x" * 1200) for index in range(10))
    )

    context = service.retrieve("paper-a", "x", selection=None, max_chars=2500)

    assert len(context.prompt_block) <= 2500


def test_retrieval_isolates_papers() -> None:
    repository = FakeNotesRepository(
        {
            "paper-a": (_note("note-a", "路由负载均衡损失"),),
            "paper-b": (_note("note-b", "路由负载均衡损失"),),
        }
    )
    service = NoteMemoryService(repository)

    context = service.retrieve("paper-a", "负载均衡", selection=None)

    assert [reference.note_id for reference in context.references] == ["note-a"]


def test_selection_page_and_quote_boost_matching_notes() -> None:
    repository = FakeNotesRepository(
        {
            "paper-a": (
                _note("far", "同页无关内容", page_number=9),
                _note("same-page", "同页内容", page_number=2),
                _note("adjacent", "相邻页内容", page_number=3),
            )
        }
    )
    service = NoteMemoryService(repository)

    context = service.retrieve(
        "paper-a", "同页内容", selection=_selection(page=2, quote="同页内容")
    )

    ids = [reference.note_id for reference in context.references]
    assert ids[0] == "same-page"
    assert "adjacent" in ids


def test_zero_score_notes_are_returned_only_on_same_selection_page() -> None:
    repository = FakeNotesRepository(
        {
            "paper-a": (
                _note("unrelated-same-page", "香蕉", page_number=2),
                _note("unrelated-other-page", "香蕉", page_number=4),
            )
        }
    )
    service = NoteMemoryService(repository)

    context = service.retrieve(
        "paper-a", "苹果", selection=_selection(page=2)
    )

    assert [reference.note_id for reference in context.references] == [
        "unrelated-same-page"
    ]


def test_retrieval_returns_empty_context_when_nothing_matches() -> None:
    repository = FakeNotesRepository(
        {"paper-a": (_note("note-a", "香蕉"),)}
    )
    service = NoteMemoryService(repository)

    context = service.retrieve("paper-a", "苹果", selection=None)

    assert context.prompt_block == ""
    assert context.references == ()


def test_sorting_is_deterministic_for_equal_scores() -> None:
    timestamp = datetime(2026, 8, 23, tzinfo=UTC)
    repository = FakeNotesRepository(
        {
            "paper-a": (
                _note("note-b", "负载均衡", updated_at=timestamp),
                _note("note-a", "负载均衡", updated_at=timestamp),
            )
        }
    )
    service = NoteMemoryService(repository)

    first = service.retrieve("paper-a", "负载均衡")
    second = service.retrieve("paper-a", "负载均衡")

    assert [item.note_id for item in first.references] == [
        item.note_id for item in second.references
    ]
