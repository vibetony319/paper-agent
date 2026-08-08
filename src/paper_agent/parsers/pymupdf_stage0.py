from math import isfinite
from pathlib import Path
from typing import Iterable

import pymupdf

from paper_agent.domain import BoundingBox, Page
from paper_agent.parsers.base import (
    PdfParseError,
    Stage0Result,
    TextBlock,
    VisualElement,
    VisualKind,
)


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
                for order, block in enumerate(page.get_text("blocks", sort=True)):
                    text = block[4].strip()
                    if not text:
                        continue
                    bbox = _normalized_candidate_bbox(
                        block[:4], page_rect, rotation_matrix
                    )
                    if bbox is None:
                        continue
                    text_blocks.append(
                        TextBlock(
                            text=text,
                            page_number=page_number,
                            bbox=bbox,
                            order=order,
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
        )
