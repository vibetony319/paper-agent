import json

import pytest

from paper_agent.annotation_storage import PaperAnnotationRepository
from paper_agent.annotations import NoteType, TextAnchorDraft, TextAnchorRect
from paper_agent.domain import Page, Paper
from paper_agent.model_profiles import ModelSnapshot
from paper_agent.services.selection_assists import (
    SelectionAssistAction,
    SelectionAssistService,
)
from paper_agent.storage import PaperRepository


class FakeChatClient:
    def __init__(self, chunks: list[str], fail: bool = False) -> None:
        self.chunks = chunks
        self.fail = fail
        self.messages: list[list[dict[str, str]]] = []

    def stream_text(self, messages):
        self.messages.append(messages)
        if self.fail:
            raise RuntimeError("raw provider failure")
        yield from self.chunks


@pytest.fixture
def database_url(tmp_path) -> str:
    return f"sqlite:///{tmp_path / 'paper-agent.db'}"


@pytest.fixture
def repository(database_url: str) -> PaperAnnotationRepository:
    papers = PaperRepository(database_url)
    return PaperAnnotationRepository(engine=papers.engine)


@pytest.fixture
def prepared_paper(
    database_url: str, repository: PaperAnnotationRepository
) -> Paper:
    papers = PaperRepository(database_url)
    paper = papers.create_paper(
        original_filename="paper.pdf", stored_filename="paper.pdf"
    )
    papers.save_page(paper.id, Page(number=1, width=600, height=800))
    return paper


def _snapshot() -> ModelSnapshot:
    return ModelSnapshot(
        profile_id="11111111-1111-4111-8111-111111111111",
        display_name="本地 Qwen",
        base_url="http://127.0.0.1:8001/v1",
        model_name="Qwen3-32B",
        revision=1,
    )


def _draft() -> TextAnchorDraft:
    return TextAnchorDraft(
        quote="Mixture of Experts",
        page_number=1,
        rects=(TextAnchorRect(0, 0.1, 0.2, 0.7, 0.25),),
    )


def _events(
    service: SelectionAssistService,
    paper_id: str,
    action: SelectionAssistAction,
    client: FakeChatClient,
    request_id: str,
):
    return list(
        service.stream(
            paper_id=paper_id,
            draft=_draft(),
            action=action,
            client=client,
            model_snapshot=_snapshot(),
            request_id=request_id,
        )
    )


def test_completed_explanation_creates_one_generated_note(
    repository: PaperAnnotationRepository, prepared_paper: Paper
) -> None:
    service = SelectionAssistService(repository)
    client = FakeChatClient(["这是", "一种专家混合结构。"])

    events = _events(
        service,
        prepared_paper.id,
        SelectionAssistAction.explain,
        client,
        "assist-a",
    )

    assert [event.event for event in events] == [
        "started",
        "delta",
        "delta",
        "completed",
    ]
    notes = repository.get_notes(prepared_paper.id)
    assert len(notes) == 1
    assert notes[0].note_type is NoteType.explanation
    assert notes[0].ai_generated is True
    assert notes[0].anchor_ids
    assert client.messages[0][0]["content"].startswith("你是论文阅读助手")


def test_translate_prompt_and_note_type(
    repository: PaperAnnotationRepository, prepared_paper: Paper
) -> None:
    service = SelectionAssistService(repository)
    client = FakeChatClient(["专家混合。"])

    events = _events(
        service,
        prepared_paper.id,
        SelectionAssistAction.translate,
        client,
        "assist-translate",
    )

    assert events[-1].event == "completed"
    assert repository.get_notes(prepared_paper.id)[0].note_type is NoteType.translation
    assert client.messages[0][0]["content"].startswith("把 <selected_text>")
    assert "Mixture of Experts" in client.messages[0][1]["content"]


def test_closed_stream_does_not_create_partial_note(
    repository: PaperAnnotationRepository, prepared_paper: Paper
) -> None:
    service = SelectionAssistService(repository)
    stream = service.stream(
        paper_id=prepared_paper.id,
        draft=_draft(),
        action=SelectionAssistAction.translate,
        client=FakeChatClient(["部分", "结果"]),
        model_snapshot=_snapshot(),
        request_id="assist-b",
    )
    next(stream)
    next(stream)
    stream.close()

    assert repository.get_notes(prepared_paper.id) == ()
    assert repository.get_selection_assist(
        prepared_paper.id, "assist-b"
    )["status"] == "failed"


