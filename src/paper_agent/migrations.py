from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    apply: Callable[[Connection], None]


def run_schema_migrations(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version INTEGER PRIMARY KEY, name VARCHAR NOT NULL, applied_at VARCHAR NOT NULL)"
        )
        applied = set(
            connection.exec_driver_sql("SELECT version FROM schema_migrations").scalars()
        )
        for migration in MIGRATIONS:
            if migration.version in applied:
                continue
            migration.apply(connection)
            connection.execute(
                text(
                    "INSERT INTO schema_migrations(version, name, applied_at) "
                    "VALUES (:version, :name, :applied_at)"
                ),
                {
                    "version": migration.version,
                    "name": migration.name,
                    "applied_at": datetime.now(UTC).isoformat(),
                },
            )


def _apply_model_profiles_and_provenance_columns(connection: Connection) -> None:
    connection.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS model_profiles ("
        "id VARCHAR(36) NOT NULL, "
        "display_name VARCHAR NOT NULL, "
        "base_url VARCHAR NOT NULL, "
        "model_name VARCHAR NOT NULL, "
        "secret_ref VARCHAR, "
        "enabled BOOLEAN NOT NULL DEFAULT '1', "
        "is_default BOOLEAN NOT NULL DEFAULT '0', "
        "revision INTEGER NOT NULL, "
        "basic_chat BOOLEAN NOT NULL DEFAULT '0', "
        "structured_output BOOLEAN NOT NULL DEFAULT '0', "
        "tool_calling BOOLEAN NOT NULL DEFAULT '0', "
        "capabilities_checked_at VARCHAR, "
        "created_at VARCHAR NOT NULL, "
        "updated_at VARCHAR NOT NULL, "
        "deleted_at VARCHAR, "
        "PRIMARY KEY (id))"
    )
    connection.exec_driver_sql(
        "CREATE UNIQUE INDEX IF NOT EXISTS model_profiles_one_default "
        "ON model_profiles(is_default) "
        "WHERE is_default = 1 AND deleted_at IS NULL"
    )
    _add_nullable_columns(
        connection,
        "conversation_messages",
        {
            "model_profile_id": "VARCHAR(36)",
            "model_snapshot_json": "VARCHAR",
            "request_id": "VARCHAR(36)",
        },
    )
    _add_nullable_columns(
        connection,
        "processing_runs",
        {
            "model_profile_id": "VARCHAR(36)",
            "model_snapshot_json": "VARCHAR",
            "request_id": "VARCHAR(36)",
        },
    )


def _add_nullable_columns(
    connection: Connection, table_name: str, columns: dict[str, str]
) -> None:
    existing_columns = {
        row[1] for row in connection.exec_driver_sql(f"PRAGMA table_info({table_name})")
    }
    if not existing_columns:
        return
    for column_name, column_type in columns.items():
        if column_name not in existing_columns:
            connection.exec_driver_sql(
                f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}"
            )


def _apply_agent_response_snapshots(connection: Connection) -> None:
    """Frozen migration 2 schema for exact durable Agent response replay."""
    _add_nullable_columns(
        connection,
        "conversation_messages",
        {"background_explanation": "VARCHAR"},
    )
    _add_nullable_columns(
        connection,
        "conversation_message_citations",
        {
            "ordinal": "INTEGER",
            "citation_snapshot_json": "VARCHAR",
        },
    )


