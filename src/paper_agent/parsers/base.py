from dataclasses import dataclass
from typing import Literal

import pymupdf

from paper_agent.domain import BoundingBox, Page


class PdfParseError(Exception):
    """Raised when a PDF source cannot be read safely."""


def detect_tables(page) -> list:
    """Return the page's detected tables without find_tables' side effects.

    PyMuPDF's ``find_tables`` resets the page's CropBox, which corrupts
    every later geometry call (``page.rect``, ``get_drawings``,
    ``get_text`` coordinates) on cropped pages. The CropBox is restored so
    callers can run table detection anywhere in their extraction sequence.
    """
    try:
        cropbox = page.cropbox
    except Exception:
        cropbox = None
    try:
        return list(page.find_tables())
    except Exception:
        return []
    finally:
        if cropbox is not None:
            try:
                page.set_cropbox(cropbox)
            except Exception:
                pass


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
class TableBlock:
    """One detected table, flattened to GitHub-flavored Markdown."""

    text: str
    page_number: int
    bbox: BoundingBox
    order: int


@dataclass(frozen=True)
class Stage0Result:
    pages: tuple[Page, ...]
    text_blocks: tuple[TextBlock, ...]
    visual_elements: tuple[VisualElement, ...]
    tables: tuple[TableBlock, ...] = ()


