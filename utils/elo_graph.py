"""Data prep for the -graph / /history Graph view (ClashPerk-style Elo chart).

Kept separate from utils/rendering.py so the averaging/series logic can be
unit-tested without touching Pillow. Only two entry points are meant to be
used by callers:

- compute_month_averages(): AVG. WIN / AVG. LOSS / AVG. GAIN / FINAL ELO for
  one season/month, computed strictly from actual match_history rows.
- build_match_series(): one (date_label, elo) point per MATCH ("set")
  actually played, in the order they were played - the elo VALUE plotted
  is always taken straight from a real match_history row. No gap-filled
  "not played" days are fabricated. The date label under each point is
  purely for display (ClashPerk-style "MM-DD") - the x-axis position
  itself is just evenly spaced by point index for layout purposes, it
  does not represent elapsed calendar time. This is the function used for
  both the current month's main line and the previous month's dashed
  comparison line (see cogs/history.py).
- build_daily_series(): one (label, elo) point per CALENDAR DAY,
  including days the player didn't play (flat-filled). Kept as a helper
  for callers that specifically want a day-based (rather than set-based)
  view; not used by the -graph card by default.
- build_calendar_series(): the x-axis used by the -graph card. Like
  build_match_series(), one point per MATCH actually played (no
  fabricated elo values) - but positioned on a real calendar timeline
  instead of evenly spaced by index, and paired with a pre-generated
  set of axis tick days covering the whole month (see its docstring).
- find_rest_gaps() / rest_gap_fractions(): detect multi-day spans with
  no match played at all ("rest periods") between two active days, and
  convert them to the same 0..1 x-axis fractions as
  build_calendar_series(), so the -graph card can shade them with a
  distinct color instead of leaving them looking like an ordinary
  (played) stretch of the line.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from db.match_history_db import MatchHistoryRow


@dataclass(frozen=True)
class MonthAverages:
    avg_win: int
    avg_loss: int
    avg_gain: int
    win_count: int
    loss_count: int
    final_elo: int | None


def compute_month_averages(rows_chronological: list[MatchHistoryRow]) -> MonthAverages:
    """Compute AVG. WIN / AVG. LOSS / AVG. GAIN / FINAL ELO for one month.

    ``rows_chronological`` must contain ONLY matches actually played within
    the target season/month (oldest first), e.g. the reversed output of
    get_full_match_history_by_season_month(). It must NOT include the
    previous month's carry-over anchor point used by build_daily_series()
    below - that point represents elo before this month's -decay/-reset
    ran, not a played match, and mixing it in would skew every average.

    - AVG. WIN: average elo_delta across rows the player won (wins > losses).
    - AVG. LOSS: average elo_delta across rows the player lost (already
      negative, so it prints the same way the original AVG. DEFENSE did).
    - AVG. GAIN: AVG. WIN + AVG. LOSS, i.e. the net average change per match.
    - FINAL ELO: elo_final of the most recent match this month, or None if
      there were no matches at all.
    """
    win_deltas = [r.elo_delta for r in rows_chronological if r.wins > r.losses]
    loss_deltas = [r.elo_delta for r in rows_chronological if r.wins <= r.losses]

    avg_win = round(sum(win_deltas) / len(win_deltas)) if win_deltas else 0
    avg_loss = round(sum(loss_deltas) / len(loss_deltas)) if loss_deltas else 0
    avg_gain = avg_win + avg_loss
    final_elo = rows_chronological[-1].elo_final if rows_chronological else None

    return MonthAverages(
        avg_win=avg_win,
        avg_loss=avg_loss,
        avg_gain=avg_gain,
        win_count=len(win_deltas),
        loss_count=len(loss_deltas),
        final_elo=final_elo,
    )


def build_match_series(
    rows_chronological: list[MatchHistoryRow],
    *,
    anchor_elo: int | None = None,
    anchor_date: date | None = None,
) -> list[tuple[str, int]]:
    """Build one (date_label, elo) point per MATCH ("set") actually
    played, oldest first - the elo plotted at each point is always a real
    elo_final from match_history, never interpolated or flat-filled for a
    day the player didn't play.

    ``rows_chronological`` is oldest-first (reverse of the usual query
    order), matches of ONE season/month.

    The date label under each point is a display-only "MM-DD" string
    (ClashPerk-style) and may repeat across consecutive points if the
    player played more than one set the same day - that's expected. The
    actual x-axis POSITION of each point (computed by the renderer) is
    just evenly spaced by index, since the x-axis here is a layout device
    for showing the sequence of sets, not a literal calendar timeline.

    If ``anchor_elo`` is given, the series is prefixed with an extra point
    holding the previous month's last known elo (before any -decay/-reset
    adjustment), so the line doesn't start mid-air at the first match of
    the month. ``anchor_date`` supplies that point's display label; if
    omitted, the anchor point is labeled "-".
    """
    series: list[tuple[str, int]] = []
    if anchor_elo is not None:
        anchor_label = anchor_date.strftime("%m-%d") if anchor_date is not None else "-"
        series.append((anchor_label, anchor_elo))

    for row in rows_chronological:
        label = datetime.fromtimestamp(row.played_at, tz=timezone.utc).strftime("%m-%d")
        series.append((label, row.elo_final))

    return series


def build_daily_series(
    rows_chronological: list[MatchHistoryRow],
    *,
    anchor_elo: int | None = None,
    anchor_date: date | None = None,
    end_date: date | None = None,
) -> list[tuple[str, int]]:
    """Build one (date_label, elo) point per CALENDAR DAY for the line
    chart - including days the player didn't play. A day with no recorded
    match simply repeats the last known elo (a flat segment on the chart)
    instead of being skipped, matching ClashPerk's daily x-axis.

    ``rows_chronological`` is oldest-first (reverse of the usual query
    order), matches of ONE season/month. Multiple matches on the same day
    collapse into that day's LAST elo_final.

    If ``anchor_elo``/``anchor_date`` are given, the series starts there
    instead of at the first match of the month - the previous month's
    last known elo, before any -decay/-reset adjustment, so the line
    doesn't start mid-air at the first match of the month (and any gap
    before the first match is flat-filled too).

    ``end_date``, if given, extends the generated days up through that
    date even past the last recorded match - pass today's date for the
    CURRENT in-progress month so "not played yet" days still appear on
    the x-axis. Leave it None for a completed past month, where the
    series simply stops at the last actual match day.
    """
    if not rows_chronological and anchor_elo is None:
        return []

    # elo_final actually recorded on each day that had at least one match
    # (rows are chronological, so a later same-day match overwrites an
    # earlier one - the day's point holds the LAST elo of that day).
    day_elo: dict[date, int] = {}
    for row in rows_chronological:
        day = datetime.fromtimestamp(row.played_at, tz=timezone.utc).date()
        day_elo[day] = row.elo_final

    if anchor_date is not None:
        start_day = anchor_date
    elif day_elo:
        start_day = min(day_elo)
    else:
        # anchor_elo given but no anchor_date and no matches at all -
        # nothing to iterate over.
        return []

    last_day = max(day_elo) if day_elo else anchor_date
    stop_day = max(last_day, end_date) if end_date is not None else last_day

    series: list[tuple[str, int]] = []
    current_elo = anchor_elo
    day = start_day
    while day <= stop_day:
        if day in day_elo:
            current_elo = day_elo[day]
        if current_elo is not None:
            series.append((day.strftime("%m-%d"), current_elo))
        day += timedelta(days=1)

    return series


def _month_end(d: date) -> date:
    """Return the last calendar day of the month containing ``d``."""
    last_day = calendar.monthrange(d.year, d.month)[1]
    return d.replace(day=last_day)


def _first_of_next_month(d: date) -> date:
    """Return the 1st of the calendar month right after ``d``'s month."""
    if d.month == 12:
        return date(d.year + 1, 1, 1)
    return date(d.year, d.month + 1, 1)


