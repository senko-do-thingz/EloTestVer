"""Per-match history for /history and revert tracking."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone


# A player's match_history rows used to be capped and FIFO-evicted once full;
# this limit has been removed, so match history is now kept indefinitely.
# Kept as None (rather than deleting the constant) since trim_match_history()
# still accepts an explicit `keep` value for callers that want a cap.
MAX_HISTORY_PER_PLAYER = None


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
    season: int | None = None
    month: int | None = None

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
            season INTEGER DEFAULT NULL,
            month INTEGER DEFAULT NULL,
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
    season: int | None = None,
    month: int | None = None,
) -> None:
    ts = played_at if played_at is not None else int(datetime.now(timezone.utc).timestamp())
    cursor.execute(
        """
        INSERT INTO match_history (
            match_id, player_id, opponent_name, wins, losses,
            elo_init, elo_final, played_at, season, month
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (match_id, str(player_id), opponent_name, wins, losses, elo_init, elo_final, ts, season, month),
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


def _row_from_tuple(row: tuple) -> MatchHistoryRow:
    return MatchHistoryRow(
        match_id=row[0],
        player_id=row[1],
        opponent_name=row[2],
        wins=row[3],
        losses=row[4],
        elo_init=row[5],
        elo_final=row[6],
        played_at=row[7],
        season=row[8] if len(row) > 8 else None,
        month=row[9] if len(row) > 9 else None,
    )


_HISTORY_COLUMNS = (
    "match_id, player_id, opponent_name, wins, losses, "
    "elo_init, elo_final, played_at, season, month"
)


def get_recent_match_history(
    cursor: sqlite3.Cursor, player_id: str | int, limit: int, offset: int = 0
) -> list[MatchHistoryRow]:
    cursor.execute(
        f"""
        SELECT {_HISTORY_COLUMNS}
        FROM match_history
        WHERE player_id=?
        ORDER BY played_at DESC, match_id DESC
        LIMIT ? OFFSET ?
        """,
        (str(player_id), limit, offset),
    )
    return [_row_from_tuple(row) for row in cursor.fetchall()]


def get_match_history_by_season_month(
    cursor: sqlite3.Cursor,
    player_id: str | int,
    season: int,
    month: int,
    limit: int,
    offset: int = 0,
) -> list[MatchHistoryRow]:
    """Same as get_recent_match_history(), but filtered to one season/month."""
    cursor.execute(
        f"""
        SELECT {_HISTORY_COLUMNS}
        FROM match_history
        WHERE player_id=? AND season=? AND month=?
        ORDER BY played_at DESC, match_id DESC
        LIMIT ? OFFSET ?
        """,
        (str(player_id), season, month, limit, offset),
    )
    return [_row_from_tuple(row) for row in cursor.fetchall()]


def get_full_match_history_by_season_month(
    cursor: sqlite3.Cursor,
    player_id: str | int,
    season: int,
    month: int,
) -> list[MatchHistoryRow]:
    """All match_history rows for one season/month, unpaginated (most recent
    first). Used to compute a month-scoped stat (e.g. -history's "Best
    Streak") that needs the whole month's data, not just the current page."""
    cursor.execute(
        f"""
        SELECT {_HISTORY_COLUMNS}
        FROM match_history
        WHERE player_id=? AND season=? AND month=?
        ORDER BY played_at DESC, match_id DESC
        """,
        (str(player_id), season, month),
    )
    return [_row_from_tuple(row) for row in cursor.fetchall()]


def compute_streak_labels(rows: list[MatchHistoryRow]) -> list[int | None]:
    """Group consecutive same-result (win/loss) rows into streaks.

    ``rows`` must be ordered most-recent-first (as returned by every query
    in this module). Only the first (most recent) row of each streak group
    gets the streak's length as its label; the rest are left as None.

    This MUST be computed over a player's full month of matches, not a
    paginated slice - a streak that spans a page boundary would otherwise
    be undercounted on each page, and would disagree with the "Best Streak"
    badge (get_best_streak_by_season_month) and the /export history sheet,
    both of which already use the full month. Callers that only want to
    display one page should slice the *result* of this function, not call
    it with a pre-sliced row list.
    """
    labels: list[int | None] = [None] * len(rows)
    i = 0
    while i < len(rows):
        result = rows[i].wins > rows[i].losses
        j = i
        while j < len(rows) and (rows[j].wins > rows[j].losses) == result:
            j += 1
        labels[i] = j - i
        i = j
    return labels


def get_best_streak_by_season_month(
    cursor: sqlite3.Cursor,
    player_id: str | int,
    season: int,
    month: int,
) -> int:
    """Longest run of consecutive same-result matches (win or loss) within
    one season/month, independent of the player's all-time
    win_streak/highest_win_streak fields (which get_win_streak() reads and
    which are shared with /stats). A "win" is any row where wins > losses."""
    rows = get_full_match_history_by_season_month(cursor, player_id, season, month)
    if not rows:
        return 0

    best = 0
    current_len = 0
    current_result: bool | None = None
    for row in rows:
        result = row.wins > row.losses
        if result == current_result:
            current_len += 1
        else:
            current_result = result
            current_len = 1
        best = max(best, current_len)
    return best


def count_player_match_history(cursor: sqlite3.Cursor, player_id: str | int) -> int:
    """Number of match_history rows actually stored for this player (match
    history storage is unbounded; see trim_match_history)."""
    cursor.execute(
        "SELECT COUNT(*) FROM match_history WHERE player_id=?",
        (str(player_id),),
    )
    row = cursor.fetchone()
    return int(row[0]) if row else 0


def count_player_match_history_by_season_month(
    cursor: sqlite3.Cursor, player_id: str | int, season: int, month: int
) -> int:
    """Number of match_history rows stored for this player in one season/month."""
    cursor.execute(
        "SELECT COUNT(*) FROM match_history WHERE player_id=? AND season=? AND month=?",
        (str(player_id), season, month),
    )
    row = cursor.fetchone()
    return int(row[0]) if row else 0


def get_seasons_with_history(cursor: sqlite3.Cursor, player_id: str | int) -> list[int]:
    """Distinct seasons (naturally sorted, most recent first) this player has matches in."""
    cursor.execute(
        """
        SELECT DISTINCT season FROM match_history
        WHERE player_id=? AND season IS NOT NULL
        ORDER BY season DESC
        """,
        (str(player_id),),
    )
    return [row[0] for row in cursor.fetchall()]


def get_months_with_history(
    cursor: sqlite3.Cursor, player_id: str | int, season: int
) -> list[int]:
    """Distinct months (ascending) this player has matches in, for one season."""
    cursor.execute(
        """
        SELECT DISTINCT month FROM match_history
        WHERE player_id=? AND season=? AND month IS NOT NULL
        ORDER BY month ASC
        """,
        (str(player_id), season),
    )
    return [row[0] for row in cursor.fetchall()]


def trim_match_history(
    cursor: sqlite3.Cursor,
    player_id: str | int,
    keep: int | None = MAX_HISTORY_PER_PLAYER,
) -> None:
    """Keep only the ``keep`` most recent match_history rows for a player.

    Match history storage is unbounded by default (``keep=None``, i.e. no
    rows are ever evicted). Pass an explicit ``keep`` count to cap it again.

    When capped, once a player would have more than ``keep`` rows stored,
    the oldest rows beyond the limit are deleted - equivalent to evicting
    match #1 and shifting #2 -> #1, #3 -> #2, ... since rows are always read
    back ordered by recency.
    """
    if keep is None:
        return

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


def rename_opponent_in_match_history(
    cursor: sqlite3.Cursor, old_name: str, new_name: str
) -> None:
    """Rename opponent_name in match_history rows when a player's display
    name changes, so -history keeps showing a renamed player's current
    name for matches recorded before the rename - mirrors
    `rename_opponent_in_h2h`. Unlike h2h_stats, match_history has no
    per-(player, opponent_name) uniqueness to worry about, so a plain
    UPDATE is enough."""
    if not old_name or not new_name or old_name == new_name:
        return
    cursor.execute(
        "UPDATE match_history SET opponent_name=? WHERE opponent_name=?",
        (new_name, old_name),
    )