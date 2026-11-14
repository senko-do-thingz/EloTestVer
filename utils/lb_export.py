"""Shared leaderboard export/aggregation helpers.

Used by:
- ``-get_lb`` (cogs/admin_import.py): export current DB standings to a zip.
- ``-sum_lb`` (cogs/admin_import.py): aggregate a season's archived monthly
  files into totals.
- The automatic monthly ``-decay`` task (cogs/admin_elo/decay_auto.py):
  writes the same category files straight into the ``lb/<guild_id>/Season
  <n>/Month <m>/`` (or ``.../Overall/``) archive folders instead of zipping
  them for a Discord upload.
"""

from __future__ import annotations

import io
import os
import re
import zipfile

from db.elo_db import db_connection

from .elo import calculate_win_rate
from .lb_archive import format_leaderboard_export_line, read_leaderboard_rows

# Category filename -> Discord-friendly section title, in the same order
# used by -get_lb / -gld / -sum_lb.
CATEGORY_FILENAMES = ("data.txt", "w.txt", "l.txt", "wr.txt", "games.txt")


def build_leaderboard_categories(guild_id: int) -> dict[str, str]:
    """Read a guild's current DB standings and build the same five category
    files -get_lb has always produced (data/w/l/wr/games), keyed by
    filename. Returns an empty dict if no players are registered."""
    with db_connection(guild_id) as (_conn, cursor):
        cursor.execute(
            "SELECT id, name, elo, games_won, games_lost, games_played_this_month FROM players"
        )
        all_players = cursor.fetchall()

    if not all_players:
        return {}

    elo_sorted = sorted(all_players, key=lambda x: x[2], reverse=True)
    wins_sorted = sorted(all_players, key=lambda x: x[3], reverse=True)
    losses_sorted = sorted(all_players, key=lambda x: x[4], reverse=True)
    monthly_sorted = sorted(all_players, key=lambda x: x[5], reverse=True)
    wr_sorted = sorted(
        [
            (did, name, calculate_win_rate(won, lost))
            for did, name, _, won, lost, _ in all_players
        ],
        key=lambda x: x[2],
        reverse=True,
    )

    return {
        "data.txt": "\n".join(
            format_leaderboard_export_line(name, elo, did)
            for did, name, elo, _, _, _ in elo_sorted
        ),
        "w.txt": "\n".join(
            format_leaderboard_export_line(name, won, did)
            for did, name, _, won, _, _ in wins_sorted
        ),
        "l.txt": "\n".join(
            format_leaderboard_export_line(name, lost, did)
            for did, name, _, _, lost, _ in losses_sorted
        ),
        "wr.txt": "\n".join(
            format_leaderboard_export_line(name, f"{wr:.2f}", did)
            for did, name, wr in wr_sorted
        ),
        "games.txt": "\n".join(
            format_leaderboard_export_line(name, games, did)
            for did, name, _, _, _, games in monthly_sorted
        ),
    }


