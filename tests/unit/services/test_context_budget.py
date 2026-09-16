from types import SimpleNamespace

from paper_agent.services.context_budget import (
    COMPACT_THRESHOLD,
    DEFAULT_OUTPUT_RESERVE,
    HARD_TRUNCATION_THRESHOLD,
    TOOL_DEFINITION_OVERHEAD,
    CompactionParts,
    ContextBudget,
    compacted_messages,
    drop_oldest_exchange,
    estimate_messages_tokens,
    estimate_tokens,
    partition_messages,
    rebuilt_messages,
)


def _message(role: str, content: str) -> dict[str, object]:
    return {"role": role, "content": content}


def _assistant_tool_call(call_id: str = "call-1") -> dict[str, object]:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": "search_paper", "arguments": "{}"},
            }
        ],
    }


def _tool_result(call_id: str = "call-1") -> dict[str, object]:
    return {"role": "tool", "tool_call_id": call_id, "content": "result"}


def test_estimate_tokens_charges_cjk_per_character_and_other_text_per_four():
    assert estimate_tokens("") == 0
    assert estimate_tokens("论文助手") == 4
    assert estimate_tokens("abcdefgh") == 2
    assert estimate_tokens("abcdefghij") == 3
    # 4 CJK characters plus 4 ASCII characters: 4 + 1.
    assert estimate_tokens("论文助手abcd") == 5


def test_estimate_messages_tokens_counts_content_and_tool_calls():
    messages = [
        _message("system", "系统提示"),
        {"role": "user", "content": None},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {
                        "name": "search_paper",
                        "arguments": '{"query": "router"}',
                    },
                }
            ],
        },
    ]

    total = estimate_messages_tokens(messages)

    assert total == (
        estimate_tokens("系统提示")
        + estimate_tokens("search_paper")
        + estimate_tokens('{"query": "router"}')
    )


def test_context_budget_from_profile_returns_none_without_context_length():
    assert ContextBudget.from_profile(
        SimpleNamespace(context_length=None, max_output_tokens=None)
    ) is None
    assert ContextBudget.from_profile(SimpleNamespace()) is None


def test_context_budget_reserves_output_room_and_derives_thresholds():
    budget = ContextBudget(context_length=2_000, max_output_tokens=200)

    assert budget.effective_limit == 1_800
    assert budget.compaction_threshold == int(1_800 * COMPACT_THRESHOLD)
    assert budget.hard_truncation_limit == int(1_800 * HARD_TRUNCATION_THRESHOLD)
    assert TOOL_DEFINITION_OVERHEAD == 1_000
    assert DEFAULT_OUTPUT_RESERVE == 8_192


def test_context_budget_without_output_cap_uses_the_default_reserve():
    capped = ContextBudget(context_length=100_000, max_output_tokens=None)

    assert capped.effective_limit == 100_000 - DEFAULT_OUTPUT_RESERVE
    # A context smaller than the default reserve degenerates to a minimal
    # budget instead of a negative one.
    assert ContextBudget(context_length=2_000).effective_limit == 1


def test_needs_compaction_counts_tool_definition_overhead():
    budget = ContextBudget(context_length=2_000, max_output_tokens=200)
    small = [_message("user", "a" * 200)]  # 50 tokens + 1_000 overhead

    assert budget.request_tokens(small) == 1_050
    assert not budget.needs_compaction(small)

    large = [_message("user", "a" * 1_600)]  # 400 tokens + 1_000 overhead

    assert budget.needs_compaction(large)


def test_partition_messages_keeps_tool_pair_and_trailing_systems_in_the_tail():
    messages = [
        _message("system", "prompt"),
        _message("user", "first"),
        _message("assistant", "first answer"),
        _message("user", "current"),
        _assistant_tool_call(),
        _tool_result(),
        _message("system", "wrap up"),
    ]

    parts = partition_messages(messages)

    assert parts.head == [_message("system", "prompt")]
    assert parts.middle == [
        _message("user", "first"),
        _message("assistant", "first answer"),
    ]
    assert parts.current_user == _message("user", "current")
    assert parts.tail == [
        _assistant_tool_call(),
        _tool_result(),
        _message("system", "wrap up"),
    ]


def test_partition_messages_folds_history_between_head_and_the_last_user_turn():
    messages = [
        _message("system", "prompt"),
        _message("user", "first"),
        _message("assistant", "first answer"),
        _message("user", "second"),
    ]

    parts = partition_messages(messages)

    assert parts.head == [_message("system", "prompt")]
    assert parts.middle == [
        _message("user", "first"),
        _message("assistant", "first answer"),
    ]
    assert parts.current_user == _message("user", "second")
    assert parts.tail == []


def test_partition_messages_without_a_user_turn_leads_only_head_and_tail():
    messages = [_message("system", "prompt"), _assistant_tool_call(), _tool_result()]

    parts = partition_messages(messages)

    assert parts.head == [_message("system", "prompt")]
    assert parts.middle == []
    assert parts.current_user is None
    assert parts.tail == [_assistant_tool_call(), _tool_result()]


def test_partition_messages_never_walks_the_tail_into_the_head():
    parts = partition_messages([_message("system", "a"), _message("system", "b")])

    assert parts.head == [_message("system", "a"), _message("system", "b")]
    assert parts.middle == []
    assert parts.tail == []
    assert parts.current_user is None


def test_compacted_messages_places_the_summary_between_head_and_current_user():
    parts = CompactionParts(
        head=[_message("system", "prompt")],
        middle=[_message("user", "first")],
        current_user=_message("user", "current"),
        tail=[_message("system", "wrap up")],
    )

    rebuilt = compacted_messages(parts, "摘要正文")

    assert rebuilt == [
        _message("system", "prompt"),
        {
            "role": "system",
            "content": "以下是此前对话历史的压缩摘要，用于延续上下文：\n摘要正文",
        },
        _message("user", "current"),
        _message("system", "wrap up"),
    ]


def test_rebuilt_messages_rejoins_the_original_request():
    messages = [
        _message("system", "prompt"),
        _message("user", "first"),
        _message("user", "current"),
        _message("system", "wrap up"),
    ]
    parts = partition_messages(messages)

    assert rebuilt_messages(parts) == messages


def test_drop_oldest_exchange_removes_an_assistant_tool_block_as_one_unit():
    middle = [
        _assistant_tool_call("call-1"),
        _tool_result("call-1"),
        _tool_result("call-1"),
        _message("user", "next"),
    ]

    assert drop_oldest_exchange(middle) is True
    assert middle == [_message("user", "next")]


def test_drop_oldest_exchange_removes_a_user_led_exchange():
    middle = [
        _message("user", "first"),
        _message("assistant", "answer"),
        _message("user", "second"),
    ]

    assert drop_oldest_exchange(middle) is True
    assert middle == [_message("user", "second")]


def test_drop_oldest_exchange_stops_before_the_next_tool_block():
    middle = [
        _message("user", "first"),
        _assistant_tool_call("call-9"),
        _tool_result("call-9"),
    ]

    assert drop_oldest_exchange(middle) is True
    assert middle == [_assistant_tool_call("call-9"), _tool_result("call-9")]


def test_drop_oldest_exchange_reports_an_empty_middle():
    assert drop_oldest_exchange([]) is False
