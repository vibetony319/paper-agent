"""Parse the structured answer returned by the paper agent.

The reader deliberately does not gate or replace model answers.  Citation IDs
are treated as optional UI links and are cleaned up by the runtime only as
needed for persistence.  The small parser here remains so malformed provider
responses still produce a normal, sanitized service error instead of breaking
the conversation with a raw exception.
"""

from dataclasses import dataclass
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, ValidationError


class CitationGuardError(ValueError):
    """Raised when a provider response cannot be parsed as the answer shape."""


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
        """Return the JSON shape used to transport the model answer."""
        return cast(dict[str, object], _FinalAnswerPayload.model_json_schema())

    def parse_model_answer(
        self,
        payload: dict[str, object],
    ) -> CitationValidatedAnswer:
        """Return the model answer without evidence-based rejection.

        ``status``, prose, optional background text, and citation IDs are all
        kept as returned by the model.  In particular, a missing, duplicated,
        or out-of-request citation must not replace the model's answer with a
        fixed refusal message.
        """
        try:
            final_answer = _FinalAnswerPayload.model_validate(payload)
        except ValidationError:
            raise CitationGuardError("invalid final answer contract") from None

        return CitationValidatedAnswer(
            status=final_answer.status,
            paper_answer=final_answer.paper_answer.strip(),
            citation_element_ids=tuple(final_answer.citation_element_ids),
            background_explanation=final_answer.background_explanation,
        )

    def validate(
        self,
        payload: dict[str, object],
        *,
        allowed_evidence_ids: frozenset[str] | None = None,
    ) -> CitationValidatedAnswer:
        """Backward-compatible alias for callers that used the old name.

        ``allowed_evidence_ids`` is intentionally ignored.  It used to gate
        the answer and is retained only so older integrations do not fail at
        import or call time while they migrate to ``parse_model_answer``.
        """
        del allowed_evidence_ids
        return self.parse_model_answer(payload)
