"""Usage statistics.

Every number here is a SQL aggregation over rows written by real invocations:
token counts reported by the providers themselves, latency measured locally, and
statuses recorded from actual outcomes. Nothing is estimated, extrapolated or
filled in with sample data.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Literal

import discord
from discord import app_commands

from the_sun.cogs.base import SunCog
from the_sun.errors import InvalidInputError
from the_sun.repositories import CommandUsage, DailyUsage, ProviderUsage, UsageSummary

if TYPE_CHECKING:  # pragma: no cover - annotation only
    from the_sun.bot import SunBot

__all__ = ["StatsCog", "stats_embed"]

logger = logging.getLogger(__name__)

#: Rolling windows offered by the command, in days. ``None`` means all time.
WINDOWS: dict[str, int | None] = {"day": 1, "week": 7, "month": 30, "all": None}
#: Days of per-day series shown for the "all" window.
ALL_TIME_SERIES_DAYS = 30


def _tokens(prompt: int, completion: int) -> str:
    return f"{prompt:,} in · {completion:,} out · {prompt + completion:,} total"


def stats_embed(
    *,
    scope: str,
    window: str,
    summary: UsageSummary,
    top_commands: list[CommandUsage],
    providers: list[ProviderUsage],
    series: list[DailyUsage],
) -> discord.Embed:
    """Render real aggregates as one embed."""
    embed = discord.Embed(
        title=f"The Sun · {scope} usage ({window})",
        colour=discord.Colour.blurple(),
    )
    if summary.invocations == 0:
        embed.description = "No requests have been recorded in this window yet."
        return embed
    embed.add_field(
        name="Requests",
        value=(
            f"{summary.invocations:,} total\n"
            f"{summary.successful:,} succeeded · {summary.failed:,} failed · "
            f"{summary.rate_limited:,} rate limited"
        ),
        inline=True,
    )
    embed.add_field(
        name="Tokens (reported by providers)",
        value=_tokens(summary.prompt_tokens, summary.completion_tokens),
        inline=True,
    )
    if summary.average_latency_ms is not None:
        embed.add_field(
            name="Latency",
            value=f"{summary.average_latency_ms:,.0f} ms average",
            inline=True,
        )
    if top_commands:
        embed.add_field(
            name="Commands",
            value="\n".join(
                f"`/{entry.command}` · {entry.invocations:,}"
                + (f" · {entry.failures:,} failed" if entry.failures else "")
                for entry in top_commands[:8]
            ),
            inline=False,
        )
    if providers:
        embed.add_field(
            name="Providers",
            value="\n".join(
                f"`{entry.provider or 'unset'}` / `{entry.model or 'unset'}` · "
                f"{entry.invocations:,} requests · "
                f"{entry.prompt_tokens + entry.completion_tokens:,} tokens"
                for entry in providers[:6]
            ),
            inline=False,
        )
    if series:
        latest = series[0]
        embed.add_field(
            name="Most recent day",
            value=(
                f"{latest.day.date().isoformat()} · {latest.invocations:,} requests · "
                f"{latest.prompt_tokens + latest.completion_tokens:,} tokens"
            ),
            inline=False,
        )
    if summary.last_occurred_at is not None:
        embed.set_footer(
            text=f"first recorded {_stamp(summary.first_occurred_at)}"
            f" · last {_stamp(summary.last_occurred_at)}"
        )
    return embed


def _stamp(moment: datetime | None) -> str:
    return moment.astimezone().strftime("%Y-%m-%d %H:%M") if moment else "unknown"


class StatsCog(SunCog):
    """The ``/stats`` command."""

    @app_commands.command(name="stats", description="Show how this server uses The Sun.")
    @app_commands.describe(
        scope="Server-wide totals, or only your own requests.",
        window="How far back to look.",
    )
    async def stats(
        self,
        interaction: discord.Interaction,
        scope: Literal["server", "me"] = "server",
        window: Literal["day", "week", "month", "all"] = "week",
    ) -> None:
        settings = await self.ensure_allowed(interaction)
        if scope == "server" and interaction.guild_id is None:
            raise InvalidInputError(
                "server statistics need a guild",
                public_message="Server statistics are only available inside a server.",
            )
        days = WINDOWS[window]
        since = self.bot.usage_service.window_start(days=days)
        guild_id = interaction.guild_id if scope == "server" else None
        user_id = interaction.user.id if scope == "me" else None

        summary = await self.bot.usage_service.summary(
            guild_id=guild_id, user_id=user_id, since=since
        )
        top_commands = await self.bot.usage_service.top_commands(
            guild_id=guild_id, since=since, limit=8
        )
        providers = await self.bot.usage_service.provider_breakdown(
            guild_id=guild_id, since=since, limit=6
        )
        series = await self.bot.usage_service.daily_series(
            guild_id=guild_id,
            since=self.bot.usage_service.window_start(days=ALL_TIME_SERIES_DAYS),
            limit=ALL_TIME_SERIES_DAYS,
        )
        embed = stats_embed(
            scope="server" if scope == "server" else "your",
            window=window,
            summary=summary,
            top_commands=top_commands,
            providers=providers,
            series=series,
        )
        await self.reply(
            interaction,
            "",
            ephemeral=settings.ephemeral_responses or scope == "me",
            embed=embed,
        )


async def setup(bot: SunBot) -> None:
    """Register this cog with the bot."""
    await bot.add_cog(StatsCog(bot))
