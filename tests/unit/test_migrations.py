import pytest
from sqlalchemy import Column, Integer, MetaData, String, Table, insert
from sqlalchemy.exc import IntegrityError

from paper_agent import migrations
from paper_agent.migrations import run_schema_migrations
from paper_agent.database import (
    conversation_message_citations,
    conversation_messages,
    create_database_engine,
    database_url_for,
    initialize_database,
    model_profiles,
    notes,
)


MODEL_PROFILE_COLUMNS = {
    "id",
    "display_name",
    "base_url",
    "model_name",
    "secret_ref",
    "enabled",
    "is_default",
    "revision",
    "basic_chat",
    "structured_output",
    "tool_calling",
    "capabilities_checked_at",
    "created_at",
    "updated_at",
    "deleted_at",
}


def test_model_profile_migration_upgrades_an_existing_database(tmp_path):
    """Breaks if migration 1 skips provenance columns on legacy tables."""
    database_url = database_url_for(tmp_path)
    engine = create_database_engine(database_url)
    legacy = MetaData()
    Table(
        "conversation_messages",
        legacy,
        Column("id", String(36), primary_key=True),
        Column("conversation_id", String(36), nullable=False),
        Column("paper_id", String(36), nullable=False),
        Column("role", String(16), nullable=False),
        Column("content", String, nullable=False),
        Column("sequence", Integer, nullable=False),
    )
    Table(
        "processing_runs",
        legacy,
        Column("id", String(36), primary_key=True),
        Column("paper_id", String(36), nullable=False),
        Column("sequence", Integer, nullable=False),
        Column("status", String(16), nullable=False),
    )
    legacy.create_all(engine)

    run_schema_migrations(engine)

    with engine.connect() as connection:
        message_columns = {
            column[1]
            for column in connection.exec_driver_sql(
                "PRAGMA table_info(conversation_messages)"
            )
        }
        run_columns = {
            column[1]
            for column in connection.exec_driver_sql("PRAGMA table_info(processing_runs)")
        }
        versions = connection.exec_driver_sql(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).scalars().all()

    assert {"model_profile_id", "model_snapshot_json", "request_id"} <= message_columns
    assert {"model_profile_id", "model_snapshot_json", "request_id"} <= run_columns
    assert versions == [1, 2, 3]


def test_model_profile_migration_records_each_version_once_when_rerun(tmp_path):
    """Breaks if a rerun inserts a duplicate migration version record."""
    engine = create_database_engine(database_url_for(tmp_path))

    run_schema_migrations(engine)
    run_schema_migrations(engine)

    with engine.connect() as connection:
        versions = connection.exec_driver_sql(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).scalars().all()

    assert versions == [1, 2, 3]


def test_model_profile_migration_uses_frozen_schema_not_live_metadata(
    tmp_path, monkeypatch
):
    """Breaks if migration 1 adopts a model_profiles column added after its release."""
    future_metadata = MetaData()
    future_model_profiles = model_profiles.to_metadata(future_metadata)
    future_model_profiles.append_column(Column("future_metadata_column", String))
    monkeypatch.setattr(migrations, "model_profiles", future_model_profiles, raising=False)
    engine = create_database_engine(database_url_for(tmp_path))

    migrations.run_schema_migrations(engine)

    with engine.connect() as connection:
        columns = {
            column[1]
            for column in connection.exec_driver_sql("PRAGMA table_info(model_profiles)")
        }

    assert columns == MODEL_PROFILE_COLUMNS


