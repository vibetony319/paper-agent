from pathlib import Path

from sqlalchemy import (
    CheckConstraint,
    Column,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    create_engine,
    event,
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
)

processing_runs = Table(
    "processing_runs",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("paper_id", String(36), ForeignKey("papers.id"), nullable=False),
    Column("status", String(16), nullable=False),
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
    return engine


def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()
