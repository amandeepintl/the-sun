"""Token estimation and context window trimming."""

from __future__ import annotations

from the_sun.ai import ChatMessage, trim_to_budget
from the_sun.ai.tokenizer import (
    REPLY_PRIMING_TOKENS,
    estimate_conversation_tokens,
    estimate_message_tokens,
    estimate_tokens,
    remaining_completion_budget,
)


def test_empty_text_costs_nothing() -> None:
    assert estimate_tokens("") == 0


def test_longer_text_costs_more() -> None:
    assert estimate_tokens("a" * 400) > estimate_tokens("a" * 40)


def test_message_cost_includes_the_envelope() -> None:
    message = ChatMessage.user("hello")
    assert estimate_message_tokens(message) > estimate_tokens("hello")


def test_conversation_cost_is_the_sum_of_its_messages_plus_the_envelope() -> None:
    messages = [ChatMessage.system("be brief"), ChatMessage.user("hello there")]
    expected = sum(estimate_message_tokens(message) for message in messages) + REPLY_PRIMING_TOKENS
    assert estimate_conversation_tokens(messages) == expected
    assert estimate_conversation_tokens([]) == 0


def test_trimming_keeps_system_messages_and_recent_turns() -> None:
    system = ChatMessage.system("instructions")
    history = [ChatMessage.user(f"question {index} " + "x" * 200) for index in range(20)]
    trimmed = trim_to_budget([system, *history], max_tokens=600, reserve_for_completion=100)
    assert trimmed[0] is system
    assert trimmed[-1] == history[-1]
    assert len(trimmed) < len(history) + 1


def test_trimming_preserves_the_newest_message_when_nothing_else_fits() -> None:
    oversized = ChatMessage.user("y" * 5_000)
    trimmed = trim_to_budget([oversized], max_tokens=200, reserve_for_completion=100)
    assert trimmed == [oversized]


def test_zero_budget_still_keeps_the_system_prompt_and_last_question() -> None:
    system = ChatMessage.system("instructions")
    question = ChatMessage.user("what is the deployment status?")
    trimmed = trim_to_budget([system, question], max_tokens=10, reserve_for_completion=500)
    assert trimmed == [system, question]


def test_remaining_completion_budget_shrinks_as_context_grows() -> None:
    small = [ChatMessage.user("hi")]
    large = [ChatMessage.user("h" * 4_000)]
    assert remaining_completion_budget(
        small, max_tokens=8_000, reserve_for_completion=0
    ) > remaining_completion_budget(large, max_tokens=8_000, reserve_for_completion=0)
