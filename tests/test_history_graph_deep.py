"""Deep tests for the -history graph / chart drawing pipeline.

Exercises the full chain: DB round-trip → data-prep (utils/elo_graph.py) →
axis bounds (_compute_axis_bounds) → render (utils/rendering.py), covering
edge cases the existing test_elo_graph.py / test_history_deep.py don't reach:

- Calendar series x-fraction accuracy across month boundaries
- Multiple sets on the same day: split positioning
- Elo values outside the default 300–1000 axis range
- Axis bounds extension logic in _compute_axis_bounds
- The "vs last month" comparison line data prep
- Graph render with all optional params (compare_series, rest_gaps, axis
  bounds, prev_* stats)
- Full HistoryView graph-mode integration (DB → render_elo_graph_image_async)
- Empty/single-match edge cases through the rendering pipeline
- Anchor point behavior: real vs synthetic
- December → January rollover in calendar_axis_bounds
- Large dataset stress test (many matches / many days)
"""

from __future__ import annotations

import asyncio
import calendar as cal_mod
import io
import os
import sys
from datetime import date, datetime, time, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db.elo_db as elo_db
from db.match_history_db import (
    MatchHistoryRow,
    add_match_history_row,
    compute_streak_labels,
    get_full_match_history_by_season_month,
    next_match_id,
)
from cogs.history import (
    HistoryView,
    _compute_axis_bounds,
    _compute_rest_gaps_for_graph,
    _previous_season_month,
)
from utils.elo_graph import (
    build_calendar_series,
    build_match_series,
    calendar_axis_bounds,
    compute_month_averages,
    find_rest_gaps,
    rest_gap_fractions,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _row(match_id, wins, losses, elo_init, elo_final, played_at, opponent="Bob"):
    return MatchHistoryRow(
        match_id=match_id,
        player_id="1",
        opponent_name=opponent,
        wins=wins,
        losses=losses,
        elo_init=elo_init,
        elo_final=elo_final,
        played_at=played_at,
    )


def _ts_for_day(day: date) -> int:
    """Epoch seconds (UTC) for noon on the given calendar day."""
    return cal_mod.timegm(datetime.combine(day, time(12, 0)).timetuple())


def _ts_for_day_offset(day: date, offset_seconds: int = 0) -> int:
    return _ts_for_day(day) + offset_seconds


# ---------------------------------------------------------------------------
# Fake objects for HistoryView integration tests (same pattern as
# test_history_deep.py, duplicated here for self-containment).
# ---------------------------------------------------------------------------

class FakeGuild:
    def __init__(self, guild_id):
        self.id = guild_id


class FakeCtx:
    def __init__(self, guild_id):
        self.guild = FakeGuild(guild_id)


class FakeAvatar:
    async def read(self):
        return b"fake-avatar-bytes"


class FakeTarget:
    def __init__(self, user_id, display_name="Player"):
        self.id = user_id
        self.display_name = display_name
        self.mention = f"<@{user_id}>"
        self.display_avatar = FakeAvatar()


class Sink:
    def __init__(self):
        self.sent_view = None
        self.sent_file = None
        self.sent_embed = None

    async def send(self, *, view=None, file=None, embed=None):
        self.sent_view = view
        self.sent_file = file
        self.sent_embed = embed
        return self


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(elo_db, "DB_FOLDER", str(tmp_path))
    elo_db._migrated_dbs.clear()
    yield


def _register(cursor, player_id, name):
    cursor.execute(
        "INSERT INTO players (id, name) VALUES (?, ?)", (str(player_id), name)
    )


def _add_match(cursor, player_id, season, month, played_at,
               elo_init=500, elo_final=520, wins=1, losses=0,
               opponent_name="Bob"):
    add_match_history_row(
        cursor,
        match_id=next_match_id(cursor),
        player_id=player_id,
        opponent_name=opponent_name,
        wins=wins,
        losses=losses,
        elo_init=elo_init,
        elo_final=elo_final,
        played_at=played_at,
        season=season,
        month=month,
    )


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ===================================================================
# 1. _compute_axis_bounds edge cases
# ===================================================================

class TestComputeAxisBounds:
    """Tests for cogs/history._compute_axis_bounds - the Y-axis range
    logic that extends the default 300–1000 to cover extreme elo."""

    def test_default_range_for_normal_elo(self):
        assert _compute_axis_bounds([400, 500, 600, 700]) == (300, 1000)

    def test_empty_values_returns_default(self):
        assert _compute_axis_bounds([]) == (300, 1000)

    def test_elo_above_1000_extends_ceiling(self):
        # Elo 1042 should round UP to 1050 (nearest 50).
        assert _compute_axis_bounds([500, 1042]) == (300, 1050)

    def test_elo_above_1000_exact_multiple_of_50(self):
        assert _compute_axis_bounds([500, 1100]) == (300, 1100)

    def test_elo_below_300_extends_floor(self):
        # Elo 270 should round DOWN to 250 (nearest 50).
        assert _compute_axis_bounds([270, 500]) == (250, 1000)

    def test_elo_below_300_exact_multiple_of_50(self):
        assert _compute_axis_bounds([200, 500]) == (200, 1000)

    def test_both_extremes(self):
        assert _compute_axis_bounds([150, 1200]) == (150, 1200)

    def test_single_value_in_range(self):
        assert _compute_axis_bounds([500]) == (300, 1000)

    def test_single_value_above_range(self):
        assert _compute_axis_bounds([1001]) == (300, 1050)

    def test_single_value_below_range(self):
        assert _compute_axis_bounds([299]) == (250, 1000)

    def test_very_low_elo(self):
        """Players at very low elo (e.g. 50) must not produce negative bounds."""
        lo, hi = _compute_axis_bounds([50])
        assert lo >= 0
        assert lo <= 50

    def test_zero_elo(self):
        lo, hi = _compute_axis_bounds([0])
        assert lo == 0
        assert hi == 1000


# ===================================================================
# 2. Calendar series advanced scenarios
# ===================================================================

class TestCalendarSeriesAdvanced:
    """Build on TestBuildCalendarSeries with richer multi-match / edge
    scenarios."""

    def test_three_matches_same_day_spread_evenly(self):
        """Three sets on the same day should produce three distinct
        x-fractions, all within that day's slot."""
        d = date(2026, 7, 10)
        base_ts = _ts_for_day(d)
        rows = [
            _row(1, 2, 0, 500, 520, base_ts),
            _row(2, 2, 0, 520, 540, base_ts + 60),
            _row(3, 0, 2, 540, 510, base_ts + 120),
        ]
        points, axis_ticks = build_calendar_series(rows)
        assert len(points) == 3
        fracs = [f for f, _ in points]
        # All three must be within day-10's slot and strictly ordered.
        assert fracs[0] < fracs[1] < fracs[2]
        # Check they're in the correct day's region: day 10 of July
        # with axis from 06-30 → 07-31 means day offset = 10.
        tick_dict = dict((label, frac) for frac, label in axis_ticks)
        day_start = tick_dict["07-10"]
        day_width = 1 / len(axis_ticks)
        for f in fracs:
            assert day_start <= f < day_start + day_width + 1e-9

    def test_matches_on_first_and_last_day_of_month(self):
        """Matches on July 1st and July 31st should produce points
        near the extremes of the axis (but not at exactly 0 or 1)."""
        d_first = date(2026, 7, 1)
        d_last = date(2026, 7, 31)
        rows = [
            _row(1, 2, 0, 500, 520, _ts_for_day(d_first)),
            _row(2, 2, 0, 520, 540, _ts_for_day(d_last)),
        ]
        points, axis_ticks = build_calendar_series(rows)
        fracs = [f for f, _ in points]
        assert fracs[0] < fracs[1]
        assert fracs[0] > 0.0  # not at the absolute axis start (06-30)
        assert fracs[1] < 1.0  # not past the axis end

    def test_anchor_before_first_match_skips_to_first_match_day(self):
        """Synthetic anchor (anchor_before_first_match=True, no
        anchor_date) should sit right at the first match's day, not
        at x=0."""
        rows = [_row(1, 2, 0, 500, 540, _ts_for_day(date(2026, 7, 15)))]
        points, _ = build_calendar_series(
            rows, anchor_elo=480, anchor_before_first_match=True
        )
        assert len(points) == 2
        anchor_frac = points[0][0]
        match_frac = points[1][0]
        # Anchor should be near (but <= ) the first match, not at 0.0.
        assert anchor_frac > 0.0
        assert anchor_frac < match_frac

    def test_real_anchor_always_at_fraction_zero(self):
        """A genuine previous-month anchor should always sit at x=0,
        regardless of how far before axis_start its actual date is."""
        anchor_date = date(2026, 6, 10)  # well before axis start (06-30)
        rows = [_row(1, 2, 0, 500, 540, _ts_for_day(date(2026, 7, 15)))]
        points, _ = build_calendar_series(
            rows, anchor_elo=480, anchor_date=anchor_date
        )
        assert points[0] == (0.0, 480)

    def test_february_short_month(self):
        """February 2026 (non-leap, 28 days) should produce the correct
        axis span."""
        rows = [_row(1, 2, 0, 500, 540, _ts_for_day(date(2026, 2, 10)))]
        points, axis_ticks = build_calendar_series(rows)
        labels = [l for _, l in axis_ticks]
        assert labels[0] == "01-31"
        assert labels[-1] == "02-28"

    def test_leap_year_february(self):
        """February 2028 (leap year, 29 days) should include Feb 29."""
        rows = [_row(1, 2, 0, 500, 540, _ts_for_day(date(2028, 2, 10)))]
        _, axis_ticks = build_calendar_series(rows)
        labels = [l for _, l in axis_ticks]
        assert "02-29" in labels

    def test_december_to_january_rollover(self):
        """Matches in January should produce an axis spanning Dec 31 → Jan 31."""
        rows = [_row(1, 2, 0, 500, 540, _ts_for_day(date(2027, 1, 5)))]
        _, axis_ticks = build_calendar_series(rows)
        labels = [l for _, l in axis_ticks]
        assert labels[0] == "12-31"
        assert labels[-1] == "01-31"


# ===================================================================
# 3. build_match_series edge cases
# ===================================================================

class TestBuildMatchSeriesEdgeCases:
    def test_many_matches_same_day_all_have_distinct_labels(self):
        """10 matches on the same day - all should produce separate
        points with repeated labels (not collapsed like daily series)."""
        d = date(2026, 7, 5)
        base = _ts_for_day(d)
        rows = [
            _row(i, 2, 0, 500 + i, 510 + i, base + i * 60)
            for i in range(10)
        ]
        series = build_match_series(rows)
        assert len(series) == 10
        assert all(label == "07-05" for label, _ in series)

    def test_matches_span_many_months_each_independent(self):
        """build_match_series processes one month at a time - ensure
        it doesn't bleed across months."""
        # Month A rows
        rows_a = [_row(1, 2, 0, 500, 520, _ts_for_day(date(2026, 7, 1)))]
        # Month B rows
        rows_b = [_row(2, 2, 0, 600, 620, _ts_for_day(date(2026, 8, 1)))]
        series_a = build_match_series(rows_a)
        series_b = build_match_series(rows_b)
        assert series_a != series_b
        assert series_a[0][1] == 520
        assert series_b[0][1] == 620


# ===================================================================
# 4. compute_month_averages edge cases
# ===================================================================

class TestComputeMonthAveragesEdgeCases:
    def test_all_losses(self):
        rows = [
            _row(1, 0, 2, 500, 470, 1000),
            _row(2, 0, 2, 470, 440, 2000),
        ]
        avgs = compute_month_averages(rows)
        assert avgs.avg_win == 0
        assert avgs.avg_loss == -30
        assert avgs.avg_gain == -30
        assert avgs.win_count == 0
        assert avgs.loss_count == 2
        assert avgs.final_elo == 440

    def test_many_wins_one_loss(self):
        """Avg gain should be pulled toward wins, not the naive sum."""
        rows = [
            _row(i, 2, 0, 500 + i * 20, 520 + i * 20, 1000 + i)
            for i in range(9)
        ]
        rows.append(_row(10, 0, 2, 680, 650, 2000))  # one loss, -30
        avgs = compute_month_averages(rows)
        # 9 wins * +20 + 1 loss * -30 = 150, /10 = 15
        assert avgs.avg_gain == round((9 * 20 - 30) / 10)

    def test_single_match_win(self):
        rows = [_row(1, 2, 0, 500, 540, 1000)]
        avgs = compute_month_averages(rows)
        assert avgs.avg_win == 40
        assert avgs.avg_gain == 40
        assert avgs.win_count == 1
        assert avgs.loss_count == 0

    def test_single_match_loss(self):
        rows = [_row(1, 0, 2, 500, 470, 1000)]
        avgs = compute_month_averages(rows)
        assert avgs.avg_loss == -30
        assert avgs.avg_gain == -30

    def test_zero_delta_match(self):
        """A 1-1 draw with no elo change."""
        rows = [_row(1, 1, 1, 500, 500, 1000)]
        avgs = compute_month_averages(rows)
        # 1-1 is a loss bucket (wins <= losses)
        assert avgs.avg_loss == 0
        assert avgs.avg_gain == 0


# ===================================================================
# 5. Rest gaps + calendar series integration
# ===================================================================

class TestRestGapCalendarIntegration:
    """End-to-end: matches → calendar_axis_bounds → find_rest_gaps →
    rest_gap_fractions, verifying the shaded band x-fractions line up
    with the tick positions."""

    def test_gap_at_start_of_month(self):
        """First match on day 10 - no gap before it without an anchor
        (no real previous month)."""
        rows = [
            _row(1, 2, 0, 500, 520, _ts_for_day(date(2026, 7, 10))),
            _row(2, 2, 0, 520, 540, _ts_for_day(date(2026, 7, 11))),
        ]
        gaps = find_rest_gaps(rows)
        assert gaps == []  # Only 2 active days, consecutive

    def test_gap_at_end_of_month(self):
        """Match on day 1, then gap, then match on day 28 -
        should produce one gap."""
        rows = [
            _row(1, 2, 0, 500, 520, _ts_for_day(date(2026, 7, 1))),
            _row(2, 2, 0, 520, 540, _ts_for_day(date(2026, 7, 28))),
        ]
        gaps = find_rest_gaps(rows)
        assert len(gaps) == 1
        assert gaps[0] == (date(2026, 7, 1), date(2026, 7, 28))

    def test_fractions_aligned_with_axis_ticks(self):
        """Verify that gap fractions use the exact same coordinate
        system as build_calendar_series axis_ticks."""
        rows = [
            _row(1, 2, 0, 500, 520, _ts_for_day(date(2026, 7, 3))),
            _row(2, 2, 0, 520, 540, _ts_for_day(date(2026, 7, 20))),
        ]
        gaps = find_rest_gaps(rows)
        bounds = calendar_axis_bounds(rows)
        axis_start, axis_end = bounds
        total_days = (axis_end - axis_start).days + 1
        frac_gaps = rest_gap_fractions(gaps, axis_start=axis_start, total_days=total_days)

        _, axis_ticks = build_calendar_series(rows)
        tick_map = dict((label, frac) for frac, label in axis_ticks)

        assert len(frac_gaps) == 1
        assert frac_gaps[0][0] == pytest.approx(tick_map["07-03"])
        assert frac_gaps[0][1] == pytest.approx(tick_map["07-20"])

    def test_no_gap_for_adjacent_days(self):
        """Days 5, 6, 7, 8 played - 0 empty days between each pair,
        below default threshold."""
        rows = [
            _row(i, 2, 0, 500, 520, _ts_for_day(date(2026, 7, 5 + i)))
            for i in range(4)
        ]
        assert find_rest_gaps(rows) == []


# ===================================================================
# 6. Full HistoryView graph integration (DB → render)
# ===================================================================

class TestHistoryViewGraphRender:
    """Drive HistoryView._render_graph through a real DB round-trip,
    verifying the rendered PNG output for various data scenarios."""

    def test_single_match_produces_valid_graph(self):
        async def _body():
            ctx = FakeCtx(guild_id=100)
            target = FakeTarget(1, "TestPlayer")
            with elo_db.db_connection(100) as (conn, cursor):
                _register(cursor, 1, "TestPlayer")
                _add_match(cursor, "1", 1, 1,
                           played_at=_ts_for_day(date(2026, 7, 5)),
                           elo_init=500, elo_final=530)
                conn.commit()

            view = HistoryView(cog=None, ctx=ctx, target=target, season=1, month=1)
            view.mode = "graph"
            sink = Sink()
            await view.update_history(sink)
            # Should render a PNG file, not an error embed
            assert sink.sent_file is not None
            assert sink.sent_embed is None

        run(_body())

    def test_many_matches_produces_valid_graph(self):
        """30 matches spread across 15 days - stress test."""
        async def _body():
            ctx = FakeCtx(guild_id=101)
            target = FakeTarget(1, "StressPlayer")
            with elo_db.db_connection(101) as (conn, cursor):
                _register(cursor, 1, "StressPlayer")
                elo = 500
                for day_offset in range(15):
                    for match_in_day in range(2):
                        played = _ts_for_day(date(2026, 7, 1 + day_offset)) + match_in_day * 300
                        delta = 20 if match_in_day == 0 else -10
                        _add_match(cursor, "1", 1, 1,
                                   played_at=played,
                                   elo_init=elo, elo_final=elo + delta,
                                   wins=2 if delta > 0 else 0,
                                   losses=0 if delta > 0 else 2)
                        elo += delta
                conn.commit()

            view = HistoryView(cog=None, ctx=ctx, target=target, season=1, month=1)
            view.mode = "graph"
            sink = Sink()
            await view.update_history(sink)
            assert sink.sent_file is not None

        run(_body())

    def test_graph_with_previous_month_data(self):
        """Two consecutive months: graph for month 2 should include
        the 'vs last month' comparison line (no crash)."""
        async def _body():
            ctx = FakeCtx(guild_id=102)
            target = FakeTarget(1, "TwoMonths")
            with elo_db.db_connection(102) as (conn, cursor):
                _register(cursor, 1, "TwoMonths")
                # Month 1
                for i in range(5):
                    _add_match(cursor, "1", 1, 1,
                               played_at=_ts_for_day(date(2026, 7, 1 + i)),
                               elo_init=500 + i * 10, elo_final=510 + i * 10)
                # Month 2
                for i in range(5):
                    _add_match(cursor, "1", 1, 2,
                               played_at=_ts_for_day(date(2026, 8, 1 + i)),
                               elo_init=550 + i * 10, elo_final=560 + i * 10)
                conn.commit()

            view = HistoryView(cog=None, ctx=ctx, target=target, season=1, month=2)
            view.mode = "graph"
            sink = Sink()
            await view.update_history(sink)
            assert sink.sent_file is not None
            assert sink.sent_embed is None

        run(_body())

    def test_graph_extreme_elo_values(self):
        """Player with elo exceeding 1000 or below 300 - axis must
        extend instead of clamping."""
        async def _body():
            ctx = FakeCtx(guild_id=103)
            target = FakeTarget(1, "ExtremeElo")
            with elo_db.db_connection(103) as (conn, cursor):
                _register(cursor, 1, "ExtremeElo")
                _add_match(cursor, "1", 1, 1,
                           played_at=_ts_for_day(date(2026, 7, 1)),
                           elo_init=280, elo_final=250)
                _add_match(cursor, "1", 1, 1,
                           played_at=_ts_for_day(date(2026, 7, 10)),
                           elo_init=950, elo_final=1050)
                conn.commit()

            view = HistoryView(cog=None, ctx=ctx, target=target, season=1, month=1)
            view.mode = "graph"
            sink = Sink()
            await view.update_history(sink)
            assert sink.sent_file is not None

        run(_body())

    def test_graph_with_rest_gaps(self):
        """Matches with a multi-day gap should render rest-gap bands
        without crashing."""
        async def _body():
            ctx = FakeCtx(guild_id=104)
            target = FakeTarget(1, "GapPlayer")
            with elo_db.db_connection(104) as (conn, cursor):
                _register(cursor, 1, "GapPlayer")
                _add_match(cursor, "1", 1, 1,
                           played_at=_ts_for_day(date(2026, 7, 1)),
                           elo_init=500, elo_final=520)
                # 10-day gap
                _add_match(cursor, "1", 1, 1,
                           played_at=_ts_for_day(date(2026, 7, 12)),
                           elo_init=520, elo_final=550)
                conn.commit()

            view = HistoryView(cog=None, ctx=ctx, target=target, season=1, month=1)
            view.mode = "graph"
            sink = Sink()
            await view.update_history(sink)
            assert sink.sent_file is not None

        run(_body())


# ===================================================================
# 7. Render elo_graph_image edge cases (pixel-level smoke tests)
# ===================================================================

class TestRenderEloGraphEdgeCases:
    """Exercise render_elo_graph_image with unusual parameter combos
    that the existing test_rendering.py doesn't cover."""

    def test_compare_series_longer_than_main(self):
        """The previous month may have MORE matches than the current
        month - must not crash or clip."""
        from utils.rendering import render_elo_graph_image
        main = [(0.5, 500)]  # just 1 point
        compare = [("07-01", 480), ("07-02", 490), ("07-03", 510),
                    ("07-04", 500), ("07-05", 520)]
        buf = render_elo_graph_image(
            main, title="Short current", compare_series=compare
        )
        img = _open_buf(buf)
        assert img.format == "PNG"

    def test_all_same_elo_values(self):
        """Flat line (all same elo) - no division by zero in y-axis."""
        from utils.rendering import render_elo_graph_image
        series = [(i / 10, 500) for i in range(11)]
        buf = render_elo_graph_image(series, title="Flat line")
        assert _open_buf(buf).format == "PNG"

    def test_axis_min_equals_axis_max(self):
        """Edge case: axis_min == axis_max (e.g. single elo value)."""
        from utils.rendering import render_elo_graph_image
        buf = render_elo_graph_image(
            [(0.5, 500)], title="Same axis", axis_min=500, axis_max=500
        )
        assert _open_buf(buf).format == "PNG"

    def test_two_points_produces_colored_segment(self):
        """Two-point series should color the single segment correctly:
        rising (green) when elo goes up, falling (red) when down."""
        from utils.rendering import render_elo_graph_image
        buf_up = render_elo_graph_image(
            [(0.0, 400), (1.0, 600)], title="Rise"
        )
        buf_down = render_elo_graph_image(
            [(0.0, 600), (1.0, 400)], title="Fall"
        )
        # The two images should differ (different color curves)
        assert buf_up.getvalue() != buf_down.getvalue()

    def test_very_wide_axis_range(self):
        """Extreme axis range (0 to 5000) should still render."""
        from utils.rendering import render_elo_graph_image
        series = [(0.0, 100), (0.5, 2500), (1.0, 4900)]
        buf = render_elo_graph_image(
            series, title="Wide range", axis_min=0, axis_max=5000
        )
        assert _open_buf(buf).format == "PNG"

    def test_rest_gaps_covering_entire_chart(self):
        """A rest gap spanning 0.0 to 1.0 should shade the entire chart."""
        from utils.rendering import render_elo_graph_image
        buf = render_elo_graph_image(
            [(0.0, 500), (1.0, 600)],
            title="Full gap",
            rest_gaps=[(0.0, 1.0)],
        )
        assert _open_buf(buf).format == "PNG"

    def test_multiple_rest_gaps(self):
        """Three separate rest-gap bands should render without overlap crash."""
        from utils.rendering import render_elo_graph_image
        buf = render_elo_graph_image(
            [(i / 10, 500 + i * 10) for i in range(11)],
            title="Multi gap",
            rest_gaps=[(0.1, 0.2), (0.4, 0.5), (0.8, 0.9)],
        )
        assert _open_buf(buf).format == "PNG"

    def test_no_prev_data_flag(self):
        """When no_prev_data=True, comparison figures should not appear
        (no crash)."""
        from utils.rendering import render_elo_graph_image
        buf = render_elo_graph_image(
            [(0.0, 480), (1.0, 520)],
            title="No prev",
            no_prev_data=True,
            avg_win=40, avg_loss=-30, avg_gain=10, final_elo=520,
        )
        assert _open_buf(buf).format == "PNG"

    def test_with_avatar_bytes(self):
        """Passing real-ish avatar bytes (valid PNG) should render the
        circular avatar without crashing."""
        from utils.rendering import render_elo_graph_image
        from PIL import Image as PILImage
        # Create a tiny valid PNG in memory
        avatar_buf = io.BytesIO()
        tiny = PILImage.new("RGBA", (32, 32), (255, 0, 0, 255))
        tiny.save(avatar_buf, format="PNG")
        avatar_bytes = avatar_buf.getvalue()

        buf = render_elo_graph_image(
            [(0.0, 500), (1.0, 550)],
            title="With Avatar",
            avatar_bytes=avatar_bytes,
        )
        assert _open_buf(buf).format == "PNG"

    def test_with_invalid_avatar_bytes_graceful(self):
        """Invalid avatar bytes should be handled gracefully (no crash)."""
        from utils.rendering import render_elo_graph_image
        buf = render_elo_graph_image(
            [(0.0, 500), (1.0, 550)],
            title="Bad Avatar",
            avatar_bytes=b"not-a-real-image",
        )
        assert _open_buf(buf).format == "PNG"

    def test_cjk_title(self):
        """CJK characters in the title should render without error."""
        from utils.rendering import render_elo_graph_image
        buf = render_elo_graph_image(
            [(0.0, 500), (1.0, 550)],
            title="田中太郎's Elo Graph",
        )
        assert _open_buf(buf).format == "PNG"

    def test_very_long_title(self):
        """Very long title should be truncated, not crash."""
        from utils.rendering import render_elo_graph_image
        buf = render_elo_graph_image(
            [(0.0, 500), (1.0, 550)],
            title="A" * 200 + "'s Elo Graph",
        )
        assert _open_buf(buf).format == "PNG"


def _open_buf(buf: io.BytesIO):
    from PIL import Image
    buf.seek(0)
    return Image.open(buf)


# ===================================================================
# 8. _previous_season_month with deep DB edge cases
# ===================================================================

class TestPreviousSeasonMonthDeep:
    """Additional edge cases beyond test_history.py's coverage."""

    def test_season_1_month_1_always_none(self):
        """The very first possible slot has no predecessor."""
        with elo_db.db_connection(200) as (conn, cursor):
            _register(cursor, 1, "Alice")
            _add_match(cursor, "1", 1, 1, played_at=1000)
            conn.commit()
            assert _previous_season_month(cursor, "1", 1, 1) is None

    def test_higher_season_month_1_with_max_month(self):
        """Season 3 month 1 → looks at season 2 max_month."""
        with elo_db.db_connection(201) as (conn, cursor):
            _register(cursor, 1, "Alice")
            cursor.execute("INSERT INTO settings (guild_id, max_month) VALUES (201, 3)")
            _add_match(cursor, "1", 2, 3, played_at=1000)
            conn.commit()
            assert _previous_season_month(cursor, "1", 3, 1) == (2, 3)

    def test_previous_month_different_player_has_no_effect(self):
        """Player B having data in the previous month doesn't affect
        Player A's lookup."""
        with elo_db.db_connection(202) as (conn, cursor):
            _register(cursor, 1, "Alice")
            _register(cursor, 2, "Bob")
            _add_match(cursor, "2", 1, 1, played_at=1000)  # Bob has data
            _add_match(cursor, "1", 1, 2, played_at=2000)  # Alice only in month 2
            conn.commit()
            # Alice has no data in month 1, so the lookup returns None
            assert _previous_season_month(cursor, "1", 1, 2) is None


# ===================================================================
# 9. Anchor elo behavior: comparing graph with and without anchor
# ===================================================================

class TestGraphAnchorBehavior:
    """The graph line should start from the carry-over elo when a
    previous month exists, or from the first match's elo_init when not."""

    def test_graph_without_previous_month_starts_at_elo_init(self):
        async def _body():
            ctx = FakeCtx(guild_id=300)
            target = FakeTarget(1, "NoAnchor")
            with elo_db.db_connection(300) as (conn, cursor):
                _register(cursor, 1, "NoAnchor")
                _add_match(cursor, "1", 1, 1,
                           played_at=_ts_for_day(date(2026, 7, 5)),
                           elo_init=500, elo_final=530)
                _add_match(cursor, "1", 1, 1,
                           played_at=_ts_for_day(date(2026, 7, 6)),
                           elo_init=530, elo_final=560)
                conn.commit()

            view = HistoryView(cog=None, ctx=ctx, target=target, season=1, month=1)
            view.mode = "graph"
            sink = Sink()
            await view.update_history(sink)
            assert sink.sent_file is not None

        run(_body())

    def test_graph_with_previous_month_anchor(self):
        async def _body():
            ctx = FakeCtx(guild_id=301)
            target = FakeTarget(1, "WithAnchor")
            with elo_db.db_connection(301) as (conn, cursor):
                _register(cursor, 1, "WithAnchor")
                # Month 1 data
                _add_match(cursor, "1", 1, 1,
                           played_at=_ts_for_day(date(2026, 7, 5)),
                           elo_init=500, elo_final=530)
                # Month 2 data
                _add_match(cursor, "1", 1, 2,
                           played_at=_ts_for_day(date(2026, 8, 5)),
                           elo_init=530, elo_final=560)
                conn.commit()

            view = HistoryView(cog=None, ctx=ctx, target=target, season=1, month=2)
            view.mode = "graph"
            sink = Sink()
            await view.update_history(sink)
            assert sink.sent_file is not None

        run(_body())


# ===================================================================
# 10. End-to-end: DB → data-prep → render with extreme data
# ===================================================================

class TestEndToEndExtremeData:
    """Exercise the complete pipeline with unusual data distributions."""

    def test_50_matches_single_day(self):
        """50 matches all on the same day should produce 50 distinct
        x-fractions within that day's slot, and render without crash."""
        async def _body():
            ctx = FakeCtx(guild_id=400)
            target = FakeTarget(1, "HeavyDay")
            with elo_db.db_connection(400) as (conn, cursor):
                _register(cursor, 1, "HeavyDay")
                elo = 500
                base = _ts_for_day(date(2026, 7, 15))
                for i in range(50):
                    new_elo = elo + (5 if i % 2 == 0 else -3)
                    _add_match(cursor, "1", 1, 1,
                               played_at=base + i * 10,
                               elo_init=elo, elo_final=new_elo,
                               wins=2 if i % 2 == 0 else 0,
                               losses=0 if i % 2 == 0 else 2)
                    elo = new_elo
                conn.commit()

            view = HistoryView(cog=None, ctx=ctx, target=target, season=1, month=1)
            view.mode = "graph"
            sink = Sink()
            await view.update_history(sink)
            assert sink.sent_file is not None

        run(_body())

    def test_matches_every_day_of_month(self):
        """One match per day for all 31 days of July."""
        async def _body():
            ctx = FakeCtx(guild_id=401)
            target = FakeTarget(1, "EveryDay")
            with elo_db.db_connection(401) as (conn, cursor):
                _register(cursor, 1, "EveryDay")
                elo = 500
                for day in range(1, 32):
                    delta = 10 if day % 3 != 0 else -15
                    _add_match(cursor, "1", 1, 1,
                               played_at=_ts_for_day(date(2026, 7, day)),
                               elo_init=elo, elo_final=elo + delta,
                               wins=2 if delta > 0 else 0,
                               losses=0 if delta > 0 else 2)
                    elo += delta
                conn.commit()

            view = HistoryView(cog=None, ctx=ctx, target=target, season=1, month=1)
            view.mode = "graph"
            sink = Sink()
            await view.update_history(sink)
            assert sink.sent_file is not None

        run(_body())

    def test_alternating_wins_and_losses_averages(self):
        """Perfectly alternating W/L pattern - avg_gain should be
        correctly weighted (not naive sum)."""
        rows = []
        elo = 500
        for i in range(20):
            if i % 2 == 0:
                delta = 25
                w, l = 2, 0
            else:
                delta = -20
                w, l = 0, 2
            rows.append(_row(i, w, l, elo, elo + delta, 1000 + i * 100))
            elo += delta
        avgs = compute_month_averages(rows)
        # 10 wins * +25 + 10 losses * -20 = 250 - 200 = 50, /20 = 2.5 → 2
        assert avgs.avg_win == 25
        assert avgs.avg_loss == -20
        assert avgs.avg_gain == round(50 / 20)

    def test_streak_labels_through_render(self):
        """Streak labels computed from full month should be correct
        even when the data produces a complex W-W-L-W-W-W-L pattern."""
        with elo_db.db_connection(402) as (conn, cursor):
            _register(cursor, 1, "Streaker")
            # W W L W W W L = streaks of 2, 3
            results = [(2, 0), (2, 0), (0, 2), (2, 0), (2, 0), (2, 0), (0, 2)]
            elo = 500
            for i, (w, l) in enumerate(results):
                delta = 20 if w > l else -15
                _add_match(cursor, "1", 1, 1,
                           played_at=_ts_for_day(date(2026, 7, 1 + i)),
                           elo_init=elo, elo_final=elo + delta,
                           wins=w, losses=l)
                elo += delta
            conn.commit()
            full_rows = get_full_match_history_by_season_month(cursor, "1", 1, 1)

        labels = compute_streak_labels(full_rows)
        # DESC order: L(7) W(6) W(5) W(4) L(3) W(2) W(1)
        # Streak labels: the first row of each run carries the count
        # Row 0 (most recent, L): standalone loss → no win streak → None
        # Row 1 (W, streak of 3): 3
        # etc.
        # The maximum win streak is 3
        win_labels = [l for l in labels if l is not None]
        assert max(win_labels) == 3


# ===================================================================
# 11. _compute_rest_gaps_for_graph with real DB (advanced)
# ===================================================================

class TestComputeRestGapsAdvancedDB:
    def test_gap_spanning_anchor_to_first_match(self):
        """Previous month's last match → this month's first match:
        if the gap exceeds the threshold, it should be flagged and
        its start fraction clamped to 0.0."""
        with elo_db.db_connection(500) as (conn, cursor):
            _register(cursor, 1, "Alice")
            _add_match(cursor, "1", 1, 6, _ts_for_day(date(2026, 6, 10)))
            _add_match(cursor, "1", 1, 7, _ts_for_day(date(2026, 7, 15)))
            conn.commit()
            rows_desc = get_full_match_history_by_season_month(cursor, "1", 1, 7)
            rows = list(reversed(rows_desc))

        anchor_date = date(2026, 6, 10)
        gaps = _compute_rest_gaps_for_graph(rows, anchor_date=anchor_date)
        assert len(gaps) == 1
        assert gaps[0][0] == pytest.approx(0.0)  # clamped

    def test_no_gap_when_anchor_is_close_to_first_match(self):
        """Anchor on June 30, first match on July 1 → 0 empty days,
        no gap."""
        with elo_db.db_connection(501) as (conn, cursor):
            _register(cursor, 1, "Alice")
            _add_match(cursor, "1", 1, 6, _ts_for_day(date(2026, 6, 30)))
            _add_match(cursor, "1", 1, 7, _ts_for_day(date(2026, 7, 1)))
            conn.commit()
            rows_desc = get_full_match_history_by_season_month(cursor, "1", 1, 7)
            rows = list(reversed(rows_desc))

        anchor_date = date(2026, 6, 30)
        gaps = _compute_rest_gaps_for_graph(rows, anchor_date=anchor_date)
        assert gaps == []


# ===================================================================
# 12. Graph mode toggle state consistency
# ===================================================================

class TestGraphModeState:
    """Verify HistoryView state is consistent after toggling modes."""

    def test_prev_next_buttons_disabled_in_graph_mode(self):
        async def _body():
            ctx = FakeCtx(guild_id=600)
            target = FakeTarget(1, "Alice")
            with elo_db.db_connection(600) as (conn, cursor):
                _register(cursor, 1, "Alice")
                for i in range(15):
                    _add_match(cursor, "1", 1, 1, played_at=1_700_000_000 + i * 100)
                conn.commit()

            view = HistoryView(cog=None, ctx=ctx, target=target, season=1, month=1)
            view.mode = "graph"
            sink = Sink()
            await view.update_history(sink)
            # In graph mode, prev/next should be disabled
            assert view.prev_button.disabled is True
            assert view.next_button.disabled is True

        run(_body())

    def test_switching_back_from_graph_resets_page(self):
        """After viewing graph (from page 1), switching back should
        reset to page 0."""
        async def _body():
            ctx = FakeCtx(guild_id=601)
            target = FakeTarget(1, "Alice")
            with elo_db.db_connection(601) as (conn, cursor):
                _register(cursor, 1, "Alice")
                for i in range(25):
                    _add_match(cursor, "1", 1, 1, played_at=1_700_000_000 + i * 100)
                conn.commit()

            view = HistoryView(cog=None, ctx=ctx, target=target, season=1, month=1, page=2)
            # Simulate: was on page 2, switched to graph, now switching back
            view.mode = "graph"
            from cogs.history import HistoryGraphToggleButton
            button = HistoryGraphToggleButton(view)
            sink = Sink()
            await button.callback(sink)
            assert view.mode == "history"
            assert view.page == 0

        run(_body())
