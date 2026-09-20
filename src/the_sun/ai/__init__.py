"""AI layer: provider abstraction, adapters and the registry.

Anything that talks to an AI provider goes through :class:`~the_sun.ai.base.AIProvider`.
"""

from __future__ import annotations

from the_sun.ai.base import (
    AIProvider,
    AIProviderFactory,
    AIRole,
    ChatMessage,
    ChatResult,
    ModelInfo,
    ProviderHealth,
    StreamChunk,
    Usage,
)
from the_sun.ai.circuit import CircuitBreaker, CircuitSnapshot, CircuitState, build_breaker
from the_sun.ai.openai_compat import OpenAICompatibleProvider
from the_sun.ai.registry import ADAPTER_TYPES, ProviderRegistry, create_registry
from the_sun.ai.retry import RetryPolicy, execute_with_retries
from the_sun.ai.tokenizer import (
    DEFAULT_COMPLETION_RESERVE,
    estimate_conversation_tokens,
    estimate_message_tokens,
    estimate_tokens,
    trim_to_budget,
)

__all__ = [
    "ADAPTER_TYPES",
    "DEFAULT_COMPLETION_RESERVE",
    "AIProvider",
    "AIProviderFactory",
    "AIRole",
    "ChatMessage",
    "ChatResult",
    "CircuitBreaker",
    "CircuitSnapshot",
    "CircuitState",
    "ModelInfo",
    "OpenAICompatibleProvider",
    "ProviderHealth",
    "ProviderRegistry",
    "RetryPolicy",
    "StreamChunk",
    "Usage",
    "build_breaker",
    "create_registry",
    "estimate_conversation_tokens",
    "estimate_message_tokens",
    "estimate_tokens",
    "execute_with_retries",
    "trim_to_budget",
]
