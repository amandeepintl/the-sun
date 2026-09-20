"""Test doubles.

These exist only to make unit tests deterministic and are never imported by
``src/``. Their interfaces are the project's own protocols, so a double cannot
drift away from the contract the real implementation satisfies.

Integration and live tests deliberately do not use anything from this package:
they run against real PostgreSQL, real Redis, a real provider and real Discord.
"""

from __future__ import annotations

from tests.fakes.cache import FailingCacheBackend, InMemoryCacheBackend
from tests.fakes.http_provider import (
    RecordedTransport,
    attach_transport,
    chat_completion_payload,
    error_payload,
    json_response,
    model_list_payload,
    streaming_body,
    streaming_response,
    transport_client,
)

__all__ = [
    "FailingCacheBackend",
    "InMemoryCacheBackend",
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
