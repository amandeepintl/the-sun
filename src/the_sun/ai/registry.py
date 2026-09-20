"""Provider registry.

The registry turns the ``AI_PROVIDERS`` configuration into live adapter
instances, resolves which one a guild should use, and owns their lifecycles.
Adding a vendor means registering one adapter type here.
"""

from __future__ import annotations

import logging

from the_sun.ai.base import AIProvider, AIProviderFactory, ModelInfo, ProviderHealth
from the_sun.ai.openai_compat import OpenAICompatibleProvider
from the_sun.config import AIConfig, ProviderConfig
from the_sun.errors import ConfigurationError, ProviderNotConfiguredError

__all__ = ["ADAPTER_TYPES", "ProviderRegistry", "create_registry"]

logger = logging.getLogger(__name__)

#: Adapter type identifier -> implementation.
ADAPTER_TYPES: dict[str, AIProviderFactory] = {
    OpenAICompatibleProvider.type: OpenAICompatibleProvider,
}


class ProviderRegistry:
    """Builds, caches and reports on the configured providers."""

    def __init__(self, config: AIConfig) -> None:
        self._config = config
        self._providers: dict[str, AIProvider] = {}

    @property
    def config(self) -> AIConfig:
        return self._config

    @property
    def default_name(self) -> str:
        return self._config.default_provider

    def names(self) -> tuple[str, ...]:
        return self._config.names()

    def _build(self, provider_config: ProviderConfig) -> AIProvider:
        try:
            adapter = ADAPTER_TYPES[provider_config.type]
        except KeyError as exc:
            known = ", ".join(sorted(ADAPTER_TYPES))
            raise ConfigurationError(
                f"provider {provider_config.name!r} requests adapter type "
                f"{provider_config.type!r}, which is not registered (known: {known})"
            ) from exc
        # The adapter is always handed a concrete timeout: the provider's own value
        # when it sets one, otherwise the deployment default.
        return adapter(self._config.with_resolved_timeout(provider_config))

    def get(self, name: str | None = None) -> AIProvider:
        """Return the provider called ``name``, or the configured default."""
        resolved = name or self.default_name
        cached = self._providers.get(resolved)
        if cached is not None:
            return cached
        try:
            provider_config = self._config.providers[resolved]
        except KeyError as exc:
            known = ", ".join(self._config.names())
            raise ProviderNotConfiguredError(
                f"provider {resolved!r} is not configured (available: {known})"
            ) from exc
        provider = self._build(provider_config)
        self._providers[resolved] = provider
        return provider

    def resolve(self, *, guild_provider: str | None = None) -> AIProvider:
        """Resolve the provider a guild should use.

        A guild override that no longer exists in the configuration falls back to
        the default instead of failing the user's command.
        """
        if guild_provider and guild_provider in self._config.providers:
            return self.get(guild_provider)
        if guild_provider and guild_provider not in self._config.providers:
            logger.warning(
                "guild configured an unknown provider, falling back to the default",
                extra={"requested_provider": guild_provider, "default": self.default_name},
            )
        return self.get(None)

    def describe(self) -> list[dict[str, object]]:
        """Credential-free descriptions of every configured provider."""
        return [
            self._config.providers[name].safe_summary(
                effective_timeout=self._config.effective_timeout(self._config.providers[name])
            )
            for name in sorted(self._config.providers)
        ]

    async def fetch_models(self, name: str | None = None) -> list[ModelInfo]:
        """Fetch the live model catalogue of one provider."""
        return await self.get(name).list_models()

    async def health_all(self) -> list[ProviderHealth]:
        """Run a real connectivity check against every configured provider."""
        results: list[ProviderHealth] = []
        for name in sorted(self._config.providers):
            results.append(await self.get(name).health_check())
        return results

    async def aclose(self) -> None:
        """Close every provider that has been constructed."""
        for provider in list(self._providers.values()):
            await provider.aclose()
        self._providers.clear()


def create_registry(config: AIConfig) -> ProviderRegistry:
    """Build the registry from resolved AI settings."""
    return ProviderRegistry(config)
