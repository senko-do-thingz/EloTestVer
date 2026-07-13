"""Tests for the -graph data-prep helpers (utils/elo_graph.py)."""

import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.match_history_db import MatchHistoryRow
from utils.elo_graph import (
    build_calendar_series,
    build_daily_series,
    build_match_series,
    calendar_axis_bounds,
    compute_month_averages,
)


def _row(match_id, wins, losses, elo_init, elo_final, played_at):
    return MatchHistoryRow(
        match_id=match_id,
        player_id="1",
        opponent_name="Bob",
        wins=wins,
        losses=losses,
        elo_init=elo_init,
        elo_final=elo_final,
        played_at=played_at,
    )


def _ts_for_day(day: date) -> int:
    """Epoch seconds (UTC) for noon on the given calendar day, used so
    tests don't depend on local timezone rounding to the wrong day."""
    import calendar
    from datetime import datetime, time

    return calendar.timegm(datetime.combine(day, time(12, 0)).timetuple())


class TestComputeMonthAverages:
    def test_empty_rows(self):
        avgs = compute_month_averages([])
        assert avgs.avg_win == 0
        assert avgs.avg_loss == 0
        assert avgs.avg_gain == 0
        assert avgs.final_elo is None

    def test_only_wins(self):
        rows = [
            _row(1, 2, 0, 500, 540, 1000),
            _row(2, 2, 1, 540, 560, 2000),
        ]
        avgs = compute_month_averages(rows)
        assert avgs.avg_win == 30  # (40 + 20) / 2
        assert avgs.avg_loss == 0
        assert avgs.avg_gain == 30
        assert avgs.win_count == 2
        assert avgs.loss_count == 0
        assert avgs.final_elo == 560

    def test_mixed_wins_and_losses(self):
        rows = [
            _row(1, 2, 0, 500, 540, 1000),  # win, +40
            _row(2, 0, 2, 540, 510, 2000),  # loss, -30
            _row(3, 2, 1, 510, 545, 3000),  # win, +35
        ]
        avgs = compute_month_averages(rows)
        assert avgs.avg_win == round((40 + 35) / 2)
        assert avgs.avg_loss == -30
        assert avgs.avg_gain == avgs.avg_win + avgs.avg_loss
        assert avgs.final_elo == 545

    def test_tie_counts_as_loss_bucket(self):
        # wins == losses is treated as "not a win" for bucketing purposes,
        # matching the same wins > losses convention used everywhere else
        # in this codebase (e.g. render_history_image's win/loss coloring).
        rows = [_row(1, 1, 1, 500, 500, 1000)]
        avgs = compute_month_averages(rows)
        assert avgs.win_count == 0
        assert avgs.loss_count == 1


