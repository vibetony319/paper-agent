"""Structure a local PDF into sections and paragraphs via font-style heuristics.

Heading detection is intentionally lightweight: a line becomes a section when
its font is larger than the body text, rendered bold, set in all caps, or the
whole line is a well-known heading label such as "Abstract" or "2. Method".
Page-1 lines before the first body-size line (title/author banner) are never
treated as headings.
"""

from collections import Counter
from dataclasses import dataclass
import re
from pathlib import Path

import pymupdf

from paper_agent.domain import Section


class Stage1ParseError(Exception):
    """Raised when the PDF cannot be read for semantic structuring."""


@dataclass(frozen=True)
class Stage1Paragraph:
    text: str
    section_id: str | None = None
    order: int | None = None


@dataclass(frozen=True)
class Stage1Document:
    sections: tuple[Section, ...]
    paragraphs: tuple[Stage1Paragraph, ...]


@dataclass(frozen=True)
class _Line:
    text: str
    page_number: int
    size: float
    bold: bool


_MAX_HEADING_CHARS = 120
_MAX_HEADING_WORDS = 16
_BODY_SIZE_TOLERANCE = 0.5
_DEFAULT_BODY_SIZE = 10.0


def _heading_size_margin(body_size: float) -> float:
    return max(1.0, body_size * 0.12)

_EXACT_HEADING_LABEL = re.compile(
    r"(?:\d+(?:\.\d+)*[.)]?[ \t]*|[IVXLC]+\.[ \t]*)?"
    r"(?:abstract|introduction|background|related work|preliminaries|overview|"
    r"approach|methods?|methodology|materials and methods|"
    r"experiments?|experimental setup|evaluation|results?(?: and discussion)?|"
    r"discussion|analysis|ablations?|limitations?|conclusions?|future work|"
    r"summary|acknowledg(?:e?ments?)?|references|bibliography|"
    r"append(?:ix|ices)|supplementary(?: material)?)"
    r"\.?",
    re.IGNORECASE,
)
_SENTENCE_ENDING = re.compile(r"[.,;，；。]$")
_NUMBERED_HEADING = re.compile(r"^\d{1,2}(?:\.\d{1,2})*[.)]?[ \t]+\S")


def _collapse_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _is_bold(span: dict) -> bool:
    return bool(span["flags"] & 16) or "bold" in span["font"].lower()


def _page_line_blocks(pdf_path: Path) -> list[list[_Line]]:
    """Return text lines grouped into pymupdf blocks, in reading order.

    Grouping mirrors stage0's ``get_text("blocks", sort=True)`` so stage1
    paragraphs align one-to-one with stage0 text blocks.
    """
    blocks: list[list[_Line]] = []
    with pymupdf.open(pdf_path) as document:
        if document.needs_pass:
            raise pymupdf.FileDataError("PDF requires authentication")
        for page_number, page in enumerate(document, start=1):
            for block in page.get_text("dict", sort=True)["blocks"]:
                if block.get("type") != 0:
                    continue
                lines: list[_Line] = []
                for line in block["lines"]:
                    spans = line["spans"]
                    text = _collapse_spaces(
                        "".join(span["text"] for span in spans)
                    )
                    if not text:
                        continue
                    visible = [span for span in spans if span["text"].strip()]
                    lines.append(
                        _Line(
                            text=text,
                            page_number=page_number,
                            size=max(span["size"] for span in visible),
                            bold=any(_is_bold(span) for span in visible),
                        )
                    )
                if lines:
                    blocks.append(lines)
    return blocks


def _body_size(blocks: list[list[_Line]]) -> float:
    weights: Counter[float] = Counter()
    for block in blocks:
        for line in block:
            weights[round(line.size * 2) / 2] += len(line.text)
    if not weights:
        return _DEFAULT_BODY_SIZE
    return weights.most_common(1)[0][0]


def _is_heading_line(line: _Line, body_size: float) -> bool:
    if len(line.text) > _MAX_HEADING_CHARS or len(line.text.split()) > _MAX_HEADING_WORDS:
        return False
    if line.size < body_size - _BODY_SIZE_TOLERANCE:
        return False
    if _SENTENCE_ENDING.search(line.text):
        return False
    if line.size >= body_size + _heading_size_margin(body_size):
        return True
    if line.bold:
        return True
    letters = [character for character in line.text if character.isalpha()]
    if len(letters) >= 4 and line.text == line.text.upper():
        return True
    return _EXACT_HEADING_LABEL.fullmatch(line.text) is not None


def _line_groups(
    lines: list[_Line], body_size: float
) -> list[tuple[bool, list[_Line]]]:
    """Merge adjacent same-size heading lines (two-line titles) into one group."""
    groups: list[tuple[bool, list[_Line]]] = []
    for line in lines:
        heading = _is_heading_line(line, body_size)
        previous = groups[-1] if groups else None
        if (
            previous is not None
            and previous[0] == heading
            and (
                not heading
                or (
                    previous[1][-1].page_number == line.page_number
                    and abs(previous[1][-1].size - line.size) <= _BODY_SIZE_TOLERANCE
                )
            )
        ):
            previous[1].append(line)
        else:
            groups.append((heading, [line]))
    return groups


class PyMuPdfStage1Parser:
    """Convert a validated local PDF into sections and located paragraphs."""

    def parse(self, pdf_path: Path) -> Stage1Document:
        try:
            blocks = _page_line_blocks(pdf_path)
        except (OSError, pymupdf.FileDataError, pymupdf.mupdf.FzErrorBase) as error:
            raise Stage1ParseError(
                f"PDF could not be structured: {pdf_path}"
            ) from error

        body_size = _body_size(blocks)
        sections: list[Section] = []
        paragraphs: list[Stage1Paragraph] = []
        buffer: list[str] = []
        current_section_id: str | None = None
        seen_body_line = False

        def flush() -> None:
            nonlocal buffer
            text = " ".join(buffer).strip()
            buffer = []
            if text:
                paragraphs.append(
                    Stage1Paragraph(
                        text=text,
                        section_id=current_section_id,
                        order=len(paragraphs),
                    )
                )

        for block in blocks:
            for is_heading, lines in _line_groups(block, body_size):
                first = lines[0]
                if not seen_body_line and first.page_number == 1:
                    in_title_zone = (
                        first.size > body_size + _BODY_SIZE_TOLERANCE
                        and not first.bold
                        and _EXACT_HEADING_LABEL.fullmatch(first.text) is None
                        and _NUMBERED_HEADING.match(first.text) is None
                    )
                    if in_title_zone:
                        flush()
                        buffer.extend(line.text for line in lines)
                        continue
                    seen_body_line = True
                if is_heading:
                    flush()
                    section = Section(
                        title=_collapse_spaces(
                            " ".join(line.text for line in lines)
                        ),
                        order=len(sections),
                        page_number=lines[0].page_number,
                    )
                    sections.append(section)
                    current_section_id = section.id
                else:
                    buffer.extend(line.text for line in lines)
            flush()

        flush()
        return Stage1Document(
            sections=tuple(sections), paragraphs=tuple(paragraphs)
        )