def test_provider_failure_emits_safe_error_event(
    repository: PaperAnnotationRepository, prepared_paper: Paper
) -> None:
    service = SelectionAssistService(repository)

    events = _events(
        service,
        prepared_paper.id,
        SelectionAssistAction.explain,
        FakeChatClient([], fail=True),
        "assist-error",
    )

    assert events[0].event == "started"
    assert events[-1].event == "error"
    assert events[-1].payload["code"] == "assist_failed"
    assert "raw provider failure" not in str(events)
    assert repository.get_notes(prepared_paper.id) == ()


def test_failed_selection_assist_retries_with_the_same_request_id_once(
    repository: PaperAnnotationRepository, prepared_paper: Paper
) -> None:
    service = SelectionAssistService(repository)

    first_attempt = _events(
        service,
        prepared_paper.id,
        SelectionAssistAction.explain,
        FakeChatClient([], fail=True),
        "assist-retry-after-failure",
    )
    assert first_attempt[-1].event == "error"
    assert repository.get_selection_assist(
        prepared_paper.id, "assist-retry-after-failure"
    )["status"] == "failed"

    retry = _events(
        service,
        prepared_paper.id,
        SelectionAssistAction.explain,
        FakeChatClient(["重试完成。"]),
        "assist-retry-after-failure",
    )

    assert [event.event for event in retry] == ["started", "delta", "completed"]
    request = repository.get_selection_assist(
        prepared_paper.id, "assist-retry-after-failure"
    )
    assert request["status"] == "completed"
    assert len(repository.get_notes(prepared_paper.id)) == 1


def test_completed_duplicate_replays_without_calling_the_model(
    repository: PaperAnnotationRepository, prepared_paper: Paper
) -> None:
    service = SelectionAssistService(repository)
    first_client = FakeChatClient(["已完成的解释。"])
    _events(
        service,
        prepared_paper.id,
        SelectionAssistAction.explain,
        first_client,
        "assist-replay",
    )
    replay_client = FakeChatClient([])

    events = _events(
        service,
        prepared_paper.id,
        SelectionAssistAction.explain,
        replay_client,
        "assist-replay",
    )

    assert [event.event for event in events] == ["completed"]
    assert replay_client.messages == []


def test_running_duplicate_conflicts(
    repository: PaperAnnotationRepository, prepared_paper: Paper
) -> None:
    repository.create_selection_assist_running(
        prepared_paper.id,
        request_id="assist-running",
        action="explain",
        model_profile_id="11111111-1111-4111-8111-111111111111",
        model_snapshot=_snapshot(),
    )
    service = SelectionAssistService(repository)

    events = _events(
        service,
        prepared_paper.id,
        SelectionAssistAction.explain,
        FakeChatClient([]),
        "assist-running",
    )

    assert [event.event for event in events] == ["error"]
    assert events[0].payload["code"] == "assist_running"


def test_restart_failed_selection_assist_only_claims_a_failed_row(
    repository: PaperAnnotationRepository, prepared_paper: Paper
) -> None:
    repository.create_selection_assist_running(
        prepared_paper.id,
        request_id="assist-retry",
        action="explain",
        model_profile_id="11111111-1111-4111-8111-111111111111",
        model_snapshot=_snapshot(),
    )
    repository.fail_selection_assist(prepared_paper.id, "assist-retry")
    retry_snapshot = ModelSnapshot(
        profile_id="22222222-2222-4222-8222-222222222222",
        display_name="重试模型",
        base_url="http://127.0.0.1:8002/v1",
        model_name="Retry-Qwen",
        revision=2,
    )

    assert repository.restart_failed_selection_assist(
        prepared_paper.id,
        request_id="assist-retry",
        action="translate",
        model_profile_id=retry_snapshot.profile_id,
        model_snapshot=retry_snapshot,
    ) is True

    request = repository.get_selection_assist(prepared_paper.id, "assist-retry")
    assert request["status"] == "running"
    assert request["action"] == "translate"
    assert request["model_profile_id"] == retry_snapshot.profile_id
    assert json.loads(request["model_snapshot_json"])["model_name"] == "Retry-Qwen"
    assert request["anchor_id"] is None
    assert request["note_id"] is None
    assert repository.restart_failed_selection_assist(
        prepared_paper.id,
        request_id="assist-retry",
        action="translate",
        model_profile_id=retry_snapshot.profile_id,
        model_snapshot=retry_snapshot,
    ) is False
