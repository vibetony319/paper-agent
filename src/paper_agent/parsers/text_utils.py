"""Text cleanup shared by every PDF parsing stage."""

import re

# A word broken across lines by a trailing hyphen ("ca-\npabilities") is one
# word when the next line starts with a lowercase letter.
_HYPHENATED_LINE_BREAK = re.compile(r"([A-Za-z])-\s*\n\s*([a-z])")


def dehyphenate(text: str) -> str:
    """Merge words that the PDF breaks with a trailing hyphen.

    Both parsing stages must apply this to the same line breaks: the
    aligner matches stage-1 paragraphs to stage-0 blocks by text equality,
    so dehyphenating only one side would drop page and bounding box from
    every hyphen-broken paragraph's citation.
    """
    return _HYPHENATED_LINE_BREAK.sub(r"\1\2", text)
