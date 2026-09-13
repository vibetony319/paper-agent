from dataclasses import replace
from datetime import datetime
import json
from urllib.parse import urlparse
from uuid import uuid4

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.engine import Engine

from paper_agent.annotations import NoteType
from paper_agent.database import (
    conversation_message_anchors,
    conversation_message_citations,
    conversation_message_note_citations,
    conversation_messages,
    conversations,
    document_elements,
    graph_edge_evidence,
    graph_edges,
    graph_node_evidence,
    graph_nodes,
    highlights,
    initialize_database,
    note_anchors,
    notes,
    pages,
    papers,
    processing_runs,
    selection_assist_requests,
    sections,
    text_anchor_rects,
    text_anchors,
)
from paper_agent.domain import (
    AgentMessageRole,
    BoundingBox,
    CitationSnapshot,
    Conversation,
    ConversationMessage,
    DocumentElement,
    GraphEdge,
    GraphNode,
    GraphStage,
    Note,
    Page,
    Paper,
    PaperDocument,
    PaperGraph,
    ProcessingStatus,
    Section,
    normalize_graph_node_name,
)
from paper_agent.model_profiles import ModelSnapshot, validate_model_profile_id


_MODEL_SNAPSHOT_KEYS = frozenset(
    {"profile_id", "display_name", "base_url", "model_name", "revision"}
)
_CITATION_SNAPSHOT_KEYS = frozenset({"id", "kind", "page_number", "bbox"})
_CITATION_BBOX_KEYS = frozenset({"x0", "y0", "x1", "y1"})

PAPER_DELETE_ORDER = (
    conversation_message_note_citations,
    conversation_message_anchors,
    conversation_message_citations,
    conversation_messages,
    conversations,
    selection_assist_requests,
    note_anchors,
    notes,
    highlights,
    text_anchor_rects,
    text_anchors,
    graph_edge_evidence,
    graph_node_evidence,
    graph_edges,
    graph_nodes,
    document_elements,
    sections,
    pages,
    processing_runs,
)


def _snapshot_values(snapshot: ModelSnapshot) -> dict[str, object]:
    validate_model_profile_id(snapshot.profile_id)
    if any(
        not isinstance(value, str) or not value.strip()
        for value in (snapshot.display_name, snapshot.base_url, snapshot.model_name)
    ):
        raise ValueError("model snapshot text fields must be nonempty")
    if (
        not isinstance(snapshot.revision, int)
        or isinstance(snapshot.revision, bool)
        or snapshot.revision < 1
    ):
        raise ValueError("model snapshot revision must be positive")
    try:
        parsed_url = urlparse(snapshot.base_url)
        hostname = parsed_url.hostname
        parsed_url.port
    except ValueError:
        raise ValueError("model snapshot base URL is invalid") from None
    if (
        parsed_url.scheme not in {"http", "https"}
        or not parsed_url.netloc
        or hostname is None
        or parsed_url.username is not None
        or parsed_url.password is not None
        or parsed_url.query
        or parsed_url.fragment
    ):
        raise ValueError("model snapshot base URL is invalid")
    return {
        "profile_id": snapshot.profile_id,
        "display_name": snapshot.display_name,
        "base_url": snapshot.base_url,
        "model_name": snapshot.model_name,
        "revision": snapshot.revision,
    }


def _serialize_model_snapshot(snapshot: ModelSnapshot | None) -> str | None:
    if snapshot is None:
        return None
    try:
        payload = _snapshot_values(snapshot)
    except (TypeError, ValueError):
        raise ConversationReferenceError("message model snapshot is invalid") from None
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _parse_model_snapshot(payload: str | None) -> ModelSnapshot | None:
    if payload is None:
        return None
    try:
        values = json.loads(payload)
        if not isinstance(values, dict) or set(values) != _MODEL_SNAPSHOT_KEYS:
            return None
        snapshot = ModelSnapshot(**values)
        _snapshot_values(snapshot)
        return snapshot
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def _citation_snapshot_values(snapshot: CitationSnapshot) -> dict[str, object]:
    coordinates = (
        snapshot.bbox.x0,
        snapshot.bbox.y0,
        snapshot.bbox.x1,
        snapshot.bbox.y1,
    ) if isinstance(snapshot.bbox, BoundingBox) else ()
    if (
        not isinstance(snapshot.id, str)
        or not snapshot.id
        or snapshot.id != snapshot.id.strip()
        or not isinstance(snapshot.kind, str)
        or not snapshot.kind
        or snapshot.kind != snapshot.kind.strip()
        or not isinstance(snapshot.page_number, int)
        or isinstance(snapshot.page_number, bool)
        or snapshot.page_number < 1
        or not isinstance(snapshot.bbox, BoundingBox)
        or any(
            not isinstance(coordinate, (int, float))
            or isinstance(coordinate, bool)
            for coordinate in coordinates
        )
    ):
        raise ValueError("citation snapshot is invalid")
    return {
        "id": snapshot.id,
        "kind": snapshot.kind,
        "page_number": snapshot.page_number,
        "bbox": {
            "x0": snapshot.bbox.x0,
            "y0": snapshot.bbox.y0,
            "x1": snapshot.bbox.x1,
            "y1": snapshot.bbox.y1,
        },
    }


