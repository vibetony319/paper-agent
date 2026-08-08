import re
from dataclasses import dataclass
from pathlib import Path

from markitdown import MarkItDown, MarkItDownException

from paper_agent.domain import Section


class MarkdownParseError(Exception):
    """Raised when MarkItDown cannot convert an owned local PDF."""


@dataclass(frozen=True)
class MarkdownParagraph:
    text: str
    section_id: str | None = None
    order: int | None = None


@dataclass(frozen=True)
class MarkdownDocument:
    sections: tuple[Section, ...]
    paragraphs: tuple[MarkdownParagraph, ...]


_ATX_HEADING = re.compile(
    r"^ {0,3}#{1,6}(?:[ \t]+|$)(?P<title>.*?)[ \t]*$"
)
_CLOSING_HASHES = re.compile(r"[ \t]+#+[ \t]*$")


def _parse_markdown(markdown: str) -> MarkdownDocument:
    sections: list[Section] = []
    paragraphs: list[MarkdownParagraph] = []
    body_lines: list[str] = []
    current_section_id: str | None = None

    def flush_body() -> None:
        if not body_lines:
            return
        text = "\n".join(body_lines).strip()
        body_lines.clear()
        if text:
            paragraphs.append(
                MarkdownParagraph(
                    text=text,
                    section_id=current_section_id,
                    order=len(paragraphs),
                )
            )

    for line in markdown.splitlines():
        heading = _ATX_HEADING.match(line)
        if heading is not None:
            flush_body()
            title = _CLOSING_HASHES.sub("", heading.group("title")).strip()
            section = Section(title=title, order=len(sections))
            sections.append(section)
            current_section_id = section.id
        elif line.strip():
            body_lines.append(line)
        else:
            flush_body()

    flush_body()
    return MarkdownDocument(sections=tuple(sections), paragraphs=tuple(paragraphs))


class MarkItDownStage1Parser:
    """Convert a validated local PDF into Markdown semantic structure."""

    def parse(self, pdf_path: Path) -> MarkdownDocument:
        try:
            result = MarkItDown(enable_plugins=False).convert_local(pdf_path)
        except (OSError, MarkItDownException) as error:
            raise MarkdownParseError(
                f"PDF could not be converted to Markdown: {pdf_path}"
            ) from error
        return _parse_markdown(result.text_content)
