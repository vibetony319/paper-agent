"""Parse the Markdown answer returned by the paper agent.

The reader deliberately does not gate or replace model answers.  Citation IDs
are treated as optional UI links and are cleaned up by the runtime only as
needed for persistence.  The small parser here remains so malformed provider
responses still produce a normal, sanitized service error instead of breaking
the conversation with a raw exception.
"""

from dataclasses import dataclass
import re
from typing import Literal

from paper_agent.services.answer_format import (
    INSUFFICIENT_EVIDENCE_DIRECTIVE,
    split_answer_sections,
)


class CitationGuardError(ValueError):
    """Raised when a provider response cannot be parsed as the answer shape."""


@dataclass(frozen=True)
class CitationValidatedAnswer:
    status: Literal["grounded", "insufficient_evidence"]
    paper_answer: str
    citation_element_ids: tuple[str, ...]
    background_explanation: str | None


class CitationGuard:
    def parse_markdown_answer(self, text: str) -> CitationValidatedAnswer:
        """Return the Markdown answer with its inline paper citations.

        ``status``, prose, optional background text, and citation IDs are all
        kept as returned by the model.  In particular, a missing, duplicated,
        or out-of-request citation must not replace the model's answer with a
        fixed refusal message.
        """
        if not isinstance(text, str):
            raise CitationGuardError("invalid final answer contract")

        status: Literal["grounded", "insufficient_evidence"] = "grounded"
        lines = text.strip().splitlines()
        if lines and lines[0].strip() == INSUFFICIENT_EVIDENCE_DIRECTIVE:
            status = "insufficient_evidence"
            lines = lines[1:]

        paper_answer, background_explanation = split_answer_sections(
            "\n".join(lines).strip()
        )
        if not paper_answer:
            raise CitationGuardError("invalid final answer contract")

        return CitationValidatedAnswer(
            status=status,
            paper_answer=paper_answer,
            citation_element_ids=extract_citation_ids(paper_answer),
            background_explanation=background_explanation,
        )


_CITATION_PATTERN = re.compile(r"\[\[([^\[\]]+)\]\]")


def extract_citation_ids(markdown: str) -> tuple[str, ...]:
    """Return unique ``[[element_id]]`` markers in the order they appear."""
    return tuple(
        dict.fromkeys(
            match.group(1).strip()
            for match in _CITATION_PATTERN.finditer(markdown)
            if match.group(1).strip()
        )
    )
