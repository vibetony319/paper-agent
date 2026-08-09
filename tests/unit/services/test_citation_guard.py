import pytest

from paper_agent.domain import AgentMode


CANONICAL_INSUFFICIENT_EVIDENCE = (
    "I could not find enough evidence in this paper to answer that reliably."
)


def _grounded_payload(
    *, citations: list[str], background: str | None = None
) -> dict[str, object]:
    return {
        "status": "grounded",
        "paper_answer": "The paper reports the measured result.",
        "citation_element_ids": citations,
        "background_explanation": background,
    }


def test_guard_replaces_an_unsupported_paper_answer_with_a_safe_refusal():
    """Breaks if a fabricated or cross-request citation can carry model prose."""
    from paper_agent.services.citation_guard import CitationGuard

    answer = CitationGuard().validate(
        {
            "status": "grounded",
            "paper_answer": "The method improves accuracy.",
            "citation_element_ids": ["invented-element"],
            "background_explanation": None,
        },
        mode=AgentMode.paper_only,
        allowed_evidence_ids=frozenset({"e1"}),
    )

    assert answer.status == "insufficient_evidence"
    assert answer.paper_answer == CANONICAL_INSUFFICIENT_EVIDENCE
    assert answer.citation_element_ids == ()
    assert answer.background_explanation is None
    assert "improves accuracy" not in answer.paper_answer


def test_guard_returns_grounded_answer_for_unique_allowed_citations():
    """Breaks if evidence returned in this request is discarded or altered."""
    from paper_agent.services.citation_guard import CitationGuard

    answer = CitationGuard().validate(
        _grounded_payload(citations=["e2", "e1"]),
        mode=AgentMode.paper_only,
        allowed_evidence_ids=frozenset({"e1", "e2"}),
    )

    assert answer.status == "grounded"
    assert answer.paper_answer == "The paper reports the measured result."
    assert answer.citation_element_ids == ("e2", "e1")
    assert answer.background_explanation is None


@pytest.mark.parametrize("citations", [[], ["e1", "e1"]])
def test_guard_replaces_answers_without_a_nonempty_unique_citation_set(citations: list[str]):
    """Breaks if empty or duplicate citation lists can return a paper claim."""
    from paper_agent.services.citation_guard import CitationGuard

    answer = CitationGuard().validate(
        _grounded_payload(citations=citations),
        mode=AgentMode.paper_only,
        allowed_evidence_ids=frozenset({"e1"}),
    )

    assert answer.status == "insufficient_evidence"
    assert answer.paper_answer == CANONICAL_INSUFFICIENT_EVIDENCE
    assert answer.citation_element_ids == ()


def test_guard_rejects_blank_evidence_ids_even_if_a_bad_allow_list_contains_them():
    """Breaks if a malformed evidence ID can become a grounded citation."""
    from paper_agent.services.citation_guard import CitationGuard

    answer = CitationGuard().validate(
        _grounded_payload(citations=["   "]),
        mode=AgentMode.paper_only,
        allowed_evidence_ids=frozenset({"   "}),
    )

    assert answer.status == "insufficient_evidence"
    assert answer.paper_answer == CANONICAL_INSUFFICIENT_EVIDENCE
    assert answer.citation_element_ids == ()


def test_external_mode_keeps_background_separate_from_cited_paper_answer():
    """Breaks if external background is dropped or merged into the paper answer."""
    from paper_agent.services.citation_guard import CitationGuard

    answer = CitationGuard().validate(
        _grounded_payload(citations=["e1"], background="  General background.  "),
        mode=AgentMode.external_knowledge,
        allowed_evidence_ids=frozenset({"e1"}),
    )

    assert answer.paper_answer == "The paper reports the measured result."
    assert answer.background_explanation == "General background."


def test_paper_only_background_causes_a_safe_refusal():
    """Breaks if paper-only responses can expose uncited background prose."""
    from paper_agent.services.citation_guard import CitationGuard

    answer = CitationGuard().validate(
        _grounded_payload(citations=["e1"], background="Outside the paper."),
        mode=AgentMode.paper_only,
        allowed_evidence_ids=frozenset({"e1"}),
    )

    assert answer.status == "insufficient_evidence"
    assert answer.paper_answer == CANONICAL_INSUFFICIENT_EVIDENCE
    assert answer.citation_element_ids == ()
    assert answer.background_explanation is None


def test_insufficient_evidence_status_discards_all_model_prose():
    """Breaks if a model can attach unverified prose to its own refusal."""
    from paper_agent.services.citation_guard import CitationGuard

    answer = CitationGuard().validate(
        {
            "status": "insufficient_evidence",
            "paper_answer": "Unverified model claim.",
            "citation_element_ids": ["e1"],
            "background_explanation": "Unverified background.",
        },
        mode=AgentMode.external_knowledge,
        allowed_evidence_ids=frozenset({"e1"}),
    )

    assert answer.status == "insufficient_evidence"
    assert answer.paper_answer == CANONICAL_INSUFFICIENT_EVIDENCE
    assert answer.citation_element_ids == ()
    assert answer.background_explanation is None


def test_insufficient_evidence_status_ignores_blank_model_content():
    """Breaks if irrelevant refusal fields block the canonical safe response."""
    from paper_agent.services.citation_guard import CitationGuard

    answer = CitationGuard().validate(
        {
            "status": "insufficient_evidence",
            "paper_answer": "",
            "citation_element_ids": [],
            "background_explanation": "   ",
        },
        mode=AgentMode.paper_only,
        allowed_evidence_ids=frozenset(),
    )

    assert answer.status == "insufficient_evidence"
    assert answer.paper_answer == CANONICAL_INSUFFICIENT_EVIDENCE
    assert answer.citation_element_ids == ()
    assert answer.background_explanation is None


def test_invalid_contract_raises_a_sanitized_error_without_model_content():
    """Breaks if unknown or malformed model fields are silently accepted or leaked."""
    from paper_agent.services.citation_guard import CitationGuard, CitationGuardError

    payload = _grounded_payload(citations=["e1"])
    payload["unexpected"] = "secret model content"

    with pytest.raises(CitationGuardError) as error:
        CitationGuard().validate(
            payload,
            mode=AgentMode.paper_only,
            allowed_evidence_ids=frozenset({"e1"}),
        )

    assert str(error.value) == "invalid final answer contract"
    assert "secret model content" not in str(error.value)
    assert error.value.__cause__ is None