def calendar_axis_bounds(
    rows_chronological: list[MatchHistoryRow],
    *,
    anchor_date: date | None = None,
) -> tuple[date, date] | None:
    """Work out the fixed calendar range the -graph x-axis should span.

    The axis runs from the last calendar day of the month BEFORE the
    reference point's month through the last calendar day of the
    reference point's own month - e.g. a reference date of 2026-06-25
    gives an axis of 2026-06-30 .. 2026-07-31.

    ``anchor_date`` (the previous virtual month's carry-over elo), when
    given, sits in the month BEFORE the target month, so the target
    month is the one right after it. Without an anchor, the date of the
    first match in ``rows_chronological`` is used instead - it already
    sits inside the target month, since there is no carry-over point to
    anchor to. Returns None if there is neither an anchor nor any
    matches at all - nothing to lay an axis out for.
    """
    if anchor_date is not None:
        target_month_ref = _first_of_next_month(anchor_date)
    elif rows_chronological:
        target_month_ref = datetime.fromtimestamp(
            rows_chronological[0].played_at, tz=timezone.utc
        ).date()
    else:
        return None

    axis_end = _month_end(target_month_ref)
    axis_start = axis_end.replace(day=1) - timedelta(days=1)
    return axis_start, axis_end


def build_calendar_series(
    rows_chronological: list[MatchHistoryRow],
    *,
    anchor_elo: int | None = None,
    anchor_date: date | None = None,
    anchor_before_first_match: bool = False,
) -> tuple[list[tuple[float, int]], list[tuple[float, str]]]:
    """Build the calendar-based x-axis used by the -graph card.

    Returns a ``(points, axis_ticks)`` tuple:

    - ``points``: one (x_fraction, elo) entry per MATCH actually played
      (plus the carry-over anchor, if given), oldest first. The elo
      plotted is always a real elo_final from match_history (or
      ``anchor_elo``) - never interpolated or flat-filled for a day
      with no match, same "no fabricated points" rule as
      build_match_series(). A day with more than one match ("set")
      divides that day's slot evenly between however many sets were
      played, centering each match in its own share of the day - these
      per-set divisions are an internal layout detail only and are
      never labeled on the axis themselves.
    - ``axis_ticks``: one (x_fraction, "MM-DD") entry per CALENDAR DAY
      across the full axis span (see calendar_axis_bounds()), generated
      up front regardless of whether that day has any data yet - this
      is what lets the -graph card lay out the whole month ahead of
      time, including future/unplayed days, instead of only the days
      that already have matches.

    ``x_fraction`` in both lists is a 0..1 position along the SAME full
    axis span, so points and ticks line up directly; the renderer turns
    it into a pixel x by multiplying against the chart's pixel width.

    The anchor point (if ``anchor_elo`` is given) is normally pinned to
    x_fraction 0.0 - the axis start / previous-vs-current month
    boundary - which is correct when it represents a genuine
    previous-month carry-over elo (``anchor_date`` given): that elo is
    meant to mark "what the player carried INTO this month", shown at
    the month boundary regardless of which day their last match of the
    prior month actually fell on.

    But when there is no real previous month at all (``anchor_date`` is
    None) and the caller passes ``anchor_before_first_match=True``, the
    anchor instead represents a synthetic "starting elo" point (e.g. a
    brand new player's default elo before their very first match this
    month). Pinning that to x_fraction 0.0 would fabricate a flat
    segment across every empty day between the axis start and the
    player's actual first match - days that never happened. In that
    case the anchor is positioned right at the start of the first
    match's own calendar day instead, immediately before that match.

    Returns ``([], [])`` if there is neither an anchor nor any matches
    (see calendar_axis_bounds()).
    """
    bounds = calendar_axis_bounds(rows_chronological, anchor_date=anchor_date)
    if bounds is None:
        return [], []
    axis_start, axis_end = bounds

    total_days = (axis_end - axis_start).days + 1
    axis_ticks = [
        (i / total_days, (axis_start + timedelta(days=i)).strftime("%m-%d"))
        for i in range(total_days)
    ]

    # Group matches by their calendar day, in the order they were
    # played, so same-day sets can be spread evenly within that day's
    # slot instead of all stacking on one x position. Computed before
    # the anchor point below, since a synthetic anchor needs to know
    # the first match's day to sit just before it.
    by_day: dict[date, list[MatchHistoryRow]] = {}
    for row in rows_chronological:
        day = datetime.fromtimestamp(row.played_at, tz=timezone.utc).date()
        by_day.setdefault(day, []).append(row)

    points: list[tuple[float, int]] = []
    if anchor_elo is not None:
        anchor_fraction = 0.0
        if anchor_date is None and anchor_before_first_match and by_day:
            first_day = min(by_day)
            anchor_fraction = (first_day - axis_start).days / total_days
        points.append((anchor_fraction, anchor_elo))

    for day, day_rows in by_day.items():
        day_offset = (day - axis_start).days
        slots_in_day = len(day_rows)
        for slot_index, row in enumerate(day_rows, start=1):
            within_day = (slot_index - 0.5) / slots_in_day
            fraction = (day_offset + within_day) / total_days
            points.append((fraction, row.elo_final))

    return points, axis_ticks


