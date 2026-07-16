"""Shared Elo decay math and summary formatting.

Extracted from cogs/admin_elo/decay.py so the manual ``-decay`` command and
the automatic monthly decay task (cogs/admin_elo/decay_auto.py) apply
identical decay math and produce identically formatted change logs.
"""

from __future__ import annotations

import datetime as dt
import math
import os

import discord
from discord.ext import commands

from db.elo_db import (
    db_connection,
    get_db_path,
    get_decay_log_channel_id,
    get_log_channel_id,
    get_max_month,
    get_nickname,
)
from db.match_history_db import (
    delete_match_history_by_season_month,
    get_match_history_rows_by_season_month_all_players,
)
from utils.league import get_league_announce_channels
from utils.logger import PRIVATE_GUILD_ID
from utils.match_export import build_match_listing_workbook, workbook_to_file_bytes

# The automatic monthly decay task (cogs/admin_elo/decay_auto.py) fires at
# this UTC time on the 1st of every calendar month. Defined here (single
# source of truth) so both decay_auto.py's scheduled loop and -decay's
# "time remaining" preview (dev mode off, see cogs/admin_elo/decay.py) can
# never drift apart.
DECAY_AUTO_RUN_TIME_UTC = dt.time(hour=0, minute=10, second=0, tzinfo=dt.timezone.utc)


def next_scheduled_decay_run(now: dt.datetime | None = None) -> dt.datetime:
    """The next UTC datetime the automatic monthly decay task will fire:
    the next day-1-of-month at DECAY_AUTO_RUN_TIME_UTC strictly after
    `now` (defaults to the current time). Used by -decay's dev-mode-off
    "time remaining" preview."""
    now = now or dt.datetime.now(dt.timezone.utc)
    candidate = now.replace(
        day=1,
        hour=DECAY_AUTO_RUN_TIME_UTC.hour,
        minute=DECAY_AUTO_RUN_TIME_UTC.minute,
        second=DECAY_AUTO_RUN_TIME_UTC.second,
        microsecond=0,
    )
    if candidate <= now:
        if candidate.month == 12:
            candidate = candidate.replace(year=candidate.year + 1, month=1)
        else:
            candidate = candidate.replace(month=candidate.month + 1)
    return candidate


async def post_decay_log(bot: commands.Bot, guild_id: int, embed: discord.Embed) -> None:
    """Post a decay change-log embed to the channel configured via
    -decay_setup for this guild, if any. Shared by both the manual -decay
    command (cogs/admin_elo/decay.py) and the automatic monthly task
    (cogs/admin_elo/decay_auto.py) so both paths honor the same
    per-guild setting instead of only the automatic task doing so."""
    channel_id = get_decay_log_channel_id(guild_id)
    if channel_id is None:
        return

    channel = bot.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except Exception:
            return

    try:
        await channel.send(embed=embed)
    except Exception:
        pass


def season_end_notice(season: int, *, auto_clear_registrations: bool = False) -> str:
    """The exact message shown/logged when -decay is run on the last
    configured month of a season, in place of applying Elo decay.

    ``auto_clear_registrations`` is True only for the manual -decay
    command (cogs/admin_elo/decay.py), which now runs -c_reg itself with
    no separate confirmation step. The automatic monthly task
    (cogs/admin_elo/decay_auto.py) still leaves that to the admin, so it
    keeps the original wording.
    """
    if auto_clear_registrations:
        return (
            f"Because this is the final month of Season {season}, no Elo "
            f"decay has been applied. `-c_reg` has been run automatically "
            f"(no separate confirmation needed) so everything starts fresh "
            f"for the next season."
        )
    return (
        f"Because this is the final month of Season {season}, no Elo decay "
        f"has been applied. Use `-c_reg` so everything starts fresh for the "
        f"next season."
    )


def _format_write_log_line(folder: str, filenames: list[str]) -> str:
    return (
        f"Created folder `{folder}/` (if it did not already exist) and wrote "
        f"files: {', '.join(sorted(filenames))}"
    )


