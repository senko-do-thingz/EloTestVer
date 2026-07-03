from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from db.elo_db import db_connection
from db.match_history_db import get_player_matches_count, get_recent_match_history
from utils import render_history_image_async

DEFAULT_HISTORY_COUNT = 10


class HistoryCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _history_impl(self, ctx: commands.Context, count: int | None) -> None:
        # Modified: empty/omitted input now defaults to the 10 most recent
        # matches instead of erroring out or requiring an explicit count.
        if count is None:
            count = DEFAULT_HISTORY_COUNT

        if count < 1:
            await ctx.send(
                embed=discord.Embed(
                    title="History Error",
                    description="Count must be at least 1.",
                    color=discord.Color.red(),
                )
            )
            return

        user = ctx.author
        with db_connection(ctx.guild.id) as (_conn, cursor):
            cursor.execute("SELECT name FROM players WHERE id=?", (user.id,))
            row = cursor.fetchone()
            if not row:
                await ctx.send(
                    embed=discord.Embed(
                        title="Player Not Registered",
                        description=(
                            f"{user.mention} is not registered. "
                            "Use `-register <name>` to sign up!"
                        ),
                        color=discord.Color.red(),
                    )
                )
                return

            player_name = row[0]
            total_matches = get_player_matches_count(cursor, user.id)
            if total_matches <= 0:
                await ctx.send(
                    embed=discord.Embed(
                        title="Match History",
                        description=f"**{player_name}** has no recorded matches yet.",
                        color=discord.Color.blue(),
                    )
                )
                return

            limit = min(count, total_matches)
            history_rows = get_recent_match_history(cursor, user.id, limit)

        # Modified: render match history as an image table (colored win/loss
        # accent bar + DATE/OPPONENT/W/L/+-/INIT/FINAL grid) instead of a
        # single monospace text block, to match ClashPerk's log style.
        image_rows = [
            (
                datetime.fromtimestamp(r.played_at, tz=timezone.utc).strftime("%m-%d"),
                r.opponent_name,
                r.wins,
                r.losses,
                r.elo_delta,
                r.elo_init,
                r.elo_final,
            )
            for r in history_rows
        ]
        buf = await render_history_image_async(
            image_rows,
            title=f"{player_name}'s Match History",
            summary=f"Showing {len(history_rows)} of {total_matches} match(es)",
        )
        filename = "history.png"
        file = discord.File(buf, filename=filename)
        embed = discord.Embed(color=discord.Color.dark_grey())
        embed.set_image(url=f"attachment://{filename}")
        await ctx.send(embed=embed, file=file)

    @commands.hybrid_command(
        name="history",
        description="Show your recent match history.",
        with_app_command=True,
    )
    @app_commands.describe(
        count=f"Number of recent matches to show (default {DEFAULT_HISTORY_COUNT} if left empty)"
    )
    @app_commands.guild_only()
    async def history(self, ctx: commands.Context, count: int | None = None):
        await self._history_impl(ctx, count)


async def setup(bot: commands.Bot):
    await bot.add_cog(HistoryCog(bot))