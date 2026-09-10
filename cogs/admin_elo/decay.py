"""Elo decay and full-reset commands (admin only).

The core decay math/formatting lives in utils/elo_decay.py so the automatic
monthly decay task (cogs/admin_elo/decay_auto.py) applies the exact same
logic instead of duplicating it.
"""

from __future__ import annotations

import asyncio
import os
import tempfile as _tempfile

import discord
from discord.ext import commands

from config import YES_EMOJI, NO_EMOJI
from db.elo_db import (
    db_connection,
    advance_season_month_cursor,
    get_decay_log_channel_id,
    get_dev_mode,
    get_max_month,
    get_season_month,
    set_decay_log_channel_id,
)
from utils import is_admin, sync_league_roles
from utils.elo_decay import (
    announce_league_changes,
    apply_elo_decay_in_transaction,
    build_decay_summary_lines,
    chunk_decay_summary,
    export_month_snapshot,
    export_season_overall,
    next_scheduled_decay_run,
    post_decay_log,
    purge_old_match_history,
    season_end_notice,
    send_decay_db_backup,
)
from utils.lb_cache import (
    invalidate as lb_cache_invalidate,
    schedule_rebuild as lb_cache_rebuild,
)
from utils.logger import bot_log

from .base import AdminEloBaseCog
from .registration import clear_all_registrations


