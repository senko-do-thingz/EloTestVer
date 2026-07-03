"""Format match history tables (ClashPerk-style monospace grid)."""

from __future__ import annotations

from datetime import datetime, timezone

from db.match_history_db import MatchHistoryRow


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
                row.opponent_name,
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
        name = f"{icon} {_format_date(row.played_at)} · vs {row.opponent_name}"
        value = (
            f"**{row.wins} - {row.losses}**\n"
            f"{row.elo_init} → {row.elo_final} ({_format_delta(delta)})"
        )
        fields.append((name, value))
    return fields