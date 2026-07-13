"""Tests for cogs/history.py's private helpers - specifically
_previous_season_month(), which only checks the single month/season
immediately adjacent to the one given, and returns None (shown to the user
as "No data available") rather than walking back further through empty
months/seasons - see Q6 in the accompanying design discussion.
"""

import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.match_history_db import add_match_history_row, create_match_history_table, next_match_id
from cogs.history import _previous_season_month


@pytest.fixture
def history_db():
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys=ON")
    cur = conn.cursor()
    cur.execute(
        "CREATE TABLE players (id TEXT PRIMARY KEY, name TEXT NOT NULL, matches INTEGER DEFAULT 0)"
    )
    cur.execute("INSERT INTO players (id, name) VALUES ('1', 'Alice')")
    cur.execute(
        "CREATE TABLE settings (guild_id INTEGER PRIMARY KEY, max_month INTEGER DEFAULT NULL)"
    )
    create_match_history_table(cur)
    conn.commit()
    yield conn, cur
    conn.close()


def _set_max_month(cur, max_month):
    cur.execute("DELETE FROM settings")
    cur.execute("INSERT INTO settings (guild_id, max_month) VALUES (1, ?)", (max_month,))


def _add(cur, season, month, played_at):
    add_match_history_row(
        cur,
        match_id=next_match_id(cur),
        player_id="1",
        opponent_name="Bob",
        wins=1,
        losses=0,
        elo_init=500,
        elo_final=520,
        played_at=played_at,
        season=season,
        month=month,
    )


class TestPreviousSeasonMonth:
    def test_no_data_anywhere_returns_none(self, history_db):
        conn, cur = history_db
        assert _previous_season_month(cur, "1", 1, 1) is None

    def test_players_very_first_month_returns_none(self, history_db):
        conn, cur = history_db
        _add(cur, 1, 1, 1000)
        conn.commit()
        assert _previous_season_month(cur, "1", 1, 1) is None

    def test_normal_month_minus_one_case(self, history_db):
        conn, cur = history_db
        _add(cur, 1, 1, 1000)
        _add(cur, 1, 2, 2000)
        conn.commit()
        assert _previous_season_month(cur, "1", 1, 2) == (1, 1)

    def test_skipped_month_returns_none_instead_of_walking_back(self, history_db):
        # Month 2 has NO matches at all - the adjacent-only lookup must
        # NOT skip past it to month 1; it should report "no data" so the
        # graph shows an explicit "No data available" label.
        conn, cur = history_db
        _add(cur, 1, 1, 1000)
        _add(cur, 1, 3, 3000)
        conn.commit()
        assert _previous_season_month(cur, "1", 1, 3) is None

    def test_season_rollover_uses_configured_max_month(self, history_db):
        conn, cur = history_db
        _set_max_month(cur, 5)
        _add(cur, 1, 5, 1000)
        conn.commit()
        assert _previous_season_month(cur, "1", 2, 1) == (1, 5)

    def test_season_rollover_without_max_month_returns_none(self, history_db):
        conn, cur = history_db
        _add(cur, 1, 5, 1000)
        conn.commit()
        assert _previous_season_month(cur, "1", 2, 1) is None

    def test_season_rollover_last_month_empty_returns_none(self, history_db):
        # max_month is configured as 5, but season 1 has no data in month 5
        # specifically - the adjacent slot is empty, so no walking back to
        # month 2 (where data actually exists).
        conn, cur = history_db
        _set_max_month(cur, 5)
        _add(cur, 1, 2, 1000)
        conn.commit()
        assert _previous_season_month(cur, "1", 3, 1) is None