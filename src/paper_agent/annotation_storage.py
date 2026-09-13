"""Durable storage for paper text anchors, highlights, and anchored notes."""

from __future__ import annotations

import hashlib
import unicodedata
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.engine import Connection, Engine

from paper_agent.annotations import (
    HIGHLIGHT_COLORS,
    Highlight,
    NoteType,
    TextAnchor,
    TextAnchorDraft,
    TextAnchorRect,
)
from paper_agent.database import (
    create_database_engine,
    document_elements,
    highlights,
    note_anchors,
    notes,
    pages,
    papers,
    selection_assist_requests,
    text_anchor_rects,
    text_anchors,
)
from paper_agent.domain import Note
from paper_agent.model_profiles import ModelSnapshot
from paper_agent.storage import _parse_model_snapshot, _serialize_model_snapshot


class AnnotationNotFoundError(RuntimeError):
    pass


class IdempotencyConflictError(RuntimeError):
    pass


class AnnotationInputError(ValueError):
    pass


def quote_hash(quote: str) -> str:
    normalized = unicodedata.normalize("NFKC", " ".join(quote.split())).casefold()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _serialize_time(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _parse_time(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value)


class PaperAnnotationRepository:
    def __init__(self, database_url: str | None = None, *, engine: Engine | None = None) -> None:
        if engine is not None:
            self.engine = engine
        elif database_url is not None:
            self.engine = create_database_engine(database_url)
        else:
            raise ValueError("database_url or engine is required")

    def create_highlight(
        self,
        paper_id: str,
        draft: TextAnchorDraft,
        color: str = "yellow",
        request_id: str | None = None,
    ) -> Highlight:
        if color not in HIGHLIGHT_COLORS:
            raise AnnotationInputError(
                f"highlight color must be one of {', '.join(HIGHLIGHT_COLORS)}"
            )
        with self.engine.begin() as connection:
            self._require_paper(connection, paper_id)
            self._require_draft_targets(connection, paper_id, draft)
            existing = None
            if request_id is not None:
                existing = self._highlight_by_request(connection, paper_id, request_id)
            if existing is not None:
                anchor = self._load_anchor(connection, existing["anchor_id"])
                if (
                    anchor.quote != draft.quote
                    or anchor.page_number != draft.page_number
                    or anchor.rects != draft.rects
                    or existing["color"] != color
                ):
                    raise IdempotencyConflictError(
                        "highlight request conflicts with its stored retry"
                    )
                return Highlight(
                    anchor=anchor, color=color, id=existing["id"]
                )

            anchor = self._find_anchor_by_hash(connection, paper_id, draft)
            if anchor is None:
                anchor = self._insert_anchor(connection, paper_id, draft)
            else:
                if (
                    anchor.quote != draft.quote
                    or anchor.page_number != draft.page_number
                    or anchor.rects != draft.rects
                ):
                    raise IdempotencyConflictError(
                        "highlight selection conflicts with an existing anchor"
                    )
                occupied = connection.execute(
                    select(highlights.c.id)
                    .where(highlights.c.anchor_id == anchor.id)
                ).scalar_one_or_none()
                if occupied is not None:
                    raise IdempotencyConflictError(
                        "highlight selection is already highlighted"
                    )

            highlight_id = str(uuid4())
            now = _serialize_time(datetime.now(UTC))
            connection.execute(
                insert(highlights).values(
                    id=highlight_id,
                    paper_id=paper_id,
                    anchor_id=anchor.id,
                    color=color,
                    request_id=request_id,
                    created_at=now,
                    updated_at=now,
                )
            )
            return Highlight(anchor=anchor, color=color, id=highlight_id)

    def create_anchor(self, paper_id: str, draft: TextAnchorDraft) -> TextAnchor:
        with self.engine.begin() as connection:
            self._require_paper(connection, paper_id)
            self._require_draft_targets(connection, paper_id, draft)
            anchor = self._find_anchor_by_hash(connection, paper_id, draft)
            if anchor is not None:
                if (
                    anchor.quote != draft.quote
                    or anchor.page_number != draft.page_number
                    or anchor.rects != draft.rects
                ):
                    raise IdempotencyConflictError(
                        "anchor selection conflicts with an existing anchor"
                    )
                return anchor
            return self._insert_anchor(connection, paper_id, draft)

    def list_highlights(self, paper_id: str) -> tuple[Highlight, ...]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(highlights)
                .where(highlights.c.paper_id == paper_id)
                .order_by(highlights.c.created_at, highlights.c.id)
            ).mappings()
            return tuple(
                Highlight(
                    anchor=self._load_anchor(connection, row["anchor_id"]),
                    color=row["color"],
                    id=row["id"],
                )
                for row in rows
            )

    def delete_highlight(self, paper_id: str, highlight_id: str) -> bool:
        with self.engine.begin() as connection:
            result = connection.execute(
                delete(highlights)
                .where(highlights.c.paper_id == paper_id)
                .where(highlights.c.id == highlight_id)
            )
            return result.rowcount == 1

    def update_highlight_color(
        self, paper_id: str, highlight_id: str, color: str
    ) -> Highlight | None:
        if color not in HIGHLIGHT_COLORS:
            raise AnnotationInputError(
                f"highlight color must be one of {', '.join(HIGHLIGHT_COLORS)}"
            )
        with self.engine.begin() as connection:
            self._require_paper(connection, paper_id)
            row = connection.execute(
                select(highlights)
                .where(highlights.c.paper_id == paper_id)
                .where(highlights.c.id == highlight_id)
            ).mappings().first()
            if row is None:
                return None
            connection.execute(
                update(highlights)
                .where(highlights.c.paper_id == paper_id)
                .where(highlights.c.id == highlight_id)
                .values(
                    color=color,
                    updated_at=_serialize_time(datetime.now(UTC)),
                )
            )
            anchor = self._load_anchor(connection, row["anchor_id"])
            return Highlight(anchor=anchor, color=color, id=highlight_id)

    def create_note(
        self,
        paper_id: str,
        note: Note,
        anchor_draft: TextAnchorDraft | None = None,
        request_id: str | None = None,
    ) -> Note:
        if not note.body or not note.body.strip():
            raise AnnotationInputError("note body must be nonempty")
        if note.page_number is not None and note.page_number < 1:
            raise AnnotationInputError("note page number must be positive")
        with self.engine.begin() as connection:
            self._require_paper(connection, paper_id)
            if note.page_number is not None:
                self._require_page(connection, paper_id, note.page_number)
            if note.element_id is not None:
                self._require_element_owned(
                    connection, paper_id, note.element_id
                )
            existing = None
            if request_id is not None:
                existing = self._note_by_request(connection, paper_id, request_id)
            if existing is not None:
                existing_note = self._note_from_row(connection, existing)
                if existing_note.body != note.body:
                    raise IdempotencyConflictError(
                        "note request conflicts with its stored retry"
                    )
                return existing_note

            anchor_ids = list(note.anchor_ids)
            if anchor_draft is not None:
                self._require_draft_targets(connection, paper_id, anchor_draft)
                anchor = self._find_anchor_by_hash(connection, paper_id, anchor_draft)
                if anchor is None:
                    anchor = self._insert_anchor(connection, paper_id, anchor_draft)
                if anchor.id not in anchor_ids:
                    anchor_ids.append(anchor.id)
            for anchor_id in anchor_ids:
                self._require_owned_anchor(connection, paper_id, anchor_id)

            order_index = self._next_order(connection, paper_id)
            now = datetime.now(UTC)
            connection.execute(
                insert(notes).values(
                    id=note.id,
                    paper_id=paper_id,
                    element_id=note.element_id,
                    page_number=note.page_number,
                    body=note.body,
                    order_index=order_index,
                    note_type=note.note_type.value,
                    model_profile_id=note.model_profile_id,
                    model_snapshot_json=_serialize_model_snapshot(note.model_snapshot),
                    ai_generated=note.ai_generated,
                    user_edited=note.user_edited,
                    request_id=request_id,
                    created_at=_serialize_time(now),
                    updated_at=_serialize_time(now),
                )
            )
            if anchor_ids:
                connection.execute(
                    insert(note_anchors),
                    [
                        {
                            "note_id": note.id,
                            "paper_id": paper_id,
                            "anchor_id": anchor_id,
                        }
                        for anchor_id in anchor_ids
                    ],
                )
        return self.get_note(paper_id, note.id)

    def update_note(
        self,
        paper_id: str,
        note_id: str,
        body: str,
        expected_updated_at: datetime | None,
    ) -> Note:
        if not body or not body.strip():
            raise AnnotationInputError("note body must be nonempty")
        with self.engine.begin() as connection:
            current = self._note_row(connection, paper_id, note_id)
            if current is None:
                raise AnnotationNotFoundError("note was not found")
            stored_updated_at = _parse_time(current["updated_at"])
            if stored_updated_at != expected_updated_at:
                raise IdempotencyConflictError("note was changed by another request")
            now = _serialize_time(datetime.now(UTC))
            connection.execute(
                update(notes)
                .where(notes.c.paper_id == paper_id)
                .where(notes.c.id == note_id)
                .values(body=body, user_edited=True, updated_at=now)
            )
        return self.get_note(paper_id, note_id)

    def delete_note(self, paper_id: str, note_id: str) -> bool:
        with self.engine.begin() as connection:
            if self._note_row(connection, paper_id, note_id) is None:
                return False
            connection.execute(
                update(selection_assist_requests)
                .where(selection_assist_requests.c.paper_id == paper_id)
                .where(selection_assist_requests.c.note_id == note_id)
                .values(note_id=None, updated_at=_serialize_time(datetime.now(UTC)))
            )
            connection.execute(
                delete(note_anchors)
                .where(note_anchors.c.paper_id == paper_id)
                .where(note_anchors.c.note_id == note_id)
            )
            connection.execute(
                delete(notes)
                .where(notes.c.paper_id == paper_id)
                .where(notes.c.id == note_id)
            )
            return True

    def get_note(self, paper_id: str, note_id: str) -> Note:
        with self.engine.connect() as connection:
            row = self._note_row(connection, paper_id, note_id)
            if row is None:
                raise AnnotationNotFoundError("note was not found")
            return self._note_from_row(connection, row)

    def get_notes(self, paper_id: str) -> tuple[Note, ...]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(notes)
                .where(notes.c.paper_id == paper_id)
                .order_by(notes.c.order_index, notes.c.id)
            ).mappings()
            return tuple(self._note_from_row(connection, row) for row in rows)

    def list_anchors(self, paper_id: str) -> tuple[TextAnchor, ...]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(text_anchors.c.id)
                .where(text_anchors.c.paper_id == paper_id)
                .order_by(text_anchors.c.created_at, text_anchors.c.id)
            ).scalars()
            return tuple(self._load_anchor(connection, anchor_id) for anchor_id in rows)

    def paper_exists(self, paper_id: str) -> bool:
        with self.engine.connect() as connection:
            return connection.execute(
                select(papers.c.id).where(papers.c.id == paper_id)
            ).scalar_one_or_none() is not None

    def get_selection_assist(self, paper_id: str, request_id: str):
        with self.engine.connect() as connection:
            return connection.execute(
                select(selection_assist_requests)
                .where(selection_assist_requests.c.paper_id == paper_id)
                .where(selection_assist_requests.c.request_id == request_id)
            ).mappings().one_or_none()

    def create_selection_assist_running(
        self,
        paper_id: str,
        *,
        request_id: str,
        action: str,
        model_profile_id: str,
        model_snapshot: ModelSnapshot,
    ) -> None:
        now = _serialize_time(datetime.now(UTC))
        with self.engine.begin() as connection:
            connection.execute(
                insert(selection_assist_requests).values(
                    id=str(uuid4()),
                    paper_id=paper_id,
                    request_id=request_id,
                    action=action,
                    status="running",
                    model_profile_id=model_profile_id,
                    model_snapshot_json=_serialize_model_snapshot(model_snapshot),
                    created_at=now,
                    updated_at=now,
                )
            )

    def restart_failed_selection_assist(
        self,
        paper_id: str,
        *,
        request_id: str,
        action: str,
        model_profile_id: str,
        model_snapshot: ModelSnapshot,
    ) -> bool:
        with self.engine.begin() as connection:
            result = connection.execute(
                update(selection_assist_requests)
                .where(selection_assist_requests.c.paper_id == paper_id)
                .where(selection_assist_requests.c.request_id == request_id)
                .where(selection_assist_requests.c.status == "failed")
                .values(
                    action=action,
                    status="running",
                    anchor_id=None,
                    note_id=None,
                    model_profile_id=model_profile_id,
                    model_snapshot_json=_serialize_model_snapshot(model_snapshot),
                    updated_at=_serialize_time(datetime.now(UTC)),
                )
            )
        return result.rowcount == 1

    def complete_selection_assist(
        self,
        paper_id: str,
        request_id: str,
        *,
        anchor_id: str,
        note_id: str,
    ) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                update(selection_assist_requests)
                .where(selection_assist_requests.c.paper_id == paper_id)
                .where(selection_assist_requests.c.request_id == request_id)
                .values(
                    status="completed",
                    anchor_id=anchor_id,
                    note_id=note_id,
                    updated_at=_serialize_time(datetime.now(UTC)),
                )
            )

    def fail_selection_assist(self, paper_id: str, request_id: str) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                update(selection_assist_requests)
                .where(selection_assist_requests.c.paper_id == paper_id)
                .where(selection_assist_requests.c.request_id == request_id)
                .values(
                    status="failed",
                    updated_at=_serialize_time(datetime.now(UTC)),
                )
            )

    def _insert_anchor(
        self, connection: Connection, paper_id: str, draft: TextAnchorDraft
    ) -> TextAnchor:
        anchor_id = str(uuid4())
        connection.execute(
            insert(text_anchors).values(
                id=anchor_id,
                paper_id=paper_id,
                quote=draft.quote,
                quote_hash=quote_hash(draft.quote),
                element_id=draft.element_id,
                page_number=draft.page_number,
                created_at=_serialize_time(datetime.now(UTC)),
            )
        )
        connection.execute(
            insert(text_anchor_rects),
            [
                {
                    "anchor_id": anchor_id,
                    "order_index": rect.order,
                    "paper_id": paper_id,
                    "x0": rect.x0,
                    "y0": rect.y0,
                    "x1": rect.x1,
                    "y1": rect.y1,
                }
                for rect in draft.rects
            ],
        )
        return self._load_anchor(connection, anchor_id)

    def _find_anchor_by_hash(
        self, connection: Connection, paper_id: str, draft: TextAnchorDraft
    ) -> TextAnchor | None:
        anchor_id = connection.execute(
            select(text_anchors.c.id)
            .where(text_anchors.c.paper_id == paper_id)
            .where(text_anchors.c.quote_hash == quote_hash(draft.quote))
            .order_by(text_anchors.c.created_at, text_anchors.c.id)
            .limit(1)
        ).scalar_one_or_none()
        if anchor_id is None:
            return None
        return self._load_anchor(connection, anchor_id)

    def _load_anchor(self, connection: Connection, anchor_id: str) -> TextAnchor:
        row = connection.execute(
            select(text_anchors).where(text_anchors.c.id == anchor_id)
        ).mappings().one_or_none()
        if row is None:
            raise AnnotationNotFoundError("text anchor was not found")
        rect_rows = connection.execute(
            select(text_anchor_rects)
            .where(text_anchor_rects.c.anchor_id == anchor_id)
            .order_by(text_anchor_rects.c.order_index)
        ).mappings()
        return TextAnchor(
            id=row["id"],
            paper_id=row["paper_id"],
            quote=row["quote"],
            page_number=row["page_number"],
            element_id=row["element_id"],
            rects=tuple(
                TextAnchorRect(
                    order=rect["order_index"],
                    x0=rect["x0"],
                    y0=rect["y0"],
                    x1=rect["x1"],
                    y1=rect["y1"],
                )
                for rect in rect_rows
            ),
        )

    def _highlight_by_request(
        self, connection: Connection, paper_id: str, request_id: str
    ):
        return connection.execute(
            select(highlights)
            .where(highlights.c.paper_id == paper_id)
            .where(highlights.c.request_id == request_id)
        ).mappings().one_or_none()

    def _note_by_request(
        self, connection: Connection, paper_id: str, request_id: str
    ):
        return connection.execute(
            select(notes)
            .where(notes.c.paper_id == paper_id)
            .where(notes.c.request_id == request_id)
        ).mappings().one_or_none()

    def _note_row(
        self, connection: Connection, paper_id: str, note_id: str
    ):
        return connection.execute(
            select(notes)
            .where(notes.c.paper_id == paper_id)
            .where(notes.c.id == note_id)
        ).mappings().one_or_none()

    def _note_from_row(self, connection: Connection, row) -> Note:
        anchor_ids = tuple(
            connection.execute(
                select(note_anchors.c.anchor_id)
                .where(note_anchors.c.paper_id == row["paper_id"])
                .where(note_anchors.c.note_id == row["id"])
                .order_by(note_anchors.c.anchor_id)
            ).scalars()
        )
        return Note(
            id=row["id"],
            body=row["body"],
            element_id=row["element_id"],
            page_number=row["page_number"],
            note_type=NoteType(row["note_type"]),
            anchor_ids=anchor_ids,
            model_profile_id=row["model_profile_id"],
            model_snapshot=_parse_model_snapshot(row["model_snapshot_json"]),
            ai_generated=bool(row["ai_generated"]),
            user_edited=bool(row["user_edited"]),
            created_at=_parse_time(row["created_at"]),
            updated_at=_parse_time(row["updated_at"]),
        )

    @staticmethod
    def _next_order(connection: Connection, paper_id: str) -> int:
        current = connection.execute(
            select(func.max(notes.c.order_index)).where(notes.c.paper_id == paper_id)
        ).scalar_one()
        return 0 if current is None else current + 1

    @staticmethod
    def _require_paper(connection: Connection, paper_id: str) -> None:
        if connection.execute(
            select(papers.c.id).where(papers.c.id == paper_id)
        ).scalar_one_or_none() is None:
            raise AnnotationNotFoundError("paper was not found")

    @staticmethod
    def _require_page(
        connection: Connection, paper_id: str, page_number: int
    ) -> None:
        if connection.execute(
            select(pages.c.id)
            .where(pages.c.paper_id == paper_id)
            .where(pages.c.number == page_number)
        ).scalar_one_or_none() is None:
            raise AnnotationInputError("note page does not belong to paper")

    @staticmethod
    def _require_element_owned(
        connection: Connection, paper_id: str, element_id: str
    ) -> None:
        if connection.execute(
            select(document_elements.c.id)
            .where(document_elements.c.paper_id == paper_id)
            .where(document_elements.c.id == element_id)
        ).scalar_one_or_none() is None:
            raise AnnotationInputError("element does not belong to paper")

    @staticmethod
    def _require_owned_anchor(
        connection: Connection, paper_id: str, anchor_id: str
    ) -> None:
        if connection.execute(
            select(text_anchors.c.id)
            .where(text_anchors.c.paper_id == paper_id)
            .where(text_anchors.c.id == anchor_id)
        ).scalar_one_or_none() is None:
            raise AnnotationInputError("anchor does not belong to paper")

    def _require_draft_targets(
        self, connection: Connection, paper_id: str, draft: TextAnchorDraft
    ) -> None:
        self._require_page(connection, paper_id, draft.page_number)
        if draft.element_id is not None:
            self._require_element_owned(connection, paper_id, draft.element_id)
