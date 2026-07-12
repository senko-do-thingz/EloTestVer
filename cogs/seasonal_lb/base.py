"""Shared helpers for all SeasonalLB sub-cogs."""

from __future__ import annotations

import os

import discord
from discord.ext import commands

from utils import get_lb_root

from .helpers import sort_seasons
from .season_view import SeasonView


class SeasonalLBBaseCog(commands.Cog):
    """Base class providing shared utilities for seasonal leaderboard commands."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_season_view(
        base: str, lb_guild_id: int, invoker_id: int | None = None
    ) -> tuple[SeasonView | None, discord.Embed]:
        """Return (SeasonView, embed) or (None, error_embed) if no seasons found."""
        seasons = sort_seasons(
            [d for d in os.listdir(base) if os.path.isdir(os.path.join(base, d))]
        )
        if not seasons:
            return None, discord.Embed(
                title="No Seasons Found",
                description="No seasons available.",
                color=discord.Color.red(),
            )
        return SeasonView(seasons, base, lb_guild_id, invoker_id), discord.Embed(
            title="Select a Season",
            description="Choose a season to view its leaderboards.",
            color=discord.Color.blue(),
        )

    async def _open_lbs_picker(
        self, ctx: commands.Context, lb_guild_id: int
    ) -> None:
        base = get_lb_root(lb_guild_id)

        if not os.path.isdir(base):
            await ctx.send(
                embed=discord.Embed(
                    title="Error",
                    description=(
                        f"No leaderboard archive found for this server "
                        f"(`{base.replace(os.sep, '/')}/`). "
                        f"Expected `lb/{lb_guild_id}/` or `lb/{lb_guild_id}_<note>/`."
                    ),
                    color=discord.Color.red(),
                )
            )
            return

        view, embed = self._build_season_view(base, lb_guild_id, ctx.author.id)
        if view is None:
            await ctx.send(embed=embed)
        else:
            await ctx.send(embed=embed, view=view)