class TestBuildDailySeries:
    def test_no_data_at_all(self):
        assert build_daily_series([]) == []

    def test_consecutive_days_no_gaps(self):
        d1, d2 = date(2026, 7, 1), date(2026, 7, 2)
        rows = [
            _row(1, 2, 0, 500, 540, _ts_for_day(d1)),
            _row(2, 2, 0, 540, 560, _ts_for_day(d2)),
        ]
        series = build_daily_series(rows)
        assert series == [("07-01", 540), ("07-02", 560)]

    def test_unplayed_days_are_flat_filled(self):
        # Matches on 07-01 and 07-04 only - 07-02/07-03 must still appear,
        # repeating the 07-01 elo, instead of being skipped.
        d1 = date(2026, 7, 1)
        d4 = date(2026, 7, 4)
        rows = [
            _row(1, 2, 0, 500, 540, _ts_for_day(d1)),
            _row(2, 2, 0, 540, 560, _ts_for_day(d4)),
        ]
        series = build_daily_series(rows)
        assert [label for label, _ in series] == ["07-01", "07-02", "07-03", "07-04"]
        assert [elo for _, elo in series] == [540, 540, 540, 560]

    def test_same_day_matches_collapse_to_last_elo(self):
        d1 = date(2026, 7, 1)
        base_ts = _ts_for_day(d1)
        rows = [
            _row(1, 2, 0, 500, 540, base_ts),
            _row(2, 0, 2, 540, 510, base_ts + 60),  # same day, later match
        ]
        series = build_daily_series(rows)
        assert series == [("07-01", 510)]

    def test_with_anchor_fills_gap_before_first_match(self):
        anchor_date = date(2026, 6, 30)
        d3 = date(2026, 7, 3)
        rows = [_row(1, 2, 0, 500, 540, _ts_for_day(d3))]
        series = build_daily_series(rows, anchor_elo=480, anchor_date=anchor_date)
        assert [label for label, _ in series] == ["06-30", "07-01", "07-02", "07-03"]
        assert [elo for _, elo in series] == [480, 480, 480, 540]

    def test_anchor_only_no_matches_no_end_date(self):
        # No matches yet this month and no end_date -> just the anchor
        # point itself, nothing fabricated past it.
        anchor_date = date(2026, 6, 30)
        series = build_daily_series([], anchor_elo=480, anchor_date=anchor_date)
        assert series == [("06-30", 480)]

    def test_anchor_only_extends_flat_to_end_date(self):
        # No matches yet this (in-progress) month, but end_date is given
        # (e.g. today) -> the anchor elo carries forward as a flat line.
        anchor_date = date(2026, 7, 1)
        end_date = date(2026, 7, 4)
        series = build_daily_series([], anchor_elo=480, anchor_date=anchor_date, end_date=end_date)
        assert [label for label, _ in series] == ["07-01", "07-02", "07-03", "07-04"]
        assert all(elo == 480 for _, elo in series)

    def test_end_date_extends_past_last_match(self):
        d1 = date(2026, 7, 1)
        end_date = date(2026, 7, 3)
        rows = [_row(1, 2, 0, 500, 540, _ts_for_day(d1))]
        series = build_daily_series(rows, end_date=end_date)
        assert [label for label, _ in series] == ["07-01", "07-02", "07-03"]
        assert [elo for _, elo in series] == [540, 540, 540]

    def test_end_date_before_last_match_does_not_truncate(self):
        # end_date should never cut off real data - only extend.
        d1 = date(2026, 7, 5)
        rows = [_row(1, 2, 0, 500, 540, _ts_for_day(d1))]
        series = build_daily_series(rows, end_date=date(2026, 7, 1))
        assert series == [("07-05", 540)]


class TestBuildMatchSeries:
    def test_no_data_no_anchor(self):
        assert build_match_series([]) == []

    def test_one_point_per_match_no_gap_filling(self):
        # The elo plotted must always come from a real match row - no
        # flat-filled points for days without a match, unlike
        # build_daily_series.
        d1 = date(2026, 7, 1)
        d4 = date(2026, 7, 4)
        rows = [
            _row(1, 2, 0, 500, 540, _ts_for_day(d1)),
            _row(2, 2, 0, 540, 560, _ts_for_day(d4)),
        ]
        series = build_match_series(rows)
        assert series == [("07-01", 540), ("07-04", 560)]

    def test_same_day_matches_are_two_separate_points(self):
        # Two sets played the same day must still be two distinct points
        # (unlike build_daily_series, which collapses them into one) -
        # the date label is simply allowed to repeat.
        d1 = date(2026, 7, 1)
        base_ts = _ts_for_day(d1)
        rows = [
            _row(1, 2, 0, 500, 540, base_ts),
            _row(2, 0, 2, 540, 510, base_ts + 60),
        ]
        series = build_match_series(rows)
        assert series == [("07-01", 540), ("07-01", 510)]

    def test_with_anchor_prefixes_anchor_date(self):
        anchor_date = date(2026, 6, 30)
        rows = [_row(1, 2, 0, 500, 540, _ts_for_day(date(2026, 7, 3)))]
        series = build_match_series(rows, anchor_elo=480, anchor_date=anchor_date)
        assert series == [("06-30", 480), ("07-03", 540)]

    def test_anchor_without_date_uses_placeholder_label(self):
        series = build_match_series([], anchor_elo=480)
        assert series == [("-", 480)]

    def test_anchor_only_no_matches(self):
        anchor_date = date(2026, 6, 30)
        series = build_match_series([], anchor_elo=480, anchor_date=anchor_date)
        assert series == [("06-30", 480)]


