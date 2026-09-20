"""Provider abstraction.

Chat completions are expressed with the small, provider-neutral types below, so
adding a native adapter for another vendor means implementing this protocol and
registering its type - no service or command changes are needed.

No prompt template, model identifier or response text lives in this module.
Everything is supplied by the caller or comes back from the provider.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:  # pragma: no cover - annotation only
    from the_sun.config.providers import ProviderConfig

__all__ = [
    "AIProvider",
    "AIProviderFactory",
    "AIRole",
    "ChatMessage",
    "ChatResult",
    "ModelInfo",
    "ProviderHealth",
    "StreamChunk",
    "Usage",
]


class AIRole(StrEnum):
    """Message author, in the vocabulary every chat API shares."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


@dataclass(frozen=True, slots=True)
class ChatMessage:
    """One message handed to a provider."""

    role: AIRole
    content: str
    name: str | None = None

    @classmethod
    def system(cls, content: str) -> ChatMessage:
        return cls(role=AIRole.SYSTEM, content=content)

    @classmethod
    def user(cls, content: str, *, name: str | None = None) -> ChatMessage:
        return cls(role=AIRole.USER, content=content, name=name)

    @classmethod
    def assistant(cls, content: str) -> ChatMessage:
        return cls(role=AIRole.ASSISTANT, content=content)

    def as_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"role": self.role.value, "content": self.content}
        if self.name:
            payload["name"] = self.name
        return payload


@dataclass(frozen=True, slots=True)
class Usage:
    """Token accounting exactly as reported by the provider."""

    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None

    @classmethod
    def from_payload(cls, payload: Any) -> Usage | None:
        """Parse a provider usage block, tolerating missing or odd fields."""
        if not isinstance(payload, dict):
            return None
        prompt = _as_int(payload.get("prompt_tokens"))
        completion = _as_int(payload.get("completion_tokens"))
        total = _as_int(payload.get("total_tokens"))
        if prompt is None and completion is None and total is None:
            return None
        if total is None and (prompt is not None or completion is not None):
            total = (prompt or 0) + (completion or 0)
        return cls(prompt_tokens=prompt, completion_tokens=completion, total_tokens=total)


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return None


@dataclass(frozen=True, slots=True)
class ChatResult:
    """A completed non-streaming response."""

    content: str
    provider: str
    model: str
    finish_reason: str | None = None
    usage: Usage | None = None
    latency_ms: float | None = None
    response_id: str | None = None


@dataclass(frozen=True, slots=True)
class StreamChunk:
    """One incremental piece of a streaming response."""

    delta: str = ""
    finish_reason: str | None = None
    usage: Usage | None = None
    model: str | None = None
    response_id: str | None = None


@dataclass(frozen=True, slots=True)
class ModelInfo:
    """A model offered by a provider's live catalogue."""

    id: str
    owned_by: str | None = None
    created: int | None = None


@dataclass(frozen=True, slots=True)
class ProviderHealth:
    """Outcome of a real connectivity and authentication check."""

    provider: str
    ok: bool
    detail: str
    latency_ms: float
    models: list[str] = field(default_factory=list)


@runtime_checkable
class AIProvider(Protocol):
    """The contract every provider adapter implements."""

    @property
    def name(self) -> str: ...

    @property
    def default_model(self) -> str: ...

    async def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> ChatResult: ...

    def stream(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamChunk]: ...

    async def list_models(self) -> list[ModelInfo]: ...

    async def health_check(self) -> ProviderHealth: ...

    async def aclose(self) -> None: ...


class AIProviderFactory(Protocol):
    """Builds a provider adapter from its configuration."""

    def __call__(self, config: ProviderConfig) -> AIProvider: ...
