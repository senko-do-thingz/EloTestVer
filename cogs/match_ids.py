"""`-match_ids` (admin only): export every recorded match in this guild to
an Excel file, showing each match's ID (needed for `-revert <match_id>`)
plus the exact date/time (down to the minute) it was recorded.
"""

from __future__ import annotations

import discord
from discord.ext import commands

from db.elo_db import db_connection
from db.match_history_db import get_all_match_history_rows
from utils import is_admin
from utils.logger import bot_log
from utils.match_export import build_match_listing_workbook, workbook_to_file_bytes


def _sanitize_filename(name: str) -> str:
    safe = "".join(c for c in name if c.isalnum() or c in ("-", "_"))
    return safe or "server"


class MatchIdsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(
        name="match_ids",
        hidden=True,
        help="Export every recorded match's ID in this server to an Excel file (admin only).",
    )
    @commands.check(is_admin)
    async def match_ids(self, ctx: commands.Context) -> None:
        guild_id = ctx.guild.id

        with db_connection(guild_id) as (_conn, cursor):
            rows = get_all_match_history_rows(cursor)
            cursor.execute("SELECT id, name FROM players")
            name_lookup = {str(pid): name for pid, name in cursor.fetchall()}

        if not rows:
            await ctx.send(
                embed=discord.Embed(
                    title="Match IDs",
                    description="No matches have been recorded on this server yet.",
                    color=discord.Color.blue(),
                )
            )
            return

        workbook = build_match_listing_workbook(
            rows,
            name_lookup,
            sheet_title="Match IDs",
            header_text=f"Server {guild_id} - All Recorded Matches",
        )
        buf = workbook_to_file_bytes(workbook)

        filename = f"{_sanitize_filename(str(guild_id))}_match_ids.xlsx"
        await ctx.send(
            content=(
                f"Match ID export - {len(rows)} match row(s). "
                "Use a `Match ID` with `-revert <match_id>` to undo that match."
            ),
            file=discord.File(buf, filename=filename),
        )

        await bot_log(
            ctx.bot,
            guild_id,
            "Command used: -match_ids",
            level="INFO",
            fields={"User": f"{ctx.author.mention} ({ctx.author.id})"},
        )

    @match_ids.error
    async def match_ids_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.CheckFailure):
            await ctx.send(
                embed=discord.Embed(
                    title="Permission Denied",
                    description="You do not have permission to use this command.",
                    color=discord.Color.red(),
                )
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(MatchIdsCog(bot))