class TestCalendarAxisBounds:
    def test_no_anchor_no_rows(self):
        assert calendar_axis_bounds([]) is None

    def test_bounds_from_anchor_date(self):
        # Reference 2026-06-25 -> axis runs from the last day of June
        # (its own month) through the last day of July (the month after).
        bounds = calendar_axis_bounds([], anchor_date=date(2026, 6, 25))
        assert bounds == (date(2026, 6, 30), date(2026, 7, 31))

    def test_bounds_from_first_match_when_no_anchor(self):
        rows = [_row(1, 2, 0, 500, 540, _ts_for_day(date(2026, 7, 5)))]
        bounds = calendar_axis_bounds(rows)
        assert bounds == (date(2026, 6, 30), date(2026, 7, 31))

    def test_bounds_handle_december_rollover(self):
        bounds = calendar_axis_bounds([], anchor_date=date(2026, 12, 10))
        assert bounds == (date(2026, 12, 31), date(2027, 1, 31))


class TestBuildCalendarSeries:
    def test_no_data_no_anchor(self):
        points, axis_ticks = build_calendar_series([])
        assert points == []
        assert axis_ticks == []

    def test_axis_ticks_cover_every_day_even_without_matches(self):
        # Only one match on 07-05, but the axis must still pre-generate a
        # tick for every day from 06-30 through 07-31, including days
        # with no data at all (past AND future relative to the match).
        rows = [_row(1, 2, 0, 500, 540, _ts_for_day(date(2026, 7, 5)))]
        points, axis_ticks = build_calendar_series(rows)
        labels = [label for _, label in axis_ticks]
        assert labels[0] == "06-30"
        assert labels[-1] == "07-31"
        assert len(labels) == 32  # 06-30 + all 31 days of July
        assert "07-01" in labels and "07-20" in labels  # unplayed days present

    def test_axis_ticks_are_sorted_and_span_0_to_1(self):
        rows = [_row(1, 2, 0, 500, 540, _ts_for_day(date(2026, 7, 5)))]
        _, axis_ticks = build_calendar_series(rows)
        fractions = [frac for frac, _ in axis_ticks]
        assert fractions == sorted(fractions)
        assert fractions[0] == 0.0
        assert 0 < fractions[-1] < 1

    def test_single_match_position_matches_its_calendar_day(self):
        rows = [_row(1, 2, 0, 500, 540, _ts_for_day(date(2026, 7, 5)))]
        points, axis_ticks = build_calendar_series(rows)
        assert len(points) == 1
        match_frac, elo = points[0]
        assert elo == 540
        # 07-05 is day index 5 (06-30=0, 07-01=1, ..., 07-05=5) out of
        # 32 total days - the single set that day should land in the
        # middle of that day's slot.
        tick_frac = dict((label, frac) for frac, label in axis_ticks)["07-05"]
        day_width = 1 / 32
        assert match_frac == pytest.approx(tick_frac + day_width / 2)

    def test_same_day_matches_split_the_day_evenly(self):
        d1 = date(2026, 7, 5)
        base_ts = _ts_for_day(d1)
        rows = [
            _row(1, 2, 0, 500, 540, base_ts),
            _row(2, 0, 2, 540, 510, base_ts + 60),
        ]
        points, _ = build_calendar_series(rows)
        assert [elo for _, elo in points] == [540, 510]
        first_frac, second_frac = points[0][0], points[1][0]
        # Both sets fall strictly within the same day's slot, in order,
        # and don't collapse onto the same x position.
        assert first_frac < second_frac

    def test_anchor_point_sits_at_fraction_zero(self):
        anchor_date = date(2026, 6, 25)
        points, axis_ticks = build_calendar_series(
            [], anchor_elo=480, anchor_date=anchor_date
        )
        assert points == [(0.0, 480)]
        assert axis_ticks[0] == (0.0, "06-30")

    def test_anchor_plus_matches_no_fabricated_points(self):
        # Same "no gap-filled days" rule as build_match_series(): only
        # the anchor and real matches produce points, unplayed days only
        # show up in axis_ticks, never as elo data.
        anchor_date = date(2026, 6, 25)
        rows = [_row(1, 2, 0, 500, 540, _ts_for_day(date(2026, 7, 10)))]
        points, axis_ticks = build_calendar_series(
            rows, anchor_elo=480, anchor_date=anchor_date
        )
        assert len(points) == 2
        assert len(axis_ticks) == 32
        assert [elo for _, elo in points] == [480, 540]