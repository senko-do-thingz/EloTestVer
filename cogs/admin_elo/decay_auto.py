"""Automatic monthly -decay task.

-decay is no longer something admins need to remember to run by hand every
month: this cog fires once, automatically, whenever the bot's UTC clock
rolls into a new calendar month, and does the following for every guild
database found under databases/ (see utils/elo_decay.py for the shared
helpers used here and by the manual -decay command):

1. Send a backup of that guild's database file, plus a short log message,
   to the private/management guild's log channel (see -log), named
   ``<nickname>_S<season>M<month>.db`` (e.g. ``QS_S6M3.db``).
2. Export the guild's current standings (the same 5 files -get_lb
   produces) into ``lb/<guild_id>/Season <n>/Month <m>/``, creating the
   Season/Month folders if they don't exist yet.
3. If the current month is NOT the season's last configured month (see
   /months): apply Elo decay + reset monthly game counts, exactly like a
   manual -decay run.
   If it IS the last month of the season: skip the Elo decay math (the
   final month's standings are left untouched), show the season-end
   notice (see utils.elo_decay.season_end_notice), and instead aggregate
   the whole season (-sum_lb's logic) into ``lb/<guild_id>/Season <n>/Overall/``.
4. Advance the season/month cursor by 1 (rolling over into next season if
   needed), same rule as advance_season_month_cursor() has always used.
5. Post a change-log embed (same format as a manual -decay run), including
   exactly which folders/files were written, to the channel configured via
   -decay_setup, if any.
"""

from __future__ import annotations

import datetime as dt

import discord
from discord.ext import commands, tasks