def build_zip_bytes(categories: dict[str, str]) -> io.BytesIO:
    """Zip a category dict (as returned by build_leaderboard_categories or
    aggregate_season_categories) into an in-memory buffer, ready to attach
    to a discord.File."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for filename, content in categories.items():
            zf.writestr(filename, content)
    buf.seek(0)
    return buf


def write_leaderboard_files(dest_dir: str, categories: dict[str, str]) -> None:
    """Write a category dict out as plain files under dest_dir, creating the
    directory (and any missing parents, e.g. Season X/Month Y) if needed."""
    os.makedirs(dest_dir, exist_ok=True)
    for filename, content in categories.items():
        with open(os.path.join(dest_dir, filename), "w", encoding="utf-8") as f:
            f.write(content)


def _month_dir_sort_key(dirname: str) -> tuple[int, str]:
    """Sort "Month <n>" directories numerically (Month 2 before Month 10),
    with anything unrecognized pushed to the end. Used so aggregation
    below processes months in chronological order, meaning a player's
    most-recently-seen display name is the one carried into the Overall
    archive."""
    match = re.match(r"month\s+(\d+)", dirname, re.IGNORECASE)
    return (int(match.group(1)), dirname) if match else (10**9, dirname)


def aggregate_season_categories(season_path: str) -> dict[str, str]:
    """Sum every month archived under season_path (e.g. lb/<id>/Season 6/)
    into total elo/wins/losses/win-rate category files, the same way
    -sum_lb has always done it. Returns an empty dict if no month data is
    found or none of it parses.

    Each player is summed by Discord ID (the stable identity every other
    archive file is matched on - see parse_leaderboard_line() /
    _read_player_stat() in cogs/stats.py), not by display name, so a name
    change between months doesn't split one player into two totals and
    the resulting Overall file still carries a Discord ID for each line.
    Lines from very old archives written before Discord IDs were included
    fall back to being keyed/summed by name instead, since there's no
    stable ID to sum them against, and are written back out without an ID
    the same way they were read.
    """
    months = [
        d
        for d in os.listdir(season_path)
        if os.path.isdir(os.path.join(season_path, d)) and d.lower() != "overall"
    ]
    months.sort(key=_month_dir_sort_key)

    # category filename -> { key: [name, discord_id, total] }, where key
    # is the Discord ID when known, else a name-based fallback key.
    player_elo: dict[str, list] = {}
    player_wins: dict[str, list] = {}
    player_losses: dict[str, list] = {}

    def _accumulate(target: dict[str, list], name: str, value: int, discord_id):
        key = discord_id or f"name:{name}"
        entry = target.get(key)
        if entry is None:
            target[key] = [name, discord_id, value]
        else:
            entry[0] = name  # keep the most-recently-seen display name
            entry[2] += value

    for month in months:
        month_path = os.path.join(season_path, month)

        for filename, target in (
            ("data.txt", player_elo),
            ("w.txt", player_wins),
            ("l.txt", player_losses),
        ):
            file_path = os.path.join(month_path, filename)
            try:
                rows = read_leaderboard_rows(file_path)
            except Exception:
                continue
            for name, value, discord_id in rows:
                _accumulate(target, name, value, discord_id)

    if not player_elo and not player_wins and not player_losses:
        return {}

    def _format_line(name: str, value, discord_id) -> str:
        return (
            format_leaderboard_export_line(name, value, discord_id)
            if discord_id
            else f"{name} {value}"
        )

    categories: dict[str, str] = {}

    if player_elo:
        elo_lines = [
            _format_line(name, elo, discord_id)
            for name, discord_id, elo in sorted(
                player_elo.values(), key=lambda x: x[2], reverse=True
            )
        ]
        categories["data.txt"] = "Elo Leaderboard\n\n" + "\n".join(elo_lines)

    if player_wins:
        wins_lines = [
            _format_line(name, wins, discord_id)
            for name, discord_id, wins in sorted(
                player_wins.values(), key=lambda x: x[2], reverse=True
            )
        ]
        categories["w.txt"] = "Wins Leaderboard\n\n" + "\n".join(wins_lines)

    if player_losses:
        losses_lines = [
            _format_line(name, losses, discord_id)
            for name, discord_id, losses in sorted(
                player_losses.values(), key=lambda x: x[2], reverse=True
            )
        ]
        categories["l.txt"] = "Losses Leaderboard\n\n" + "\n".join(losses_lines)

    # Win Rate: combine wins/losses keyed the same way (Discord ID when
    # known, else name) so wr.txt also carries IDs and stays matchable by
    # _read_player_stat.
    all_keys = set(player_wins.keys()) | set(player_losses.keys())
    if all_keys:
        wr_entries = []
        for key in all_keys:
            w_name, w_id, wins = player_wins.get(key, (None, None, 0))
            l_name, l_id, losses = player_losses.get(key, (None, None, 0))
            name = w_name or l_name
            discord_id = w_id or l_id
            wr_entries.append((name, discord_id, calculate_win_rate(wins, losses)))
        wr_entries.sort(key=lambda x: x[2], reverse=True)
        categories["wr.txt"] = "Win Rate Leaderboard\n\n" + "\n".join(
            _format_line(name, f"{wr:.2f}", discord_id)
            for name, discord_id, wr in wr_entries
        )

    return categories