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
