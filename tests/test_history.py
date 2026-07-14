"""Tests for cogs/history.py's private helpers - specifically
_previous_season_month(), which only checks the single month/season
immediately adjacent to the one given, and returns None (shown to the user
as "No data available") rather than walking back further through empty
months/seasons - see Q6 in the accompanying design discussion.
"""

import calendar
import os
import sqlite3
import sys
from datetime import date, datetime, time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.match_history_db import (
    add_match_history_row,
    create_match_history_table,
    get_full_match_history_by_season_month,
    next_match_id,
)
from utils.elo_graph import build_calendar_series
from cogs.history import _compute_rest_gaps_for_graph, _previous_season_month


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


def _ts_for_day(day: date) -> int:
    """Epoch seconds (UTC) for noon on the given calendar day - matches
    the helper in tests/test_elo_graph.py so timestamps map to a
    predictable calendar day regardless of local timezone rounding."""
    return calendar.timegm(datetime.combine(day, time(12, 0)).timetuple())


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


class TestComputeRestGapsForGraph:
    """Deep tests for the -graph rest-period wiring in cogs/history.py -
    exercises the exact same real-DB round trip
    (add_match_history_row -> get_full_match_history_by_season_month ->
    _compute_rest_gaps_for_graph) that HistoryCog's graph command runs,
    rather than only the pure-function unit tests in test_elo_graph.py.
    """

    def _rows_chronological(self, cur, season, month):
        rows_desc = get_full_match_history_by_season_month(cur, "1", season, month)
        return list(reversed(rows_desc))

    def test_no_matches_returns_empty(self, history_db):
        conn, cur = history_db
        rows = self._rows_chronological(cur, 1, 1)
        assert rows == []
        assert _compute_rest_gaps_for_graph(rows) == []

    def test_consecutive_days_no_gap(self, history_db):
        conn, cur = history_db
        _add(cur, 1, 7, _ts_for_day(date(2026, 7, 5)))
        _add(cur, 1, 7, _ts_for_day(date(2026, 7, 6)))
        _add(cur, 1, 7, _ts_for_day(date(2026, 7, 7)))
        conn.commit()
        rows = self._rows_chronological(cur, 1, 7)
        assert _compute_rest_gaps_for_graph(rows) == []

    def test_single_skipped_day_not_flagged(self, history_db):
        # 1 empty day between two active days - below the default
        # 2-day threshold, treated as normal play, not a rest period.
        conn, cur = history_db
        _add(cur, 1, 7, _ts_for_day(date(2026, 7, 5)))
        _add(cur, 1, 7, _ts_for_day(date(2026, 7, 7)))
        conn.commit()
        rows = self._rows_chronological(cur, 1, 7)
        assert _compute_rest_gaps_for_graph(rows) == []

    def test_multi_day_gap_flagged_and_aligned_with_axis_ticks(self, history_db):
        conn, cur = history_db
        _add(cur, 1, 7, _ts_for_day(date(2026, 7, 5)))
        _add(cur, 1, 7, _ts_for_day(date(2026, 7, 12)))
        conn.commit()
        rows = self._rows_chronological(cur, 1, 7)

        rest_gaps = _compute_rest_gaps_for_graph(rows)
        assert len(rest_gaps) == 1
        start_frac, end_frac = rest_gaps[0]
        assert 0.0 <= start_frac < end_frac <= 1.0

        # The band's edges must line up exactly with the same x-axis
        # fractions build_calendar_series() gives the two active days -
        # otherwise the shaded band drawn by rendering.py would visibly
        # drift from the elo line's own points.
        _, axis_ticks = build_calendar_series(rows)
        tick_frac = dict((label, frac) for frac, label in axis_ticks)
        assert start_frac == pytest.approx(tick_frac["07-05"])
        assert end_frac == pytest.approx(tick_frac["07-12"])

    def test_multiple_gaps_in_one_month(self, history_db):
        conn, cur = history_db
        _add(cur, 1, 7, _ts_for_day(date(2026, 7, 1)))
        _add(cur, 1, 7, _ts_for_day(date(2026, 7, 5)))  # gap #1: 3 empty days
        _add(cur, 1, 7, _ts_for_day(date(2026, 7, 6)))
        _add(cur, 1, 7, _ts_for_day(date(2026, 7, 20)))  # gap #2: 13 empty days
        conn.commit()
        rows = self._rows_chronological(cur, 1, 7)

        rest_gaps = _compute_rest_gaps_for_graph(rows)
        assert len(rest_gaps) == 2
        # Sorted chronologically, first gap's band must end before the
        # second gap's band starts.
        assert rest_gaps[0][1] < rest_gaps[1][0]

    def test_real_previous_month_anchor_can_open_a_gap(self, history_db):
        # A genuine previous-month carry-over match (last match of the
        # prior month) that's several days before this month's first
        # match must itself open a rest-period band spanning the month
        # boundary - see the "REAL anchor_date" note in
        # _compute_rest_gaps_for_graph()'s docstring.
        conn, cur = history_db
        _add(cur, 1, 6, _ts_for_day(date(2026, 6, 20)))
        _add(cur, 1, 7, _ts_for_day(date(2026, 7, 8)))
        conn.commit()
        rows = self._rows_chronological(cur, 1, 7)
        anchor_date = date(2026, 6, 20)

        rest_gaps = _compute_rest_gaps_for_graph(rows, anchor_date=anchor_date)
        assert len(rest_gaps) == 1
        assert rest_gaps[0][0] == pytest.approx(0.0)  # anchor sits at the axis start

    def test_synthetic_anchor_must_not_be_passed_here(self, history_db):
        # Sanity check for the documented contract: calling with
        # anchor_date=None (the correct behavior when there is no real
        # previous month, i.e. HistoryCog's own anchor_date variable is
        # None even though series_anchor_date/anchor_before_first_match
        # may be set for build_calendar_series()) must NOT fabricate a
        # gap before the first match.
        conn, cur = history_db
        _add(cur, 1, 7, _ts_for_day(date(2026, 7, 20)))
        conn.commit()
        rows = self._rows_chronological(cur, 1, 7)
        assert _compute_rest_gaps_for_graph(rows, anchor_date=None) == []

    def test_same_day_multiple_sets_no_gap(self, history_db):
        conn, cur = history_db
        base_ts = _ts_for_day(date(2026, 7, 5))
        _add(cur, 1, 7, base_ts)
        _add(cur, 1, 7, base_ts + 120)
        _add(cur, 1, 7, base_ts + 240)
        conn.commit()
        rows = self._rows_chronological(cur, 1, 7)
        assert _compute_rest_gaps_for_graph(rows) == []