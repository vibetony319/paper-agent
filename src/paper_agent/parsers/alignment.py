import re
import unicodedata
from collections.abc import Sequence

from paper_agent.domain import DocumentElement
from paper_agent.parsers.base import TextBlock
from paper_agent.parsers.markitdown_stage1 import MarkdownParagraph


_MIN_CONTAINMENT_LENGTH = 40


def _normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text.replace("\u00ad", ""))
    normalized = "".join(
        " " if unicodedata.category(character).startswith("P") else character
        for character in normalized
    ).lower()
    return re.sub(r"\s+", " ", normalized).strip()


class TextAligner:
    def align(
        self,
        paragraphs: Sequence[MarkdownParagraph],
        text_blocks: Sequence[TextBlock],
    ) -> list[DocumentElement]:
        normalized_blocks = [(_normalize_text(block.text), block) for block in text_blocks]
        elements: list[DocumentElement] = []

        for paragraph in paragraphs:
            normalized_paragraph = _normalize_text(paragraph.text)
            matches = [
                block
                for normalized_text, block in normalized_blocks
                if normalized_text == normalized_paragraph
            ]
            if not matches and len(normalized_paragraph) >= _MIN_CONTAINMENT_LENGTH:
                matches = [
                    block
                    for normalized_text, block in normalized_blocks
                    if normalized_paragraph in normalized_text
                ]
            if len(matches) == 1:
                block = matches[0]
                elements.append(
                    DocumentElement.paragraph(
                        text=paragraph.text,
                        page_number=block.page_number,
                        bbox=block.bbox,
                        section_id=paragraph.section_id,
                        order=paragraph.order,
                    )
                )
            else:
                elements.append(
                    DocumentElement.paragraph(
                        text=paragraph.text,
                        section_id=paragraph.section_id,
                        location_status="unlocated",
                        order=paragraph.order,
                    )
                )

        return elements
