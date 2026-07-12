"""`/export history` (slash-only): exports the invoking user's full match
history to an .xlsx workbook - one sheet per season, with one table per
month inside that season's sheet."""

from __future__ import annotations

import io
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from db.elo_db import db_connection
from db.match_history_db import (
    get_full_match_history_by_season_month,
    get_months_with_history,
    get_seasons_with_history,
)

COLUMN_HEADERS = ["Streak", "Date", "Opponent", "Wins", "Losses", "+/-", "Init", "Final"]

# Columns that get a small fixed width instead of participating in
# content-based autofit - the Streak column only ever holds 1-2 digit
# numbers, and the Date column is deliberately kept narrow so the Opponent
# column (which used to share the autofit budget with Date) gets the extra
# room instead.
STREAK_COL = 1
DATE_COL = 2
STREAK_COL_WIDTH = 9
DATE_COL_WIDTH = 12

TITLE_FONT = Font(name="Arial", bold=True, size=14)
MONTH_FONT = Font(name="Arial", bold=True, size=12, color="FFFFFF")
MONTH_FILL = PatternFill(start_color="404040", end_color="404040", fill_type="solid")
HEADER_FONT = Font(name="Arial", bold=True, color="FFFFFF")
HEADER_FILL = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
BODY_FONT = Font(name="Arial")
STREAK_FONT = Font(name="Arial", bold=True)
SUMMARY_FONT = Font(name="Arial", italic=True, color="595959")
WIN_FILL = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
LOSS_FILL = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
CENTER = Alignment(horizontal="center", vertical="center")


def _sanitize_filename(name: str) -> str:
    safe = "".join(c for c in name if c.isalnum() or c in ("-", "_"))
    return safe or "player"


def _format_delta(delta: int) -> str:
    """Format an Elo delta with an explicit '+' sign for positive values,
    e.g. 32 -> '+32', -13 -> '-13', 0 -> '0'."""
    if delta > 0:
        return f"+{delta}"
    return str(delta)


def _compute_streak_labels(rows: list) -> list[int | None]:
    """Mirror the streak grouping used by -history's rendered image: ``rows``
    is most-recent-first, and consecutive rows with the same result (win or
    loss) form a "streak". Only the first (most recent) row of each streak
    group gets a number; the rest are left as None (blank)."""
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


def _write_month_table(sheet, row: int, month: int, rows: list) -> int:
    """Write one month's section starting at `row`. Returns the next free row."""
    last_col = len(COLUMN_HEADERS)

    # Month banner
    sheet.cell(row=row, column=1, value=f"Month {month}")
    sheet.merge_cells(start_row=row, start_column=1, end_row=row, end_column=last_col)
    banner_cell = sheet.cell(row=row, column=1)
    banner_cell.font = MONTH_FONT
    banner_cell.fill = MONTH_FILL
    banner_cell.alignment = CENTER
    row += 1

    # Column headers
    for col, title in enumerate(COLUMN_HEADERS, start=1):
        cell = sheet.cell(row=row, column=col, value=title)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = CENTER
    row += 1

    streak_labels = _compute_streak_labels(rows)
    total_games = 0

    # Data rows (most recent match first, matching /history's display order)
    for match, streak_label in zip(rows, streak_labels):
        is_win = match.wins > match.losses
        fill = WIN_FILL if is_win else LOSS_FILL
        played_date = datetime.fromtimestamp(match.played_at, tz=timezone.utc).strftime("%Y-%m-%d")
        total_games += match.wins + match.losses
        values = [
            streak_label if streak_label is not None else "",
            played_date,
            match.opponent_name,
            match.wins,
            match.losses,
            _format_delta(match.elo_delta),
            match.elo_init,
            match.elo_final,
        ]
        for col, value in enumerate(values, start=1):
            cell = sheet.cell(row=row, column=col, value=value)
            cell.fill = fill
            if col == STREAK_COL and streak_label is not None:
                cell.font = STREAK_FONT
            else:
                cell.font = BODY_FONT
            if col > 1:
                cell.alignment = CENTER
        row += 1

    # Best streak / total games summary for this month
    best_streak = max((label for label in streak_labels if label is not None), default=0)
    summary_cell = sheet.cell(
        row=row,
        column=1,
        value=f"Best Streak: {best_streak}    |    Total Games: {total_games}",
    )
    sheet.merge_cells(start_row=row, start_column=1, end_row=row, end_column=last_col)
    summary_cell.font = SUMMARY_FONT
    row += 1

    return row + 1  # blank spacer row before the next month


