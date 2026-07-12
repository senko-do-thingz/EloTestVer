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
import zipfile

from db.elo_db import db_connection

from .elo import calculate_win_rate
from .lb_archive import format_leaderboard_export_line, parse_leaderboard_line

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


def aggregate_season_categories(season_path: str) -> dict[str, str]:
    """Sum every month archived under season_path (e.g. lb/<id>/Season 6/)
    into total elo/wins/losses/win-rate category files, the same way
    -sum_lb has always done it. Returns an empty dict if no month data is
    found or none of it parses.
    """
    months = [
        d
        for d in os.listdir(season_path)
        if os.path.isdir(os.path.join(season_path, d)) and d.lower() != "overall"
    ]

    player_elo: dict[str, int] = {}
    player_wins: dict[str, int] = {}
    player_losses: dict[str, int] = {}

    for month in months:
        month_path = os.path.join(season_path, month)

        for filename, target in (
            ("data.txt", player_elo),
            ("w.txt", player_wins),
            ("l.txt", player_losses),
        ):
            file_path = os.path.join(month_path, filename)
            if not os.path.exists(file_path):
                continue
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    lines = f.readlines()
            except Exception:
                continue
            for line in lines:
                stripped = line.strip()
                if not stripped or stripped.lower().endswith("leaderboard"):
                    continue
                parsed = parse_leaderboard_line(stripped)
                if parsed:
                    name, value, _ = parsed
                    target[name] = target.get(name, 0) + value

    if not player_elo and not player_wins and not player_losses:
        return {}

    categories: dict[str, str] = {}

    if player_elo:
        elo_lines = [
            f"{name} {elo}"
            for name, elo in sorted(player_elo.items(), key=lambda x: x[1], reverse=True)
        ]
        categories["data.txt"] = "Elo Leaderboard\n\n" + "\n".join(elo_lines)

    if player_wins:
        wins_lines = [
            f"{name} {wins}"
            for name, wins in sorted(player_wins.items(), key=lambda x: x[1], reverse=True)
        ]
        categories["w.txt"] = "Wins Leaderboard\n\n" + "\n".join(wins_lines)

    if player_losses:
        losses_lines = [
            f"{name} {losses}"
            for name, losses in sorted(player_losses.items(), key=lambda x: x[1], reverse=True)
        ]
        categories["l.txt"] = "Losses Leaderboard\n\n" + "\n".join(losses_lines)

    all_player_names = set(player_wins.keys()) | set(player_losses.keys())
    if all_player_names:
        wr_players = sorted(
            [
                (
                    name,
                    calculate_win_rate(player_wins.get(name, 0), player_losses.get(name, 0)),
                )
                for name in all_player_names
            ],
            key=lambda x: x[1],
            reverse=True,
        )
        categories["wr.txt"] = "Win Rate Leaderboard\n\n" + "\n".join(
            f"{name} {wr:.2f}" for name, wr in wr_players
        )

    return categories