from db.elo_db import (
    advance_season_month_cursor,
    db_connection,
    get_max_month,
    get_season_month,
    list_all_guild_ids,
)
from utils import sync_league_roles
from utils.elo_decay import (
    DECAY_AUTO_RUN_TIME_UTC,
    announce_league_changes,
    apply_elo_decay_in_transaction,
    build_decay_summary_lines,
    chunk_decay_summary,
    export_month_snapshot,
    export_season_overall,
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

# A few minutes after daily_streak_refresh.py's midnight task, so both never
# race for the same guild databases. Sourced from utils/elo_decay.py so this
# schedule and -decay's "time remaining" preview (dev mode off) never drift
# apart.
_MONTHLY_CHECK_UTC = DECAY_AUTO_RUN_TIME_UTC


async def run_monthly_decay_for_guild(bot: commands.Bot, guild_id: int) -> None:
    """Run the full automatic monthly routine for a single guild database."""
    season, month = get_season_month(guild_id)

    await send_decay_db_backup(bot, guild_id, season, month)

    # 1. Export the current standings snapshot into lb/<id>/Season n/Month m/,
    # using the exact same folder layout -lbs / -sum_lb already read from.
    _, month_log_line = await export_month_snapshot(guild_id, season, month)

    max_month = get_max_month(guild_id)
    is_last_month_of_season = max_month is not None and month >= max_month

    guild = bot.get_guild(guild_id)

    if is_last_month_of_season:
        # 2a. Season finished: skip the decay formula, aggregate the whole
        # season into Season n/Overall/ instead (same math as -sum_lb).
        _, overall_log_line = await export_season_overall(guild_id, season)

        with db_connection(guild_id) as (conn, cursor):
            cursor.execute("UPDATE players SET games_played_this_month=0")
            old_season, old_month, new_season, new_month = advance_season_month_cursor(
                cursor, guild_id
            )
            conn.commit()

        lb_cache_invalidate(guild_id)
        if guild is not None:
            lb_cache_rebuild(bot, guild_id)

        purge_log_line = await purge_old_match_history(bot, guild_id, new_season, new_month)

        embed = discord.Embed(
            title="Season Complete - Elo Decay Skipped",
            description=(
                f"{season_end_notice(old_season)}\n\n"
                f"**Season/Month transition:** Season {old_season}, Month {old_month} "
                f"-> Season {new_season}, Month {new_month}\n\n"
                f"**File export log:**\n- {month_log_line}\n- {overall_log_line}\n"
                f"- {purge_log_line}"
            ),
            color=discord.Color.blue(),
        )
        await post_decay_log(bot, guild_id, embed)
        await bot_log(
            bot,
            guild_id,
            "Automatic monthly task: season complete, decay skipped",
            level="INFO",
            fields={
                "Season/Month": f"{old_season}/{old_month} -> {new_season}/{new_month}",
                "Month export": month_log_line,
                "Overall export": overall_log_line,
            },
        )
        return

    # 2b. Regular month: apply Elo decay + reset monthly counts, exactly
    # like a manual -decay confirmation would, just without the prompt.
    with db_connection(guild_id) as (conn, cursor):
        changes = apply_elo_decay_in_transaction(cursor)
        cursor.execute("UPDATE players SET games_played_this_month=0")
        old_season, old_month, new_season, new_month = advance_season_month_cursor(
            cursor, guild_id
        )
        conn.commit()

    lb_cache_invalidate(guild_id)

    league_changes: list[tuple[str, str, str]] = []
    if guild is not None:
        lb_cache_rebuild(bot, guild_id)
        await sync_league_roles(guild, silent=True, league_changes_out=league_changes)
        await announce_league_changes(guild, league_changes)

    purge_log_line = await purge_old_match_history(bot, guild_id, new_season, new_month)

    summary_lines = build_decay_summary_lines(changes)
    summary_chunks = chunk_decay_summary(summary_lines)

    embed = discord.Embed(
        title="Elo Decay Applied (automatic)",
        description=(
            "Elo decay has been applied automatically, and monthly stats "
            "have been reset.\n\n"
            f"**Season/Month transition:** Season {old_season}, Month {old_month} "
            f"-> Season {new_season}, Month {new_month}\n\n"
            f"**File export log:**\n- {month_log_line}\n- {purge_log_line}\n\n"
            f"{summary_chunks[0]}"
        ),
        color=discord.Color.green(),
    )
    await post_decay_log(bot, guild_id, embed)
    for chunk in summary_chunks[1:]:
        await post_decay_log(
            bot,
            guild_id,
            discord.Embed(
                title="Elo Decay Applied (automatic, cont.)",
                description=chunk,
                color=discord.Color.green(),
            ),
        )

    await bot_log(
        bot,
        guild_id,
        "Automatic monthly task: Elo decay applied",
        level="WARNING",
        fields={
            "Season/Month": f"{old_season}/{old_month} -> {new_season}/{new_month}",
            "Players affected": str(len(changes)),
            "Month export": month_log_line,
        },
    )


class AutoDecayCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._last_run_month: str | None = None
        self.monthly_decay_check.start()

    def cog_unload(self) -> None:
        self.monthly_decay_check.cancel()

    @tasks.loop(time=_MONTHLY_CHECK_UTC)
    async def monthly_decay_check(self) -> None:
        today = dt.datetime.now(dt.timezone.utc).date()
        if today.day != 1:
            return

        current_month_key = f"{today.year:04d}-{today.month:02d}"
        if self._last_run_month == current_month_key:
            # Already handled this calendar month (e.g. bot restarted on
            # the 1st and the loop fired again) - never double-decay.
            return
        self._last_run_month = current_month_key

        guild_ids = list_all_guild_ids()
        processed = 0
        for guild_id in guild_ids:
            try:
                await run_monthly_decay_for_guild(self.bot, guild_id)
                processed += 1
            except Exception as exc:
                await bot_log(
                    self.bot,
                    guild_id,
                    "Automatic monthly decay task failed for this server",
                    level="ERROR",
                    exc=exc,
                )

        await bot_log(
            self.bot,
            0,
            "Automatic monthly decay task completed",
            level="INFO",
            fields={"Servers processed": str(processed)},
        )

    @monthly_decay_check.before_loop
    async def before_monthly_decay_check(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AutoDecayCog(bot))