def _apply_text_anchors_and_annotations(connection: Connection) -> None:
    """Frozen migration 3 schema for anchors, highlights, and generated notes."""
    existing_notes_columns = {
        row[1]
        for row in connection.exec_driver_sql("PRAGMA table_info(notes)")
    }
    if existing_notes_columns:
        if "note_type" not in existing_notes_columns:
            connection.exec_driver_sql(
                "ALTER TABLE notes ADD COLUMN note_type VARCHAR(32) "
                "NOT NULL DEFAULT 'manual'"
            )
        if "model_profile_id" not in existing_notes_columns:
            connection.exec_driver_sql(
                "ALTER TABLE notes ADD COLUMN model_profile_id VARCHAR(36)"
            )
        if "model_snapshot_json" not in existing_notes_columns:
            connection.exec_driver_sql(
                "ALTER TABLE notes ADD COLUMN model_snapshot_json VARCHAR"
            )
        if "ai_generated" not in existing_notes_columns:
            connection.exec_driver_sql(
                "ALTER TABLE notes ADD COLUMN ai_generated BOOLEAN NOT NULL DEFAULT '0'"
            )
        if "user_edited" not in existing_notes_columns:
            connection.exec_driver_sql(
                "ALTER TABLE notes ADD COLUMN user_edited BOOLEAN NOT NULL DEFAULT '0'"
            )
        if "created_at" not in existing_notes_columns:
            connection.exec_driver_sql("ALTER TABLE notes ADD COLUMN created_at VARCHAR")
        if "updated_at" not in existing_notes_columns:
            connection.exec_driver_sql("ALTER TABLE notes ADD COLUMN updated_at VARCHAR")

    connection.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS text_anchors ("
        "id VARCHAR(36) NOT NULL, "
        "paper_id VARCHAR(36) NOT NULL, "
        "quote VARCHAR NOT NULL, "
        "quote_hash VARCHAR(64) NOT NULL, "
        "element_id VARCHAR(36), "
        "page_number INTEGER NOT NULL, "
        "created_at VARCHAR NOT NULL, "
        "PRIMARY KEY (id))"
    )
    connection.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS text_anchor_rects ("
        "anchor_id VARCHAR(36) NOT NULL, "
        "order_index INTEGER NOT NULL, "
        "x0 FLOAT NOT NULL, "
        "y0 FLOAT NOT NULL, "
        "x1 FLOAT NOT NULL, "
        "y1 FLOAT NOT NULL, "
        "PRIMARY KEY (anchor_id, order_index), "
        "CHECK (0 <= x0 AND x0 <= x1 AND x1 <= 1 "
        "AND 0 <= y0 AND y0 <= y1 AND y1 <= 1))"
    )
    connection.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS highlights ("
        "id VARCHAR(36) NOT NULL, "
        "paper_id VARCHAR(36) NOT NULL, "
        "anchor_id VARCHAR(36) NOT NULL, "
        "color VARCHAR(16) NOT NULL, "
        "created_at VARCHAR NOT NULL, "
        "updated_at VARCHAR, "
        "PRIMARY KEY (id), "
        "UNIQUE (anchor_id))"
    )
    connection.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS note_anchors ("
        "note_id VARCHAR(36) NOT NULL, "
        "paper_id VARCHAR(36) NOT NULL, "
        "anchor_id VARCHAR(36) NOT NULL, "
        "PRIMARY KEY (note_id, paper_id, anchor_id))"
    )
    connection.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS selection_assist_requests ("
        "id VARCHAR(36) NOT NULL, "
        "paper_id VARCHAR(36) NOT NULL, "
        "request_id VARCHAR(36) NOT NULL, "
        "action VARCHAR(16) NOT NULL, "
        "status VARCHAR(16) NOT NULL, "
        "anchor_id VARCHAR(36), "
        "note_id VARCHAR(36), "
        "model_profile_id VARCHAR(36), "
        "model_snapshot_json VARCHAR, "
        "created_at VARCHAR NOT NULL, "
        "updated_at VARCHAR, "
        "PRIMARY KEY (id), "
        "UNIQUE (paper_id, request_id))"
    )
    connection.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS conversation_message_note_citations ("
        "paper_id VARCHAR(36) NOT NULL, "
        "message_id VARCHAR(36) NOT NULL, "
        "note_id VARCHAR(36) NOT NULL, "
        "ordinal INTEGER, "
        "PRIMARY KEY (paper_id, message_id, note_id))"
    )
    connection.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS conversation_message_anchors ("
        "paper_id VARCHAR(36) NOT NULL, "
        "message_id VARCHAR(36) NOT NULL, "
        "anchor_id VARCHAR(36) NOT NULL, "
        "PRIMARY KEY (paper_id, message_id, anchor_id))"
    )


