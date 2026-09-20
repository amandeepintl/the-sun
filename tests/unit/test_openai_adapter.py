"""The OpenAI-compatible adapter.

The adapter tested here is the production one: request building, status mapping,
response parsing and SSE handling are all real. Only the bytes on the wire come
from a transport double.
"""

from __future__ import annotations

import json

import httpx
import pytest
from tests.fakes import (
    chat_completion_payload,
    error_payload,
    json_response,
    model_list_payload,
    streaming_response,
    transport_client,
)

from the_sun.ai import AIRole, ChatMessage, OpenAICompatibleProvider
from the_sun.config import ProviderConfig
from the_sun.errors import (
    ProviderAuthError,
    ProviderBadResponseError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUpstreamError,
)

BASE_URL = "https://provider.invalid/v1"


def _provider(*, api_key: str | None = "unit-test-key") -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        ProviderConfig(
            name="primary",
            base_url=BASE_URL,
            default_model="unit-test-model",
            api_key=api_key,
        )
    )


def _attach(provider: OpenAICompatibleProvider, client: httpx.AsyncClient) -> None:
    """Install a transport double. The adapter code itself is untouched."""
    provider._client = client


async def test_chat_returns_provider_content_and_usage() -> None:
    provider = _provider()
    captured: dict[str, object] = {}

    def responder(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        captured["body"] = request.content.decode()
        return json_response(chat_completion_payload("the sun is a star", model="unit-test-model"))

    _attach(provider, transport_client(BASE_URL, responder))
    result = await provider.chat([ChatMessage.user("what is the sun?")])
    await provider.aclose()

    assert result.content == "the sun is a star"
    assert result.provider == "primary"
    assert result.model == "unit-test-model"
    assert result.usage is not None
    assert result.usage.prompt_tokens == 11
    assert result.usage.total_tokens == 18
    assert result.latency_ms is not None
    assert str(captured["url"]).endswith("/chat/completions")
    assert captured["auth"] == "Bearer unit-test-key"
    sent = json.loads(str(captured["body"]))
    assert sent["model"] == "unit-test-model"
    assert sent["messages"][0]["role"] == "user"
    assert sent["messages"][0]["content"] == "what is the sun?"


async def test_chat_without_a_key_sends_no_authorization_header() -> None:
    provider = _provider(api_key=None)
    captured: dict[str, object] = {}

    def responder(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("authorization")
        return json_response(chat_completion_payload("ok", model="unit-test-model"))

    _attach(provider, transport_client(BASE_URL, responder))
    await provider.chat([ChatMessage.user("hello")])
    await provider.aclose()
    assert captured["auth"] is None


async def test_explicit_model_and_sampling_options_are_sent() -> None:
    provider = _provider()
    captured: dict[str, object] = {}

    def responder(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content.decode()
        return json_response(chat_completion_payload("ok", model="other-model"))

    _attach(provider, transport_client(BASE_URL, responder))
    result = await provider.chat(
        [ChatMessage.system("be terse"), ChatMessage.user("hello")],
        model="other-model",
        temperature=0.25,
        max_tokens=64,
    )
    await provider.aclose()
    sent = json.loads(str(captured["body"]))
    assert sent["model"] == "other-model"
    assert sent["temperature"] == 0.25
    assert sent["max_tokens"] == 64
    assert [message["role"] for message in sent["messages"]] == ["system", "user"]
    assert result.model == "other-model"


async def test_empty_message_list_is_rejected_before_the_request() -> None:
    provider = _provider()
    with pytest.raises(ProviderBadResponseError):
        await provider.chat([])
    await provider.aclose()


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, ProviderAuthError),
        (429, ProviderRateLimitError),
        (500, ProviderUpstreamError),
        (400, ProviderBadResponseError),
    ],
)
async def test_http_status_codes_map_to_typed_errors(
    status: int, expected: type[Exception]
) -> None:
    provider = _provider()
    _attach(
        provider,
        transport_client(
            BASE_URL,
            lambda request: json_response(error_payload("failure"), status_code=status),
        ),
    )
    with pytest.raises(expected):
        await provider.chat([ChatMessage.user("hello")])
    await provider.aclose()


async def test_transport_timeout_maps_to_a_timeout_error() -> None:
    provider = _provider()

    def responder(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow", request=request)

    _attach(provider, transport_client(BASE_URL, responder))
    with pytest.raises(ProviderTimeoutError):
        await provider.chat([ChatMessage.user("hello")])
    await provider.aclose()


async def test_non_json_body_is_rejected() -> None:
    provider = _provider()
    _attach(
        provider,
        transport_client(BASE_URL, lambda request: json_response(b"<html>nope</html>")),
    )
    with pytest.raises(ProviderBadResponseError):
        await provider.chat([ChatMessage.user("hello")])
    await provider.aclose()


async def test_response_without_choices_is_rejected() -> None:
    provider = _provider()
    _attach(provider, transport_client(BASE_URL, lambda request: json_response({"choices": []})))
    with pytest.raises(ProviderBadResponseError):
        await provider.chat([ChatMessage.user("hello")])
    await provider.aclose()


async def test_streaming_yields_deltas_and_stops_at_the_terminator() -> None:
    provider = _provider()
    _attach(
        provider,
        transport_client(
            BASE_URL,
            lambda request: streaming_response(
                "The ", "sun ", "is ", "a ", "star.", model="unit-test-model"
            ),
        ),
    )
    chunks = [chunk async for chunk in provider.stream([ChatMessage.user("describe the sun")])]
    await provider.aclose()

    assert "".join(chunk.delta for chunk in chunks) == "The sun is a star."
    assert chunks[-1].finish_reason == "stop"


async def test_list_models_reads_the_live_catalogue() -> None:
    provider = _provider()
    captured: dict[str, object] = {}

    def responder(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return json_response(model_list_payload(["model-a", "model-b"]))

    _attach(provider, transport_client(BASE_URL, responder))
    models = await provider.list_models()
    await provider.aclose()

    assert [model.id for model in models] == ["model-a", "model-b"]
    assert str(captured["url"]).endswith("/models")


async def test_list_models_accepts_a_bare_list_payload() -> None:
    provider = _provider()
    _attach(
        provider,
        transport_client(BASE_URL, lambda request: json_response([{"id": "only-model"}])),
    )
    models = await provider.list_models()
    await provider.aclose()
    assert [model.id for model in models] == ["only-model"]


async def test_health_check_reports_success_and_catalogue_size() -> None:
    provider = _provider()
    _attach(
        provider,
        transport_client(
            BASE_URL, lambda request: json_response(model_list_payload(["model-a", "model-b"]))
        ),
    )
    health = await provider.health_check()
    await provider.aclose()

    assert health.ok is True
    assert health.provider == "primary"
    assert health.models == ["model-a", "model-b"]


async def test_health_check_reports_an_authentication_failure() -> None:
    provider = _provider()
    _attach(
        provider,
        transport_client(
            BASE_URL, lambda request: json_response(error_payload("bad key"), status_code=401)
        ),
    )
    health = await provider.health_check()
    await provider.aclose()

    assert health.ok is False
    assert "ProviderAuthError" in health.detail


def test_chat_messages_serialise_to_the_shared_vocabulary() -> None:
    assert ChatMessage.system("rules").as_payload() == {"role": "system", "content": "rules"}
    assert ChatMessage.user("hi", name="alice").as_payload() == {
        "role": "user",
        "content": "hi",
        "name": "alice",
    }
    assert ChatMessage.assistant("hello").as_payload()["role"] == AIRole.ASSISTANT.value
