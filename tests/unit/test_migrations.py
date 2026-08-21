import pytest
from sqlalchemy import Column, Integer, MetaData, String, Table, insert
from sqlalchemy.exc import IntegrityError

from paper_agent.migrations import run_schema_migrations
from paper_agent.database import (
    create_database_engine,
    database_url_for,
    initialize_database,
    model_profiles,
)


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
    assert versions == [1]


def test_model_profile_migration_records_each_version_once_when_rerun(tmp_path):
    """Breaks if a rerun inserts a duplicate migration version record."""
    engine = create_database_engine(database_url_for(tmp_path))

    run_schema_migrations(engine)
    run_schema_migrations(engine)

    with engine.connect() as connection:
        versions = connection.exec_driver_sql(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).scalars().all()

    assert versions == [1]


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
        run_columns = {
            column[1]
            for column in connection.exec_driver_sql("PRAGMA table_info(processing_runs)")
        }

    assert "model_profiles" in tables
    assert {"model_profile_id", "model_snapshot_json", "request_id"} <= message_columns
    assert {"model_profile_id", "model_snapshot_json", "request_id"} <= run_columns


def test_model_profiles_allows_only_one_active_default(tmp_path):
    """Breaks if two non-deleted profiles can both be the default."""
    engine = initialize_database(database_url_for(tmp_path))
    first_profile = {
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
    }
    second_profile = {**first_profile, "id": "profile-b", "display_name": "Second"}

    with engine.begin() as connection:
        connection.execute(insert(model_profiles).values(first_profile))
        with pytest.raises(IntegrityError):
            connection.execute(insert(model_profiles).values(second_profile))
