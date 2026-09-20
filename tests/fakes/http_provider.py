"""HTTP transport doubles for adapter tests.

These intercept bytes on the wire only: the adapter under test is the real one,
with its real request building, real status mapping and real SSE parsing. The
payloads here are fixtures for the test suite, never available to the running
application.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

import httpx

from the_sun.ai import OpenAICompatibleProvider

__all__ = [
    "RecordedTransport",
    "attach_transport",
    "chat_completion_payload",
    "error_payload",
    "json_response",
    "model_list_payload",
    "streaming_body",
    "streaming_response",
    "transport_client",
]


def chat_completion_payload(
    content: str,
    *,
    model: str,
    prompt_tokens: int = 11,
    completion_tokens: int = 7,
    finish_reason: str = "stop",
) -> dict[str, object]:
    """A minimal chat completion document."""
    return {
        "id": "test-completion",
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": finish_reason,
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


def error_payload(message: str, *, error_type: str = "invalid_request_error") -> dict[str, object]:
    """An error document in the shape OpenAI-compatible servers use."""
    return {"error": {"message": message, "type": error_type}}


def model_list_payload(model_ids: Sequence[str]) -> dict[str, object]:
    """A model catalogue document."""
    return {
        "object": "list",
        "data": [
            {"id": model_id, "object": "model", "owned_by": "fixture"} for model_id in model_ids
        ],
    }


def json_response(payload: object, *, status_code: int = 200) -> httpx.Response:
    """A JSON (or raw-body) HTTP response."""
    if isinstance(payload, bytes):
        return httpx.Response(
            status_code, content=payload, headers={"content-type": "application/json"}
        )
    return httpx.Response(status_code, json=payload)


def streaming_response(*chunks: str, model: str, status_code: int = 200) -> httpx.Response:
    """An SSE response carrying ``chunks`` as successive deltas."""
    return httpx.Response(
        status_code,
        content=streaming_body(chunks, model=model),
        headers={"content-type": "text/event-stream"},
    )


def transport_client(
    base_url: str, responder: Callable[[httpx.Request], httpx.Response]
) -> httpx.AsyncClient:
    """An AsyncClient that answers through ``responder`` instead of the network."""
    return RecordedTransport(base_url=base_url, responder=responder).client()


def attach_transport(
    provider: OpenAICompatibleProvider, responder: Callable[[httpx.Request], httpx.Response]
) -> None:
    """Point a real adapter at a transport double.

    This is the single place in the suite that reaches into the adapter's client,
    keeping the seam visible and the adapter itself free of test hooks.
    """
    provider._client = transport_client(provider.config.base_url, responder)


def streaming_body(chunks: Sequence[str], *, model: str) -> bytes:
    """Server-sent events carrying ``chunks`` followed by the terminator."""
    lines: list[str] = []
    for chunk in chunks:
        frame = {"id": "test-stream", "model": model, "choices": [{"delta": {"content": chunk}}]}
        lines.append(f"data: {json.dumps(frame)}\n\n")
    final = {
        "id": "test-stream",
        "model": model,
        "choices": [{"delta": {}, "finish_reason": "stop"}],
    }
    lines.append(f"data: {json.dumps(final)}\n\n")
    lines.append("data: [DONE]\n\n")
    return "".join(lines).encode("utf-8")


@dataclass
class RecordedTransport:
    """An httpx transport that answers from a responder and records requests."""

    base_url: str
    responder: Callable[[httpx.Request], httpx.Response]
    requests: list[httpx.Request] = field(default_factory=list)

    def client(self) -> httpx.AsyncClient:
        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return self.responder(request)

        return httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url=self.base_url,
            timeout=httpx.Timeout(5.0),
        )
