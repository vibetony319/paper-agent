from pathlib import Path

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    create_engine,
    event,
    text,
)
from sqlalchemy.engine import Engine


metadata = MetaData()

papers = Table(
    "papers",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("original_filename", String, nullable=False),
    Column("stored_filename", String, nullable=False),
    Column("status", String(16), nullable=False),
    Column("source_published", Boolean, nullable=False, server_default="0"),
)

processing_runs = Table(
    "processing_runs",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("paper_id", String(36), ForeignKey("papers.id"), nullable=False),
    Column("sequence", Integer, nullable=False),
    Column("stage", String(16)),
    Column("status", String(16), nullable=False),
    Column("error_summary", String),
    Column("model_profile_id", String(36)),
    Column("model_snapshot_json", String),
    Column("request_id", String(36)),
    UniqueConstraint("paper_id", "sequence"),
)

pages = Table(
    "pages",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("paper_id", String(36), ForeignKey("papers.id"), nullable=False),
    Column("number", Integer, nullable=False),
    Column("width", Float, nullable=False),
    Column("height", Float, nullable=False),
    UniqueConstraint("paper_id", "number"),
)

sections = Table(
    "sections",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("paper_id", String(36), ForeignKey("papers.id"), nullable=False),
    Column("title", String, nullable=False),
    Column("page_number", Integer),
    Column("order_index", Integer, nullable=False),
    UniqueConstraint("paper_id", "id"),
    UniqueConstraint("paper_id", "order_index"),
)

document_elements = Table(
    "document_elements",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("paper_id", String(36), ForeignKey("papers.id"), nullable=False),
    Column("section_id", String(36)),
    Column("kind", String(64), nullable=False),
    Column("text", String, nullable=False),
    Column("page_number", Integer),
    Column("bbox_x0", Float),
    Column("bbox_y0", Float),
    Column("bbox_x1", Float),
    Column("bbox_y1", Float),
    Column("location_status", String(16), nullable=False),
    Column("order_index", Integer, nullable=False),
    CheckConstraint(
        "(location_status = 'unlocated' AND page_number IS NULL "
        "AND bbox_x0 IS NULL AND bbox_y0 IS NULL AND bbox_x1 IS NULL AND bbox_y1 IS NULL) "
        "OR (location_status = 'located' AND page_number IS NOT NULL "
        "AND bbox_x0 IS NOT NULL AND bbox_y0 IS NOT NULL AND bbox_x1 IS NOT NULL AND bbox_y1 IS NOT NULL)",
        name="document_element_location_consistency",
    ),
    CheckConstraint(
        "bbox_x0 IS NULL OR (0 <= bbox_x0 AND bbox_x0 <= bbox_x1 AND bbox_x1 <= 1 "
        "AND 0 <= bbox_y0 AND bbox_y0 <= bbox_y1 AND bbox_y1 <= 1)",
        name="document_element_bbox_normalized",
    ),
    ForeignKeyConstraint(
        ["paper_id", "section_id"],
        ["sections.paper_id", "sections.id"],
    ),
    UniqueConstraint("paper_id", "id"),
    UniqueConstraint("paper_id", "order_index"),
)

graph_nodes = Table(
    "graph_nodes",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("paper_id", String(36), ForeignKey("papers.id"), nullable=False),
    Column("node_type", String(64), nullable=False),
    Column("normalized_name", String, nullable=False),
    Column("name", String, nullable=False),
    Column("summary", String, nullable=False),
    Column("stage", String(16), nullable=False),
    UniqueConstraint("paper_id", "id"),
    UniqueConstraint("paper_id", "node_type", "normalized_name"),
)

graph_edges = Table(
    "graph_edges",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("paper_id", String(36), ForeignKey("papers.id"), nullable=False),
    Column("source_node_id", String(36), nullable=False),
    Column("target_node_id", String(36), nullable=False),
    Column("relation_type", String(64), nullable=False),
    Column("stage", String(16), nullable=False),
    ForeignKeyConstraint(
        ["paper_id", "source_node_id"],
        ["graph_nodes.paper_id", "graph_nodes.id"],
    ),
    ForeignKeyConstraint(
        ["paper_id", "target_node_id"],
        ["graph_nodes.paper_id", "graph_nodes.id"],
    ),
    UniqueConstraint("paper_id", "id"),
)