def _autofit_columns(
    sheet, min_width: int = 10, max_width: int = 40, skip_cols: frozenset[int] = frozenset()
) -> None:
    widths: dict[int, int] = {}
    for row in sheet.iter_rows():
        for cell in row:
            if cell.value is None:
                continue
            col = cell.column
            if col in skip_cols:
                continue
            length = len(str(cell.value))
            widths[col] = max(widths.get(col, 0), length)
    for col, width in widths.items():
        sheet.column_dimensions[get_column_letter(col)].width = max(min_width, min(width + 2, max_width))


def _apply_fixed_column_widths(sheet) -> None:
    """Streak and Date get small fixed widths instead of autofit, so the
    Opponent column gets the dynamic width Date used to have."""
    sheet.column_dimensions[get_column_letter(STREAK_COL)].width = STREAK_COL_WIDTH
    sheet.column_dimensions[get_column_letter(DATE_COL)].width = DATE_COL_WIDTH


# GroupCog turns this whole cog into the `/export` command group, so the
# `history` command below becomes `/export history`.
class ExportCog(commands.GroupCog, name="export", description="Export your data to a file."):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="history",
        description="Export your full match history to an Excel file (one sheet per season).",
    )
    @app_commands.guild_only()
    async def history(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)

        guild_id = interaction.guild_id
        player_id = interaction.user.id

        with db_connection(guild_id) as (_conn, cursor):
            cursor.execute("SELECT name FROM players WHERE id=?", (str(player_id),))
            row = cursor.fetchone()
            if not row:
                await interaction.followup.send(
                    embed=discord.Embed(
                        title="Player Not Registered",
                        description="You are not registered. Use `-register <name>` to sign up!",
                        color=discord.Color.red(),
                    )
                )
                return
            player_name = row[0]

            seasons = get_seasons_with_history(cursor, player_id)
            if not seasons:
                await interaction.followup.send(
                    embed=discord.Embed(
                        title="Match History",
                        description=f"**{player_name}** has no recorded matches yet.",
                        color=discord.Color.blue(),
                    )
                )
                return

            workbook = Workbook()
            # Remove the default blank sheet; one sheet is added per season below.
            workbook.remove(workbook.active)

            for season in sorted(seasons):
                sheet = workbook.create_sheet(title=f"Season {season}")

                sheet.cell(row=1, column=1, value=f"{player_name} - Season {season} Match History")
                sheet.cell(row=1, column=1).font = TITLE_FONT
                sheet.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(COLUMN_HEADERS))

                months = get_months_with_history(cursor, player_id, season)
                next_row = 3
                for month in months:
                    month_rows = get_full_match_history_by_season_month(
                        cursor, player_id, season, month
                    )
                    next_row = _write_month_table(sheet, next_row, month, month_rows)

                _autofit_columns(sheet, skip_cols=frozenset({STREAK_COL, DATE_COL}))
                _apply_fixed_column_widths(sheet)

        buf = io.BytesIO()
        workbook.save(buf)
        buf.seek(0)

        filename = f"{_sanitize_filename(player_name)}_match_history.xlsx"
        await interaction.followup.send(
            content=f"Match history export for **{player_name}**.",
            file=discord.File(buf, filename=filename),
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(ExportCog(bot))