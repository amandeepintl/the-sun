"""Health service.

Every check here performs a real operation against a real dependency: a query
against PostgreSQL, a round trip to Redis, a live model-catalogue request to each
configured provider, and an authenticated identity request to Discord. A check
that cannot reach its dependency reports the failure rather than assuming success.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from the_sun.ai import ProviderHealth
from the_sun.errors import TheSunError
from the_sun.integrations import DiscordIdentity, fetch_bot_identity
from the_sun.observability import get_metrics
from the_sun.services.base import ServiceContext

__all__ = ["CheckResult", "HealthReport", "HealthService"]

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CheckResult:
    """Outcome of one real dependency check."""

    name: str
    ok: bool
    detail: str
    latency_ms: float
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ok": self.ok,
            "detail": self.detail,
            "latency_ms": round(self.latency_ms, 2),
            "data": self.data,
        }


@dataclass(frozen=True, slots=True)
class HealthReport:
    """All check results plus an overall verdict."""

    checks: list[CheckResult]

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks)

    @property
    def failures(self) -> list[CheckResult]:
        return [check for check in self.checks if not check.ok]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "checks": [check.to_dict() for check in self.checks],
            "failures": [check.name for check in self.failures],
        }


class HealthService:
    """Runs dependency checks and records them as metrics."""

    def __init__(self, context: ServiceContext) -> None:
        self._context = context

    def _record(self, result: CheckResult) -> CheckResult:
        metrics = get_metrics()
        metrics.counter(
            "sun_health_checks_total", "Dependency health checks by outcome."
        ).increment(check=result.name, outcome="ok" if result.ok else "failure")
        metrics.histogram(
            "sun_health_check_duration_seconds", "Wall-clock duration of dependency checks."
        ).observe(result.latency_ms / 1000.0, check=result.name)
        return result

    def check_config(self) -> CheckResult:
        """Offline check that the configured providers parse and are complete."""
        started = time.perf_counter()
        try:
            providers = self._context.providers.describe()
        except TheSunError as exc:
            return self._record(
                CheckResult(
                    name="config",
                    ok=False,
                    detail=f"{type(exc).__name__}: {exc}",
                    latency_ms=(time.perf_counter() - started) * 1000,
                )
            )
        return self._record(
            CheckResult(
                name="config",
                ok=True,
                detail=f"{len(providers)} provider(s) configured",
                latency_ms=(time.perf_counter() - started) * 1000,
                data={
                    "app_env": self._context.settings.app_env,
                    "default_provider": self._context.providers.default_name,
                    "providers": providers,
                    "database": self._context.database.config.safe_url,
                    "cache": self._context.settings.cache.safe_url,
                    "redis_key_prefix": self._context.settings.cache.key_prefix,
                    "discord_token": self._context.settings.discord_token_description,
                },
            )
        )

    async def check_database(self) -> CheckResult:
        """Query PostgreSQL and report the applied migration revision."""
        started = time.perf_counter()
        try:
            async with self._context.unit_of_work() as uow:
                info = await uow.database_info.describe()
        except TheSunError as exc:
            return self._record(
                CheckResult(
                    name="database",
                    ok=False,
                    detail=f"{type(exc).__name__}: {exc}",
                    latency_ms=(time.perf_counter() - started) * 1000,
                )
            )
        return self._record(
            CheckResult(
                name="database",
                ok=info.migration_revision is not None,
                detail=(
                    f"PostgreSQL {info.server_version}, revision "
                    f"{info.migration_revision or 'not applied'}"
                ),
                latency_ms=info.latency_ms,
                data={
                    "server_version": info.server_version,
                    "migration_revision": info.migration_revision,
                    "tables": info.tables,
                    "url": self._context.database.config.safe_url,
                },
            )
        )

    async def check_cache(self) -> CheckResult:
        """Round trip Redis and report its version."""
        health = await self._context.cache.ping()
        return self._record(
            CheckResult(
                name="cache",
                ok=health.ok,
                detail=health.detail,
                latency_ms=health.latency_ms,
                data={"url": self._context.settings.cache.safe_url},
            )
        )

    async def check_providers(self) -> list[CheckResult]:
        """Fetch the live model catalogue from every configured provider."""
        results: list[CheckResult] = []
        for health in await self._context.providers.health_all():
            results.append(self._record(_provider_result(health)))
        return results

    async def check_discord(self) -> CheckResult:
        """Prove the Discord token works and report the bot's real identity."""
        started = time.perf_counter()
        try:
            identity = await fetch_bot_identity(self._context.settings.discord_token_value)
        except TheSunError as exc:
            return self._record(
                CheckResult(
                    name="discord",
                    ok=False,
                    detail=f"{type(exc).__name__}: {exc}",
                    latency_ms=(time.perf_counter() - started) * 1000,
                )
            )
        return self._record(
            CheckResult(
                name="discord",
                ok=True,
                detail=f"authenticated as {identity.label} (id {identity.id})",
                latency_ms=(time.perf_counter() - started) * 1000,
                data=_identity_payload(identity),
            )
        )

    async def run(
        self, *, include_discord: bool = True, include_providers: bool = True
    ) -> HealthReport:
        """Run every check and return the combined report."""
        checks: list[CheckResult] = [self.check_config()]
        checks.append(await self.check_database())
        checks.append(await self.check_cache())
        if include_providers:
            checks.extend(await self.check_providers())
        if include_discord:
            checks.append(await self.check_discord())
        return HealthReport(checks=checks)


def _provider_result(health: ProviderHealth) -> CheckResult:
    return CheckResult(
        name=f"provider:{health.provider}",
        ok=health.ok,
        detail=health.detail,
        latency_ms=health.latency_ms,
        data={"models": health.models},
    )


def _identity_payload(identity: DiscordIdentity) -> dict[str, Any]:
    return {
        "id": identity.id,
        "username": identity.username,
        "global_name": identity.global_name,
        "discriminator": identity.discriminator,
        "is_bot": identity.bot,
    }