async def export_month_snapshot(
    guild_id: int, season: int, month: int
) -> tuple[bool, str]:
    """Export the current standings snapshot (the same 5 files -get_lb
    produces) into ``lb/<guild_id>/Season <season>/Month <month>/``,
    creating the Season/Month folders if they don't exist yet.

    Returns (written, log_line): written is False (and log_line explains
    why) if no players are registered yet.
    """
    from utils.lb_archive import get_lb_root
    from utils.lb_export import build_leaderboard_categories, write_leaderboard_files

    month_dir = os.path.join(get_lb_root(guild_id), f"Season {season}", f"Month {month}")
    categories = build_leaderboard_categories(guild_id)
    if not categories:
        return False, f"No registered players found - skipped writing `{month_dir}/`."

    write_leaderboard_files(month_dir, categories)
    return True, _format_write_log_line(month_dir, list(categories.keys()))


async def export_season_overall(guild_id: int, season: int) -> tuple[bool, str]:
    """Aggregate the whole season (same math as -sum_lb) into
    ``lb/<guild_id>/Season <season>/Overall/``, creating the folder if it
    doesn't exist yet.

    Returns (written, log_line): written is False (and log_line explains
    why) if no month data has been archived for this season yet.
    """
    from utils.lb_archive import get_lb_root
    from utils.lb_export import aggregate_season_categories, write_leaderboard_files

    season_dir = os.path.join(get_lb_root(guild_id), f"Season {season}")
    overall_dir = os.path.join(season_dir, "Overall")
    categories = aggregate_season_categories(season_dir)
    if not categories:
        return False, f"No month data found under `{season_dir}/` - skipped writing `{overall_dir}/`."

    write_leaderboard_files(overall_dir, categories)
    return True, _format_write_log_line(overall_dir, list(categories.keys()))


def db_backup_filename(guild_id: int, season: int, month: int) -> str:
    """Build the "<nickname>_S<season>M<month>.db" backup file name, e.g.
    "QS_S6M3.db". Falls back to the raw guild id if no nickname was set
    via -nickname."""
    nickname = get_nickname(guild_id) or str(guild_id)
    return f"{nickname}_S{season}M{month}.db"


async def send_decay_db_backup(
    bot: commands.Bot, guild_id: int, season: int, month: int
) -> None:
    """Upload a copy of guild_id's database file to the private guild's log
    channel, right before decay runs (manual or automatic), so a snapshot
    of the pre-decay data always survives. Never raises - a backup issue
    must never block decay from running - but every skip/failure path now
    reports why via bot_log instead of failing silently, so a missing
    backup is diagnosable instead of just "the bot didn't grab it".
    """
    # Imported lazily to avoid a circular import (utils.logger imports
    # from db.elo_db, which this module also imports from).
    from utils.logger import bot_log

    channel_id = get_log_channel_id(PRIVATE_GUILD_ID)
    if channel_id is None:
        # Nothing to report to - the log channel itself is what's unset.
        return

    channel = bot.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except Exception as exc:
            await bot_log(
                bot,
                guild_id,
                "Decay DB backup skipped: log channel could not be fetched",
                level="ERROR",
                fields={"Configured channel ID": str(channel_id)},
                exc=exc,
            )
            return

    db_path = get_db_path(guild_id)
    if not os.path.isfile(db_path):
        await bot_log(
            bot,
            guild_id,
            "Decay DB backup skipped: no database file found on disk",
            level="ERROR",
            fields={
                "Resolved path": db_path,
                "Expected under": f"databases/{guild_id}/",
            },
        )
        return

    filename = db_backup_filename(guild_id, season, month)
    try:
        await channel.send(
            content=(
                f"Decay backup - Server `{guild_id}` (Season {season}, Month {month})"
            ),
            file=discord.File(db_path, filename=filename),
        )
    except Exception as exc:
        # Common real-world cause: the .db file is larger than the
        # guild's Discord upload limit (discord.HTTPException). Report
        # the file size + exact exception so this is diagnosable instead
        # of a silent no-op.
        try:
            size_bytes = os.path.getsize(db_path)
            size_desc = f"{size_bytes / (1024 * 1024):.2f} MB"
        except OSError:
            size_desc = "unknown"
        await bot_log(
            bot,
            guild_id,
            "Decay DB backup upload failed",
            level="ERROR",
            fields={"Resolved path": db_path, "File size": size_desc},
            exc=exc,
        )


