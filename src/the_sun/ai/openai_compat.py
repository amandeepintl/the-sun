"""OpenAI-compatible adapter.

One adapter serves every runtime that speaks the ``/chat/completions`` contract -
hosted APIs and local servers alike - because the endpoint, credential and model
all come from configuration. Real responses and real token usage come back from
the provider; nothing is synthesised here.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx

from the_sun.ai.base import (
    AIProvider,
    ChatMessage,
    ChatResult,
    ModelInfo,
    ProviderHealth,
    StreamChunk,
    Usage,
)
from the_sun.ai.errors import error_from_response, error_from_transport
from the_sun.config import ProviderConfig
from the_sun.errors import ProviderBadResponseError, TheSunError

__all__ = ["OpenAICompatibleProvider"]

logger = logging.getLogger(__name__)

_CHAT_ENDPOINT = "/chat/completions"
_MODELS_ENDPOINT = "/models"
_STREAM_DONE = "[DONE]"


class OpenAICompatibleProvider(AIProvider):
    """Chat completions over any OpenAI-compatible HTTP endpoint."""

    #: Registry identifier for this adapter.
    type = "openai_compatible"

    def __init__(self, config: ProviderConfig) -> None:
        self._config = config
        self._client: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------ #
    # Lifecycle and identity
    # ------------------------------------------------------------------ #
    @property
    def name(self) -> str:
        return self._config.name

    @property
    def config(self) -> ProviderConfig:
        return self._config

    @property
    def default_model(self) -> str:
        return self._config.default_model

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._config.base_url,
                timeout=httpx.Timeout(self._config.timeout_seconds, connect=10.0),
            )
        return self._client

    def _request_headers(self) -> dict[str, str]:
        """Headers for one request.

        Credentials travel with the request rather than being attached to the
        client, so an instance that is handed a preconfigured client still
        authenticates correctly.
        """
        headers = {"Accept": "application/json", **self._config.extra_headers}
        api_key = self._config.api_key_value
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------ #
    # Requests
    # ------------------------------------------------------------------ #
    def _payload(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: str | None,
        temperature: float | None,
        max_tokens: int | None,
        stream: bool,
    ) -> dict[str, Any]:
        if not messages:
            raise ProviderBadResponseError(
                "a chat request needs at least one message", provider=self.name
            )
        payload: dict[str, Any] = {
            "model": model or self.default_model,
            "messages": [message.as_payload() for message in messages],
        }
        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if stream:
            payload["stream"] = True
        return payload

    async def _raise_for_status(self, response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        body = await response.aread()
        error = error_from_response(
            provider=self.name,
            status_code=response.status_code,
            body=body,
            headers=dict(response.headers),
        )
        logger.warning(
            "provider request failed",
            extra={
                "provider": self.name,
                "status_code": response.status_code,
                "error": str(error),
            },
        )
        raise error

    async def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> ChatResult:
        payload = self._payload(
            messages, model=model, temperature=temperature, max_tokens=max_tokens, stream=False
        )
        started = time.perf_counter()
        try:
            response = await self.client.post(
                _CHAT_ENDPOINT, json=payload, headers=self._request_headers()
            )
        except httpx.HTTPError as exc:
            raise error_from_transport(provider=self.name, error=exc) from exc
        await self._raise_for_status(response)
        latency_ms = (time.perf_counter() - started) * 1000

        try:
            document = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise ProviderBadResponseError(
                f"{self.name} returned a body that is not JSON", provider=self.name
            ) from exc
        return self._parse_chat_response(
            document, requested_model=payload["model"], latency_ms=latency_ms
        )

    def _parse_chat_response(
        self, document: Any, *, requested_model: str, latency_ms: float
    ) -> ChatResult:
        if not isinstance(document, dict):
            raise ProviderBadResponseError(
                f"{self.name} returned an unexpected response shape", provider=self.name
            )
        choices = document.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ProviderBadResponseError(f"{self.name} returned no choices", provider=self.name)
        first: dict[str, Any] = choices[0] if isinstance(choices[0], dict) else {}
        raw_message = first.get("message")
        message: dict[str, Any] = raw_message if isinstance(raw_message, dict) else {}
        content = message.get("content")
        if content is None:
            content = ""
        if not isinstance(content, str):
            raise ProviderBadResponseError(
                f"{self.name} returned non-text content this adapter cannot render",
                provider=self.name,
            )
        return ChatResult(
            content=content,
            provider=self.name,
            model=str(document.get("model") or requested_model),
            finish_reason=first.get("finish_reason"),
            usage=Usage.from_payload(document.get("usage")),
            latency_ms=latency_ms,
            response_id=str(document.get("id")) if document.get("id") else None,
        )

    async def stream(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamChunk]:
        payload = self._payload(
            messages, model=model, temperature=temperature, max_tokens=max_tokens, stream=True
        )
        try:
            async with self.client.stream(
                "POST", _CHAT_ENDPOINT, json=payload, headers=self._request_headers()
            ) as response:
                if response.status_code >= 400:
                    await self._raise_for_status(response)
                    return
                async for line in response.aiter_lines():
                    chunk = self._parse_stream_line(line)
                    if chunk is not None:
                        yield chunk
        except httpx.HTTPError as exc:
            raise error_from_transport(provider=self.name, error=exc) from exc

    def _parse_stream_line(self, line: str) -> StreamChunk | None:
        stripped = line.strip()
        if not stripped or stripped.startswith(":"):
            return None
        if not stripped.startswith("data:"):
            return None
        data = stripped[len("data:") :].strip()
        if data == _STREAM_DONE:
            return None
        try:
            document = json.loads(data)
        except json.JSONDecodeError:
            logger.warning("skipping unparseable stream frame", extra={"provider": self.name})
            return None
        if not isinstance(document, dict):
            return None
        choices = document.get("choices")
        delta_text = ""
        finish_reason: str | None = None
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            first = choices[0]
            delta = first.get("delta")
            if isinstance(delta, dict) and isinstance(delta.get("content"), str):
                delta_text = delta["content"]
            if isinstance(first.get("finish_reason"), str):
                finish_reason = first["finish_reason"]
        return StreamChunk(
            delta=delta_text,
            finish_reason=finish_reason,
            usage=Usage.from_payload(document.get("usage")),
            model=str(document.get("model")) if document.get("model") else None,
            response_id=str(document.get("id")) if document.get("id") else None,
        )

    async def list_models(self) -> list[ModelInfo]:
        """Fetch the provider's live model catalogue."""
        try:
            response = await self.client.get(_MODELS_ENDPOINT, headers=self._request_headers())
        except httpx.HTTPError as exc:
            raise error_from_transport(provider=self.name, error=exc) from exc
        await self._raise_for_status(response)
        try:
            document = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise ProviderBadResponseError(
                f"{self.name} returned a model list that is not JSON", provider=self.name
            ) from exc
        entries = document.get("data") if isinstance(document, dict) else document
        if not isinstance(entries, list):
            raise ProviderBadResponseError(
                f"{self.name} returned an unexpected model list shape", provider=self.name
            )
        models: list[ModelInfo] = []
        for entry in entries:
            if isinstance(entry, dict) and isinstance(entry.get("id"), str):
                created = entry.get("created")
                models.append(
                    ModelInfo(
                        id=entry["id"],
                        owned_by=entry.get("owned_by")
                        if isinstance(entry.get("owned_by"), str)
                        else None,
                        created=int(created) if isinstance(created, int) else None,
                    )
                )
            elif isinstance(entry, str):
                models.append(ModelInfo(id=entry))
        return models

    async def health_check(self) -> ProviderHealth:
        """Prove connectivity and credentials with a real catalogue request."""
        started = time.perf_counter()
        try:
            models = await self.list_models()
        except TheSunError as exc:
            return ProviderHealth(
                provider=self.name,
                ok=False,
                detail=f"{type(exc).__name__}: {exc}",
                latency_ms=(time.perf_counter() - started) * 1000,
            )
        latency = (time.perf_counter() - started) * 1000
        return ProviderHealth(
            provider=self.name,
            ok=True,
            detail=f"{len(models)} models available",
            latency_ms=latency,
            models=[model.id for model in models],
        )