graph_node_evidence = Table(
    "graph_node_evidence",
    metadata,
    Column("paper_id", String(36), primary_key=True),
    Column("node_id", String(36), primary_key=True),
    Column("element_id", String(36), primary_key=True),
    ForeignKeyConstraint(
        ["paper_id", "node_id"],
        ["graph_nodes.paper_id", "graph_nodes.id"],
    ),
    ForeignKeyConstraint(
        ["paper_id", "element_id"],
        ["document_elements.paper_id", "document_elements.id"],
    ),
)

graph_edge_evidence = Table(
    "graph_edge_evidence",
    metadata,
    Column("paper_id", String(36), primary_key=True),
    Column("edge_id", String(36), primary_key=True),
    Column("element_id", String(36), primary_key=True),
    ForeignKeyConstraint(
        ["paper_id", "edge_id"],
        ["graph_edges.paper_id", "graph_edges.id"],
    ),
    ForeignKeyConstraint(
        ["paper_id", "element_id"],
        ["document_elements.paper_id", "document_elements.id"],
    ),
)

notes = Table(
    "notes",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("paper_id", String(36), ForeignKey("papers.id"), nullable=False),
    Column("element_id", String(36)),
    Column("page_number", Integer),
    Column("body", String, nullable=False),
    Column("order_index", Integer, nullable=False),
    ForeignKeyConstraint(
        ["paper_id", "element_id"],
        ["document_elements.paper_id", "document_elements.id"],
    ),
    UniqueConstraint("paper_id", "order_index"),
)

conversations = Table(
    "conversations",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("paper_id", String(36), ForeignKey("papers.id"), nullable=False),
    Column("mode", String(32), nullable=False),
    UniqueConstraint("paper_id", "id"),
)

conversation_messages = Table(
    "conversation_messages",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("conversation_id", String(36), nullable=False),
    Column("paper_id", String(36), nullable=False),
    Column("role", String(16), nullable=False),
    Column("content", String, nullable=False),
    Column("sequence", Integer, nullable=False),
    Column("model_profile_id", String(36)),
    Column("model_snapshot_json", String),
    Column("request_id", String(36)),
    Column("background_explanation", String),
    ForeignKeyConstraint(
        ["paper_id", "conversation_id"],
        ["conversations.paper_id", "conversations.id"],
    ),
    UniqueConstraint("paper_id", "id"),
    UniqueConstraint("conversation_id", "sequence"),
)

model_profiles = Table(
    "model_profiles",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("display_name", String, nullable=False),
    Column("base_url", String, nullable=False),
    Column("model_name", String, nullable=False),
    Column("secret_ref", String),
    Column("enabled", Boolean, nullable=False, server_default="1"),
    Column("is_default", Boolean, nullable=False, server_default="0"),
    Column("revision", Integer, nullable=False),
    Column("basic_chat", Boolean, nullable=False, server_default="0"),
    Column("structured_output", Boolean, nullable=False, server_default="0"),
    Column("tool_calling", Boolean, nullable=False, server_default="0"),
    Column("capabilities_checked_at", String),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    Column("deleted_at", String),
)

Index(
    "model_profiles_one_default",
    model_profiles.c.is_default,
    unique=True,
    sqlite_where=text("is_default = 1 AND deleted_at IS NULL"),
)

conversation_message_citations = Table(
    "conversation_message_citations",
    metadata,
    Column("paper_id", String(36), primary_key=True),
    Column("message_id", String(36), primary_key=True),
    Column("element_id", String(36), primary_key=True),
    Column("ordinal", Integer),
    Column("citation_snapshot_json", String),
    ForeignKeyConstraint(
        ["paper_id", "message_id"],
        ["conversation_messages.paper_id", "conversation_messages.id"],
    ),
    ForeignKeyConstraint(
        ["paper_id", "element_id"],
        ["document_elements.paper_id", "document_elements.id"],
    ),
)


