"""Provider failure classification."""

from __future__ import annotations

import httpx
import pytest

from the_sun.ai.errors import describe_response_body, error_from_response, error_from_transport
from the_sun.errors import (
    ProviderAuthError,
    ProviderBadResponseError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUpstreamError,
)


def test_401_and_403_are_authentication_failures() -> None:
    for status in (401, 403):
        error = error_from_response(provider="primary", status_code=status, body=b'{"error":{}}')
        assert isinstance(error, ProviderAuthError)
        assert not error.retryable


def test_429_is_retryable_and_reads_retry_after() -> None:
    error = error_from_response(
        provider="primary",
        status_code=429,
        body=b'{"error": {"message": "slow down"}}',
        headers={"retry-after": "12"},
    )
    assert isinstance(error, ProviderRateLimitError)
    assert error.retryable
    assert error.retry_after_seconds == 12.0


def test_429_without_a_usable_retry_after_header() -> None:
    error = error_from_response(
        provider="primary", status_code=429, body=b"", headers={"retry-after": "later"}
    )
    assert isinstance(error, ProviderRateLimitError)
    assert error.retry_after_seconds is None


def test_server_errors_are_retryable() -> None:
    error = error_from_response(provider="primary", status_code=503, body=b"upstream down")
    assert isinstance(error, ProviderUpstreamError)
    assert error.retryable


def test_client_errors_are_not_retryable() -> None:
    error = error_from_response(
        provider="primary", status_code=400, body=b'{"error": {"message": "bad model"}}'
    )
    assert isinstance(error, ProviderBadResponseError)
    assert not error.retryable
    assert "bad model" in str(error)


def test_missing_endpoint_is_reported_as_a_bad_response() -> None:
    error = error_from_response(provider="primary", status_code=404, body=b"")
    assert isinstance(error, ProviderBadResponseError)
    assert "does not exist" in str(error)


def test_timeout_exceptions_map_to_timeouts() -> None:
    error = error_from_transport(
        provider="primary",
        error=httpx.ReadTimeout("too slow", request=httpx.Request("POST", "http://x")),
    )
    assert isinstance(error, ProviderTimeoutError)
    assert error.retryable


def test_connect_errors_map_to_upstream_failures() -> None:
    error = error_from_transport(
        provider="primary",
        error=httpx.ConnectError("refused", request=httpx.Request("GET", "http://x")),
    )
    assert isinstance(error, ProviderUpstreamError)
    assert error.retryable


def test_unexpected_exceptions_still_map_to_upstream_failures() -> None:
    error = error_from_transport(provider="primary", error=ValueError("odd"))
    assert isinstance(error, ProviderUpstreamError)


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (b'{"error": {"message": "quota exceeded"}}', "quota exceeded"),
        (b'{"error": {"type": "invalid_request"}}', "invalid_request"),
        (b'{"error": "plain string"}', '{"error": "plain string"}'),
        (b"<html>gateway</html>", "<html>gateway</html>"),
        (b"", "<empty body>"),
    ],
)
def test_describe_response_body_extracts_a_short_reason(body: bytes, expected: str) -> None:
    assert describe_response_body(body) == expected
