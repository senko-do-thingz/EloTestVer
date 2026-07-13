"""Shared Excel export for guild-wide match listings.

Used by:
- `-match_ids` (cogs/match_ids.py): lets admins look up a match's ID (needed
  for the ID-based `-revert`) across every player in the guild.
- The automatic decay history purge (utils/elo_decay.py): archives a
  season/month's match_history rows to the decay log channel right before
  they are deleted.

Both reuse the same row layout and win/green - loss/red coloring already
used by `/export history` (cogs/export_history.py), extended with a
Match ID column and a Date/Time (down to the minute) column, and covering
every player instead of just one.
"""

from __future__ import annotations

import io
from datetime import datetime, timezone

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from db.match_history_db import MatchHistoryRow

COLUMN_HEADERS = [
    "Match ID", "Date", "Time", "Player", "Opponent",
    "Wins", "Losses", "+/-", "Init", "Final",
]

TITLE_FONT = Font(name="Arial", bold=True, size=14)
HEADER_FONT = Font(name="Arial", bold=True, color="FFFFFF")
HEADER_FILL = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
BODY_FONT = Font(name="Arial")
WIN_FILL = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
LOSS_FILL = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
CENTER = Alignment(horizontal="center", vertical="center")


def _format_delta(delta: int) -> str:
    if delta > 0:
        return f"+{delta}"
    return str(delta)


def build_match_listing_workbook(
    rows: list[MatchHistoryRow],
    name_lookup: dict[str, str],
    *,
    sheet_title: str,
    header_text: str,
) -> Workbook:
    """Build a single-sheet workbook listing `rows` (one row per player per
    match - i.e. exactly as stored in match_history), most recent first.

    `name_lookup` maps player_id (str) -> current display name, used since
    match_history only stores the *opponent's* name, not the row owner's.
    A row is colored green if that participant won the match, red if they
    lost - matching /export history's coloring.
    """
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = sheet_title

    last_col = len(COLUMN_HEADERS)
    sheet.cell(row=1, column=1, value=header_text)
    sheet.cell(row=1, column=1).font = TITLE_FONT
    sheet.merge_cells(start_row=1, start_column=1, end_row=1, end_column=last_col)

    header_row = 3
    for col, title in enumerate(COLUMN_HEADERS, start=1):
        cell = sheet.cell(row=header_row, column=col, value=title)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = CENTER

    row_idx = header_row + 1
    for match in rows:
        is_win = match.wins > match.losses
        fill = WIN_FILL if is_win else LOSS_FILL
        played_dt = datetime.fromtimestamp(match.played_at, tz=timezone.utc)
        player_name = name_lookup.get(str(match.player_id), f"Unknown ({match.player_id})")
        values = [
            match.match_id,
            played_dt.strftime("%Y-%m-%d"),
            played_dt.strftime("%H:%M"),
            player_name,
            match.opponent_name,
            match.wins,
            match.losses,
            _format_delta(match.elo_delta),
            match.elo_init,
            match.elo_final,
        ]
        for col, value in enumerate(values, start=1):
            cell = sheet.cell(row=row_idx, column=col, value=value)
            cell.fill = fill
            cell.font = BODY_FONT
            if col > 1:
                cell.alignment = CENTER
        row_idx += 1

    widths: dict[int, int] = {}
    for row in sheet.iter_rows(min_row=header_row):
        for cell in row:
            if cell.value is None:
                continue
            widths[cell.column] = max(widths.get(cell.column, 0), len(str(cell.value)))
    for col, width in widths.items():
        sheet.column_dimensions[get_column_letter(col)].width = max(9, min(width + 2, 40))

    return workbook


def workbook_to_file_bytes(workbook: Workbook) -> io.BytesIO:
    buf = io.BytesIO()
    workbook.save(buf)
    buf.seek(0)
    return buf