def _apply_annotation_request_ids(connection: Connection) -> None:
    """Frozen migration 4 schema for idempotent note and highlight requests."""
    notes_columns = {
        row[1]
        for row in connection.exec_driver_sql("PRAGMA table_info(notes)")
    }
    if notes_columns:
        if "request_id" not in notes_columns:
            connection.exec_driver_sql(
                "ALTER TABLE notes ADD COLUMN request_id VARCHAR(36)"
            )
        connection.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS notes_one_request_per_paper "
            "ON notes(paper_id, request_id) WHERE request_id IS NOT NULL"
        )
    highlight_columns = {
        row[1]
        for row in connection.exec_driver_sql("PRAGMA table_info(highlights)")
    }
    if highlight_columns:
        if "request_id" not in highlight_columns:
            connection.exec_driver_sql(
                "ALTER TABLE highlights ADD COLUMN request_id VARCHAR(36)"
            )
        connection.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS highlights_one_request_per_paper "
            "ON highlights(paper_id, request_id) WHERE request_id IS NOT NULL"
        )


def _apply_text_anchor_rect_paper_id(connection: Connection) -> None:
    """Frozen migration 5 schema for paper-scoped deletion of anchor rects."""
    columns = {
        row[1]
        for row in connection.exec_driver_sql(
            "PRAGMA table_info(text_anchor_rects)"
        )
    }
    if columns and "paper_id" not in columns:
        connection.exec_driver_sql(
            "ALTER TABLE text_anchor_rects ADD COLUMN paper_id VARCHAR(36)"
        )


def _apply_model_profile_token_limits(connection: Connection) -> None:
    """Frozen migration 6 schema for model profile context/output limits."""
    _add_nullable_columns(
        connection,
        "model_profiles",
        {"context_length": "INTEGER", "max_output_tokens": "INTEGER"},
    )


def _apply_section_levels(connection: Connection) -> None:
    """Frozen migration 7 schema for hierarchical section navigation."""
    columns = {
        row[1]
        for row in connection.exec_driver_sql("PRAGMA table_info(sections)")
    }
    if columns and "level" not in columns:
        connection.exec_driver_sql(
            "ALTER TABLE sections ADD COLUMN level INTEGER NOT NULL DEFAULT 1"
        )


def _apply_conversation_created_at(connection: Connection) -> None:
    _add_nullable_columns(connection, "conversations", {"created_at": "VARCHAR"})
    if not connection.exec_driver_sql("PRAGMA table_info(conversations)").fetchone():
        return
    rows = connection.exec_driver_sql(
        "SELECT id FROM conversations WHERE created_at IS NULL ORDER BY rowid"
    ).fetchall()
    started = datetime.now(UTC)
    for index, row in enumerate(rows):
        connection.execute(
            text("UPDATE conversations SET created_at = :created_at WHERE id = :id"),
            {
                "created_at": (started + timedelta(microseconds=index)).isoformat(),
                "id": row[0],
            },
        )


MIGRATIONS = (
    Migration(
        version=1,
        name="add_model_profiles_and_provenance_columns",
        apply=_apply_model_profiles_and_provenance_columns,
    ),
    Migration(
        version=2,
        name="add_agent_response_snapshots",
        apply=_apply_agent_response_snapshots,
    ),
    Migration(
        version=3,
        name="add_text_anchors_and_annotations",
        apply=_apply_text_anchors_and_annotations,
    ),
    Migration(
        version=4,
        name="add_annotation_request_ids",
        apply=_apply_annotation_request_ids,
    ),
    Migration(
        version=5,
        name="add_text_anchor_rect_paper_id",
        apply=_apply_text_anchor_rect_paper_id,
    ),
    Migration(
        version=6,
        name="add_model_profile_token_limits",
        apply=_apply_model_profile_token_limits,
    ),
    Migration(
        version=7,
        name="add_section_levels",
        apply=_apply_section_levels,
    ),
    Migration(
        version=8,
        name="add_conversation_created_at",
        apply=_apply_conversation_created_at,
    ),
)
