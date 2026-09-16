"""Heuristic token budgeting and message compaction for agent requests.

The estimator deliberately avoids a tokenizer dependency: CJK text is charged
at roughly one token per character and other text at roughly four characters
per token, matching the character-budget style used elsewhere in the repo.
"""

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Tool JSON definitions, message framing, and provider overhead charged on
# top of the visible message text of a tool-carrying agent request.
TOOL_DEFINITION_OVERHEAD = 1_000
# Input headroom reserved for the answer when the profile sets no output cap.
DEFAULT_OUTPUT_RESERVE = 8_192
# Compact proactively; only hard-truncate when compaction itself fails.
COMPACT_THRESHOLD = 0.75
HARD_TRUNCATION_THRESHOLD = 0.9

_CJK_RANGES = (
    (0x3000, 0x303F),  # CJK symbols and punctuation
    (0x3040, 0x30FF),  # Hiragana and Katakana
    (0x3400, 0x4DBF),  # CJK unified ideographs extension A
    (0x4E00, 0x9FFF),  # CJK unified ideographs
    (0xF900, 0xFAFF),  # CJK compatibility ideographs
)


def _is_cjk(char: str) -> bool:
    code = ord(char)
    return any(low <= code <= high for low, high in _CJK_RANGES)


def estimate_tokens(text: str) -> int:
    """Estimate tokens: CJK ~1 token per character, other text ~4 per token."""
    if not text:
        return 0
    cjk = sum(1 for char in text if _is_cjk(char))
    return cjk + (len(text) - cjk + 3) // 4


def estimate_messages_tokens(
    messages: Iterable[Mapping[str, object]],
) -> int:
    """Estimate the token weight of an OpenAI-style message list."""
    total = 0
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            total += estimate_tokens(content)
        calls = message.get("tool_calls")
        if isinstance(calls, list):
            for call in calls:
                if not isinstance(call, dict):
                    continue
                function = call.get("function")
                if not isinstance(function, dict):
                    continue
                total += estimate_tokens(str(function.get("name", "")))
                total += estimate_tokens(str(function.get("arguments", "")))
    return total


@dataclass(frozen=True)
class ContextBudget:
    """Token budget for one model profile; None context_length means unlimited."""

    context_length: int
    max_output_tokens: int | None = None

    @classmethod
    def from_profile(cls, profile: object) -> "ContextBudget | None":
        """Build a budget from a model profile, or None when unconfigured."""
        if getattr(profile, "context_length", None) is None:
            return None
        return cls(
            context_length=profile.context_length,
            max_output_tokens=profile.max_output_tokens,
        )

    @property
    def effective_limit(self) -> int:
        """Input budget after reserving room for the answer."""
        reserve = self.max_output_tokens or DEFAULT_OUTPUT_RESERVE
        return max(1, self.context_length - reserve)

    @property
    def compaction_threshold(self) -> int:
        return int(self.effective_limit * COMPACT_THRESHOLD)

    @property
    def hard_truncation_limit(self) -> int:
        return int(self.effective_limit * HARD_TRUNCATION_THRESHOLD)

    def request_tokens(
        self, messages: Iterable[Mapping[str, object]]
    ) -> int:
        return estimate_messages_tokens(messages) + TOOL_DEFINITION_OVERHEAD

    def needs_compaction(self, messages: Iterable[Mapping[str, object]]) -> bool:
        return self.request_tokens(messages) > self.compaction_threshold


@dataclass(frozen=True)
class ContextUsage:
    """Estimated occupancy of the next agent request for a conversation.

    A None context_length means the profile sets no limit, so only
    used_tokens is meaningful. Percent is measured against the effective
    limit (context minus the answer reserve), matching compaction.
    """

    used_tokens: int
    context_length: int | None = None
    effective_limit: int | None = None
    compaction_threshold: int | None = None

    @property
    def percent(self) -> float | None:
        if self.effective_limit is None or self.effective_limit <= 0:
            return None
        return round(self.used_tokens * 100 / self.effective_limit, 1)