def apply_elo_decay_in_transaction(
    cursor, *, apply: bool = True
) -> list[tuple[str, int, int, int]]:
    """Compute (and, unless ``apply=False``, write) the decay formulae
    inside an already-open transaction (no commit).

    Returns a list of (player_id, old_elo, new_elo, games_played_this_month)
    for every player whose Elo actually changed, so callers (e.g. -decay's
    preview/summary embeds) can report the exact per-player deltas.

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


def build_decay_summary_lines(changes: list[tuple[str, int, int, int]]) -> list[str]:
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


def chunk_decay_summary(lines: list[str], *, max_len: int = 3800) -> list[str]:
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


async def announce_league_changes(
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


# ---------------------------------------------------------------------------
# Match history purge - 2 months behind the new current month
#
# Every time -decay runs (manual or automatic) and the season/month cursor
# advances, match_history for the season/month 2 months behind the NEW
# current month is archived to an Excel file (same layout as -match_ids)
# and posted to the -decay_setup log channel, then deleted. Example: current
# is Season 7 Month 2, -decay advances to Season 7 Month 3, so Season 7
# Month 1's match_history is archived + purged.
# ---------------------------------------------------------------------------


def _step_back_one_month(
    season: int, month: int, max_month: int | None
) -> tuple[int, int] | None:
    if month > 1:
        return season, month - 1
    if season <= 1 or max_month is None:
        # Can't resolve the previous season's last month without /months
        # being configured, and there is no season before season 1.
        return None
    return season - 1, max_month


def two_months_before(
    season: int, month: int, max_month: int | None
) -> tuple[int, int] | None:
    """(season, month) 2 months behind the given one, or None if it can't
    be resolved (e.g. crosses a season boundary with /months never set)."""
    step1 = _step_back_one_month(season, month, max_month)
    if step1 is None:
        return None
    return _step_back_one_month(step1[0], step1[1], max_month)


async def purge_old_match_history(
    bot: commands.Bot, guild_id: int, new_season: int, new_month: int
) -> str:
    """Archive + delete match_history for the season/month 2 months behind
    (new_season, new_month), the season/month -decay just moved into.

    The archive (every player's matches for that season/month, same
    win/green - loss/red layout as -match_ids) is sent to the channel
    configured via -decay_setup, if any, BEFORE anything is deleted.
    Never raises - a purge issue must never block -decay from completing.
    Returns a one-line summary for the decay change-log embed.
    """
    max_month = get_max_month(guild_id)
    target = two_months_before(new_season, new_month, max_month)
    if target is None:
        return (
            "History purge skipped: could not resolve a season/month 2 "
            "months back (check /months configuration)."
        )
    target_season, target_month = target

    with db_connection(guild_id) as (_conn, cursor):
        rows = get_match_history_rows_by_season_month_all_players(
            cursor, target_season, target_month
        )

    if not rows:
        return (
            f"History purge skipped: no recorded matches in Season "
            f"{target_season}, Month {target_month}."
        )

    with db_connection(guild_id) as (_conn, cursor):
        cursor.execute("SELECT id, name FROM players")
        name_lookup = {str(pid): name for pid, name in cursor.fetchall()}

    try:
        workbook = build_match_listing_workbook(
            rows,
            name_lookup,
            sheet_title=f"S{target_season}M{target_month}",
            header_text=(
                f"Server {guild_id} - Season {target_season} Month "
                f"{target_month} Archive (purged by -decay)"
            ),
        )
        buf = workbook_to_file_bytes(workbook)
        channel_id = get_decay_log_channel_id(guild_id)
        if channel_id is not None:
            channel = bot.get_channel(channel_id)
            if channel is None:
                try:
                    channel = await bot.fetch_channel(channel_id)
                except Exception:
                    channel = None
            if channel is not None:
                filename = f"S{target_season}M{target_month}_archive.xlsx"
                await channel.send(
                    content=(
                        f"Archiving Season {target_season}, Month "
                        f"{target_month} history before deletion "
                        f"({len(rows)} row(s))."
                    ),
                    file=discord.File(buf, filename=filename),
                )
    except Exception:
        # A failed archive upload must not block the purge from running -
        # the change log line below still records exactly what happened.
        pass

    with db_connection(guild_id) as (conn, cursor):
        deleted = delete_match_history_by_season_month(cursor, target_season, target_month)
        conn.commit()

    return (
        f"Purged {deleted} match_history row(s) from Season {target_season}, "
        f"Month {target_month} (archived to decay log channel first, if configured)."
    )