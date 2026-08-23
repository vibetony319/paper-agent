"""Streaming explanation and translation for selected paper text."""

from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum
import json
from typing import Literal

from paper_agent.annotation_storage import PaperAnnotationRepository
from paper_agent.annotations import NoteType, TextAnchorDraft
from paper_agent.domain import Note
from paper_agent.model_profiles import ModelSnapshot
from paper_agent.models.vllm import VllmChatClient, VllmResponseError
from paper_agent.schemas import NoteResponse


class SelectionAssistAction(StrEnum):
    explain = "explain"
    translate = "translate"


SelectionAssistEventName = Literal["started", "delta", "completed", "error"]
MAX_GENERATED_CHARS = 64_000

EXPLAIN_SYSTEM_PROMPT = (
    "你是论文阅读助手。只解释被 <selected_text> 包围的原文；"
    "附近内容仅用于理解语境。使用简体中文，区分原文含义与补充说明。"
)
TRANSLATE_SYSTEM_PROMPT = (
    "把 <selected_text> 中的学术文本准确翻译为简体中文。"
    "保留公式、变量、引用编号和专有名词，不增加原文没有的结论。"
)


@dataclass(frozen=True)
class SelectionAssistEvent:
    event: SelectionAssistEventName
    payload: dict[str, object]


class SelectionAssistError(RuntimeError):
    pass


class SelectionAssistService:
    def __init__(self, repository: PaperAnnotationRepository) -> None:
        self.repository = repository

    def stream(
        self,
        *,
        paper_id: str,
        draft: TextAnchorDraft,
        action: SelectionAssistAction,
        client: VllmChatClient,
        model_snapshot: ModelSnapshot,
        request_id: str,
    ) -> Iterator[SelectionAssistEvent]:
        existing = self.repository.get_selection_assist(paper_id, request_id)
        if existing is not None and existing["status"] == "completed":
            yield from self._replay_completed(paper_id, existing)
            return
        if existing is not None and existing["status"] == "running":
            yield SelectionAssistEvent(
                "error",
                {
                    "code": "assist_running",
                    "detail": "该选区请求正在处理中。",
                },
            )
            return

        self.repository.create_selection_assist_running(
            paper_id,
            request_id=request_id,
            action=action.value,
            model_profile_id=model_snapshot.profile_id,
            model_snapshot=model_snapshot,
        )
        yield SelectionAssistEvent("started", {"request_id": request_id})

        messages = [
            {
                "role": "system",
                "content": (
                    EXPLAIN_SYSTEM_PROMPT
                    if action is SelectionAssistAction.explain
                    else TRANSLATE_SYSTEM_PROMPT
                ),
            },
            {
                "role": "user",
                "content": (
                    f'<selected_text page="{draft.page_number}">'
                    f"{draft.quote}</selected_text>"
                ),
            },
        ]
        parts: list[str] = []
        try:
            for chunk in client.stream_text(messages):
                parts.append(chunk)
                if sum(len(part) for part in parts) > MAX_GENERATED_CHARS:
                    self.repository.fail_selection_assist(paper_id, request_id)
                    yield SelectionAssistEvent(
                        "error",
                        {"code": "assist_too_long", "detail": "模型输出过长。"},
                    )
                    return
                yield SelectionAssistEvent("delta", {"text": chunk})

            body = "".join(parts).strip()
            if not body:
                raise VllmResponseError("model returned no text")
            note = self.repository.create_note(
                paper_id,
                Note(
                    body=body,
                    note_type=(
                        NoteType.explanation
                        if action is SelectionAssistAction.explain
                        else NoteType.translation
                    ),
                    model_profile_id=model_snapshot.profile_id,
                    model_snapshot=model_snapshot,
                    ai_generated=True,
                ),
                anchor_draft=draft,
                request_id=request_id,
            )
            self.repository.complete_selection_assist(
                paper_id,
                request_id,
                anchor_id=note.anchor_ids[0] if note.anchor_ids else "",
                note_id=note.id,
            )
            yield SelectionAssistEvent(
                "completed",
                {"note": NoteResponse.from_note(note).model_dump(mode="json")},
            )
        except GeneratorExit:
            self.repository.fail_selection_assist(paper_id, request_id)
            raise
        except Exception:
            self.repository.fail_selection_assist(paper_id, request_id)
            yield SelectionAssistEvent(
                "error",
                {"code": "assist_failed", "detail": "模型未能完成选区请求。"},
            )

    def replay(
        self, paper_id: str, request_id: str
    ) -> Iterator[SelectionAssistEvent]:
        existing = self.repository.get_selection_assist(paper_id, request_id)
        if existing is None or existing["status"] != "completed":
            raise SelectionAssistError("selection assist is not completed")
        yield from self._replay_completed(paper_id, existing)

    def _replay_completed(
        self, paper_id: str, existing
    ) -> Iterator[SelectionAssistEvent]:
        if existing["note_id"] is None:
            yield SelectionAssistEvent(
                "error",
                {"code": "assist_failed", "detail": "选区请求没有保存结果。"},
            )
            return
        try:
            note = self.repository.get_note(paper_id, existing["note_id"])
        except Exception:
            yield SelectionAssistEvent(
                "error",
                {"code": "assist_failed", "detail": "选区结果已被删除。"},
            )
            return
        yield SelectionAssistEvent(
            "completed",
            {"note": NoteResponse.from_note(note).model_dump(mode="json")},
        )


def encode_sse(event: SelectionAssistEvent) -> bytes:
    payload = json.dumps(
        event.payload, ensure_ascii=False, separators=(",", ":")
    )
    return f"event: {event.event}\ndata: {payload}\n\n".encode("utf-8")