def database_url_for(data_dir: Path) -> str:
    return f"sqlite:///{data_dir / 'paper-agent.db'}"


def create_database_engine(database_url: str) -> Engine:
    engine = create_engine(database_url)
    if database_url.startswith("sqlite"):
        event.listen(engine, "connect", _enable_sqlite_foreign_keys)
    return engine


def initialize_database(database_url: str) -> Engine:
    engine = create_database_engine(database_url)
    metadata.create_all(engine)
    if database_url.startswith("sqlite"):
        _migrate_legacy_processing_runs(engine)
        _migrate_legacy_papers(engine)
    from paper_agent.migrations import run_schema_migrations

    run_schema_migrations(engine)
    return engine


def _migrate_legacy_papers(engine: Engine) -> None:
    with engine.begin() as connection:
        columns = {
            row[1] for row in connection.exec_driver_sql("PRAGMA table_info(papers)")
        }
        if "source_published" not in columns:
            connection.exec_driver_sql(
                "ALTER TABLE papers ADD COLUMN source_published BOOLEAN NOT NULL DEFAULT 0"
            )
        source_directory = _legacy_source_directory(engine)
        if source_directory is None:
            return
        for paper_id, stored_filename, status in connection.exec_driver_sql(
            "SELECT id, stored_filename, status FROM papers WHERE source_published = 0"
        ):
            if not _legacy_source_exists(source_directory, stored_filename):
                continue
            processing_rows = connection.exec_driver_sql(
                "SELECT stage, status, error_summary FROM processing_runs WHERE paper_id = ?",
                (paper_id,),
            )
            stage0_started = False
            source_storage_failed = False
            for stage, run_status, error_summary in processing_rows:
                stage0_started = stage0_started or (
                    stage == "stage0" and run_status in {"running", "completed"}
                )
                source_storage_failed = source_storage_failed or (
                    error_summary == "The PDF source could not be stored."
                )
            if not source_storage_failed and (
                status in {"running", "completed", "partial"} or stage0_started
            ):
                connection.exec_driver_sql(
                    "UPDATE papers SET source_published = 1 WHERE id = ?",
                    (paper_id,),
                )


def _legacy_source_directory(engine: Engine) -> Path | None:
    database_path = engine.url.database
    if database_path in (None, ":memory:"):
        return None
    return Path(database_path).parent / "papers"


def _legacy_source_exists(source_directory: Path, stored_filename: str) -> bool:
    filename = Path(stored_filename)
    if filename.is_absolute() or filename.name != stored_filename:
        return False
    source_path = source_directory / filename
    return source_path.is_file() and not source_path.is_symlink()


def _migrate_legacy_processing_runs(engine: Engine) -> None:
    with engine.begin() as connection:
        columns = {
            row[1]
            for row in connection.exec_driver_sql("PRAGMA table_info(processing_runs)")
        }
        missing_columns = {"sequence", "stage", "error_summary"} - columns
        if not missing_columns:
            return

        if "sequence" in missing_columns:
            connection.exec_driver_sql(
                "ALTER TABLE processing_runs ADD COLUMN sequence INTEGER"
            )
        if "stage" in missing_columns:
            connection.exec_driver_sql(
                "ALTER TABLE processing_runs ADD COLUMN stage VARCHAR(16)"
            )
        if "error_summary" in missing_columns:
            connection.exec_driver_sql(
                "ALTER TABLE processing_runs ADD COLUMN error_summary VARCHAR"
            )

        if "sequence" in missing_columns:
            sequences: dict[str, int] = {}
            rows = connection.exec_driver_sql(
                "SELECT rowid, paper_id FROM processing_runs ORDER BY paper_id, rowid"
            )
            for rowid, paper_id in rows:
                sequence = sequences.get(paper_id, 0)
                connection.exec_driver_sql(
                    "UPDATE processing_runs SET sequence = ? WHERE rowid = ?",
                    (sequence, rowid),
                )
                sequences[paper_id] = sequence + 1

        connection.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS "
            "processing_runs_paper_id_sequence_unique "
            "ON processing_runs (paper_id, sequence)"
        )


def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()
