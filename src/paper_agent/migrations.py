from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from paper_agent.database import model_profiles


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
    model_profiles.create(connection, checkfirst=True)
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


MIGRATIONS = (
    Migration(
        version=1,
        name="add_model_profiles_and_provenance_columns",
        apply=_apply_model_profiles_and_provenance_columns,
    ),
)
