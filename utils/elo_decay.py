"""Shared Elo decay math and summary formatting.

Extracted from cogs/admin_elo/decay.py so the manual ``-decay`` command and
the automatic monthly decay task (cogs/admin_elo/decay_auto.py) apply
identical decay math and produce identically formatted change logs.
"""

from __future__ import annotations

import math
import os

import discord
from discord.ext import commands

from db.elo_db import get_db_path, get_decay_log_channel_id, get_log_channel_id, get_nickname
from utils.league import get_league_announce_channels
from utils.logger import PRIVATE_GUILD_ID


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


def season_end_notice(season: int) -> str:
    """The exact message shown/logged when -decay is run on the last
    configured month of a season, in place of applying Elo decay."""
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