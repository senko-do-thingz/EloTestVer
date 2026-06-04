import io
import zipfile

import discord
from discord.ext import commands

from db.elo_db import db_connection, db_exists, get_db_path
from utils import calculate_win_rate, format_leaderboard_export_line
from utils.logger import bot_log
from utils.private_guard import is_private_allowed, send_not_available

class PrivateGldCog(commands.Cog, name="PrivateGld"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="gld", hidden=True)
    async def gld(self, ctx: commands.Context, server_id: int):
        """Export full leaderboard (no filters) from another server's DB as a zip (private guild only)."""
        if not is_private_allowed(ctx.guild):
            await send_not_available(ctx)
            return

        if not db_exists(server_id):
            db_path = get_db_path(server_id).replace("\\", "/")
            await ctx.send(
                embed=discord.Embed(
                    title="Database Not Found",
                    description=f"No database found for server ID **{server_id}** (`{db_path}`).",
                    color=discord.Color.red(),
                )
            )
            return

        with db_connection(server_id) as (conn, cursor):
            cursor.execute(
                "SELECT id, name, elo, games_won, games_lost, games_played_this_month FROM players"
            )
            all_players = cursor.fetchall()

        if not all_players:
            await ctx.send(
                embed=discord.Embed(
                    title="No Players Found",
                    description=f"No players registered in server **{server_id}**.",
                    color=discord.Color.orange(),
                )
            )
            return

        elo_sorted     = sorted(all_players, key=lambda x: x[2], reverse=True)
        wins_sorted    = sorted(all_players, key=lambda x: x[3], reverse=True)
        losses_sorted  = sorted(all_players, key=lambda x: x[4], reverse=True)
        monthly_sorted = sorted(all_players, key=lambda x: x[5], reverse=True)
        wr_sorted = sorted(
            [(did, name, calculate_win_rate(won, lost)) for did, name, _, won, lost, _ in all_players],
            key=lambda x: x[2],
            reverse=True,
        )

        elo_text     = "\n".join(format_leaderboard_export_line(name, elo,            did) for did, name, elo, _, _, _   in elo_sorted)
        wins_text    = "\n".join(format_leaderboard_export_line(name, won,            did) for did, name, _, won, _, _   in wins_sorted)
        losses_text  = "\n".join(format_leaderboard_export_line(name, lost,           did) for did, name, _, _, lost, _  in losses_sorted)
        wr_text      = "\n".join(format_leaderboard_export_line(name, f"{wr:.2f}",    did) for did, name, wr              in wr_sorted)
        monthly_text = "\n".join(format_leaderboard_export_line(name, games,          did) for did, name, _, _, _, games  in monthly_sorted)

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("data.txt", elo_text)
            zf.writestr("w.txt",    wins_text)
            zf.writestr("l.txt",    losses_text)
            zf.writestr("wr.txt",   wr_text)
            zf.writestr("games.txt", monthly_text)
        buf.seek(0)

        await ctx.send(
            content=f"Dev Mode — Leaderboard data from Server {server_id}",
            file=discord.File(buf, filename=f"leaderboard_{server_id}.zip"),
        )

        await bot_log(
            ctx.bot,
            ctx.guild.id,
            "Command used: -gld",
            level="INFO",
            fields={
                "User": f"{ctx.author.mention} ({ctx.author.id})",
                "Target Server": str(server_id),
                "Input": f"`{ctx.message.content[:200]}`",
            },
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(PrivateGldCog(bot))