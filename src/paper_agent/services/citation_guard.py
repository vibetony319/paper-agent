"""Validate final paper-agent answers against request-scoped evidence."""

from dataclasses import dataclass
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, ValidationError


_INSUFFICIENT_EVIDENCE_ANSWER = (
    "I could not find enough evidence in this paper to answer that reliably."
)


class CitationGuardError(ValueError):
    """Raised when a model final-answer payload violates the output contract."""


@dataclass(frozen=True)
class CitationValidatedAnswer:
    status: Literal["grounded", "insufficient_evidence"]
    paper_answer: str
    citation_element_ids: tuple[str, ...]
    background_explanation: str | None


class _FinalAnswerPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    status: Literal["grounded", "insufficient_evidence"]
    paper_answer: str
    citation_element_ids: list[str]
    background_explanation: str | None


class CitationGuard:
    def output_schema(self) -> dict[str, object]:
        """Return the strict JSON schema requested from the reasoning model."""
        return cast(dict[str, object], _FinalAnswerPayload.model_json_schema())

    def validate(
        self,
        payload: dict[str, object],
        *,
        allowed_evidence_ids: frozenset[str],
    ) -> CitationValidatedAnswer:
        try:
            final_answer = _FinalAnswerPayload.model_validate(payload)
        except ValidationError:
            raise CitationGuardError("invalid final answer contract") from None

        if final_answer.status == "insufficient_evidence":
            return _insufficient_evidence_answer()

        paper_answer = final_answer.paper_answer.strip()
        if not paper_answer:
            raise CitationGuardError("invalid final answer contract")

        if final_answer.background_explanation is not None:
            return _insufficient_evidence_answer()

        citation_ids = tuple(final_answer.citation_element_ids)
        if (
            not citation_ids
            or len(set(citation_ids)) != len(citation_ids)
            or any(
                not citation_id or citation_id != citation_id.strip()
                for citation_id in citation_ids
            )
            or not set(citation_ids).issubset(allowed_evidence_ids)
        ):
            return _insufficient_evidence_answer()

        return CitationValidatedAnswer(
            status="grounded",
            paper_answer=paper_answer,
            citation_element_ids=citation_ids,
            background_explanation=None,
        )


def _insufficient_evidence_answer() -> CitationValidatedAnswer:
    return CitationValidatedAnswer(
        status="insufficient_evidence",
        paper_answer=_INSUFFICIENT_EVIDENCE_ANSWER,
        citation_element_ids=(),
        background_explanation=None,
    )
