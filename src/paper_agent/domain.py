from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal
from uuid import uuid4


class ProcessingStatus(StrEnum):
    queued = "queued"
    running = "running"
    completed = "completed"
    partial = "partial"
    failed = "failed"


class GraphStage(StrEnum):
    core = "stage2"
    deep = "stage3"


CORE_NODE_TYPES = frozenset({"problem", "contribution", "claim", "method", "experiment"})
DEEP_NODE_TYPES = frozenset(
    {"component", "concept", "dataset", "metric", "result", "ablation", "limitation"}
)
RELATION_TYPES = frozenset(
    {
        "addresses",
        "part_of",
        "uses",
        "compares_with",
        "evaluated_on",
        "measured_by",
        "produces",
        "tests",
        "supports",
        "contradicts",
        "defines",
        "illustrates",
        "related_to",
    }
)


def _require_nonempty_trimmed(value: str, label: str) -> None:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{label} must be nonempty and trimmed")


def _require_evidence_ids(evidence_element_ids: tuple[str, ...]) -> None:
    if not evidence_element_ids:
        raise ValueError("evidence element IDs are required")
    if any(
        not isinstance(element_id, str)
        or not element_id
        or element_id != element_id.strip()
        for element_id in evidence_element_ids
    ):
        raise ValueError("evidence element IDs must be nonempty and trimmed")
    if len(set(evidence_element_ids)) != len(evidence_element_ids):
        raise ValueError("evidence element IDs must be unique")


@dataclass(frozen=True)
class GraphNode:
    node_type: str
    name: str
    summary: str
    stage: GraphStage
    evidence_element_ids: tuple[str, ...]
    id: str = field(default_factory=lambda: str(uuid4()))

    def __post_init__(self) -> None:
        if not isinstance(self.stage, GraphStage):
            raise ValueError("stage must be a GraphStage")
        allowed_types = CORE_NODE_TYPES if self.stage is GraphStage.core else DEEP_NODE_TYPES
        if self.node_type not in allowed_types:
            raise ValueError("node type is not allowed for graph stage")
        _require_nonempty_trimmed(self.name, "name")
        _require_nonempty_trimmed(self.summary, "summary")
        _require_evidence_ids(self.evidence_element_ids)


@dataclass(frozen=True)
class GraphEdge:
    source_node_id: str
    target_node_id: str
    relation_type: str
    stage: GraphStage
    evidence_element_ids: tuple[str, ...]
    id: str = field(default_factory=lambda: str(uuid4()))

    def __post_init__(self) -> None:
        if not isinstance(self.stage, GraphStage):
            raise ValueError("stage must be a GraphStage")
        _require_nonempty_trimmed(self.source_node_id, "source node ID")
        _require_nonempty_trimmed(self.target_node_id, "target node ID")
        if self.source_node_id == self.target_node_id:
            raise ValueError("edge endpoints must be different")
        if self.relation_type not in RELATION_TYPES:
            raise ValueError("relation type is not allowed")
        _require_evidence_ids(self.evidence_element_ids)


@dataclass(frozen=True)
class PaperGraph:
    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]


@dataclass(frozen=True)
class BoundingBox:
    x0: float
    y0: float
    x1: float
    y1: float

    def __post_init__(self) -> None:
        if not (0 <= self.x0 <= self.x1 <= 1 and 0 <= self.y0 <= self.y1 <= 1):
            raise ValueError("bounding box coordinates must be ordered and normalized to [0, 1]")

    @classmethod
    def from_page_rect(
        cls,
        rect: tuple[float, float, float, float],
        page_width: float,
        page_height: float,
    ) -> "BoundingBox":
        if page_width <= 0 or page_height <= 0:
            raise ValueError("page dimensions must be positive")
        x0, y0, x1, y1 = rect
        return cls(
            x0=max(0.0, min(1.0, x0 / page_width)),
            y0=max(0.0, min(1.0, y0 / page_height)),
            x1=max(0.0, min(1.0, x1 / page_width)),
            y1=max(0.0, min(1.0, y1 / page_height)),
        )


LocationStatus = Literal["located", "unlocated"]


@dataclass(frozen=True)
class Paper:
    id: str
    original_filename: str
    stored_filename: str
    status: ProcessingStatus = ProcessingStatus.queued
    source_published: bool = False


@dataclass(frozen=True)
class Page:
    number: int
    width: float
    height: float
    id: str = field(default_factory=lambda: str(uuid4()))

    def __post_init__(self) -> None:
        if self.number < 1:
            raise ValueError("page number must be positive")
        if self.width <= 0 or self.height <= 0:
            raise ValueError("page dimensions must be positive")


@dataclass(frozen=True)
class Section:
    title: str
    order: int
    page_number: int | None = None
    id: str = field(default_factory=lambda: str(uuid4()))


@dataclass(frozen=True)
class DocumentElement:
    kind: str
    text: str
    page_number: int | None = None
    bbox: BoundingBox | None = None
    section_id: str | None = None
    location_status: LocationStatus = "located"
    order: int | None = None
    id: str = field(default_factory=lambda: str(uuid4()))

    def __post_init__(self) -> None:
        if self.location_status not in ("located", "unlocated"):
            raise ValueError("location_status must be located or unlocated")
        if self.location_status == "unlocated":
            if self.page_number is not None or self.bbox is not None:
                raise ValueError("unlocated elements cannot have a page or geometry")
        elif self.page_number is None or self.bbox is None:
            raise ValueError("located elements require a page and geometry")
        if self.page_number is not None and self.page_number < 1:
            raise ValueError("page number must be positive")

    @classmethod
    def paragraph(
        cls,
        text: str,
        page_number: int | None = None,
        bbox: BoundingBox | None = None,
        section_id: str | None = None,
        location_status: LocationStatus = "located",
        order: int | None = None,
    ) -> "DocumentElement":
        return cls(
            kind="paragraph",
            text=text,
            page_number=page_number,
            bbox=bbox,
            section_id=section_id,
            location_status=location_status,
            order=order,
        )


@dataclass(frozen=True)
class Note:
    body: str
    element_id: str | None = None
    page_number: int | None = None
    id: str = field(default_factory=lambda: str(uuid4()))


@dataclass(frozen=True)
class PaperDocument:
    paper: Paper
    pages: tuple[Page, ...] = ()
    sections: tuple[Section, ...] = ()
    elements: tuple[DocumentElement, ...] = ()
    notes: tuple[Note, ...] = ()
