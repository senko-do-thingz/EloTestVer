"""Per-match history for /history and revert tracking."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone


# A player's match_history rows are capped at this many. Once a player would
# have a 31st stored row, the oldest one is evicted (FIFO) - see
# trim_match_history() below, called right after each new row is added.
MAX_HISTORY_PER_PLAYER = 30


@dataclass(frozen=True)
class MatchHistoryRow:
    match_id: int
    player_id: str
    opponent_name: str
    wins: int
    losses: int
    elo_init: int
    elo_final: int
    played_at: int

    @property
    def elo_delta(self) -> int:
        return self.elo_final - self.elo_init


def create_match_history_table(cursor: sqlite3.Cursor) -> None:
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS match_history (
            match_id INTEGER NOT NULL,
            player_id TEXT NOT NULL,
            opponent_name TEXT NOT NULL,
            wins INTEGER NOT NULL,
            losses INTEGER NOT NULL,
            elo_init INTEGER NOT NULL,
            elo_final INTEGER NOT NULL,
            played_at INTEGER NOT NULL,
            PRIMARY KEY (match_id, player_id),
            FOREIGN KEY (player_id) REFERENCES players(id) ON DELETE CASCADE
        )
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_match_history_player_played
        ON match_history (player_id, played_at DESC)
        """
    )


def next_match_id(cursor: sqlite3.Cursor) -> int:
    cursor.execute("SELECT COALESCE(MAX(match_id), 0) + 1 FROM match_history")
    return int(cursor.fetchone()[0])


def add_match_history_row(
    cursor: sqlite3.Cursor,
    *,
    match_id: int,
    player_id: str | int,
    opponent_name: str,
    wins: int,
    losses: int,
    elo_init: int,
    elo_final: int,
    played_at: int | None = None,
) -> None:
    ts = played_at if played_at is not None else int(datetime.now(timezone.utc).timestamp())
    cursor.execute(
        """
        INSERT INTO match_history (
            match_id, player_id, opponent_name, wins, losses,
            elo_init, elo_final, played_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (match_id, str(player_id), opponent_name, wins, losses, elo_init, elo_final, ts),
    )


def get_player_matches_count(cursor: sqlite3.Cursor, player_id: str | int) -> int:
    cursor.execute("SELECT matches FROM players WHERE id=?", (str(player_id),))
    row = cursor.fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def increment_player_matches(cursor: sqlite3.Cursor, player_id: str | int) -> None:
    cursor.execute(
        "UPDATE players SET matches = COALESCE(matches, 0) + 1 WHERE id=?",
        (str(player_id),),
    )


def decrement_player_matches(cursor: sqlite3.Cursor, player_id: str | int) -> None:
    cursor.execute(
        "UPDATE players SET matches = COALESCE(matches, 0) - 1 WHERE id=?",
        (str(player_id),),
    )


def get_recent_match_history(
    cursor: sqlite3.Cursor, player_id: str | int, limit: int, offset: int = 0
) -> list[MatchHistoryRow]:
    cursor.execute(
        """
        SELECT match_id, player_id, opponent_name, wins, losses,
               elo_init, elo_final, played_at
        FROM match_history
        WHERE player_id=?
        ORDER BY played_at DESC, match_id DESC
        LIMIT ? OFFSET ?
        """,
        (str(player_id), limit, offset),
    )
    return [
        MatchHistoryRow(
            match_id=row[0],
            player_id=row[1],
            opponent_name=row[2],
            wins=row[3],
            losses=row[4],
            elo_init=row[5],
            elo_final=row[6],
            played_at=row[7],
        )
        for row in cursor.fetchall()
    ]


def count_player_match_history(cursor: sqlite3.Cursor, player_id: str | int) -> int:
    """Number of match_history rows actually stored for this player (capped
    at MAX_HISTORY_PER_PLAYER by trim_match_history)."""
    cursor.execute(
        "SELECT COUNT(*) FROM match_history WHERE player_id=?",
        (str(player_id),),
    )
    row = cursor.fetchone()
    return int(row[0]) if row else 0


def trim_match_history(
    cursor: sqlite3.Cursor,
    player_id: str | int,
    keep: int = MAX_HISTORY_PER_PLAYER,
) -> None:
    """Keep only the ``keep`` most recent match_history rows for a player.

    Once a player would have more than ``keep`` rows stored (e.g. a 31st
    match), the oldest rows beyond the limit are deleted - equivalent to
    evicting match #1 and shifting #2 -> #1, #3 -> #2, ... since rows are
    always read back ordered by recency.
    """
    cursor.execute(
        """
        DELETE FROM match_history
        WHERE player_id=? AND match_id NOT IN (
            SELECT match_id FROM match_history
            WHERE player_id=?
            ORDER BY played_at DESC, match_id DESC
            LIMIT ?
        )
        """,
        (str(player_id), str(player_id), keep),
    )


def find_latest_match_id_for_revert(
    cursor: sqlite3.Cursor,
    winner_id: str | int,
    loser_id: str | int,
    winner_name: str,
    loser_name: str,
    winner_score: int,
    loser_score: int,
) -> int | None:
    """Find the most recent shared match_id matching this result, if any."""
    cursor.execute(
        """
        SELECT match_id FROM match_history
        WHERE player_id=? AND opponent_name=? AND wins=? AND losses=?
        ORDER BY played_at DESC, match_id DESC
        LIMIT 1
        """,
        (str(winner_id), loser_name, winner_score, loser_score),
    )
    winner_row = cursor.fetchone()
    if not winner_row:
        return None

    match_id = winner_row[0]
    cursor.execute(
        """
        SELECT 1 FROM match_history
        WHERE match_id=? AND player_id=? AND opponent_name=? AND wins=? AND losses=?
        """,
        (match_id, str(loser_id), winner_name, loser_score, winner_score),
    )
    return match_id if cursor.fetchone() else None


def delete_match_history_by_id(cursor: sqlite3.Cursor, match_id: int) -> None:
    cursor.execute("DELETE FROM match_history WHERE match_id=?", (match_id,))