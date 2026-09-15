import pytest

ANSWER = "The method improves accuracy. [[e1]]"


def _parse(markdown: str):
    from paper_agent.services.citation_guard import CitationGuard

    return CitationGuard().parse_markdown_answer(markdown)


def test_parser_keeps_model_answer_with_its_inline_citation_markers():
    """Citations are optional links and must not replace the model prose."""
    answer = _parse("The method improves accuracy. [[invented-element]]")

    assert answer.status == "grounded"
    assert answer.paper_answer == "The method improves accuracy. [[invented-element]]"
    assert answer.citation_element_ids == ("invented-element",)
    assert answer.background_explanation is None


def test_parser_keeps_citation_order_and_drops_duplicates():
    answer = _parse("First [[e2]] then [[e1]] and [[e2]] again.")

    assert answer.citation_element_ids == ("e2", "e1")


def test_parser_keeps_markdown_formatting():
    answer = _parse("## 方法\n\n- 使用路由损失 [[e1]]\n- 结果更好")

    assert answer.paper_answer == "## 方法\n\n- 使用路由损失 [[e1]]\n- 结果更好"
    assert answer.citation_element_ids == ("e1",)


def test_parser_keeps_answer_without_any_citation_marker():
    answer = _parse(ANSWER.replace(" [[e1]]", ""))

    assert answer.status == "grounded"
    assert answer.citation_element_ids == ()


def test_parser_splits_background_explanation_after_the_separator():
    answer = _parse(f"{ANSWER}\n\n---\n\nMoE 路由是常见做法。")

    assert answer.paper_answer == ANSWER
    assert answer.citation_element_ids == ("e1",)
    assert answer.background_explanation == "MoE 路由是常见做法。"


def test_parser_keeps_a_decorative_rule_that_separates_nothing():
    """A horizontal rule mid-answer must not be read as a background separator."""
    answer = _parse(f"{ANSWER}\n\n---\n")

    assert answer.background_explanation is None
    assert answer.paper_answer == f"{ANSWER}\n\n---"


def test_parser_reads_the_insufficient_evidence_directive_from_the_first_line():
    answer = _parse("[[status:insufficient_evidence]]\n\n论文没有给出该结论。")

    assert answer.status == "insufficient_evidence"
    assert answer.paper_answer == "论文没有给出该结论。"
    assert answer.citation_element_ids == ()


def test_parser_ignores_the_directive_outside_the_first_line():
    answer = _parse(f"{ANSWER}\n\n[[status:insufficient_evidence]]")

    assert answer.status == "grounded"
    assert answer.citation_element_ids == ("e1", "status:insufficient_evidence")


def test_parser_rejects_an_empty_answer():
    from paper_agent.services.citation_guard import CitationGuardError

    with pytest.raises(CitationGuardError) as error:
        _parse("   \n\n  ")

    assert str(error.value) == "invalid final answer contract"


def test_parser_rejects_a_non_text_transport_value():
    from paper_agent.services.citation_guard import CitationGuardError

    with pytest.raises(CitationGuardError):
        _parse(None)  # type: ignore[arg-type]


def test_parser_requires_content_on_both_sides_of_the_background_separator():
    """A separator with no paper answer keeps the text as the answer itself."""
    answer = _parse("---\n\nOnly background text.")

    assert answer.status == "grounded"
    assert answer.paper_answer == "---\n\nOnly background text."
    assert answer.background_explanation is None
