"""Head-to-head stats stored in h2h_stats (replaces players.against TEXT blob)."""

from __future__ import annotations

import sqlite3
def parse_against_entries(against_data: str):
    """
    Parse legacy players.against TEXT: "Name, wins, losses; Name2, w2, l2".

    Yields (opponent_name, wins, losses). Skips malformed segments.
    """
    if not against_data or not against_data.strip():
        return

    for entry in against_data.split(";"):
        entry = entry.strip()
        if not entry:
            continue
        parts = entry.split(",")
        if len(parts) != 3:
            continue
        name = parts[0].strip()
        try:
            wins = int(parts[1].strip())
            losses = int(parts[2].strip())
        except ValueError:
            continue
        yield name, wins, losses


def create_h2h_table(cursor: sqlite3.Cursor) -> None:
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS h2h_stats (
            player_id TEXT NOT NULL,
            opponent_name TEXT NOT NULL,
            wins INTEGER NOT NULL DEFAULT 0,
            losses INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (player_id, opponent_name),
            FOREIGN KEY (player_id) REFERENCES players(id) ON DELETE CASCADE
        )
        """
    )


def get_h2h_stats(
    cursor: sqlite3.Cursor, player_id: str | int, opponent_name: str
) -> tuple[int, int]:
    cursor.execute(
        """
        SELECT wins, losses FROM h2h_stats
        WHERE player_id=? AND opponent_name=?
        """,
        (str(player_id), opponent_name),
    )
    row = cursor.fetchone()
    if not row:
        return (0, 0)
    return int(row[0]), int(row[1])


def get_opponent_games(
    cursor: sqlite3.Cursor, player_id: str | int, opponent_name: str
) -> int:
    wins, losses = get_h2h_stats(cursor, player_id, opponent_name)
    return wins + losses


def get_most_played_with(cursor: sqlite3.Cursor, player_id: str | int) -> list[str]:
    cursor.execute(
        """
        SELECT opponent_name, wins + losses AS total
        FROM h2h_stats
        WHERE player_id=? AND (wins + losses) > 0
        ORDER BY total DESC, opponent_name ASC
        """,
        (str(player_id),),
    )
    rows = cursor.fetchall()
    if not rows:
        return []
    max_total = rows[0][1]
    return [name for name, total in rows if total == max_total]


def add_h2h_match(
    cursor: sqlite3.Cursor,
    player_id: str | int,
    opponent_name: str,
    wins: int,
    losses: int,
) -> None:
    """Add wins/losses for one opponent (used after match reports)."""
    cursor.execute(
        """
        INSERT INTO h2h_stats (player_id, opponent_name, wins, losses)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(player_id, opponent_name) DO UPDATE SET
            wins = wins + excluded.wins,
            losses = losses + excluded.losses
        """,
        (str(player_id), opponent_name, wins, losses),
    )


def _set_h2h_row(
    cursor: sqlite3.Cursor,
    player_id: str,
    opponent_name: str,
    wins: int,
    losses: int,
) -> None:
    cursor.execute(
        """
        INSERT INTO h2h_stats (player_id, opponent_name, wins, losses)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(player_id, opponent_name) DO UPDATE SET
            wins = excluded.wins,
            losses = excluded.losses
        """,
        (player_id, opponent_name, wins, losses),
    )


def rename_opponent_in_h2h(
    cursor: sqlite3.Cursor, old_name: str, new_name: str
) -> None:
    """Rename opponent_name for all players when a display name changes."""
    if not old_name or not new_name or old_name == new_name:
        return

    cursor.execute(
        "SELECT player_id, wins, losses FROM h2h_stats WHERE opponent_name=?",
        (old_name,),
    )
    rows = cursor.fetchall()
    if not rows:
        return

    for player_id, wins, losses in rows:
        existing_w, existing_l = get_h2h_stats(cursor, player_id, new_name)
        cursor.execute(
            "DELETE FROM h2h_stats WHERE player_id=? AND opponent_name=?",
            (player_id, old_name),
        )
        _set_h2h_row(
            cursor,
            player_id,
            new_name,
            existing_w + int(wins),
            existing_l + int(losses),
        )


def migrate_legacy_against_column(cursor: sqlite3.Cursor) -> tuple[int, int]:
    """
    Copy players.against TEXT blobs into h2h_stats.

    Returns (players_with_legacy_data, h2h_rows_written).
    """
    cursor.execute(
        """
        SELECT id, COALESCE(against, '') FROM players
        WHERE against IS NOT NULL AND TRIM(against) != ''
        """
    )
    players_migrated = 0
    rows_written = 0

    for player_id, against_data in cursor.fetchall():
        entries = list(parse_against_entries(against_data))
        if not entries:
            continue
        players_migrated += 1
        for opponent_name, wins, losses in entries:
            _set_h2h_row(cursor, str(player_id), opponent_name, wins, losses)
            rows_written += 1

    return players_migrated, rows_written


def clear_legacy_against_column(cursor: sqlite3.Cursor) -> int:
    """Clear players.against after migration. Returns number of rows updated."""
    cursor.execute("UPDATE players SET against='' WHERE COALESCE(against, '') != ''")
    return cursor.rowcount


def has_legacy_against_data(cursor: sqlite3.Cursor) -> bool:
    cursor.execute(
        """
        SELECT 1 FROM players
        WHERE COALESCE(against, '') != ''
        LIMIT 1
        """
    )
    return cursor.fetchone() is not None
