"""Structure a local PDF into sections and paragraphs.

Section detection prefers the PDF's embedded outline (bookmarks) once it has
at least three usable entries. Without an outline, a line becomes a section
when its font is meaningfully larger than the body text, most of its
characters are rendered bold, the Latin portion is set in all caps, or the
whole line is a well-known heading label such as "Abstract", "2. Method",
"摘要" or "一、引言". Rotated margin stamps are ignored, two-column pages are
read column by column, and page-1 lines before the first body line
(title/author banner) are never treated as headings.
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
    x0: float
    y0: float
    x1: float


@dataclass(frozen=True)
class _Block:
    lines: tuple[_Line, ...]
    page_number: int
    x0: float
    y0: float
    x1: float
    column: int = 0
    gutter: float | None = None


@dataclass(frozen=True)
class _OutlineEntry:
    level: int
    title: str
    page_number: int
    x: float | None
    y: float | None


_MAX_HEADING_CHARS = 120
_MAX_HEADING_WORDS = 16
_MAX_HEADING_CJK_CHARS = 30
_BODY_SIZE_TOLERANCE = 0.5
_DEFAULT_BODY_SIZE = 10.0
_BOLD_CHAR_RATIO = 0.6
_MIN_OUTLINE_ENTRIES = 3
_MAX_LEVEL = 6


def _heading_size_margin(body_size: float) -> float:
    return max(1.0, body_size * 0.12)

_EXACT_HEADING_LABEL = re.compile(
    r"(?:\d{1,2}(?:\.\d{1,2})*[.)]?[ \t]*|[IVXLC]+\.[ \t]*"
    r"|第[一二三四五六七八九十\d]+[章节部分][ \t]*"
    r"|[一二三四五六七八九十]+[、.．][ \t]*|[（(][一二三四五六七八九十]+[）)][ \t]*)?"
    r"(?:abstract|introduction|background|related work|preliminaries|overview|"
    r"approach|methods?|methodology|materials and methods|"
    r"experiments?|experimental setup|evaluation|results?(?: and discussion)?|"
    r"discussion|analysis|ablations?|limitations?|conclusions?|future work|"
    r"summary|acknowledg(?:e?ments?)?|references|bibliography|"
    r"append(?:ix|ices)|supplementary(?: material)?"
    r"|摘要|引言|前言|绪论|综述|概述|背景|研究背景|研究现状|相关工作|研究内容|"
    r"方法|方法论|研究方法|模型|模型设计|系统设计|方案设计|技术路线|"
    r"实验|实验设置|实验设计|实验结果|结果|结果与分析|结果与讨论|评估|评测|"
    r"讨论|分析|消融(?:实验)?|局限性|结论|总结与展望|总结|未来工作|研究展望|"
    r"致谢|参考文献|引用文献|附录|补充材料|数据集)"
    r"[.。]?",
    re.IGNORECASE,
)
_SENTENCE_ENDING = re.compile(r"[.,;，；。！？）)]$")
_WRAPPED_WORD = re.compile(r"[A-Za-z]-$")
_LEADIN_COLON = re.compile(r"[:：]\s*\S")
_BULLET_PREFIX = re.compile(r"^[•·‣▪◦\-*]\s+")
_NUMBERED_HEADING = re.compile(r"^(\d{1,2}(?:\.\d{1,2})*[.)]?)[ \t]+\S")
_CJK_NUMBERED_HEADING = re.compile(
    r"^(?:第[一二三四五六七八九十\d]+[章节部分]"
    r"|[一二三四五六七八九十]+[、.．]"
    r"|[（(][一二三四五六七八九十]+[）)])\s*\S"
)
_JUNK_TITLE = re.compile(
    r"^(?:[a-f][).。]?$|[（(][a-f][）)]$"
    r"|arXiv:\d{4}\.\d{4,5}"
    r"|(?:图|表)\s*\d+|(?:figure|table|fig\.)\s*\d+"
    r"|(?:step|note|algorithm)\s*[:：\d])",
    re.IGNORECASE,
)


def _collapse_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _is_bold(span: dict) -> bool:
    return bool(span["flags"] & 16) or "bold" in span["font"].lower()


def _cjk_chars(text: str) -> int:
    return sum(1 for character in text if "\u4e00" <= character <= "\u9fff")


def _normalized_text(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


def _page_gutter(page_width: float, line_extents: list[tuple[float, float]]) -> float | None:
    """Return the gutter x position when the page is laid out in two columns."""
    if len(line_extents) < 8:
        return None
    step = max(4.0, page_width * 0.02)
    candidates = [
        page_width * 0.35 + index * step
        for index in range(int((page_width * 0.30) / step) + 1)
    ]
    best: tuple[int, float] | None = None
    for gutter in candidates:
        left = sum(1 for x0, x1 in line_extents if x1 <= gutter)
        right = sum(1 for x0, x1 in line_extents if x0 >= gutter)
        if left < 4 or right < 4:
            continue
        straddle = sum(
            1 for x0, x1 in line_extents if x0 < gutter - 2 and x1 > gutter + 2
        )
        if straddle > max(2, len(line_extents) // 20):
            continue
        if best is None or straddle < best[0]:
            best = (straddle, gutter)
    return None if best is None else best[1]


def _page_blocks(page, page_number: int) -> list[_Block]:
    raw_blocks: list[tuple[tuple[float, float, float, float], list[_Line]]] = []
    line_extents: list[tuple[float, float]] = []
    for block in page.get_text("dict", sort=True)["blocks"]:
        if block.get("type") != 0:
            continue
        lines: list[_Line] = []
        for line in block["lines"]:
            direction = line.get("dir") or (1.0, 0.0)
            if abs(direction[1]) > 0.1:
                # Rotated margin stamps (arXiv watermarks) are not content.
                continue
            spans = line["spans"]
            text = _collapse_spaces(
                "".join(span["text"] for span in spans)
            )
            if not text:
                continue
            visible = [span for span in spans if span["text"].strip()]
            visible_chars = sum(len(span["text"].strip()) for span in visible)
            bold_chars = sum(
                len(span["text"].strip()) for span in visible if _is_bold(span)
            )
            x0, y0, x1, _y1 = line["bbox"]
            lines.append(
                _Line(
                    text=text,
                    page_number=page_number,
                    size=max(span["size"] for span in visible),
                    bold=(
                        visible_chars > 0
                        and bold_chars / visible_chars >= _BOLD_CHAR_RATIO
                    ),
                    x0=x0,
                    y0=y0,
                    x1=x1,
                )
            )
            line_extents.append((x0, x1))
        if lines:
            raw_blocks.append((tuple(block["bbox"]), lines))
    gutter = _page_gutter(page.rect.width, line_extents)
    blocks: list[_Block] = []
    for bbox, lines in raw_blocks:
        x0, y0, x1, _y1 = bbox
        straddles = gutter is not None and x0 < gutter - 2 and x1 > gutter + 2
        column = 0
        if gutter is not None and not straddles and (x0 + x1) / 2 >= gutter:
            column = 1
        blocks.append(
            _Block(
                lines=tuple(lines),
                page_number=page_number,
                x0=x0,
                y0=y0,
                x1=x1,
                column=column,
                gutter=gutter,
            )
        )
    # Two-column pages read the whole left column before the right one;
    # blocks straddling the gutter (full-width headings) stay left.
    blocks.sort(key=lambda block: (block.column, block.y0, block.x0))
    return blocks


def _page_line_blocks(document) -> list[_Block]:
    """Return text blocks in reading order, column-aware within each page."""
    blocks: list[_Block] = []
    for page_number, page in enumerate(document, start=1):
        blocks.extend(_page_blocks(page, page_number))
    return blocks


def _body_size(blocks: list[_Block]) -> float:
    weights: Counter[float] = Counter()
    for block in blocks:
        for line in block.lines:
            weights[round(line.size * 2) / 2] += len(line.text)
    if not weights:
        return _DEFAULT_BODY_SIZE
    return weights.most_common(1)[0][0]


def _is_heading_line(line: _Line, body_size: float) -> bool:
    if len(line.text) > _MAX_HEADING_CHARS or len(line.text.split()) > _MAX_HEADING_WORDS:
        return False
    if _cjk_chars(line.text) > _MAX_HEADING_CJK_CHARS:
        return False
    if line.size < body_size - _BODY_SIZE_TOLERANCE:
        return False
    if _SENTENCE_ENDING.search(line.text) or _WRAPPED_WORD.search(line.text):
        return False
    if _BULLET_PREFIX.match(line.text):
        return False
    if line.size >= body_size + _heading_size_margin(body_size):
        return True
    if line.bold and _LEADIN_COLON.search(line.text) is None:
        return True
    if _cjk_chars(line.text) == 0:
        letters = [
            character
            for character in line.text
            if character.isascii() and character.isalpha()
        ]
        if (
            len(letters) >= 4
            and line.text.upper() == line.text
            and _LEADIN_COLON.search(line.text) is None
        ):
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


def _heading_level(text: str) -> int:
    match = _NUMBERED_HEADING.match(text)
    if match:
        number = match.group(1).rstrip(".).")
        return min(_MAX_LEVEL, number.count(".") + 1)
    if re.match(r"^第[一二三四五六七八九十\d]+[章节部分]", text):
        return 1
    if re.match(r"^[（(][一二三四五六七八九十]+[）)]", text):
        return 2
    if re.match(r"^[一二三四五六七八九十]+[、.．]", text):
        return 1
    return 1


def _is_explicit_heading_label(text: str) -> bool:
    return (
        _EXACT_HEADING_LABEL.fullmatch(text) is not None
        or _NUMBERED_HEADING.match(text) is not None
        or _CJK_NUMBERED_HEADING.match(text) is not None
    )


def _outline_entries(document, blocks: list[_Block]) -> list[_OutlineEntry]:
    """Return normalized outline entries, or [] when the outline is unusable."""
    try:
        toc = document.get_toc(simple=True)
    except Exception:
        return []
    usable: list[tuple[int, str, int]] = []
    for entry in toc:
        if len(entry) < 3:
            continue
        level, title, page = entry[0], entry[1], entry[2]
        title = _collapse_spaces(str(title))
        if not title or len(title) > _MAX_HEADING_CHARS:
            continue
        if not isinstance(level, int) or level < 1:
            continue
        if not isinstance(page, int) or page < 1 or page > document.page_count:
            continue
        if _JUNK_TITLE.match(title):
            continue
        usable.append((level, title, page))
    if len(usable) < _MIN_OUTLINE_ENTRIES:
        return []
    min_level = min(level for level, _, _ in usable)
    lines_by_page: dict[int, list[_Line]] = {}
    for block in blocks:
        lines_by_page.setdefault(block.page_number, []).extend(block.lines)
    entries: list[_OutlineEntry] = []
    for level, title, page in usable:
        located = _locate_outline_title(lines_by_page.get(page, []), title)
        entries.append(
            _OutlineEntry(
                level=min(_MAX_LEVEL, max(1, level - min_level + 1)),
                title=title,
                page_number=page,
                x=None if located is None else (located.x0 + located.x1) / 2,
                y=None if located is None else located.y0,
            )
        )
    return entries


def _locate_outline_title(lines: list[_Line], title: str) -> _Line | None:
    target = _normalized_text(title)
    if len(target) < 2:
        return None
    for line in lines:
        if _normalized_text(line.text) == target:
            return line
    for line in lines:
        normalized = _normalized_text(line.text)
        if len(normalized) >= 3 and target.startswith(normalized):
            return line
    return None


class PyMuPdfStage1Parser:
    """Convert a validated local PDF into sections and located paragraphs."""

    def parse(self, pdf_path: Path) -> Stage1Document:
        try:
            with pymupdf.open(pdf_path) as document:
                if document.needs_pass:
                    raise pymupdf.FileDataError("PDF requires authentication")
                blocks = _page_line_blocks(document)
                outline = _outline_entries(document, blocks)
        except (OSError, pymupdf.FileDataError, pymupdf.mupdf.FzErrorBase) as error:
            raise Stage1ParseError(
                f"PDF could not be structured: {pdf_path}"
            ) from error

        if outline:
            return self._outline_document(outline, blocks)
        return self._heuristic_document(blocks)

    def _outline_document(
        self, outline: list[_OutlineEntry], blocks: list[_Block]
    ) -> Stage1Document:
        sections = tuple(
            Section(
                title=entry.title,
                order=index,
                page_number=entry.page_number,
                level=entry.level,
            )
            for index, entry in enumerate(outline)
        )
        gutters = {
            block.page_number: block.gutter
            for block in blocks
        }
        starts = sorted(
            (
                (
                    (
                        entry.page_number,
                        1
                        if entry.y is None or (
                            gutters.get(entry.page_number) is not None
                            and entry.x is not None
                            and entry.x >= gutters[entry.page_number]
                        )
                        else 0,
                        float("inf") if entry.y is None else entry.y,
                    ),
                    section.id,
                )
                for entry, section in zip(outline, sections)
            )
        )
        paragraphs: list[Stage1Paragraph] = []
        active_section_id: str | None = None
        start_index = 0
        for block in blocks:
            position = (block.page_number, block.column, block.y0)
            while (
                start_index < len(starts)
                and starts[start_index][0] <= position
            ):
                active_section_id = starts[start_index][1]
                start_index += 1
            paragraphs.append(
                Stage1Paragraph(
                    text=_collapse_spaces(
                        " ".join(line.text for line in block.lines)
                    ),
                    section_id=active_section_id,
                    order=len(paragraphs),
                )
            )
        return Stage1Document(sections=sections, paragraphs=tuple(paragraphs))

    def _heuristic_document(self, blocks: list[_Block]) -> Stage1Document:
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
            for is_heading, lines in _line_groups(list(block.lines), body_size):
                first = lines[0]
                if not seen_body_line and first.page_number == 1:
                    if _is_explicit_heading_label(first.text) or not is_heading:
                        seen_body_line = True
                    else:
                        # Title/author banner: a heading-shaped page-1 group
                        # that is not an explicit section label.
                        flush()
                        buffer.extend(line.text for line in lines)
                        continue
                title = _collapse_spaces(" ".join(line.text for line in lines))
                if is_heading and not _JUNK_TITLE.match(title):
                    flush()
                    section = Section(
                        title=title,
                        order=len(sections),
                        page_number=lines[0].page_number,
                        level=_heading_level(title),
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