# A "rest period" needs at least this many fully empty calendar days
# strictly between two active days before it's flagged - a single
# skipped day is normal day-to-day play, not a genuine break.
DEFAULT_REST_GAP_THRESHOLD_DAYS = 2


def find_rest_gaps(
    rows_chronological: list[MatchHistoryRow],
    *,
    anchor_date: date | None = None,
    gap_threshold_days: int = DEFAULT_REST_GAP_THRESHOLD_DAYS,
) -> list[tuple[date, date]]:
    """Find multi-day inactivity spans ("rest periods") between matches,
    for the -graph card's rest-period highlighting.

    Returns one ``(last_active_day, next_active_day)`` pair per gap
    where the player went at least ``gap_threshold_days`` full calendar
    days without a single match between two active days - e.g. the
    default of 2 flags a 3+ day span (2+ fully empty days strictly
    between the two active days) but leaves a single skipped day alone.

    ``anchor_date``, if given, is treated as a genuine previous-month
    carry-over active day (same meaning as in build_calendar_series())
    and can itself open a gap leading into this month's first match.
    Callers that only have the SYNTHETIC "before first match" anchor
    (``anchor_before_first_match=True`` in build_calendar_series(), no
    real ``anchor_date``) must not pass one here either, since that
    anchor is not a real day of activity and would fabricate a rest
    period that never happened.

    Only days that actually have a match in ``rows_chronological``
    (plus the optional real anchor) count as "active" - unplayed days
    are never fabricated, matching every other function in this
    module. The returned pairs are sorted chronologically and, being
    derived from a set, contain no duplicates even if several matches
    share the same day.
    """
    active_days: set[date] = set()
    for row in rows_chronological:
        active_days.add(datetime.fromtimestamp(row.played_at, tz=timezone.utc).date())
    if anchor_date is not None:
        active_days.add(anchor_date)

    sorted_days = sorted(active_days)
    if len(sorted_days) < 2:
        return []

    gaps: list[tuple[date, date]] = []
    for prev_day, next_day in zip(sorted_days, sorted_days[1:]):
        empty_days = (next_day - prev_day).days - 1
        if empty_days >= gap_threshold_days:
            gaps.append((prev_day, next_day))
    return gaps


def rest_gap_fractions(
    rest_gaps: list[tuple[date, date]],
    *,
    axis_start: date,
    total_days: int,
) -> list[tuple[float, float]]:
    """Convert ``(start_day, end_day)`` rest gaps (see find_rest_gaps())
    into ``(x_fraction, x_fraction)`` spans on the same 0..1 calendar
    axis used by build_calendar_series() - ``axis_start`` and
    ``total_days`` should come from calendar_axis_bounds() for the same
    reference point, so a shaded gap band lines up exactly with the day
    gridlines/labels it spans.

    Returns an empty list if ``total_days`` is not positive (nothing to
    position a fraction against).
    """
    if total_days <= 0:
        return []
    return [
        ((start - axis_start).days / total_days, (end - axis_start).days / total_days)
        for start, end in rest_gaps
    ]