def _serialize_citation_snapshot(snapshot: CitationSnapshot) -> str:
    return json.dumps(
        _citation_snapshot_values(snapshot),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _parse_citation_snapshot(
    payload: str | None, *, expected_element_id: str
) -> CitationSnapshot | None:
    if payload is None:
        return None
    try:
        values = json.loads(payload)
        if not isinstance(values, dict) or set(values) != _CITATION_SNAPSHOT_KEYS:
            return None
        bbox_values = values["bbox"]
        if (
            not isinstance(bbox_values, dict)
            or set(bbox_values) != _CITATION_BBOX_KEYS
        ):
            return None
        snapshot = CitationSnapshot(
            id=values["id"],
            kind=values["kind"],
            page_number=values["page_number"],
            bbox=BoundingBox(**bbox_values),
        )
        _citation_snapshot_values(snapshot)
        if snapshot.id != expected_element_id:
            return None
        return snapshot
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def _ordered_citation_rows(citation_rows: tuple[object, ...]) -> tuple[object, ...]:
    ordinals = [row["ordinal"] for row in citation_rows]
    if (
        all(
            isinstance(ordinal, int)
            and not isinstance(ordinal, bool)
            and ordinal >= 0
            for ordinal in ordinals
        )
        and len(set(ordinals)) == len(ordinals)
    ):
        return tuple(sorted(citation_rows, key=lambda row: row["ordinal"]))
    return tuple(sorted(citation_rows, key=lambda row: row["element_id"]))


def _sanitize_model_provenance_pairs(
    messages: tuple[ConversationMessage, ...],
) -> tuple[ConversationMessage, ...]:
    positions_by_request: dict[str, list[int]] = {}
    for position, message in enumerate(messages):
        if message.request_id is not None:
            positions_by_request.setdefault(message.request_id, []).append(position)

    sanitized = list(messages)
    for positions in positions_by_request.values():
        grouped = [messages[position] for position in positions]
        roles = {message.role for message in grouped}
        if not {
            AgentMessageRole.user,
            AgentMessageRole.assistant,
        } <= roles:
            continue
        provenance = {
            (message.model_profile_id, message.model_snapshot) for message in grouped
        }
        incomplete = any(
            (message.model_profile_id is None) != (message.model_snapshot is None)
            for message in grouped
        )
        if len(provenance) == 1 and not incomplete:
            continue
        for position in positions:
            sanitized[position] = replace(
                messages[position],
                model_profile_id=None,
                model_snapshot=None,
            )
    return tuple(sanitized)


class PageReferenceError(ValueError):
    """Raised when a write references a page not owned by its paper."""


class GraphReferenceError(ValueError):
    """Raised when graph evidence or endpoints are not owned by a paper."""


class ConversationReferenceError(ValueError):
    """Raised when a conversation message references records outside its paper."""


class PaperRepository:
    def __init__(self, database_url: str) -> None:
        self.engine: Engine = initialize_database(database_url)

    def create_paper(
        self,
        *,
        original_filename: str,
        stored_filename: str,
        status: ProcessingStatus = ProcessingStatus.queued,
        paper_id: str | None = None,
    ) -> Paper:
        paper = Paper(
            id=paper_id or str(uuid4()),
            original_filename=original_filename,
            stored_filename=stored_filename,
            status=status,
        )
        with self.engine.begin() as connection:
            connection.execute(
                insert(papers).values(
                    id=paper.id,
                    original_filename=paper.original_filename,
                    stored_filename=paper.stored_filename,
                    status=paper.status.value,
                    source_published=paper.source_published,
                )
            )
        return paper

    def update_paper_status(self, paper_id: str, status: ProcessingStatus) -> Paper:
        with self.engine.begin() as connection:
            connection.execute(
                update(papers).where(papers.c.id == paper_id).values(status=status.value)
            )
        paper = self.get_paper(paper_id)
        if paper is None:
            raise KeyError(paper_id)
        return paper

    def record_processing_status(
        self,
        paper_id: str,
        status: ProcessingStatus,
        *,
        stage: str | None = None,
        error_summary: str | None = None,
        model_profile_id: str | None = None,
        model_snapshot: ModelSnapshot | None = None,
        request_id: str | None = None,
    ) -> None:
        with self.engine.begin() as connection:
            self._record_processing_status(
                connection,
                paper_id,
                status,
                stage=stage,
                error_summary=error_summary,
                model_profile_id=model_profile_id,
                model_snapshot=model_snapshot,
                request_id=request_id,
            )

    def get_processing_statuses(self, paper_id: str) -> tuple[ProcessingStatus, ...]:
        return self._get_processing_statuses(paper_id, stage=None)

    def get_stage_statuses(
        self, paper_id: str, stage: str
    ) -> tuple[ProcessingStatus, ...]:
        return self._get_processing_statuses(paper_id, stage=stage)

    def get_latest_stage_status(
        self, paper_id: str, stage: str
    ) -> ProcessingStatus | None:
        with self.engine.connect() as connection:
            status = connection.execute(
                select(processing_runs.c.status)
                .where(processing_runs.c.paper_id == paper_id)
                .where(processing_runs.c.stage == stage)
                .order_by(processing_runs.c.sequence.desc())
                .limit(1)
            ).scalar_one_or_none()
        return None if status is None else ProcessingStatus(status)

    def get_graph_build_status(
        self, paper_id: str, stage: str, request_id: str
    ) -> ProcessingStatus | None:
        with self.engine.connect() as connection:
            status = connection.execute(
                select(processing_runs.c.status)
                .where(processing_runs.c.paper_id == paper_id)
                .where(processing_runs.c.stage == stage)
                .where(processing_runs.c.request_id == request_id)
                .order_by(processing_runs.c.sequence.desc())
                .limit(1)
            ).scalar_one_or_none()
        return None if status is None else ProcessingStatus(status)

    def get_latest_graph_model(
        self, paper_id: str, stage: str
    ) -> ModelSnapshot | None:
        with self.engine.connect() as connection:
            row = connection.execute(
                select(
                    processing_runs.c.model_profile_id,
                    processing_runs.c.model_snapshot_json,
                )
                .where(processing_runs.c.paper_id == paper_id)
                .where(processing_runs.c.stage == stage)
                .where(processing_runs.c.model_snapshot_json.is_not(None))
                .order_by(processing_runs.c.sequence.desc())
                .limit(1)
            ).mappings().one_or_none()
        if row is None:
            return None
        snapshot = _parse_model_snapshot(row["model_snapshot_json"])
        if snapshot is not None and snapshot.profile_id != row["model_profile_id"]:
            return None
        return snapshot

    def mark_source_published(self, paper_id: str) -> Paper:
        with self.engine.begin() as connection:
            connection.execute(
                update(papers)
                .where(papers.c.id == paper_id)
                .values(source_published=True)
            )
        paper = self.get_paper(paper_id)
        if paper is None:
            raise KeyError(paper_id)
        return paper

    def force_stored_filename(self, paper_id: str, stored_filename: str) -> None:
        """Overwrite a paper's source basename for deletion-path tests."""
        with self.engine.begin() as connection:
            connection.execute(
                update(papers)
                .where(papers.c.id == paper_id)
                .values(stored_filename=stored_filename)
            )

    def get_processing_error(self, paper_id: str) -> str | None:
        with self.engine.connect() as connection:
            return connection.execute(
                select(processing_runs.c.error_summary)
                .where(processing_runs.c.paper_id == paper_id)
                .where(processing_runs.c.stage.is_(None))
                .order_by(processing_runs.c.sequence.desc())
                .limit(1)
            ).scalar_one_or_none()

    def get_paper(self, paper_id: str) -> Paper | None:
        with self.engine.connect() as connection:
            row = connection.execute(select(papers).where(papers.c.id == paper_id)).mappings().one_or_none()
        return None if row is None else self._paper_from_row(row)

    def list_papers(self) -> tuple[Paper, ...]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(papers).order_by(papers.c.original_filename.asc(), papers.c.id.asc())
            ).mappings().all()
        return tuple(self._paper_from_row(row) for row in rows)

    def create_conversation(self, conversation: Conversation) -> Conversation:
        with self.engine.begin() as connection:
            connection.execute(
                insert(conversations).values(
                    id=conversation.id,
                    paper_id=conversation.paper_id,
                    # Legacy NOT NULL column kept for existing databases; the
                    # answer-scope feature it stored has been removed.
                    mode="paper_only",
                )
            )
        return conversation

    def get_conversation(self, paper_id: str, conversation_id: str) -> Conversation | None:
        with self.engine.connect() as connection:
            row = connection.execute(
                select(conversations)
                .where(conversations.c.paper_id == paper_id)
                .where(conversations.c.id == conversation_id)
            ).mappings().one_or_none()
        return None if row is None else self._conversation_from_row(row)

    def append_conversation_message(self, message: ConversationMessage) -> ConversationMessage:
        if message.role is AgentMessageRole.user and message.citation_element_ids:
            raise ConversationReferenceError("user messages cannot cite source elements")
        if len(set(message.citation_element_ids)) != len(message.citation_element_ids):
            raise ConversationReferenceError("assistant citations must not contain duplicates")
        if message.citation_snapshots:
            raise ConversationReferenceError("citation snapshots are repository managed")
        if (
            message.role is AgentMessageRole.user
            and message.background_explanation is not None
        ):
            raise ConversationReferenceError("user messages cannot have background")
        if message.model_snapshot is not None and (
            message.model_profile_id != message.model_snapshot.profile_id
        ):
            raise ConversationReferenceError("message model metadata must match")
        model_snapshot_json = _serialize_model_snapshot(message.model_snapshot)
        with self.engine.begin() as connection:
            self._require_owned_conversation(
                connection, message.paper_id, message.conversation_id
            )
            citation_snapshots_by_id = self._require_located_conversation_citations(
                connection, message.paper_id, message.citation_element_ids
            )
            citation_snapshots = tuple(
                citation_snapshots_by_id[element_id]
                for element_id in message.citation_element_ids
            )
            sequence = self._next_conversation_sequence(connection, message.conversation_id)
            connection.execute(
                insert(conversation_messages).values(
                    id=message.id,
                    conversation_id=message.conversation_id,
                    paper_id=message.paper_id,
                    role=message.role.value,
                    content=message.content,
                    sequence=sequence,
                    model_profile_id=message.model_profile_id,
                    model_snapshot_json=model_snapshot_json,
                    request_id=message.request_id,
                    background_explanation=message.background_explanation,
                )
            )
            if message.citation_element_ids:
                connection.execute(
                    insert(conversation_message_citations),
                    [
                        {
                            "paper_id": message.paper_id,
                            "message_id": message.id,
                            "element_id": element_id,
                            "ordinal": ordinal,
                            "citation_snapshot_json": _serialize_citation_snapshot(
                                citation_snapshots[ordinal]
                            ),
                        }
                        for ordinal, element_id in enumerate(
                            message.citation_element_ids
                        )
                    ],
                )
        return replace(
            message,
            sequence=sequence,
            citation_snapshots=citation_snapshots,
        )

    def get_conversation_messages(
        self,
        paper_id: str,
        conversation_id: str,
        *,
        limit: int | None = None,
    ) -> tuple[ConversationMessage, ...]:
        if limit is not None and limit < 1:
            raise ValueError("conversation message limit must be positive")

        message_scope = (
            select(conversation_messages)
            .where(conversation_messages.c.paper_id == paper_id)
            .where(conversation_messages.c.conversation_id == conversation_id)
        )
        if limit is not None:
            message_scope = message_scope.order_by(
                conversation_messages.c.sequence.desc()
            ).limit(limit)
        selected_messages = message_scope.subquery()

        with self.engine.connect() as connection:
            rows = tuple(
                connection.execute(
                    select(selected_messages).order_by(selected_messages.c.sequence)
                ).mappings()
            )
            if not rows:
                return ()

            citation_rows = connection.execute(
                select(
                    conversation_message_citations.c.message_id,
                    conversation_message_citations.c.element_id,
                    conversation_message_citations.c.ordinal,
                    conversation_message_citations.c.citation_snapshot_json,
                )
                .select_from(
                    conversation_message_citations.join(
                        selected_messages,
                        (
                            conversation_message_citations.c.paper_id
                            == selected_messages.c.paper_id
                        )
                        & (
                            conversation_message_citations.c.message_id
                            == selected_messages.c.id
                        ),
                    )
                )
                .order_by(
                    conversation_message_citations.c.message_id,
                    conversation_message_citations.c.ordinal,
                    conversation_message_citations.c.element_id,
                )
            ).mappings()
            citations_by_message: dict[str, list[object]] = {}
            for citation_row in citation_rows:
                citations_by_message.setdefault(
                    citation_row["message_id"], []
                ).append(citation_row)

            messages = tuple(
                self._conversation_message_from_row(
                    row, tuple(citations_by_message.get(row["id"], ()))
                )
                for row in rows
            )
            return _sanitize_model_provenance_pairs(messages)

    def get_agent_turn_by_request(
        self, paper_id: str, request_id: str
    ) -> tuple[ConversationMessage, ConversationMessage] | None:
        messages = self._get_agent_messages_by_request(paper_id, request_id)
        if (
            len(messages) != 2
            or messages[0].role is not AgentMessageRole.user
            or messages[1].role is not AgentMessageRole.assistant
            or messages[0].conversation_id != messages[1].conversation_id
        ):
            return None
        return messages[0], messages[1]

    def get_agent_user_message_by_request(
        self, paper_id: str, request_id: str
    ) -> ConversationMessage | None:
        messages = self._get_agent_messages_by_request(paper_id, request_id)
        if len(messages) != 1 or messages[0].role is not AgentMessageRole.user:
            return None
        return messages[0]

    def _get_agent_messages_by_request(
        self, paper_id: str, request_id: str
    ) -> tuple[ConversationMessage, ...]:
        with self.engine.connect() as connection:
            rows = tuple(
                connection.execute(
                    select(conversation_messages)
                    .where(conversation_messages.c.paper_id == paper_id)
                    .where(conversation_messages.c.request_id == request_id)
                    .order_by(conversation_messages.c.sequence)
                ).mappings()
            )
            if not rows:
                return ()
            message_ids = tuple(row["id"] for row in rows)
            citation_rows = connection.execute(
                select(
                    conversation_message_citations.c.message_id,
                    conversation_message_citations.c.element_id,
                    conversation_message_citations.c.ordinal,
                    conversation_message_citations.c.citation_snapshot_json,
                )
                .where(conversation_message_citations.c.paper_id == paper_id)
                .where(conversation_message_citations.c.message_id.in_(message_ids))
                .order_by(
                    conversation_message_citations.c.message_id,
                    conversation_message_citations.c.ordinal,
                    conversation_message_citations.c.element_id,
                )
            ).mappings()
            citations_by_message: dict[str, list[object]] = {}
            for citation_row in citation_rows:
                citations_by_message.setdefault(citation_row["message_id"], []).append(
                    citation_row
                )
        messages = tuple(
            self._conversation_message_from_row(
                row, tuple(citations_by_message.get(row["id"], ()))
            )
            for row in rows
        )
        return _sanitize_model_provenance_pairs(messages)

    def link_message_anchor(
        self, paper_id: str, message_id: str, anchor_id: str
    ) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                insert(conversation_message_anchors).values(
                    paper_id=paper_id,
                    message_id=message_id,
                    anchor_id=anchor_id,
                )
            )

    def link_message_notes(
        self, paper_id: str, message_id: str, note_ids: tuple[str, ...]
    ) -> None:
        if not note_ids:
            return
        with self.engine.begin() as connection:
            connection.execute(
                insert(conversation_message_note_citations),
                [
                    {
                        "paper_id": paper_id,
                        "message_id": message_id,
                        "note_id": note_id,
                        "ordinal": ordinal,
                    }
                    for ordinal, note_id in enumerate(note_ids)
                ],
            )

    def get_message_note_ids(
        self, paper_id: str, message_ids: tuple[str, ...]
    ) -> dict[str, tuple[str, ...]]:
        if not message_ids:
            return {}
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(
                    conversation_message_note_citations.c.message_id,
                    conversation_message_note_citations.c.note_id,
                )
                .where(conversation_message_note_citations.c.paper_id == paper_id)
                .where(
                    conversation_message_note_citations.c.message_id.in_(message_ids)
                )
                .order_by(
                    conversation_message_note_citations.c.message_id,
                    conversation_message_note_citations.c.ordinal,
                    conversation_message_note_citations.c.note_id,
                )
            ).mappings()
            note_ids_by_message: dict[str, list[str]] = {}
            for row in rows:
                note_ids_by_message.setdefault(row["message_id"], []).append(
                    row["note_id"]
                )
            return {
                message_id: tuple(note_ids_by_message.get(message_id, ()))
                for message_id in message_ids
            }

    def get_message_anchor_ids(
        self, paper_id: str, message_ids: tuple[str, ...]
    ) -> dict[str, tuple[str, ...]]:
        if not message_ids:
            return {}
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(
                    conversation_message_anchors.c.message_id,
                    conversation_message_anchors.c.anchor_id,
                )
                .where(conversation_message_anchors.c.paper_id == paper_id)
                .where(conversation_message_anchors.c.message_id.in_(message_ids))
                .order_by(
                    conversation_message_anchors.c.message_id,
                    conversation_message_anchors.c.anchor_id,
                )
            ).mappings()
            anchor_ids_by_message: dict[str, list[str]] = {}
            for row in rows:
                anchor_ids_by_message.setdefault(row["message_id"], []).append(
                    row["anchor_id"]
                )
            return {
                message_id: tuple(anchor_ids_by_message.get(message_id, ()))
                for message_id in message_ids
            }

    def delete_paper_data(self, paper_id: str) -> bool:
        with self.engine.begin() as connection:
            exists = connection.execute(
                select(papers.c.id).where(papers.c.id == paper_id)
            ).scalar_one_or_none()
            if exists is None:
                return False
            for table in PAPER_DELETE_ORDER:
                self._delete_rows(connection, table, paper_id)
            connection.execute(delete(papers).where(papers.c.id == paper_id))
        return True

    @staticmethod
    def _delete_rows(connection, table, paper_id: str) -> None:
        connection.execute(delete(table).where(table.c.paper_id == paper_id))

    def save_page(self, paper_id: str, page: Page) -> Page:
        with self.engine.begin() as connection:
            connection.execute(
                insert(pages).values(
                    id=page.id, paper_id=paper_id, number=page.number, width=page.width, height=page.height
                )
            )
        return page

    def get_pages(self, paper_id: str) -> tuple[Page, ...]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(pages).where(pages.c.paper_id == paper_id).order_by(pages.c.number, pages.c.id)
            ).mappings()
            return tuple(Page(id=row["id"], number=row["number"], width=row["width"], height=row["height"]) for row in rows)

    def save_stage0_document(
        self,
        paper_id: str,
        stage0_pages: tuple[Page, ...],
        stage0_elements: tuple[DocumentElement, ...],
    ) -> None:
        next_order = self._next_order(document_elements, paper_id)
        persisted_elements = tuple(
            replace(element, order=next_order + index)
            for index, element in enumerate(stage0_elements)
        )
        with self.engine.begin() as connection:
            if stage0_pages:
                connection.execute(
                    insert(pages),
                    [
                        {
                            "id": page.id,
                            "paper_id": paper_id,
                            "number": page.number,
                            "width": page.width,
                            "height": page.height,
                        }
                        for page in stage0_pages
                    ],
                )
            for element in persisted_elements:
                self._require_owned_page(connection, paper_id, element.page_number)
            if persisted_elements:
                connection.execute(
                    insert(document_elements),
                    [
                        {
                            "id": element.id,
                            "paper_id": paper_id,
                            "section_id": element.section_id,
                            "kind": element.kind,
                            "text": element.text,
                            "page_number": element.page_number,
                            "bbox_x0": None if element.bbox is None else element.bbox.x0,
                            "bbox_y0": None if element.bbox is None else element.bbox.y0,
                            "bbox_x1": None if element.bbox is None else element.bbox.x1,
                            "bbox_y1": None if element.bbox is None else element.bbox.y1,
                            "location_status": element.location_status,
                            "order_index": element.order,
                        }
                        for element in persisted_elements
                    ],
                )

    def save_section(self, paper_id: str, section: Section) -> Section:
        with self.engine.begin() as connection:
            self._require_owned_page(connection, paper_id, section.page_number)
            connection.execute(
                insert(sections).values(
                    id=section.id,
                    paper_id=paper_id,
                    title=section.title,
                    page_number=section.page_number,
                    order_index=section.order,
                )
            )
        return section

    def save_stage1_document(
        self,
        paper_id: str,
        stage1_sections: tuple[Section, ...],
        stage1_elements: tuple[DocumentElement, ...],
    ) -> None:
        next_order = self._next_order(document_elements, paper_id)
        persisted_elements = tuple(
            replace(
                element,
                order=element.order if element.order is not None else next_order + index,
            )
            for index, element in enumerate(stage1_elements)
        )
        with self.engine.begin() as connection:
            for section in stage1_sections:
                self._require_owned_page(connection, paper_id, section.page_number)
            for element in persisted_elements:
                self._require_owned_page(connection, paper_id, element.page_number)
            if stage1_sections:
                connection.execute(
                    insert(sections),
                    [
                        {
                            "id": section.id,
                            "paper_id": paper_id,
                            "title": section.title,
                            "page_number": section.page_number,
                            "order_index": section.order,
                        }
                        for section in stage1_sections
                    ],
                )
            if persisted_elements:
                connection.execute(
                    insert(document_elements),
                    [
                        {
                            "id": element.id,
                            "paper_id": paper_id,
                            "section_id": element.section_id,
                            "kind": element.kind,
                            "text": element.text,
                            "page_number": element.page_number,
                            "bbox_x0": None if element.bbox is None else element.bbox.x0,
                            "bbox_y0": None if element.bbox is None else element.bbox.y0,
                            "bbox_x1": None if element.bbox is None else element.bbox.x1,
                            "bbox_y1": None if element.bbox is None else element.bbox.y1,
                            "location_status": element.location_status,
                            "order_index": element.order,
                        }
                        for element in persisted_elements
                    ],
                )

    def get_sections(self, paper_id: str) -> tuple[Section, ...]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(sections).where(sections.c.paper_id == paper_id).order_by(sections.c.order_index, sections.c.id)
            ).mappings()
            return tuple(
                Section(id=row["id"], title=row["title"], page_number=row["page_number"], order=row["order_index"])
                for row in rows
            )

    def save_element(self, paper_id: str, element: DocumentElement) -> DocumentElement:
        order = element.order if element.order is not None else self._next_order(document_elements, paper_id)
        persisted = replace(element, order=order)
        bbox = persisted.bbox
        with self.engine.begin() as connection:
            self._require_owned_page(connection, paper_id, persisted.page_number)
            connection.execute(
                insert(document_elements).values(
                    id=persisted.id,
                    paper_id=paper_id,
                    section_id=persisted.section_id,
                    kind=persisted.kind,
                    text=persisted.text,
                    page_number=persisted.page_number,
                    bbox_x0=None if bbox is None else bbox.x0,
                    bbox_y0=None if bbox is None else bbox.y0,
                    bbox_x1=None if bbox is None else bbox.x1,
                    bbox_y1=None if bbox is None else bbox.y1,
                    location_status=persisted.location_status,
                    order_index=persisted.order,
                )
            )
        return persisted

    def get_elements(self, paper_id: str) -> tuple[DocumentElement, ...]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(document_elements)
                .where(document_elements.c.paper_id == paper_id)
                .order_by(document_elements.c.order_index, document_elements.c.id)
            ).mappings()
            return tuple(self._element_from_row(row) for row in rows)

    def get_located_elements_by_ids(
        self, paper_id: str, element_ids: tuple[str, ...]
    ) -> tuple[DocumentElement, ...]:
        unique_ids = tuple(dict.fromkeys(element_ids))
        if not unique_ids:
            return ()
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(document_elements)
                .where(document_elements.c.paper_id == paper_id)
                .where(document_elements.c.location_status == "located")
                .where(document_elements.c.id.in_(unique_ids))
                .order_by(document_elements.c.order_index, document_elements.c.id)
            ).mappings()
            return tuple(self._element_from_row(row) for row in rows)

    def get_located_graph_source_elements(
        self, paper_id: str
    ) -> tuple[DocumentElement, ...]:
        """Return only located source records that can support graph evidence."""
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(document_elements)
                .where(document_elements.c.paper_id == paper_id)
                .where(document_elements.c.location_status == "located")
                .where(document_elements.c.kind.in_(("paragraph", "text_block")))
                .order_by(document_elements.c.order_index, document_elements.c.id)
            ).mappings()
            return tuple(self._element_from_row(row) for row in rows)

    def record_graph_stage_failure(
        self,
        paper_id: str,
        *,
        stage: str,
        error_summary: str,
        model_profile_id: str | None = None,
        model_snapshot: ModelSnapshot | None = None,
        request_id: str | None = None,
    ) -> None:
        """Durably finish a failed graph build without exposing its internal error."""
        with self.engine.begin() as connection:
            self._record_processing_status(
                connection,
                paper_id,
                ProcessingStatus.failed,
                stage=stage,
                error_summary=error_summary,
                model_profile_id=model_profile_id,
                model_snapshot=model_snapshot,
                request_id=request_id,
            )
            connection.execute(
                update(papers)
                .where(papers.c.id == paper_id)
                .values(status=ProcessingStatus.partial.value)
            )
            self._record_processing_status(
                connection,
                paper_id,
                ProcessingStatus.partial,
                error_summary=error_summary,
            )

    def replace_graph_stage(
        self,
        paper_id: str,
        stage: GraphStage,
        nodes: tuple[GraphNode, ...],
        edges: tuple[GraphEdge, ...],
    ) -> PaperGraph:
        self._require_graph_stage_replacement(stage, nodes, edges)

        with self.engine.begin() as connection:
            return self._replace_graph_stage(connection, paper_id, stage, nodes, edges)

    def replace_graph_stage_and_complete(
        self,
        paper_id: str,
        stage: GraphStage,
        nodes: tuple[GraphNode, ...],
        edges: tuple[GraphEdge, ...],
        *,
        model_profile_id: str | None = None,
        model_snapshot: ModelSnapshot | None = None,
        request_id: str | None = None,
    ) -> PaperGraph:
        """Atomically replace a graph stage and durably publish its success."""
        self._require_graph_stage_replacement(stage, nodes, edges)

        with self.engine.begin() as connection:
            invalidates_deep_stage = (
                stage is GraphStage.core
                and self._stage_has_processing_state(
                    connection, paper_id, GraphStage.deep.value
                )
            )
            graph = self._replace_graph_stage(connection, paper_id, stage, nodes, edges)
            self._record_processing_status(
                connection,
                paper_id,
                ProcessingStatus.completed,
                stage=stage.value,
                model_profile_id=model_profile_id,
                model_snapshot=model_snapshot,
                request_id=request_id,
            )
            if invalidates_deep_stage:
                self._record_processing_status(
                    connection,
                    paper_id,
                    ProcessingStatus.queued,
                    stage=GraphStage.deep.value,
                )
            connection.execute(
                update(papers)
                .where(papers.c.id == paper_id)
                .values(status=ProcessingStatus.completed.value)
            )
            self._record_processing_status(connection, paper_id, ProcessingStatus.completed)
            return graph

    def get_graph(self, paper_id: str) -> PaperGraph:
        with self.engine.connect() as connection:
            return self._get_graph(connection, paper_id)

    def get_graph_node(self, paper_id: str, node_id: str) -> GraphNode | None:
        with self.engine.connect() as connection:
            row = connection.execute(
                select(graph_nodes)
                .where(graph_nodes.c.paper_id == paper_id)
                .where(graph_nodes.c.id == node_id)
            ).mappings().one_or_none()
            return None if row is None else self._graph_node_from_row(connection, paper_id, row)

    def get_graph_neighbors(self, paper_id: str, node_id: str) -> PaperGraph:
        graph = self.get_graph(paper_id)
        if not any(node.id == node_id for node in graph.nodes):
            return PaperGraph(nodes=(), edges=())
        edges = tuple(
            edge
            for edge in graph.edges
            if edge.source_node_id == node_id or edge.target_node_id == node_id
        )
        node_ids = {node_id}
        for edge in edges:
            node_ids.add(edge.source_node_id)
            node_ids.add(edge.target_node_id)
        return PaperGraph(
            nodes=tuple(node for node in graph.nodes if node.id in node_ids), edges=edges
        )

    def find_graph_paths(
        self,
        paper_id: str,
        source_node_id: str,
        target_node_id: str,
        max_depth: int,
    ) -> tuple[tuple[str, ...], ...]:
        if max_depth < 0:
            raise ValueError("max_depth must be nonnegative")
        graph = self.get_graph(paper_id)
        node_ids = {node.id for node in graph.nodes}
        if source_node_id not in node_ids or target_node_id not in node_ids:
            return ()
        paths: list[tuple[str, ...]] = []
        pending = [(source_node_id, (source_node_id,))]
        while pending:
            current_node_id, path = pending.pop(0)
            if current_node_id == target_node_id:
                paths.append(path)
                continue
            if len(path) - 1 == max_depth:
                continue
            for edge in graph.edges:
                if edge.source_node_id != current_node_id or edge.target_node_id in path:
                    continue
                pending.append((edge.target_node_id, (*path, edge.target_node_id)))
        return tuple(paths)

    def get_graph_subgraph(
        self, paper_id: str, node_ids: tuple[str, ...], depth: int
    ) -> PaperGraph:
        if depth < 0:
            raise ValueError("depth must be nonnegative")
        graph = self.get_graph(paper_id)
        included = {node.id for node in graph.nodes if node.id in node_ids}
        frontier = set(included)
        for _ in range(depth):
            next_frontier: set[str] = set()
            for edge in graph.edges:
                if edge.source_node_id in frontier:
                    next_frontier.add(edge.target_node_id)
                if edge.target_node_id in frontier:
                    next_frontier.add(edge.source_node_id)
            next_frontier -= included
            included.update(next_frontier)
            frontier = next_frontier
            if not frontier:
                break
        return PaperGraph(
            nodes=tuple(node for node in graph.nodes if node.id in included),
            edges=tuple(
                edge
                for edge in graph.edges
                if edge.source_node_id in included and edge.target_node_id in included
            ),
        )

    def create_note(self, paper_id: str, note: Note) -> Note:
        order = self._next_order(notes, paper_id)
        with self.engine.begin() as connection:
            self._require_owned_page(connection, paper_id, note.page_number)
            connection.execute(
                insert(notes).values(
                    id=note.id,
                    paper_id=paper_id,
                    element_id=note.element_id,
                    page_number=note.page_number,
                    body=note.body,
                    order_index=order,
                )
            )
        return note

    def get_notes(self, paper_id: str) -> tuple[Note, ...]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(notes).where(notes.c.paper_id == paper_id).order_by(notes.c.order_index, notes.c.id)
            ).mappings()
            anchor_ids_by_note: dict[str, list[str]] = {}
            for anchor_row in connection.execute(
                select(note_anchors)
                .where(note_anchors.c.paper_id == paper_id)
                .order_by(note_anchors.c.note_id, note_anchors.c.anchor_id)
            ).mappings():
                anchor_ids_by_note.setdefault(anchor_row["note_id"], []).append(
                    anchor_row["anchor_id"]
                )
            return tuple(
                Note(
                    id=row["id"],
                    body=row["body"],
                    element_id=row["element_id"],
                    page_number=row["page_number"],
                    note_type=NoteType(row["note_type"]),
                    anchor_ids=tuple(anchor_ids_by_note.get(row["id"], ())),
                    model_profile_id=row["model_profile_id"],
                    model_snapshot=_parse_model_snapshot(row["model_snapshot_json"]),
                    ai_generated=bool(row["ai_generated"]),
                    user_edited=bool(row["user_edited"]),
                    created_at=(
                        None
                        if row["created_at"] is None
                        else datetime.fromisoformat(row["created_at"])
                    ),
                    updated_at=(
                        None
                        if row["updated_at"] is None
                        else datetime.fromisoformat(row["updated_at"])
                    ),
                )
                for row in rows
            )

    def get_document(self, paper_id: str) -> PaperDocument | None:
        paper = self.get_paper(paper_id)
        if paper is None:
            return None
        return PaperDocument(
            paper=paper,
            pages=self.get_pages(paper_id),
            sections=self.get_sections(paper_id),
            elements=self.get_elements(paper_id),
            notes=self.get_notes(paper_id),
        )

    def _next_order(self, table, paper_id: str) -> int:
        with self.engine.connect() as connection:
            current = connection.execute(
                select(func.max(table.c.order_index)).where(table.c.paper_id == paper_id)
            ).scalar_one()
        return 0 if current is None else current + 1

    @staticmethod
    def _require_owned_conversation(connection, paper_id: str, conversation_id: str) -> None:
        conversation = connection.execute(
            select(conversations.c.id)
            .where(conversations.c.paper_id == paper_id)
            .where(conversations.c.id == conversation_id)
        ).scalar_one_or_none()
        if conversation is None:
            raise ConversationReferenceError("conversation does not belong to paper")

    @staticmethod
    def _require_located_conversation_citations(
        connection, paper_id: str, element_ids: tuple[str, ...]
    ) -> dict[str, CitationSnapshot]:
        expected = set(element_ids)
        if not expected:
            return {}
        rows = tuple(
            connection.execute(
                select(
                    document_elements.c.id,
                    document_elements.c.kind,
                    document_elements.c.page_number,
                    document_elements.c.bbox_x0,
                    document_elements.c.bbox_y0,
                    document_elements.c.bbox_x1,
                    document_elements.c.bbox_y1,
                )
                .where(document_elements.c.paper_id == paper_id)
                .where(document_elements.c.location_status == "located")
                .where(document_elements.c.id.in_(expected))
            ).mappings()
        )
        if {row["id"] for row in rows} != expected:
            raise ConversationReferenceError(
                "citation must be a located element owned by paper"
            )
        try:
            return {
                row["id"]: CitationSnapshot(
                    id=row["id"],
                    kind=row["kind"],
                    page_number=row["page_number"],
                    bbox=BoundingBox(
                        x0=row["bbox_x0"],
                        y0=row["bbox_y0"],
                        x1=row["bbox_x1"],
                        y1=row["bbox_y1"],
                    ),
                )
                for row in rows
            }
        except (TypeError, ValueError):
            raise ConversationReferenceError(
                "citation must be a located element owned by paper"
            ) from None

    @staticmethod
    def _next_conversation_sequence(connection, conversation_id: str) -> int:
        current = connection.execute(
            select(func.max(conversation_messages.c.sequence)).where(
                conversation_messages.c.conversation_id == conversation_id
            )
        ).scalar_one()
        return 0 if current is None else current + 1

    @staticmethod
    def _require_located_graph_evidence(connection, paper_id: str, element_ids: tuple[str, ...]) -> None:
        expected = set(element_ids)
        if not expected:
            return
        found = set(
            connection.execute(
                select(document_elements.c.id)
                .where(document_elements.c.paper_id == paper_id)
                .where(document_elements.c.location_status == "located")
                .where(document_elements.c.id.in_(expected))
            ).scalars()
        )
        if found != expected:
            raise GraphReferenceError("graph evidence must be a located element owned by paper")

    @staticmethod
    def _require_owned_graph_nodes(connection, paper_id: str, node_ids: tuple[str, ...]) -> None:
        expected = set(node_ids)
        if not expected:
            return
        found = set(
            connection.execute(
                select(graph_nodes.c.id)
                .where(graph_nodes.c.paper_id == paper_id)
                .where(graph_nodes.c.id.in_(expected))
            ).scalars()
        )
        if found != expected:
            raise GraphReferenceError("graph edge endpoint does not belong to paper")

    @staticmethod
    def _record_processing_status(
        connection,
        paper_id: str,
        status: ProcessingStatus,
        *,
        stage: str | None = None,
        error_summary: str | None = None,
        model_profile_id: str | None = None,
        model_snapshot: ModelSnapshot | None = None,
        request_id: str | None = None,
    ) -> None:
        current = connection.execute(
            select(func.max(processing_runs.c.sequence)).where(
                processing_runs.c.paper_id == paper_id
            )
        ).scalar_one()
        sequence = 0 if current is None else current + 1
        connection.execute(
            insert(processing_runs).values(
                id=str(uuid4()),
                paper_id=paper_id,
                sequence=sequence,
                stage=stage,
                status=status.value,
                error_summary=error_summary,
                model_profile_id=model_profile_id,
                model_snapshot_json=_serialize_model_snapshot(model_snapshot),
                request_id=request_id,
            )
        )

    @staticmethod
    def _require_graph_stage_replacement(
        stage: GraphStage,
        nodes: tuple[GraphNode, ...],
        edges: tuple[GraphEdge, ...],
    ) -> None:
        if not isinstance(stage, GraphStage):
            raise ValueError("stage must be a GraphStage")
        if any(node.stage is not stage for node in nodes):
            raise ValueError("node stage must match replacement stage")
        if any(edge.stage is not stage for edge in edges):
            raise ValueError("edge stage must match replacement stage")

    def _replace_graph_stage(
        self,
        connection,
        paper_id: str,
        stage: GraphStage,
        nodes: tuple[GraphNode, ...],
        edges: tuple[GraphEdge, ...],
    ) -> PaperGraph:
        self._require_located_graph_evidence(
            connection,
            paper_id,
            tuple(
                element_id
                for record in (*nodes, *edges)
                for element_id in record.evidence_element_ids
            ),
        )
        self._delete_graph_stages(
            connection,
            paper_id,
            (GraphStage.deep, GraphStage.core)
            if stage is GraphStage.core
            else (GraphStage.deep,),
        )
        if nodes:
            connection.execute(
                insert(graph_nodes),
                [
                    {
                        "id": node.id,
                        "paper_id": paper_id,
                        "node_type": node.node_type,
                        "normalized_name": normalize_graph_node_name(node.name),
                        "name": node.name,
                        "summary": node.summary,
                        "stage": node.stage.value,
                    }
                    for node in nodes
                ],
            )
            connection.execute(
                insert(graph_node_evidence),
                [
                    {
                        "paper_id": paper_id,
                        "node_id": node.id,
                        "element_id": element_id,
                    }
                    for node in nodes
                    for element_id in node.evidence_element_ids
                ],
            )
        self._require_owned_graph_nodes(
            connection,
            paper_id,
            tuple(
                node_id
                for edge in edges
                for node_id in (edge.source_node_id, edge.target_node_id)
            ),
        )
        if edges:
            connection.execute(
                insert(graph_edges),
                [
                    {
                        "id": edge.id,
                        "paper_id": paper_id,
                        "source_node_id": edge.source_node_id,
                        "target_node_id": edge.target_node_id,
                        "relation_type": edge.relation_type,
                        "stage": edge.stage.value,
                    }
                    for edge in edges
                ],
            )
            connection.execute(
                insert(graph_edge_evidence),
                [
                    {
                        "paper_id": paper_id,
                        "edge_id": edge.id,
                        "element_id": element_id,
                    }
                    for edge in edges
                    for element_id in edge.evidence_element_ids
                ],
            )
        return self._get_graph(connection, paper_id)

    @staticmethod
    def _stage_has_processing_state(
        connection, paper_id: str, stage: str
    ) -> bool:
        return (
            connection.execute(
                select(processing_runs.c.id)
                .where(processing_runs.c.paper_id == paper_id)
                .where(processing_runs.c.stage == stage)
                .limit(1)
            ).scalar_one_or_none()
            is not None
        )

    @staticmethod
    def _delete_graph_stages(connection, paper_id: str, stages: tuple[GraphStage, ...]) -> None:
        for stage in stages:
            edge_ids = select(graph_edges.c.id).where(
                graph_edges.c.paper_id == paper_id,
                graph_edges.c.stage == stage.value,
            )
            connection.execute(
                delete(graph_edge_evidence).where(
                    graph_edge_evidence.c.paper_id == paper_id,
                    graph_edge_evidence.c.edge_id.in_(edge_ids),
                )
            )
            connection.execute(
                delete(graph_edges).where(
                    graph_edges.c.paper_id == paper_id,
                    graph_edges.c.stage == stage.value,
                )
            )
            node_ids = select(graph_nodes.c.id).where(
                graph_nodes.c.paper_id == paper_id,
                graph_nodes.c.stage == stage.value,
            )
            connection.execute(
                delete(graph_node_evidence).where(
                    graph_node_evidence.c.paper_id == paper_id,
                    graph_node_evidence.c.node_id.in_(node_ids),
                )
            )
            connection.execute(
                delete(graph_nodes).where(
                    graph_nodes.c.paper_id == paper_id,
                    graph_nodes.c.stage == stage.value,
                )
            )

    @classmethod
    def _get_graph(cls, connection, paper_id: str) -> PaperGraph:
        node_rows = connection.execute(
            select(graph_nodes)
            .where(graph_nodes.c.paper_id == paper_id)
            .order_by(graph_nodes.c.id)
        ).mappings()
        nodes = tuple(cls._graph_node_from_row(connection, paper_id, row) for row in node_rows)
        edge_rows = connection.execute(
            select(graph_edges)
            .where(graph_edges.c.paper_id == paper_id)
            .order_by(graph_edges.c.id)
        ).mappings()
        edges = tuple(cls._graph_edge_from_row(connection, paper_id, row) for row in edge_rows)
        return PaperGraph(nodes=nodes, edges=edges)

    @staticmethod
    def _graph_node_from_row(connection, paper_id: str, row) -> GraphNode:
        evidence_ids = tuple(
            connection.execute(
                select(graph_node_evidence.c.element_id)
                .where(graph_node_evidence.c.paper_id == paper_id)
                .where(graph_node_evidence.c.node_id == row["id"])
                .order_by(graph_node_evidence.c.element_id)
            ).scalars()
        )
        return GraphNode(
            id=row["id"],
            node_type=row["node_type"],
            name=row["name"],
            summary=row["summary"],
            stage=GraphStage(row["stage"]),
            evidence_element_ids=evidence_ids,
        )

    @staticmethod
    def _graph_edge_from_row(connection, paper_id: str, row) -> GraphEdge:
        evidence_ids = tuple(
            connection.execute(
                select(graph_edge_evidence.c.element_id)
                .where(graph_edge_evidence.c.paper_id == paper_id)
                .where(graph_edge_evidence.c.edge_id == row["id"])
                .order_by(graph_edge_evidence.c.element_id)
            ).scalars()
        )
        return GraphEdge(
            id=row["id"],
            source_node_id=row["source_node_id"],
            target_node_id=row["target_node_id"],
            relation_type=row["relation_type"],
            stage=GraphStage(row["stage"]),
            evidence_element_ids=evidence_ids,
        )

    def _next_processing_sequence(self, paper_id: str) -> int:
        with self.engine.connect() as connection:
            current = connection.execute(
                select(func.max(processing_runs.c.sequence)).where(
                    processing_runs.c.paper_id == paper_id
                )
            ).scalar_one()
        return 0 if current is None else current + 1

    def _get_processing_statuses(
        self, paper_id: str, *, stage: str | None
    ) -> tuple[ProcessingStatus, ...]:
        statement = select(processing_runs.c.status).where(
            processing_runs.c.paper_id == paper_id
        )
        if stage is None:
            statement = statement.where(processing_runs.c.stage.is_(None))
        else:
            statement = statement.where(processing_runs.c.stage == stage)
        with self.engine.connect() as connection:
            rows = connection.execute(statement.order_by(processing_runs.c.sequence))
        return tuple(ProcessingStatus(row.status) for row in rows)

    @staticmethod
    def _require_owned_page(connection, paper_id: str, page_number: int | None) -> None:
        if page_number is None:
            return
        page_id = connection.execute(
            select(pages.c.id)
            .where(pages.c.paper_id == paper_id)
            .where(pages.c.number == page_number)
        ).scalar_one_or_none()
        if page_id is None:
            raise PageReferenceError("page target does not belong to paper")

    @staticmethod
    def _paper_from_row(row) -> Paper:
        return Paper(
            id=row["id"],
            original_filename=row["original_filename"],
            stored_filename=row["stored_filename"],
            status=ProcessingStatus(row["status"]),
            source_published=bool(row["source_published"]),
        )

    @staticmethod
    def _conversation_from_row(row) -> Conversation:
        return Conversation(id=row["id"], paper_id=row["paper_id"])

    @staticmethod
    def _conversation_message_from_row(
        row, citation_rows: tuple[object, ...]
    ) -> ConversationMessage:
        ordered_citation_rows = _ordered_citation_rows(citation_rows)
        citation_element_ids = tuple(
            citation_row["element_id"] for citation_row in ordered_citation_rows
        )
        model_profile_id = row["model_profile_id"]
        model_snapshot = _parse_model_snapshot(row["model_snapshot_json"])
        if (
            model_snapshot is not None
            and model_snapshot.profile_id != model_profile_id
        ):
            model_snapshot = None
        return ConversationMessage(
            id=row["id"],
            conversation_id=row["conversation_id"],
            paper_id=row["paper_id"],
            role=AgentMessageRole(row["role"]),
            content=row["content"],
            citation_element_ids=citation_element_ids,
            citation_snapshots=tuple(
                _parse_citation_snapshot(
                    citation_row["citation_snapshot_json"],
                    expected_element_id=citation_row["element_id"],
                )
                for citation_row in ordered_citation_rows
            ),
            model_profile_id=model_profile_id,
            model_snapshot=model_snapshot,
            request_id=row["request_id"],
            background_explanation=row["background_explanation"],
            sequence=row["sequence"],
        )

    @staticmethod
    def _element_from_row(row) -> DocumentElement:
        bbox = None
        if row["bbox_x0"] is not None:
            bbox = BoundingBox(
                x0=row["bbox_x0"],
                y0=row["bbox_y0"],
                x1=row["bbox_x1"],
                y1=row["bbox_y1"],
            )
        return DocumentElement(
            id=row["id"],
            kind=row["kind"],
            text=row["text"],
            page_number=row["page_number"],
            bbox=bbox,
            section_id=row["section_id"],
            location_status=row["location_status"],
            order=row["order_index"],
        )
