"""Elo decay and full-reset commands (admin only)."""

from __future__ import annotations

import asyncio
import math

import discord
from discord.ext import commands

from config import YES_EMOJI, NO_EMOJI
from db.elo_db import db_connection, advance_season_month_cursor
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

    def _apply_elo_decay_in_transaction(
        self, cursor, *, apply: bool = True
    ) -> list[tuple[str, int, int, int]]:
        """Compute (and, unless ``apply=False``, write) the decay formulae
        inside an already-open transaction (no commit).

        Returns a list of (player_id, old_elo, new_elo, games_played_this_month)
        for every player whose Elo actually changed, so callers (e.g. -decay's
        preview and summary embeds) can report the exact per-player deltas.

        With ``apply=False`` this is a pure dry run: the UPDATE is skipped, so
        it's safe to call for a preview without mutating any player data
        (nothing is committed either way unless the caller calls conn.commit()).
        """
        cursor.execute("SELECT id, elo, games_played_this_month FROM players")
        players = cursor.fetchall()

        changes: list[tuple[str, int, int, int]] = []
        for player_id, elo, games_played_this_month in players:
            if not games_played_this_month:
                continue

            old_elo = elo
            diff = elo - 500
            if abs(diff) % 2 == 1:
                elo = 500 + math.ceil(diff / 2)
            else:
                elo = 500 + diff // 2

            if games_played_this_month < 15:
                elo -= (15 - games_played_this_month) * 2

            new_elo = max(elo, 0)
            if apply:
                cursor.execute(
                    "UPDATE players SET elo = ? WHERE id = ?",
                    (new_elo, player_id),
                )
            changes.append((player_id, old_elo, new_elo, games_played_this_month))

        return changes

    @staticmethod
    def _preview_season_month_transition(guild_id: int) -> tuple[int, int, int, int]:
        """Read-only preview of what advance_season_month_cursor() would
        produce, without writing anything to the DB. Mirrors its rollover
        logic exactly so -decay's confirmation prompt can show the admin the
        Season/Month transition before they commit to it. Returns
        (old_season, old_month, new_season, new_month)."""
        with db_connection(guild_id) as (_conn, cursor):
            cursor.execute(
                "SELECT max_month, current_season, current_month FROM settings WHERE guild_id=?",
                (guild_id,),
            )
            row = cursor.fetchone()

        max_month = row[0] if row and row[0] is not None else None
        old_season = row[1] if row and row[1] is not None else 1
        old_month = row[2] if row and row[2] is not None else 1

        new_month = old_month + 1
        new_season = old_season
        if max_month is not None and new_month > max_month:
            new_season += 1
            new_month = 1

        return old_season, old_month, new_season, new_month

    async def apply_elo_decay(self, guild_id: int) -> list[tuple[str, int, int, int]]:
        """Public helper kept for backward compatibility (e.g. tests/other callers)."""
        with db_connection(guild_id) as (conn, cursor):
            changes = self._apply_elo_decay_in_transaction(cursor)
            conn.commit()
        return changes

    @staticmethod
    def _build_decay_summary_lines(
        changes: list[tuple[str, int, int, int]]
    ) -> list[str]:
        """Build one detail line per player (mention, no ping when sent in an
        embed): "<@id>: 550 -> 525 (-25) | Games this month: 10"."""
        lines: list[str] = []
        for player_id, old_elo, new_elo, games_played_this_month in changes:
            delta = new_elo - old_elo
            delta_text = f"+{delta}" if delta >= 0 else str(delta)
            lines.append(
                f"<@{player_id}>: {old_elo} -> {new_elo} ({delta_text}) | "
                f"Games this month: {games_played_this_month}"
            )
        return lines

    @staticmethod
    def _chunk_decay_summary(lines: list[str], *, max_len: int = 3800) -> list[str]:
        """Chunk decay summary lines into embed-description-sized blocks."""
        if not lines:
            return ["No player had games played this month, so no Elo was decayed."]

        chunks: list[str] = []
        current = ""
        for line in lines:
            candidate = f"{current}\n{line}" if current else line
            if len(candidate) > max_len:
                chunks.append(current)
                current = line
            else:
                current = candidate
        if current:
            chunks.append(current)
        return chunks

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
        # Compute a full preview of what -decay would do, without writing
        # anything to the DB, so the admin can see the exact per-player Elo
        # changes and the Season/Month transition before confirming.
        with db_connection(ctx.guild.id) as (_conn, cursor):
            preview_changes = self._apply_elo_decay_in_transaction(cursor, apply=False)
        old_season, old_month, new_season, new_month = (
            self._preview_season_month_transition(ctx.guild.id)
        )

        preview_lines = self._build_decay_summary_lines(preview_changes)
        preview_chunks = self._chunk_decay_summary(preview_lines)

        header = (
            f"**This function affects all player stats.** Are you sure you want to "
            f"apply Elo decay? React with {YES_EMOJI} to confirm or {NO_EMOJI} to "
            f"cancel within 5 minutes.\n\n"
            f"**Season/Month transition:** Season {old_season}, Month {old_month} "
            f"-> Season {new_season}, Month {new_month}\n\n"
            f"**Preview of Elo changes ({len(preview_changes)} player(s) affected):**\n"
        )

        embed = discord.Embed(
            title="Elo Decay Confirmation",
            description=f"{header}{preview_chunks[0]}",
            color=discord.Color.orange(),
        )

        # If the preview didn't fit in one embed, send the rest as follow-up
        # messages first, so the reactable confirmation embed stays the most
        # recent (and only reactable) message.
        for chunk in preview_chunks[1:]:
            await ctx.send(
                embed=discord.Embed(
                    title="Elo Decay Confirmation (preview, cont.)",
                    description=chunk,
                    color=discord.Color.orange(),
                )
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
                    changes = self._apply_elo_decay_in_transaction(cursor)
                    cursor.execute("UPDATE players SET games_played_this_month=0")
                    old_season, old_month, new_season, new_month = (
                        advance_season_month_cursor(cursor, ctx.guild.id)
                    )
                    conn.commit()
                lb_cache_invalidate(ctx.guild.id)
                lb_cache_rebuild(self.bot, ctx.guild.id)
                league_changes: list[tuple[str, str, str]] = []
                await sync_league_roles(
                    ctx.guild, silent=True, league_changes_out=league_changes
                )
                await self._announce_league_changes(ctx.guild, league_changes)

                summary_lines = self._build_decay_summary_lines(changes)
                summary_chunks = self._chunk_decay_summary(summary_lines)

                embed.title = "Elo Decay Applied"
                embed.description = (
                    "Elo decay has been applied successfully, and monthly stats "
                    "have been reset!\n\n"
                    f"**Season/Month transition:** Season {old_season}, Month "
                    f"{old_month} -> Season {new_season}, Month {new_month}\n\n"
                    f"{summary_chunks[0]}"
                )
                embed.color = discord.Color.green()

                # If the per-player summary didn't fit in one embed, send the
                # rest as follow-up messages instead of truncating it.
                for chunk in summary_chunks[1:]:
                    await ctx.send(
                        embed=discord.Embed(
                            title="Elo Decay Applied (cont.)",
                            description=chunk,
                            color=discord.Color.green(),
                        )
                    )

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