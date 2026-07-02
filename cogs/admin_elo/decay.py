"""Elo decay and full-reset commands (admin only)."""

from __future__ import annotations

import asyncio
import math

import discord
from discord.ext import commands

from config import YES_EMOJI, NO_EMOJI
from db.elo_db import db_connection
from utils import get_league_announce_channels, is_admin, sync_league_roles
from utils.lb_cache import (
    invalidate as lb_cache_invalidate,
    schedule_rebuild as lb_cache_rebuild,
)
from utils.logger import bot_log

from .base import AdminEloBaseCog


class AdminEloDecayCog(AdminEloBaseCog):
    """Commands: decay, reset_e."""

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _apply_elo_decay_in_transaction(self, cursor) -> None:
        """Apply decay formulae inside an already-open transaction (no commit)."""
        cursor.execute("SELECT id, elo, games_played_this_month FROM players")
        players = cursor.fetchall()

        for player_id, elo, games_played_this_month in players:
            if not games_played_this_month:
                continue

            diff = elo - 500
            if abs(diff) % 2 == 1:
                elo = 500 + math.ceil(diff / 2)
            else:
                elo = 500 + diff // 2

            if games_played_this_month < 15:
                elo -= (15 - games_played_this_month) * 2

            cursor.execute(
                "UPDATE players SET elo = ? WHERE id = ?",
                (max(elo, 0), player_id),
            )

    async def apply_elo_decay(self, guild_id: int) -> None:
        """Public helper kept for backward compatibility (e.g. tests/other callers)."""
        with db_connection(guild_id) as (conn, cursor):
            self._apply_elo_decay_in_transaction(cursor)
            conn.commit()

    async def _announce_league_changes(
        self,
        guild: discord.Guild,
        league_changes: list[tuple[str, str, str]],
    ) -> None:
        """Send a single consolidated message summarizing all league changes after -decay.

        Each line corresponds to one person, without tagging a Role -- only the
        League name is written out:
            Decay has been applied, League changes:

            1. @A: from x to y
            2. @B: from y to z
        """
        if not league_changes:
            return

        announce_channels = get_league_announce_channels(guild)
        if not announce_channels:
            return

        lines = [
            f"{i}. {mention}: from {old_name} to {new_name}"
            for i, (mention, old_name, new_name) in enumerate(league_changes, start=1)
        ]

        header = "Decay has been applied, League changes:"
        # Discord limits messages to 2000 characters -> split into multiple
        # messages if there are too many changes, while keeping the original
        # numbering on each line.
        chunks: list[str] = []
        current = header
        for line in lines:
            candidate = f"{current}\n\n{line}" if current == header else f"{current}\n{line}"
            if len(candidate) > 1900:
                chunks.append(current)
                current = f"{header} (cont.)\n\n{line}"
            else:
                current = candidate
        chunks.append(current)

        for ch in announce_channels:
            try:
                for chunk in chunks:
                    await ch.send(chunk)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    @commands.command()
    @commands.check(is_admin)
    async def decay(self, ctx: commands.Context) -> None:
        """Apply monthly Elo decay to all players (admin only)."""
        embed = discord.Embed(
            title="Elo Decay Confirmation",
            description=(
                f"**This function affects all player stats**, Are you sure you want to "
                f"apply Elo decay? React with {YES_EMOJI} to confirm or {NO_EMOJI} to "
                f"cancel within 5 minutes."
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
                # Bug #8 fix: decay + monthly reset in a single transaction so a
                # mid-operation crash cannot leave decay applied without the reset.
                with db_connection(ctx.guild.id) as (conn, cursor):
                    self._apply_elo_decay_in_transaction(cursor)
                    cursor.execute("UPDATE players SET games_played_this_month=0")
                    conn.commit()
                lb_cache_invalidate(ctx.guild.id)
                lb_cache_rebuild(self.bot, ctx.guild.id)
                league_changes: list[tuple[str, str, str]] = []
                await sync_league_roles(
                    ctx.guild, silent=True, league_changes_out=league_changes
                )
                await self._announce_league_changes(ctx.guild, league_changes)
                embed.title = "Elo Decay Applied"
                embed.description = (
                    "Elo decay has been applied successfully, and monthly stats have been reset!"
                )
                embed.color = discord.Color.green()
                await bot_log(
                    ctx.bot,
                    ctx.guild.id,
                    "Command used: -decay",
                    level="WARNING",
                    fields={
                        "User": f"{ctx.author.mention} ({ctx.author.id})",
                        "Input": f"`{ctx.message.content[:200]}`",
                    },
                )
            else:
                embed.title = "Elo Decay Canceled"
                embed.description = "Elo decay has been canceled."
                embed.color = discord.Color.red()

            await self._remove_bot_reactions(message)
            await message.edit(embed=embed)
        except asyncio.TimeoutError:
            await self._remove_bot_reactions(message)
            await message.edit(
                embed=discord.Embed(
                    title="⌛ Timeout",
                    description="Elo decay confirmation timed out. Please try again.",
                    color=discord.Color.red(),
                )
            )

    @commands.command(help="Reset Elo for all players to 500 (admin only).")
    @commands.check(is_admin)
    async def reset_e(self, ctx: commands.Context) -> None:
        embed = discord.Embed(
            title="Confirm Elo Reset",
            description=(
                f"**This function affects all player stats**, Are you sure you want to "
                f"reset all players' Elo to 500? React with {YES_EMOJI} to confirm or "
                f"{NO_EMOJI} to cancel within 5 minutes."
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
                with db_connection(ctx.guild.id) as (conn, cursor):
                    cursor.execute("UPDATE players SET elo=500")
                    conn.commit()
                lb_cache_invalidate(ctx.guild.id)
                lb_cache_rebuild(self.bot, ctx.guild.id)
                await sync_league_roles(ctx.guild, silent=True)
                embed = discord.Embed(
                    title="Elo Reset Successful",
                    description="All players' Elo has been reset to 500.",
                    color=discord.Color.green(),
                )
            else:
                embed = discord.Embed(
                    title="Elo Reset Canceled",
                    description="Reset Elo operation has been canceled.",
                    color=discord.Color.red(),
                )
            await self._remove_bot_reactions(message)
            await message.edit(embed=embed)
        except asyncio.TimeoutError:
            await self._remove_bot_reactions(message)
            await message.edit(
                embed=discord.Embed(
                    title="Timeout",
                    description="Reset confirmation timed out. Please try again.",
                    color=discord.Color.red(),
                )
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AdminEloDecayCog(bot))