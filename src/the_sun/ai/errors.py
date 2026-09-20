"""Mapping upstream failures onto the typed error taxonomy.

Providers report problems as HTTP status codes, JSON error bodies and transport
exceptions. Translating them here keeps a single, testable place that decides
whether an error is retryable, and keeps raw upstream text out of user-facing
messages.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import httpx

from the_sun.errors import (
    ProviderAuthError,
    ProviderBadResponseError,
    ProviderError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUpstreamError,
)

__all__ = [
    "describe_response_body",
    "error_from_response",
    "error_from_transport",
    "retry_after_seconds",
]


def retry_after_seconds(headers: Mapping[str, str] | None) -> float | None:
    """Read a ``Retry-After`` header, supporting both documented formats."""
    if not headers:
        return None
    raw = headers.get("retry-after") or headers.get("Retry-After")
    if raw is None:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None


def describe_response_body(body: bytes, *, limit: int = 400) -> str:
    """Extract a short, safe description of an error body for logs."""
    text = body.decode("utf-8", errors="replace").strip()
    if not text:
        return "<empty body>"
    try:
        parsed: Any = json.loads(text)
    except json.JSONDecodeError:
        return text[:limit]
    if isinstance(parsed, dict):
        error = parsed.get("error")
        if isinstance(error, dict):
            message = error.get("message") or error.get("type") or error.get("code")
            if isinstance(message, str):
                return message[:limit]
    return text[:limit]


def error_from_response(
    *,
    provider: str,
    status_code: int,
    body: bytes,
    headers: Mapping[str, str] | None = None,
) -> ProviderError:
    """Translate a non-success HTTP response into a typed provider error."""
    detail = describe_response_body(body)
    message = f"{provider} responded {status_code}: {detail}"
    if status_code in {401, 403}:
        return ProviderAuthError(message, provider=provider, status_code=status_code)
    if status_code == 408:
        return ProviderTimeoutError(message, provider=provider, status_code=status_code)
    if status_code == 429:
        error = ProviderRateLimitError(message, provider=provider, status_code=status_code)
        error.retry_after_seconds = retry_after_seconds(headers)
        return error
    if status_code >= 500:
        return ProviderUpstreamError(message, provider=provider, status_code=status_code)
    if status_code == 404:
        return ProviderBadResponseError(
            f"{message} (the endpoint or model does not exist)",
            provider=provider,
            status_code=status_code,
        )
    return ProviderBadResponseError(message, provider=provider, status_code=status_code)


def error_from_transport(*, provider: str, error: Exception) -> ProviderError:
    """Translate an httpx transport failure into a typed provider error."""
    if isinstance(error, httpx.TimeoutException):
        return ProviderTimeoutError(f"{provider} timed out: {error}", provider=provider)
    if isinstance(error, httpx.TransportError):
        return ProviderUpstreamError(
            f"{provider} transport error: {type(error).__name__}: {error}", provider=provider
        )
    return ProviderUpstreamError(f"{provider} request failed: {error}", provider=provider)
