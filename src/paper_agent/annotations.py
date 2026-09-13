"""Domain types for paper text anchors, highlights, and generated notes."""

from dataclasses import dataclass, field
from enum import StrEnum
from uuid import uuid4

HIGHLIGHT_COLORS = ("yellow", "green", "blue", "pink")


class NoteType(StrEnum):
    manual = "manual"
    explanation = "explanation"
    translation = "translation"


def _validate_rect(rect: "TextAnchorRect") -> None:
    if not isinstance(rect.order, int) or isinstance(rect.order, bool):
        raise ValueError("anchor rectangle order must be an integer")
    if rect.order < 0:
        raise ValueError("anchor rectangle order must be nonnegative")
    coordinates = (rect.x0, rect.y0, rect.x1, rect.y1)
    if any(
        not isinstance(coordinate, (int, float))
        or isinstance(coordinate, bool)
        for coordinate in coordinates
    ):
        raise ValueError("anchor rectangle coordinates must be numbers")
    if not (0 <= rect.x0 <= rect.x1 <= 1 and 0 <= rect.y0 <= rect.y1 <= 1):
        raise ValueError("anchor rectangle must be ordered and normalized")


@dataclass(frozen=True)
class TextAnchorRect:
    order: int
    x0: float
    y0: float
    x1: float
    y1: float

    def __post_init__(self) -> None:
        _validate_rect(self)


def _validate_anchor_shape(
    quote: str,
    page_number: int,
    rects: tuple[TextAnchorRect, ...],
) -> None:
    normalized_quote = " ".join(quote.split())
    if not normalized_quote or len(normalized_quote) > 12_000:
        raise ValueError("anchor quote must be nonempty and bounded")
    if not isinstance(page_number, int) or isinstance(page_number, bool):
        raise ValueError("anchor page must be an integer")
    if page_number < 1:
        raise ValueError("anchor page number must be positive")
    if not isinstance(rects, tuple) or not rects:
        raise ValueError("anchor page and rectangles are required")
    if any(not isinstance(rect, TextAnchorRect) for rect in rects):
        raise ValueError("anchor rectangles must be TextAnchorRect values")
    if tuple(rect.order for rect in rects) != tuple(range(len(rects))):
        raise ValueError("anchor rectangle order must be contiguous")


@dataclass(frozen=True)
class TextAnchorDraft:
    quote: str
    page_number: int
    rects: tuple[TextAnchorRect, ...]
    element_id: str | None = None

    def __post_init__(self) -> None:
        _validate_anchor_shape(self.quote, self.page_number, self.rects)
        if self.element_id is not None and (
            not isinstance(self.element_id, str)
            or not self.element_id
            or self.element_id != self.element_id.strip()
        ):
            raise ValueError("anchor element ID must be nonempty and trimmed")


@dataclass(frozen=True)
class TextAnchor:
    paper_id: str
    quote: str
    page_number: int
    rects: tuple[TextAnchorRect, ...]
    element_id: str | None = None
    id: str = field(default_factory=lambda: str(uuid4()))

    def __post_init__(self) -> None:
        _validate_anchor_shape(self.quote, self.page_number, self.rects)
        if not isinstance(self.paper_id, str) or not self.paper_id:
            raise ValueError("anchor paper ID is required")
        if self.element_id is not None and (
            not isinstance(self.element_id, str)
            or not self.element_id
            or self.element_id != self.element_id.strip()
        ):
            raise ValueError("anchor element ID must be nonempty and trimmed")


@dataclass(frozen=True)
class Highlight:
    anchor: TextAnchor
    color: str = "yellow"
    id: str = field(default_factory=lambda: str(uuid4()))

    def __post_init__(self) -> None:
        if not isinstance(self.anchor, TextAnchor):
            raise ValueError("highlight requires a text anchor")
        if self.color not in HIGHLIGHT_COLORS:
            raise ValueError(
                f"highlight color must be one of {', '.join(HIGHLIGHT_COLORS)}"
            )
