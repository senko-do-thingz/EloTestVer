"""Seasonal leaderboard picker commands (lbs, lbs_id)."""

from __future__ import annotations

import discord
from discord.ext import commands

import os

from utils import PRIVATE_GUILD_ID as LB_ID_ALLOWED_GUILD_ID
from utils import get_lb_root

from .base import SeasonalLBBaseCog


class SeasonalLBPickerCog(SeasonalLBBaseCog):
    """Commands: lbs, lbs_id."""

    @commands.command(
        help="Show leaderboard categories and rankings based on selected season and month."
    )
    async def lbs(self, ctx: commands.Context) -> None:
        await self._open_lbs_picker(ctx, ctx.guild.id)

    @commands.command(name="lbs_id", hidden=True)
    async def lbs_id(self, ctx: commands.Context, server_id: int) -> None:
        """View archived seasonal leaderboards for another server (restricted guild only)."""
        if ctx.guild is None or ctx.guild.id != LB_ID_ALLOWED_GUILD_ID:
            await ctx.send(
                embed=discord.Embed(
                    title="Not Available",
                    description="This command is not available in this server.",
                    color=discord.Color.red(),
                )
            )
            return

        base = get_lb_root(server_id)
        if not os.path.isdir(base):
            await ctx.send(
                embed=discord.Embed(
                    title="Leaderboard Archive Not Found",
                    description=(
                        f"No leaderboard archive found for server ID **{server_id}** "
                        f"(`{base.replace(os.sep, '/')}/`). "
                        f"Expected `lb/{server_id}/` or `lb/{server_id}_<note>/`."
                    ),
                    color=discord.Color.red(),
                )
            )
            return

        await self._open_lbs_picker(ctx, server_id)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SeasonalLBPickerCog(bot))
