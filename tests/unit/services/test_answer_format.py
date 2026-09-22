from paper_agent.services.answer_format import (
    ANSWER_FORMAT_INSTRUCTIONS,
    BACKGROUND_SEPARATOR,
    INSUFFICIENT_EVIDENCE_DIRECTIVE,
    split_answer_sections,
)


def test_instructions_license_tables_and_latex_math():
    """Breaks if the model is never told it may answer with tables or math."""
    assert "pipe tables" in ANSWER_FORMAT_INSTRUCTIONS
    assert "$...$" in ANSWER_FORMAT_INSTRUCTIONS
    assert "$$...$$" in ANSWER_FORMAT_INSTRUCTIONS


def test_instructions_keep_the_citation_and_separator_contract():
    """Breaks if the answer contract drifts from what the parsers expect."""
    assert "[[element_id]]" in ANSWER_FORMAT_INSTRUCTIONS
    assert BACKGROUND_SEPARATOR in ANSWER_FORMAT_INSTRUCTIONS
    assert INSUFFICIENT_EVIDENCE_DIRECTIVE in ANSWER_FORMAT_INSTRUCTIONS


def test_table_pipe_rows_do_not_split_the_answer():
    """Breaks if a Markdown table row is mistaken for the background rule."""
    markdown = (
        "| Expert | Tokens |\n"
        "| --- | --- |\n"
        "| FFN-A | 12.4 |\n"
        "\n"
        "---\n"
        "\n"
        "Extra context beyond the paper."
    )

    answer, background = split_answer_sections(markdown)

    assert "| FFN-A | 12.4 |" in answer
    assert background == "Extra context beyond the paper."
