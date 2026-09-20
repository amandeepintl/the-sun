"""Token budgeting.

Provider responses report exact usage, but the context window has to be assembled
*before* the request, so this module estimates. The estimator is a documented
heuristic - roughly four characters per token plus a fixed per-message overhead -
which is deliberately conservative. It never invents content; it only decides how
much real history fits.
"""

from __future__ import annotations

from collections.abc import Sequence

from the_sun.ai.base import AIRole, ChatMessage

__all__ = [
    "CHARS_PER_TOKEN",
    "PER_MESSAGE_OVERHEAD",
    "REPLY_PRIMING_TOKENS",
    "estimate_conversation_tokens",
    "estimate_message_tokens",
    "estimate_tokens",
    "remaining_completion_budget",
    "trim_to_budget",
]

#: Characters per token for the heuristic estimator.
CHARS_PER_TOKEN = 4.0
#: Tokens charged per message for role markers and separators.
PER_MESSAGE_OVERHEAD = 4
#: Tokens the chat template adds to prime the assistant's reply.
REPLY_PRIMING_TOKENS = 3
#: Tokens reserved for the model's answer when trimming a context window.
DEFAULT_COMPLETION_RESERVE = 1024


def estimate_tokens(text: str) -> int:
    """Estimate the token count of a string."""
    if not text:
        return 0
    return max(1, int(len(text) / CHARS_PER_TOKEN) + 1)


def estimate_message_tokens(message: ChatMessage) -> int:
    """Estimate the token count of one message including its envelope."""
    return estimate_tokens(message.content) + PER_MESSAGE_OVERHEAD


def estimate_conversation_tokens(messages: Sequence[ChatMessage]) -> int:
    """Estimate the token count of a whole request."""
    if not messages:
        return 0
    return sum(estimate_message_tokens(message) for message in messages) + REPLY_PRIMING_TOKENS


def remaining_completion_budget(
    messages: Sequence[ChatMessage], *, max_tokens: int, reserve_for_completion: int
) -> int:
    """How many tokens the answer may use given the assembled context."""
    used = estimate_conversation_tokens(messages)
    return max(0, max_tokens - used - reserve_for_completion)


def trim_to_budget(
    messages: Sequence[ChatMessage],
    *,
    max_tokens: int,
    reserve_for_completion: int = DEFAULT_COMPLETION_RESERVE,
) -> list[ChatMessage]:
    """Fit the conversation into the remaining context budget.

    System messages are always kept because they carry the active instructions.
    Conversation messages are kept newest-first until the budget is exhausted, so
    the most recent turns always survive. The newest user message is preserved
    even if a single message is larger than the budget, because dropping it would
    leave nothing to answer.
    """
    budget = max_tokens - reserve_for_completion
    system_messages = [message for message in messages if message.role is AIRole.SYSTEM]
    conversation = [message for message in messages if message.role is not AIRole.SYSTEM]

    if budget <= 0:
        newest_user = next(
            (message for message in reversed(conversation) if message.role is AIRole.USER), None
        )
        return [*system_messages, newest_user] if newest_user else list(system_messages)

    used = estimate_conversation_tokens(system_messages)
    kept: list[ChatMessage] = []
    for message in reversed(conversation):
        cost = estimate_message_tokens(message)
        if used + cost <= budget:
            kept.append(message)
            used += cost
            continue
        if message.role is AIRole.USER and not kept:
            kept.append(message)
        break

    kept.reverse()
    return [*system_messages, *kept]