class AdminEloDecayCog(AdminEloBaseCog):
    """Commands: decay, decay_setup, reset_e."""

    # ------------------------------------------------------------------
    # Internal helpers - thin wrappers kept so the rest of this file
    # doesn't need to change; the actual logic moved to utils/elo_decay.py
    # so cogs/admin_elo/decay_auto.py (automatic monthly run) can share it.
    # ------------------------------------------------------------------

    def _apply_elo_decay_in_transaction(
        self, cursor, *, apply: bool = True
    ) -> list[tuple[str, int, int, int]]:
        return apply_elo_decay_in_transaction(cursor, apply=apply)

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
        return build_decay_summary_lines(changes)

    @staticmethod
    def _chunk_decay_summary(lines: list[str], *, max_len: int = 3800) -> list[str]:
        return chunk_decay_summary(lines, max_len=max_len)

    async def _announce_league_changes(
        self,
        guild: discord.Guild,
        league_changes_file: str,
    ) -> None:
        await announce_league_changes(guild, league_changes_file)

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    @commands.command(
        help=(
            "Set or clear the channel where the automatic monthly -decay "
            "change log is posted (admin only)."
        )
    )
    @commands.check(is_admin)
    async def decay_setup(
        self, ctx: commands.Context, *, channel: discord.TextChannel | None = None
    ) -> None:
        """
        Usage:
          -decay_setup <channel>   - set the automatic decay log channel
          -decay_setup off         - clear / disable it
        """
        raw = ctx.message.content.strip()
        parts = raw.split(None, 1)
        if len(parts) > 1 and parts[1].strip().lower() == "off":
            set_decay_log_channel_id(ctx.guild.id, None)
            await ctx.send(
                embed=discord.Embed(
                    title="Decay Log Channel Cleared",
                    description="Automatic monthly -decay logging has been disabled.",
                    color=discord.Color.orange(),
                )
            )
            await bot_log(
                ctx.bot,
                ctx.guild.id,
                "Command used: -decay_setup",
                level="WARNING",
                fields={
                    "User": f"{ctx.author.mention} ({ctx.author.id})",
                    "Action": "Decay log channel **disabled**",
                },
            )
            return

        if channel is None:
            current_id = get_decay_log_channel_id(ctx.guild.id)
            if current_id:
                ch = ctx.guild.get_channel(current_id)
                desc = f"Current decay log channel: {ch.mention if ch else str(current_id)}"
            else:
                desc = (
                    "No decay log channel is currently set.\n"
                    "Usage: `-decay_setup <channel>` or `-decay_setup off`."
                )
            await ctx.send(
                embed=discord.Embed(
                    title="Decay Log Channel",
                    description=desc,
                    color=discord.Color.blue(),
                )
            )
            return

        set_decay_log_channel_id(ctx.guild.id, channel.id)
        await ctx.send(
            embed=discord.Embed(
                title="Decay Log Channel Set",
                description=(
                    "Every time the bot rolls over into a new calendar month, the "
                    f"automatic -decay change log will be posted to {channel.mention}."
                ),
                color=discord.Color.green(),
            )
        )
        await bot_log(
            ctx.bot,
            ctx.guild.id,
            "Command used: -decay_setup",
            level="WARNING",
            fields={
                "User": f"{ctx.author.mention} ({ctx.author.id})",
                "Action": f"Decay log channel set to {channel.mention}",
            },
        )

    @commands.command(
        help=(
            "With dev mode off (see -devmode): show the time remaining "
            "before the next automatic Elo decay, using Discord's own "
            "timestamp formatting. With dev mode on: manually run Elo "
            "decay for all players (admin only), same as before. Either "
            "way, if the server's current month is the season's last "
            "configured month (see /months), decay is skipped and -c_reg "
            "runs automatically (no separate confirmation) instead."
        )
    )
    @commands.check(is_admin)
    async def decay(self, ctx: commands.Context) -> None:
        """Show time until the next automatic Elo decay, or (with dev mode
        enabled) manually run Elo decay for all players (admin only).

        With dev mode off, -decay is read-only: it reports how long until
        the next automatic monthly decay run (cogs/admin_elo/decay_auto.py),
        using Discord's <t:...> timestamp formatting so it renders as a
        live countdown client-side.

        With dev mode on, -decay keeps its previous behavior, with one
        addition: at the last configured month of the season (see
        /months), Elo decay is skipped entirely - see season_end_notice()
        in utils/elo_decay.py - a season-end file export runs instead
        (Month snapshot + season Overall summary), and -c_reg now runs
        automatically right after, with no separate admin confirmation.
        """
        if not get_dev_mode(ctx.guild.id):
            run_at = next_scheduled_decay_run()
            ts = int(run_at.timestamp())
            await ctx.send(
                embed=discord.Embed(
                    title="Next Elo Decay",
                    description=(
                        "Dev mode is disabled, `-decay` only reports the "
                        "countdown here\n\n"
                        f"Next automatic Elo decay: <t:{ts}:R> (<t:{ts}:F>)."
                    ),
                    color=discord.Color.blue(),
                )
            )
            return

        pre_season, pre_month = get_season_month(ctx.guild.id)
        max_month = get_max_month(ctx.guild.id)
        is_season_end = max_month is not None and pre_month >= max_month

        old_season, old_month, new_season, new_month = (
            self._preview_season_month_transition(ctx.guild.id)
        )

        if is_season_end:
            header = (
                f"Are you sure you want to proceed? React with {YES_EMOJI} to "
                f"confirm or {NO_EMOJI} to cancel within 5 minutes.\n\n"
                f"{season_end_notice(pre_season, auto_clear_registrations=True)}\n\n"
                f"**Season/Month transition:** Season {old_season}, Month {old_month} "
                f"-> Season {new_season}, Month {new_month}\n\n"
                f"Instead of Elo decay, the bot will export Season {pre_season}'s "
                f"leaderboard archive (current Month + season Overall), then run "
                f"`-c_reg` automatically, before moving on."
            )
            embed = discord.Embed(
                title="Season End Confirmation",
                description=header,
                color=discord.Color.orange(),
            )
            preview_chunks: list[str] = []
        else:
            # Compute a full preview of what -decay would do, without writing
            # anything to the DB, so the admin can see the exact per-player Elo
            # changes and the Season/Month transition before confirming.
            with db_connection(ctx.guild.id) as (_conn, cursor):
                preview_changes = self._apply_elo_decay_in_transaction(
                    cursor, apply=False
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
            preview_chunks = preview_chunks[1:]

        # If the preview didn't fit in one embed, send the rest as follow-up
        # messages first, so the reactable confirmation embed stays the most
        # recent (and only reactable) message.
        for chunk in preview_chunks:
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
                # Snapshot the database before anything changes, named
                # <nickname>_S<season>M<month>.db, same as the automatic
                # monthly task does.
                await send_decay_db_backup(self.bot, ctx.guild.id, pre_season, pre_month)

                if is_season_end:
                    await self._run_season_end(
                        ctx, embed, pre_season, pre_month
                    )
                else:
                    await self._run_regular_decay(ctx, embed, pre_season, pre_month)

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
                    title="Timeout",
                    description="Elo decay confirmation timed out. Please try again.",
                    color=discord.Color.red(),
                )
            )

    async def _run_season_end(
        self,
        ctx: commands.Context,
        embed: discord.Embed,
        pre_season: int,
        pre_month: int,
    ) -> None:
        """No Elo decay: export Month + Overall archives for the finishing
        season, reset monthly counters, and advance to the next season."""
        # 1. Export the current standings snapshot into
        #    lb/<guild_id>/Season <pre_season>/Month <pre_month>/.
        _, month_log_line = await export_month_snapshot(
            ctx.guild.id, pre_season, pre_month
        )
        # 2. Aggregate the whole season into
        #    lb/<guild_id>/Season <pre_season>/Overall/.
        _, overall_log_line = await export_season_overall(ctx.guild.id, pre_season)

        with db_connection(ctx.guild.id) as (conn, cursor):
            cursor.execute("UPDATE players SET games_played_this_month=0")
            old_season, old_month, new_season, new_month = (
                advance_season_month_cursor(cursor, ctx.guild.id)
            )
            conn.commit()

        lb_cache_invalidate(ctx.guild.id)
        lb_cache_rebuild(self.bot, ctx.guild.id)

        purge_log_line = await purge_old_match_history(
            self.bot, ctx.guild.id, new_season, new_month
        )

        # Season end: run -c_reg's exact wipe automatically, no separate
        # reaction confirmation needed - the -decay confirmation the admin
        # already gave covers this too, so everything starts fresh for the
        # next season without an extra manual step.
        result = await clear_all_registrations(self.bot, ctx.guild.id)
        c_reg_log_line = (
            f"Ran `-c_reg` automatically: {result.players_cleared} registered "
            "player(s) and their stats permanently deleted. League role "
            f"cleanup: {result.roles.removed} removed, {result.roles.failed} "
            f"failed, {result.roles.not_found} not found in server."
            if result.players_cleared
            else "Ran `-c_reg` automatically: no registered players to clear."
        )

        embed.title = "Season Complete - Elo Decay Skipped"
        embed.description = (
            f"{season_end_notice(old_season, auto_clear_registrations=True)}\n\n"
            f"**Season/Month transition:** Season {old_season}, Month {old_month} "
            f"-> Season {new_season}, Month {new_month}\n\n"
            f"**File export log:**\n- {month_log_line}\n- {overall_log_line}\n"
            f"- {purge_log_line}\n- {c_reg_log_line}"
        )
        embed.color = discord.Color.blue()

        # Also post this change log to the channel configured for THIS
        # guild via -decay_setup, if any - previously only the automatic
        # monthly task did this, so a manual -decay silently never reached
        # the configured channel no matter which guild it was set up in.
        await post_decay_log(self.bot, ctx.guild.id, embed)

    async def _run_regular_decay(
        self,
        ctx: commands.Context,
        embed: discord.Embed,
        pre_season: int,
        pre_month: int,
    ) -> None:
        """Apply Elo decay + reset monthly counters for a normal month."""
        # Export the current standings snapshot into lb/<id>/Season n/Month m/
        # BEFORE decay/reset touches the data, exactly like the automatic
        # monthly task (decay_auto.py) does - otherwise the archived files
        # would show post-decay Elo and 0 games played instead of the
        # actual monthly performance.
        _, month_log_line = await export_month_snapshot(
            ctx.guild.id, pre_season, pre_month
        )

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
        league_changes_file = _tempfile.mktemp(suffix=".txt", prefix="league_changes_")
        await sync_league_roles(
            ctx.guild, silent=True, league_changes_file=league_changes_file
        )
        await self._announce_league_changes(ctx.guild, league_changes_file)

        purge_log_line = await purge_old_match_history(
            self.bot, ctx.guild.id, new_season, new_month
        )

        summary_lines = self._build_decay_summary_lines(changes)
        summary_chunks = self._chunk_decay_summary(summary_lines)

        embed.title = "Elo Decay Applied"
        embed.description = (
            "Elo decay has been applied successfully, and monthly stats "
            "have been reset!\n\n"
            f"**Season/Month transition:** Season {old_season}, Month "
            f"{old_month} -> Season {new_season}, Month {new_month}\n\n"
            f"**File export log:**\n- {month_log_line}\n- {purge_log_line}\n\n"
            f"{summary_chunks[0]}"
        )
        embed.color = discord.Color.green()

        # Also post this change log to the channel configured for THIS
        # guild via -decay_setup, if any - previously only the automatic
        # monthly task did this, so a manual -decay silently never reached
        # the configured channel no matter which guild it was set up in.
        await post_decay_log(self.bot, ctx.guild.id, embed)

        # If the per-player summary didn't fit in one embed, send the
        # rest as follow-up messages instead of truncating it.
        for chunk in summary_chunks[1:]:
            cont_embed = discord.Embed(
                title="Elo Decay Applied (cont.)",
                description=chunk,
                color=discord.Color.green(),
            )
            await ctx.send(embed=cont_embed)
            await post_decay_log(self.bot, ctx.guild.id, cont_embed)

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