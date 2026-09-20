"""Runtime status: dependencies, provider health and latency.

``/status`` runs the same real checks ``doctor`` does - a query against PostgreSQL,
a round trip through Redis, and the model catalogue of each configured provider -
so what it reports is what is true right now, not what was true at startup.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

import discord
from discord import app_commands

from the_sun.cogs.base import SunCog
from the_sun.services import CheckResult, GuildSettingsView

if TYPE_CHECKING:  # pragma: no cover - annotation only
    from the_sun.bot import SunBot

__all__ = ["StatusCog", "status_embed"]

logger = logging.getLogger(__name__)


def _mark(ok: bool) -> str:
    return "ok" if ok else "failing"


def status_embed(
    *,
    checks: Sequence[CheckResult],
    providers: Sequence[Mapping[str, object]],
    settings: GuildSettingsView | None,
    latency_ms: float,
) -> discord.Embed:
    """Render dependency checks, provider circuits and the active model."""
    embed = discord.Embed(title="The Sun · status", colour=discord.Colour.teal())
    embed.add_field(
        name="Dependencies",
        value="\n".join(
            f"`{check.name}` · {_mark(check.ok)} · {check.latency_ms:,.0f} ms\n"
            f"-# {check.detail[:120]}"
            for check in checks
        )
        or "no checks ran",
        inline=False,
    )
    if providers:
        embed.add_field(
            name="Providers",
            value="\n".join(
                f"`{entry['provider']}` · circuit `{entry['state']}`"
                + (f" · retrying after {entry['open_until']}" if entry.get("open_until") else "")
                for entry in providers
            ),
            inline=False,
        )
    if settings is not None:
        embed.add_field(
            name="This server uses",
            value=(
                f"provider `{settings.ai_provider or 'deployment default'}` · "
                f"model `{settings.ai_model or 'provider default'}`"
            ),
            inline=False,
        )
    embed.set_footer(text=f"gateway round trip {latency_ms:,.0f} ms")
    return embed


class StatusCog(SunCog):
    """The ``/status`` and ``/ping`` commands."""

    @app_commands.command(name="status", description="Check the bot's dependencies and providers.")
    async def status(self, interaction: discord.Interaction) -> None:
        settings = await self.ensure_admin(interaction)
        await self.defer(interaction, ephemeral=True)
        health = self.bot.health_service
        checks = [
            health.check_config(),
            await health.check_database(),
            await health.check_cache(),
        ]
        circuits = await self.context.ai.circuit_report(guild_provider=settings.ai_provider)
        providers = [
            {
                "provider": snapshot.provider,
                "state": snapshot.state.value,
                "open_until": snapshot.open_until.strftime("%H:%M:%S")
                if snapshot.open_until
                else None,
            }
            for snapshot in circuits
        ]
        embed = status_embed(
            checks=checks,
            providers=providers,
            settings=settings,
            latency_ms=self.bot.latency * 1000,
        )
        await self.reply(interaction, "", ephemeral=True, embed=embed)

    @app_commands.command(name="ping", description="Measure the latency to Discord.")
    async def ping(self, interaction: discord.Interaction) -> None:
        gateway_ms = self.bot.latency * 1000
        await self.defer(interaction, ephemeral=False)
        answer = await interaction.followup.send("Measuring…", wait=True, ephemeral=False)
        round_trip_ms = (answer.created_at - interaction.created_at).total_seconds() * 1000
        await answer.edit(
            content=(
                f"Gateway heartbeat: **{gateway_ms:,.0f} ms**\n"
                f"Round trip for this command: **{round_trip_ms:,.0f} ms**"
            )
        )


async def setup(bot: SunBot) -> None:
    """Register this cog with the bot."""
    await bot.add_cog(StatusCog(bot))
