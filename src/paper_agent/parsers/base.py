from dataclasses import dataclass
from typing import Literal

from paper_agent.domain import BoundingBox, Page


class PdfParseError(Exception):
    """Raised when a PDF source cannot be read safely."""


@dataclass(frozen=True)
class TextBlock:
    text: str
    page_number: int
    bbox: BoundingBox
    order: int


VisualKind = Literal["image", "drawing", "unknown_visual"]


@dataclass(frozen=True)
class VisualElement:
    kind: VisualKind
    page_number: int
    bbox: BoundingBox
    caption: str | None = None


@dataclass(frozen=True)
class Stage0Result:
    pages: tuple[Page, ...]
    text_blocks: tuple[TextBlock, ...]
    visual_elements: tuple[VisualElement, ...]