def test_initialize_database_creates_model_profiles_and_all_provenance_columns(tmp_path):
    """Breaks if fresh database initialization omits migration-era schema."""
    engine = initialize_database(database_url_for(tmp_path))

    with engine.connect() as connection:
        tables = {
            row[0]
            for row in connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        message_columns = {
            column[1]
            for column in connection.exec_driver_sql(
                "PRAGMA table_info(conversation_messages)"
            )
        }
        citation_columns = {
            column[1]
            for column in connection.exec_driver_sql(
                "PRAGMA table_info(conversation_message_citations)"
            )
        }
        run_columns = {
            column[1]
            for column in connection.exec_driver_sql("PRAGMA table_info(processing_runs)")
        }
        profile_columns = {
            column[1]
            for column in connection.exec_driver_sql("PRAGMA table_info(model_profiles)")
        }
        note_columns = {
            column[1]
            for column in connection.exec_driver_sql("PRAGMA table_info(notes)")
        }

    assert "model_profiles" in tables
    assert profile_columns == MODEL_PROFILE_COLUMNS
    assert {
        "model_profile_id",
        "model_snapshot_json",
        "request_id",
        "background_explanation",
    } <= message_columns
    assert {"ordinal", "citation_snapshot_json"} <= citation_columns
    assert {"model_profile_id", "model_snapshot_json", "request_id"} <= run_columns
    assert {
        "note_type",
        "model_profile_id",
        "model_snapshot_json",
        "ai_generated",
        "user_edited",
        "created_at",
        "updated_at",
    } <= note_columns
    assert {
        "text_anchors",
        "text_anchor_rects",
        "highlights",
        "note_anchors",
        "selection_assist_requests",
        "conversation_message_note_citations",
        "conversation_message_anchors",
    } <= tables


def test_agent_response_snapshot_migration_upgrades_legacy_conversation_tables(
    tmp_path,
):
    """Breaks if frozen migration 2 cannot preserve exact Agent response fields."""
    engine = create_database_engine(database_url_for(tmp_path))
    legacy = MetaData()
    Table(
        "conversation_messages",
        legacy,
        Column("id", String(36), primary_key=True),
        Column("conversation_id", String(36), nullable=False),
        Column("paper_id", String(36), nullable=False),
        Column("role", String(16), nullable=False),
        Column("content", String, nullable=False),
        Column("sequence", Integer, nullable=False),
    )
    Table(
        "conversation_message_citations",
        legacy,
        Column("paper_id", String(36), primary_key=True),
        Column("message_id", String(36), primary_key=True),
        Column("element_id", String(36), primary_key=True),
    )
    legacy.create_all(engine)

    run_schema_migrations(engine)

    with engine.connect() as connection:
        message_columns = {
            column[1]
            for column in connection.exec_driver_sql(
                "PRAGMA table_info(conversation_messages)"
            )
        }
        citation_columns = {
            column[1]
            for column in connection.exec_driver_sql(
                "PRAGMA table_info(conversation_message_citations)"
            )
        }
        versions = connection.exec_driver_sql(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).scalars().all()

    assert "background_explanation" in message_columns
    assert {"ordinal", "citation_snapshot_json"} <= citation_columns
    assert versions == [1, 2, 3]


def test_agent_response_snapshot_migration_uses_frozen_schema_not_live_metadata(
    tmp_path, monkeypatch
):
    """Breaks if migration 2 starts adopting later live-table columns."""
    future_metadata = MetaData()
    future_messages = conversation_messages.to_metadata(future_metadata)
    future_messages.append_column(Column("future_response_column", String))
    future_citations = conversation_message_citations.to_metadata(future_metadata)
    future_citations.append_column(Column("future_citation_column", String))
    monkeypatch.setattr(migrations, "conversation_messages", future_messages, raising=False)
    monkeypatch.setattr(
        migrations,
        "conversation_message_citations",
        future_citations,
        raising=False,
    )
    engine = create_database_engine(database_url_for(tmp_path))

    run_schema_migrations(engine)

    with engine.connect() as connection:
        message_columns = {
            column[1]
            for column in connection.exec_driver_sql(
                "PRAGMA table_info(conversation_messages)"
            )
        }
        citation_columns = {
            column[1]
            for column in connection.exec_driver_sql(
                "PRAGMA table_info(conversation_message_citations)"
            )
        }

    assert "future_response_column" not in message_columns
    assert "future_citation_column" not in citation_columns


def test_model_profiles_allows_only_one_active_default(tmp_path):
    """Breaks if two non-deleted profiles can both be the default."""
    engine = initialize_database(database_url_for(tmp_path))
    deleted_default_profile = {
        "id": "profile-a",
        "display_name": "First",
        "base_url": "http://localhost:8000/v1",
        "model_name": "first-model",
        "enabled": True,
        "is_default": True,
        "revision": 1,
        "basic_chat": False,
        "structured_output": False,
        "tool_calling": False,
        "created_at": "2026-08-21T00:00:00+00:00",
        "updated_at": "2026-08-21T00:00:00+00:00",
        "deleted_at": "2026-08-21T01:00:00+00:00",
    }
    active_default_profile = {
        **deleted_default_profile,
        "id": "profile-b",
        "display_name": "Second",
        "deleted_at": None,
    }
    duplicate_active_default_profile = {
        **active_default_profile,
        "id": "profile-c",
        "display_name": "Third",
    }

    with engine.begin() as connection:
        connection.execute(insert(model_profiles).values(deleted_default_profile))
        connection.execute(insert(model_profiles).values(active_default_profile))
        with pytest.raises(IntegrityError):
            connection.execute(insert(model_profiles).values(duplicate_active_default_profile))


def test_annotation_migration_upgrades_legacy_notes_and_creates_tables(tmp_path):
    """Breaks if migration 3 loses legacy notes or omits annotation tables."""
    engine = create_database_engine(database_url_for(tmp_path))
    legacy = MetaData()
    legacy_notes = Table(
        "notes",
        legacy,
        Column("id", String(36), primary_key=True),
        Column("paper_id", String(36), nullable=False),
        Column("element_id", String(36)),
        Column("page_number", Integer),
        Column("body", String, nullable=False),
        Column("order_index", Integer, nullable=False),
    )
    legacy.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            insert(legacy_notes).values(
                id="legacy-note",
                paper_id="paper-a",
                body="旧手写笔记",
                order_index=0,
            )
        )

    run_schema_migrations(engine)

    with engine.connect() as connection:
        note_columns = {
            column[1]
            for column in connection.exec_driver_sql("PRAGMA table_info(notes)")
        }
        tables = {
            row[0]
            for row in connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        legacy_row = connection.exec_driver_sql(
            "SELECT note_type, ai_generated, body FROM notes WHERE id = 'legacy-note'"
        ).mappings().one()

    assert {
        "note_type",
        "model_profile_id",
        "model_snapshot_json",
        "ai_generated",
        "user_edited",
        "created_at",
        "updated_at",
    } <= note_columns
    assert {
        "text_anchors",
        "text_anchor_rects",
        "highlights",
        "note_anchors",
        "selection_assist_requests",
        "conversation_message_note_citations",
        "conversation_message_anchors",
    } <= tables
    assert legacy_row["note_type"] == "manual"
    assert legacy_row["ai_generated"] == 0
    assert legacy_row["body"] == "旧手写笔记"


def test_annotation_migration_uses_frozen_schema_not_live_metadata(
    tmp_path, monkeypatch
):
    """Breaks if migration 3 adopts future live-table columns."""
    engine = create_database_engine(database_url_for(tmp_path))
    legacy = MetaData()
    legacy_notes = Table(
        "notes",
        legacy,
        Column("id", String(36), primary_key=True),
        Column("paper_id", String(36), nullable=False),
        Column("body", String, nullable=False),
        Column("order_index", Integer, nullable=False),
    )
    legacy.create_all(engine)
    future_metadata = MetaData()
    future_notes = notes.to_metadata(future_metadata)
    future_notes.append_column(Column("future_note_column", String))
    monkeypatch.setattr(migrations, "notes", future_notes, raising=False)

    run_schema_migrations(engine)

    with engine.connect() as connection:
        columns = {
            column[1]
            for column in connection.exec_driver_sql("PRAGMA table_info(notes)")
        }
    assert "future_note_column" not in columns
