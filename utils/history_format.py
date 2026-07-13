"""Format match history tables (ClashPerk-style monospace grid)."""

from __future__ import annotations

from datetime import datetime, timezone

from db.match_history_db import MatchHistoryRow

# Cap on how many characters of an opponent's display name are shown in a
# history row. Discord names can run 30+ characters, which would otherwise
# stretch every line of the monospace table (and the per-match card titles)
# well past what's comfortable to read, especially on mobile. 20 keeps most
# real names intact while still bounding the handful of outliers that would
# dominate the row, and leaves enough room next to the other fixed-width
# columns (Date, Wins, Loses, +/-, INIT, FINAL) that the table stays legible
# without wrapping.
DEFAULT_OPPONENT_NAME_MAX_LENGTH = 20


def truncate_display_name(
    name: str, max_length: int = DEFAULT_OPPONENT_NAME_MAX_LENGTH
) -> str:
    """Truncate a Discord display name for compact display in a history
    row, appending a single ellipsis character when it doesn't fit.

    Truncation counts characters, not bytes or grapheme clusters. Python
    strings are sequences of Unicode code points, so this stays correct
    for CJK text and most single-code-point emoji - none of them get cut
    in half - without needing an extra dependency. Only a plain ellipsis
    is appended and no markdown syntax is introduced, so the truncation
    itself can never break formatting; names already under the limit are
    returned untouched.
    """
    if len(name) <= max_length:
        return name
    return name[: max_length - 1].rstrip() + "…"


def _format_date(ts: int) -> str:
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return dt.strftime("%m-%d")


def _format_delta(value: int) -> str:
    return f"+{value}" if value >= 0 else str(value)


def format_history_table(
    rows: list[MatchHistoryRow],
    *,
    player_name: str,
    total_matches: int,
) -> str:
    if not rows:
        return f"No match history for **{player_name}** yet."

    headers = ("Date", "Opponent", "Wins", "Loses", "+/-", "INIT", "FINAL")
    body_rows: list[tuple[str, ...]] = []
    for row in rows:
        delta = row.elo_delta
        body_rows.append(
            (
                _format_date(row.played_at),
                truncate_display_name(row.opponent_name),
                str(row.wins),
                str(row.losses),
                _format_delta(delta),
                str(row.elo_init),
                str(row.elo_final),
            )
        )

    widths = [
        max(len(headers[i]), max(len(r[i]) for r in body_rows))
        for i in range(len(headers))
    ]

    def fmt_row(cells: tuple[str, ...]) -> str:
        return " | ".join(cell.ljust(widths[i]) for i, cell in enumerate(cells))

    lines = [
        fmt_row(headers),
        "-+-".join("-" * w for w in widths),
    ]
    lines.extend(fmt_row(row) for row in body_rows)

    table = "```\n" + "\n".join(lines) + "\n```"
    summary = f"**{player_name}** - showing {len(rows)} of {total_matches} match(es)"
    return f"{summary}\n{table}"


# Discord embeds cap out at 25 fields, so history rendered as cards is
# truncated to that many even if more matches were requested.
MAX_HISTORY_FIELDS = 25


def format_history_summary(*, player_name: str, shown: int, total_matches: int) -> str:
    """Header line shown above the per-match cards."""
    return f"**{player_name}** - showing {shown} of {total_matches} match(es)"


def format_history_fields(
    rows: list[MatchHistoryRow],
) -> list[tuple[str, str]]:
    """
    Split match history into small ClashPerk-style cards instead of one big
    monospace table. Each match becomes its own (name, value) pair meant to
    be added to a discord.Embed via add_field(), so it renders as a separate
    boxed section rather than one giant grid.
    """
    fields: list[tuple[str, str]] = []
    for row in rows[:MAX_HISTORY_FIELDS]:
        won = row.wins > row.losses
        icon = "🏆" if won else "💀"
        delta = row.elo_delta
        name = f"{icon} {_format_date(row.played_at)} · vs {truncate_display_name(row.opponent_name)}"
        value = (
            f"**{row.wins} - {row.losses}**\n"
            f"{row.elo_init} → {row.elo_final} ({_format_delta(delta)})"
        )
        fields.append((name, value))
    return fields