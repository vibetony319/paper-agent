"""The Markdown answer contract shared by the prompt and the response parser.

One module owns the format so the instructions sent to the model and the
parsing applied to its reply cannot drift apart.
"""

INSUFFICIENT_EVIDENCE_DIRECTIVE = "[[status:insufficient_evidence]]"
BACKGROUND_SEPARATOR = "---"

ANSWER_FORMAT_INSTRUCTIONS = (
    "Answer in Markdown (headings, lists, bold text and inline code are fine). "
    "Cite paper locations inline as [[element_id]], using only element IDs that "
    "appeared in the tool results. "
    "If you want to add helpful context that does not come from the paper, put "
    f"it after a line containing only {BACKGROUND_SEPARATOR}. "
    "Use exactly that separator line once, only for that purpose. "
    "Put [[status:insufficient_evidence]] alone on the first line, and only when "
    "the paper evidence genuinely cannot answer the question; otherwise omit it. "
    "Do not return JSON and do not wrap the answer in code fences."
)


def split_answer_sections(markdown: str) -> tuple[str, str | None]:
    """Split a Markdown answer into its paper answer and optional context.

    The first standalone ``---`` line that has content on both sides separates
    the paper-grounded answer from the extra background explanation.  A
    decorative horizontal rule that is not separating two sections is kept as
    part of the single answer instead of being treated as a separator.
    """
    lines = markdown.splitlines()
    for index, line in enumerate(lines):
        if line.strip() != BACKGROUND_SEPARATOR:
            continue
        head = "\n".join(lines[:index]).strip()
        tail = "\n".join(lines[index + 1:]).strip()
        if head and tail:
            return head, tail
    return markdown.strip(), None