def context_usage(
    messages: Iterable[Mapping[str, object]],
    budget: ContextBudget | None,
) -> ContextUsage:
    """Estimate the next request's occupancy with the compaction estimator."""
    used = estimate_messages_tokens(messages) + TOOL_DEFINITION_OVERHEAD
    if budget is None:
        return ContextUsage(used_tokens=used)
    return ContextUsage(
        used_tokens=used,
        context_length=budget.context_length,
        effective_limit=budget.effective_limit,
        compaction_threshold=budget.compaction_threshold,
    )


@dataclass
class CompactionParts:
    """Message slices that decide what compaction keeps and folds away."""

    head: list[dict[str, object]] = field(default_factory=list)
    middle: list[dict[str, object]] = field(default_factory=list)
    current_user: dict[str, object] | None = None
    tail: list[dict[str, object]] = field(default_factory=list)


def partition_messages(
    messages: list[dict[str, object]],
) -> CompactionParts:
    """Split a request into head system block, foldable middle, user turn, tail.

    The head is the leading system messages (prompt + note block). The tail
    keeps the last assistant(tool_calls)+tool block valid for the OpenAI API
    pairing rule plus any trailing system framing. Everything else is history
    that can be folded into a summary.
    """
    total = len(messages)
    head_end = 0
    while head_end < total and messages[head_end].get("role") == "system":
        head_end += 1

    user_index: int | None = None
    for index in range(total - 1, -1, -1):
        if messages[index].get("role") == "user":
            user_index = index
            break

    tail_start = total
    while tail_start > head_end and messages[tail_start - 1].get("role") == "system":
        tail_start -= 1
    probe = tail_start
    while probe > head_end and messages[probe - 1].get("role") == "tool":
        probe -= 1
    if (
        probe > head_end
        and messages[probe - 1].get("role") == "assistant"
        and messages[probe - 1].get("tool_calls")
    ):
        # probe - 1 is the assistant message itself; without it the tail
        # would carry orphan tool results that the OpenAI API rejects.
        tail_start = probe - 1

    middle: list[dict[str, object]] = list(messages[head_end:tail_start])
    current_user: dict[str, object] | None = None
    if user_index is not None:
        current_user = messages[user_index]
        middle = list(messages[head_end:user_index])
        if user_index + 1 < tail_start:
            middle.extend(messages[user_index + 1 : tail_start])
    return CompactionParts(
        head=list(messages[:head_end]),
        middle=middle,
        current_user=current_user,
        tail=list(messages[tail_start:]),
    )


def compacted_messages(
    parts: CompactionParts, summary: str
) -> list[dict[str, object]]:
    """Rebuild a compact request: head + summary + current user + tail."""
    rebuilt = [
        *parts.head,
        {
            "role": "system",
            "content": "以下是此前对话历史的压缩摘要，用于延续上下文：\n" + summary,
        },
    ]
    if parts.current_user is not None:
        rebuilt.append(parts.current_user)
    rebuilt.extend(parts.tail)
    return rebuilt


def rebuilt_messages(parts: CompactionParts) -> list[dict[str, object]]:
    """Rejoin the parts without a summary, for hard truncation."""
    rebuilt = [*parts.head, *parts.middle]
    if parts.current_user is not None:
        rebuilt.append(parts.current_user)
    rebuilt.extend(parts.tail)
    return rebuilt


def drop_oldest_exchange(middle: list[dict[str, object]]) -> bool:
    """Remove the oldest complete exchange in place; False when empty."""
    if not middle:
        return False
    end = 1
    if middle[0].get("role") == "assistant" and middle[0].get("tool_calls"):
        while end < len(middle) and middle[end].get("role") == "tool":
            end += 1
    else:
        while end < len(middle):
            role = middle[end].get("role")
            if role == "user":
                break
            if role == "assistant" and middle[end].get("tool_calls"):
                break
            end += 1
    del middle[:end]
    return True
