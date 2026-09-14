import pytest


def _grounded_payload(
    *, citations: list[str], background: str | None = None
) -> dict[str, object]:
    return {
        "status": "grounded",
        "paper_answer": "The paper reports the measured result.",
        "citation_element_ids": citations,
        "background_explanation": background,
    }


def test_parser_keeps_model_answer_when_citations_are_not_request_scoped():
    """Citations are optional links and must not replace the model prose."""
    from paper_agent.services.citation_guard import CitationGuard

    answer = CitationGuard().parse_model_answer(
        {
            "status": "grounded",
            "paper_answer": "The method improves accuracy.",
            "citation_element_ids": ["invented-element"],
            "background_explanation": None,
        }
    )

    assert answer.status == "grounded"
    assert answer.paper_answer == "The method improves accuracy."
    assert answer.citation_element_ids == ("invented-element",)
    assert answer.background_explanation is None


def test_parser_keeps_grounded_answer_and_citation_order():
    from paper_agent.services.citation_guard import CitationGuard

    answer = CitationGuard().parse_model_answer(
        _grounded_payload(citations=["e2", "e1"])
    )

    assert answer.status == "grounded"
    assert answer.paper_answer == "The paper reports the measured result."
    assert answer.citation_element_ids == ("e2", "e1")
    assert answer.background_explanation is None


@pytest.mark.parametrize("citations", [[], ["e1", "e1"], ["   "]])
def test_parser_keeps_answer_without_clean_citations(citations: list[str]):
    from paper_agent.services.citation_guard import CitationGuard

    answer = CitationGuard().parse_model_answer(
        _grounded_payload(citations=citations)
    )

    assert answer.status == "grounded"
    assert answer.paper_answer == "The paper reports the measured result."
    assert answer.citation_element_ids == tuple(citations)


def test_parser_keeps_background_explanation():
    from paper_agent.services.citation_guard import CitationGuard

    answer = CitationGuard().parse_model_answer(
        _grounded_payload(citations=["e1"], background="Outside the paper.")
    )

    assert answer.status == "grounded"
    assert answer.paper_answer == "The paper reports the measured result."
    assert answer.citation_element_ids == ("e1",)
    assert answer.background_explanation == "Outside the paper."


def test_parser_keeps_model_content_for_insufficient_evidence_status():
    from paper_agent.services.citation_guard import CitationGuard

    answer = CitationGuard().parse_model_answer(
        {
            "status": "insufficient_evidence",
            "paper_answer": "I need more context before answering.",
            "citation_element_ids": ["e1"],
            "background_explanation": "Try selecting the relevant paragraph.",
        }
    )

    assert answer.status == "insufficient_evidence"
    assert answer.paper_answer == "I need more context before answering."
    assert answer.citation_element_ids == ("e1",)
    assert answer.background_explanation == "Try selecting the relevant paragraph."


def test_parser_does_not_reject_blank_model_content():
    from paper_agent.services.citation_guard import CitationGuard

    answer = CitationGuard().parse_model_answer(
        {
            "status": "insufficient_evidence",
            "paper_answer": "",
            "citation_element_ids": [],
            "background_explanation": "   ",
        }
    )

    assert answer.status == "insufficient_evidence"
    assert answer.paper_answer == ""
    assert answer.citation_element_ids == ()
    assert answer.background_explanation == "   "


def test_invalid_contract_raises_a_sanitized_error_without_model_content():
    """Only malformed transport structure remains an error boundary."""
    from paper_agent.services.citation_guard import CitationGuard, CitationGuardError

    payload = _grounded_payload(citations=["e1"])
    payload["unexpected"] = "secret model content"

    with pytest.raises(CitationGuardError) as error:
        CitationGuard().parse_model_answer(payload)

    assert str(error.value) == "invalid final answer contract"
    assert "secret model content" not in str(error.value)
    assert error.value.__cause__ is None


def test_legacy_validate_alias_no_longer_applies_an_evidence_allow_list():
    from paper_agent.services.citation_guard import CitationGuard

    answer = CitationGuard().validate(
        _grounded_payload(citations=["outside-request"]),
        allowed_evidence_ids=frozenset(),
    )

    assert answer.paper_answer == "The paper reports the measured result."
    assert answer.citation_element_ids == ("outside-request",)
