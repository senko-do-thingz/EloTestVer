"""
cogs/admin_elo/registration.py

This used to contain a full duplicate of cogs/registration.py's
RegistrationCog (register, rename, register_o, rename_o, register_oid,
rename_oid). Because both files defined a cog class named "RegistrationCog",
whichever loaded first "won" and the other failed with a silent
"Cog named 'RegistrationCog' already loaded" error during startup - so one
of the two copies was always dead code, and it was easy to lose bugfixes
made to whichever copy didn't happen to load.

cogs/registration.py is the single source of truth for register/rename
(both the public commands and their admin_o/admin_oid variants).

This module now houses AdminEloRegistrationCog with the bulk/registration-
clearing admin commands referenced in cogs/help.py and elobot.py's logging
allowlist: c_reg is implemented below. d_reg/did_reg/db_d are still not
implemented anywhere in the codebase.
"""

from __future__ import annotations

import asyncio

import discord
from discord.ext import commands

from config import YES_EMOJI, NO_EMOJI
from db.elo_db import db_connection
from utils import is_admin
from utils.lb_cache import (
    invalidate as lb_cache_invalidate,
    schedule_rebuild as lb_cache_rebuild,
)

from .base import AdminEloBaseCog


class AdminEloRegistrationCog(AdminEloBaseCog):
    """Commands: c_reg."""

    @commands.command(
        name="c_reg",
        help="Clear all registered player data (admin only).",
    )
    @commands.check(is_admin)
    async def c_reg(self, ctx: commands.Context) -> None:
        with db_connection(ctx.guild.id) as (_conn, cursor):
            cursor.execute("SELECT COUNT(*) FROM players")
            player_count = cursor.fetchone()[0]

        if not player_count:
            await ctx.send(
                embed=discord.Embed(
                    title="Nothing to Clear",
                    description="There are no registered players to clear.",
                    color=discord.Color.blue(),
                )
            )
            return

        embed = discord.Embed(
            title="Confirm Clear All Registrations",
            description=(
                f"**This will permanently delete all {player_count} registered "
                f"player(s)**, along with their match history and head-to-head "
                f"stats. This cannot be undone. React with {YES_EMOJI} to confirm "
                f"or {NO_EMOJI} to cancel within 5 minutes."
            ),
            color=discord.Color.orange(),
        )
        message = await ctx.send(embed=embed)
        await message.add_reaction(YES_EMOJI)
        await message.add_reaction(NO_EMOJI)

        def check(reaction, user):
            return (
                user == ctx.author
                and str(reaction.emoji) in [YES_EMOJI, NO_EMOJI]
                and reaction.message.id == message.id
            )

        try:
            reaction, _ = await self.bot.wait_for(
                "reaction_add", timeout=300.0, check=check
            )
            if str(reaction.emoji) == YES_EMOJI:
                # players is the parent table; h2h_stats and match_history both
                # declare ON DELETE CASCADE on player_id, and the connection
                # enables PRAGMA foreign_keys=ON, so this single DELETE also
                # wipes each cleared player's head-to-head stats and match
                # history without needing separate statements.
                with db_connection(ctx.guild.id) as (conn, cursor):
                    cursor.execute("DELETE FROM players")
                    conn.commit()
                lb_cache_invalidate(ctx.guild.id)
                lb_cache_rebuild(self.bot, ctx.guild.id)
                embed.title = "All Registrations Cleared"
                embed.description = (
                    f"{player_count} registered player(s) and their stats have "
                    "been permanently deleted."
                )
                embed.color = discord.Color.green()
            else:
                embed.title = "Clear Canceled"
                embed.description = "Clearing all registered player data has been canceled."
                embed.color = discord.Color.red()

            await self._remove_bot_reactions(message)
            await message.edit(embed=embed)
        except asyncio.TimeoutError:
            await self._remove_bot_reactions(message)
            await message.edit(
                embed=discord.Embed(
                    title="Timeout",
                    description="Clear-all confirmation timed out. Please try again.",
                    color=discord.Color.red(),
                )
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AdminEloRegistrationCog(bot))