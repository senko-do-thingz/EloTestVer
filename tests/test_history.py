"""Tests for cogs/history.py's private helpers - specifically
_previous_season_month(), which had no test coverage before this file and
turned out to have two related bugs (see the regression tests below):

1. It only ever returned ``(season, month - 1)`` for the same-season case,
   without checking that month actually has any recorded matches. A player
   who skips a whole month (no matches at all that month) would silently
   get NO "previous month" data - the Elo graph's carry-over anchor point
   and "vs last month" stats would disappear - even though real prior data
   exists just one more month back.
2. The season-rollover fallback only ever checked ``season - 1``, so a
   player who skipped an ENTIRE season had the same problem one level up.
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
    create_match_history_table(cur)
    conn.commit()
    yield conn, cur
    conn.close()


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

    def test_skipped_month_falls_back_to_last_played_month(self, history_db):
        # Regression test for bug #1: month 2 has NO matches at all, so
        # asking for month 3's "previous month" must skip past the empty
        # month 2 and land on month 1, not silently return nothing.
        conn, cur = history_db
        _add(cur, 1, 1, 1000)
        _add(cur, 1, 3, 3000)
        conn.commit()
        assert _previous_season_month(cur, "1", 1, 3) == (1, 1)

    def test_multiple_earlier_months_picks_most_recent(self, history_db):
        conn, cur = history_db
        _add(cur, 1, 1, 1000)
        _add(cur, 1, 2, 2000)
        _add(cur, 1, 5, 5000)
        conn.commit()
        assert _previous_season_month(cur, "1", 1, 7) == (1, 5)

    def test_season_rollover_finds_last_month_of_prior_season(self, history_db):
        conn, cur = history_db
        _add(cur, 1, 5, 1000)
        conn.commit()
        assert _previous_season_month(cur, "1", 2, 1) == (1, 5)

    def test_fully_skipped_season_falls_back_further(self, history_db):
        # Regression test for bug #2: season 2 has NO matches at all, so
        # asking from season 3 must walk back past it to season 1's last
        # month rather than stopping at the (empty) season 2.
        conn, cur = history_db
        _add(cur, 1, 2, 1000)
        conn.commit()
        assert _previous_season_month(cur, "1", 3, 1) == (1, 2)

    def test_picks_closest_earlier_season_not_furthest(self, history_db):
        conn, cur = history_db
        _add(cur, 1, 2, 1000)
        _add(cur, 2, 4, 2000)
        conn.commit()
        assert _previous_season_month(cur, "1", 4, 1) == (2, 4)