from math import isfinite
from pathlib import Path
from typing import Iterable
import re

import pymupdf

from paper_agent.domain import BoundingBox, Page
from paper_agent.parsers.base import (
    PdfParseError,
    Stage0Result,
    TableBlock,
    TextBlock,
    VisualElement,
    VisualKind,
    detect_tables,
)
from paper_agent.parsers.text_utils import dehyphenate


def _normalized_candidate_bbox(
    rect: Iterable[float],
    page_rect: pymupdf.Rect,
    rotation_matrix: pymupdf.Matrix,
) -> BoundingBox | None:
    try:
        coordinates = tuple(float(value) for value in rect)
    except (TypeError, ValueError):
        return None

    if len(coordinates) != 4 or not all(isfinite(value) for value in coordinates):
        return None

    x0, y0, x1, y1 = coordinates
    if x1 <= x0 or y1 <= y0:
        return None

    displayed_rect = pymupdf.Rect(coordinates) * rotation_matrix
    bbox = BoundingBox.from_page_rect(
        tuple(displayed_rect), page_rect.width, page_rect.height
    )
    if bbox.x1 <= bbox.x0 or bbox.y1 <= bbox.y0:
        return None
    return bbox


def _visual_element(
    kind: VisualKind,
    page_number: int,
    rect: Iterable[float],
    page_rect: pymupdf.Rect,
    rotation_matrix: pymupdf.Matrix,
) -> VisualElement | None:
    bbox = _normalized_candidate_bbox(rect, page_rect, rotation_matrix)
    if bbox is None:
        return None
    return VisualElement(kind=kind, page_number=page_number, bbox=bbox)


_TABLE_CAPTION_LABEL = re.compile(r"^(?:table|表)\s*\d+", re.IGNORECASE)
# A caption sits within this fraction of the page height of the table edge.
_CAPTION_DISTANCE = 0.08


def _markdown_table(rows: list) -> str:
    """Flatten a PyMuPDF table extraction into GitHub-flavored Markdown."""
    cleaned: list[list[str]] = []
    for row in rows:
        cells = [re.sub(r"\s+", " ", cell or "").strip() for cell in row]
        if any(cells):
            cleaned.append(cells)
    if not cleaned:
        return ""
    width = max(len(row) for row in cleaned)
    padded = [row + [""] * (width - len(row)) for row in cleaned]

    def _format(row: list[str]) -> str:
        escaped = [cell.replace("|", "\\|") for cell in row]
        return "| " + " | ".join(escaped) + " |"

    lines = [_format(padded[0]), "| " + " | ".join(["---"] * width) + " |"]
    lines.extend(_format(row) for row in padded[1:])
    return "\n".join(lines)


def _table_caption(
    table_bbox: BoundingBox, page_blocks: list[TextBlock]
) -> str | None:
    """Return the nearest "Table N" text block directly above or below."""
    best: tuple[float, str] | None = None
    for block in page_blocks:
        if not _TABLE_CAPTION_LABEL.match(block.text):
            continue
        if block.bbox.x1 < table_bbox.x0 or block.bbox.x0 > table_bbox.x1:
            continue
        if 0 < table_bbox.y0 - block.bbox.y1 <= _CAPTION_DISTANCE:
            distance = table_bbox.y0 - block.bbox.y1
        elif 0 < block.bbox.y0 - table_bbox.y1 <= _CAPTION_DISTANCE:
            distance = block.bbox.y0 - table_bbox.y1
        else:
            continue
        if best is None or distance < best[0]:
            best = (distance, block.text)
    return None if best is None else best[1]


def _page_tables(
    page,
    page_number: int,
    page_rect: pymupdf.Rect,
    rotation_matrix: pymupdf.Matrix,
    page_blocks: list[TextBlock],
) -> list[TableBlock]:
    """Extract detected tables as Markdown blocks, best effort.

    Unruled tables that PyMuPDF cannot detect simply produce no table
    blocks and keep the existing paragraph flow.
    """
    try:
        detected = detect_tables(page)
    except Exception:
        return []
    tables: list[TableBlock] = []
    for table in detected:
        markdown = _markdown_table(table.extract())
        if not markdown:
            continue
        bbox = _normalized_candidate_bbox(
            table.bbox, page_rect, rotation_matrix
        )
        if bbox is None:
            continue
        caption = _table_caption(bbox, page_blocks)
        text = f"{caption}\n\n{markdown}" if caption else markdown
        tables.append(
            TableBlock(
                text=text,
                page_number=page_number,
                bbox=bbox,
                order=len(tables),
            )
        )
    return tables


class PyMuPdfStage0Parser:
    """Extract source PDF geometry without assigning document semantics."""

    def render_page_png(self, pdf_path: Path, page_number: int) -> bytes:
        if page_number < 1:
            raise ValueError("page number must be positive")
        try:
            with pymupdf.open(pdf_path) as document:
                if document.needs_pass:
                    raise PdfParseError("PDF requires authentication")
                if page_number > document.page_count:
                    raise ValueError("page number does not exist")
                return document[page_number - 1].get_pixmap().tobytes("png")
        except (OSError, pymupdf.FileDataError, pymupdf.mupdf.FzErrorBase) as error:
            raise PdfParseError("PDF could not be opened or parsed") from error

    def parse(self, pdf_path: Path) -> Stage0Result:
        try:
            return self._parse(pdf_path)
        except (OSError, pymupdf.FileDataError, pymupdf.mupdf.FzErrorBase) as error:
            raise PdfParseError(
                f"PDF could not be opened or parsed: {pdf_path}"
            ) from error

    def _parse(self, pdf_path: Path) -> Stage0Result:
        pages: list[Page] = []
        text_blocks: list[TextBlock] = []
        visual_elements: list[VisualElement] = []
        tables: list[TableBlock] = []

        with pymupdf.open(pdf_path) as document:
            if document.needs_pass:
                raise PdfParseError("PDF requires authentication")
            for page_number, page in enumerate(document, start=1):
                page_rect = page.rect
                rotation_matrix = page.rotation_matrix
                pages.append(
                    Page(
                        number=page_number,
                        width=page_rect.width,
                        height=page_rect.height,
                    )
                )
                page_blocks: list[TextBlock] = []
                for order, block in enumerate(page.get_text("blocks", sort=True)):
                    text = dehyphenate(block[4]).strip()
                    if not text:
                        continue
                    bbox = _normalized_candidate_bbox(
                        block[:4], page_rect, rotation_matrix
                    )
                    if bbox is None:
                        continue
                    page_blocks.append(
                        TextBlock(
                            text=text,
                            page_number=page_number,
                            bbox=bbox,
                            order=order,
                        )
                    )
                text_blocks.extend(page_blocks)
                tables.extend(
                    _page_tables(
                        page, page_number, page_rect, rotation_matrix, page_blocks
                    )
                )

                for image in page.get_image_info():
                    visual = _visual_element(
                        "image",
                        page_number,
                        image.get("bbox", ()),
                        page_rect,
                        rotation_matrix,
                    )
                    if visual is not None:
                        visual_elements.append(visual)

                for drawing in page.get_drawings():
                    visual = _visual_element(
                        "drawing",
                        page_number,
                        drawing.get("rect", ()),
                        page_rect,
                        rotation_matrix,
                    )
                    if visual is not None:
                        visual_elements.append(visual)

        return Stage0Result(
            pages=tuple(pages),
            text_blocks=tuple(text_blocks),
            visual_elements=tuple(visual_elements),
            tables=tuple(tables),
        )
