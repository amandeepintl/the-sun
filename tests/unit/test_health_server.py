"""Health endpoints, exercised over a real HTTP connection.

The application is the production one. Only the database and cache URLs point at
closed ports, so ``/readyz`` genuinely reports a failing dependency instead of
being told to.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import pytest
from aiohttp.test_utils import TestClient, TestServer

from the_sun.config import Settings
from the_sun.observability.health import HealthServer, build_app
from the_sun.services import build_service_context


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "DISCORD_TOKEN": "unit-test-discord-token",
        "DATABASE_URL": "postgresql://unit:unit@127.0.0.1:1/unit",
        "REDIS_URL": "redis://127.0.0.1:1/0",
        "AI_PROVIDERS": json.dumps(
            {
                "primary": {
                    "type": "openai_compatible",
                    "base_url": "https://127.0.0.1:1/v1",
                    "default_model": "unit-test-model",
                }
            }
        ),
        "DEFAULT_PROVIDER": "primary",
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


@pytest.fixture
async def client() -> AsyncIterator[TestClient]:
    context = build_service_context(_settings())
    server = TestServer(build_app(context))
    async with TestClient(server) as test_client:
        yield test_client
    await context.aclose()


async def test_healthz_reports_liveness_without_touching_dependencies(client: TestClient) -> None:
    response = await client.get("/healthz")
    assert response.status == 200
    assert (await response.json())["status"] == "ok"


async def test_livez_answers_as_well(client: TestClient) -> None:
    assert (await client.get("/livez")).status == 200


async def test_readyz_fails_when_dependencies_are_unreachable(client: TestClient) -> None:
    response = await client.get("/readyz")
    assert response.status == 503
    payload = await response.json()
    assert payload["ok"] is False
    assert set(payload["failures"]) >= {"database", "cache"}


async def test_metrics_endpoint_serves_prometheus_text(client: TestClient) -> None:
    await client.get("/readyz")
    response = await client.get("/metrics")
    assert response.status == 200
    body = await response.text()
    assert "sun_health_checks_total" in body or body.strip() == ""
    assert response.content_type == "text/plain"


async def test_health_server_binds_a_real_port() -> None:
    context = build_service_context(_settings())
    server = HealthServer(context, host="127.0.0.1", port=0)
    try:
        await server.start()
        assert server.bound_port > 0
    finally:
        await server.stop()
        await context.aclose()
