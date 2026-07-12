import os
import re
import discord
from discord.ext import commands

from db.elo_db import db_connection, get_nickname
from utils import (
    is_admin,
    aggregate_season_categories,
    build_leaderboard_categories,
    build_zip_bytes,
    sync_discord_ids_to_lb_archives,
    get_lb_root,
)


class AdminImportCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_command_error(self, ctx: commands.Context, error: Exception):
        if isinstance(error, commands.CheckFailure):
            await ctx.send(
                embed=discord.Embed(
                    title="Permission Denied",
                    description="You do not have permission to use this admin command.",
                    color=discord.Color.red(),
                )
            )
            return
        raise error

    @commands.command(
        help="Export all leaderboard stats to a zip file containing separate text files for each stat."
    )
    @commands.check(is_admin)
    async def get_lb(self, ctx: commands.Context):
        categories = build_leaderboard_categories(ctx.guild.id)
        if not categories:
            await ctx.send("No players have registered yet.")
            return

        buf = build_zip_bytes(categories)
        nickname = get_nickname(ctx.guild.id)
        zip_name = f"leaderboard_{nickname or ctx.guild.id}.zip"
        await ctx.send(file=discord.File(buf, filename=zip_name))

    @commands.command(
        help="Sync all registered players' Discord IDs into archived lb/<server_id>/ files."
    )
    @commands.check(is_admin)
    async def set_id(self, ctx: commands.Context, server_id: str):
        try:
            guild_id = int(server_id)
        except ValueError:
            await ctx.send(
                embed=discord.Embed(
                    title="Invalid Server ID",
                    description="Please provide a valid numeric server (guild) ID.",
                    color=discord.Color.red(),
                )
            )
            return

        lb_root = get_lb_root(guild_id)
        if not os.path.isdir(lb_root):
            await ctx.send(
                embed=discord.Embed(
                    title="Error",
                    description=(
                        f"Leaderboard directory does not exist: `lb/{guild_id}/`"
                    ),
                    color=discord.Color.red(),
                )
            )
            return

        with db_connection(guild_id) as (conn, cursor):
            cursor.execute("SELECT id, name FROM players")
            rows = cursor.fetchall()

        if not rows:
            await ctx.send(
                embed=discord.Embed(
                    title="No Players",
                    description="No registered players in the database.",
                    color=discord.Color.red(),
                )
            )
            return

        players_by_name = {name.lower(): str(discord_id) for discord_id, name in rows}
        files_updated, lines_updated, players_updated = sync_discord_ids_to_lb_archives(
            lb_root, players_by_name
        )

        if lines_updated == 0:
            description = (
                f"No matching entries found in `lb/` for the **{len(rows)}** registered "
                "player(s), or all IDs are already up to date."
            )
            color = discord.Color.orange()
        else:
            description = (
                f"Updated **{lines_updated}** line(s) across **{files_updated}** file(s) "
                f"for **{players_updated}** player(s) "
                f"(**{len(rows)}** registered in database)."
            )
            color = discord.Color.green()

        await ctx.send(
            embed=discord.Embed(
                title="Discord IDs Updated",
                description=description,
                color=color,
            )
        )

    @commands.command(
        help="Calculate and export the total leaderboard for a specific season from a given server's lb/ folder."
    )
    @commands.check(is_admin)
    async def sum_lb(self, ctx: commands.Context, server_id: int, season: str):
        season_match = re.search(r"\d+", season)
        if not season_match:
            await ctx.send(
                embed=discord.Embed(
                    title="Invalid Season Format",
                    description=(
                        "Please provide a season number (e.g., 'S2', 's2', 'Season 2', '2')"
                    ),
                    color=discord.Color.red(),
                )
            )
            return
        season_num = int(season_match.group())
        base = get_lb_root(server_id)
        if not os.path.isdir(base):
            await ctx.send(
                embed=discord.Embed(
                    title="Error",
                    description=(
                        f"No leaderboard archive found for server **{server_id}** "
                        f"(`lb/{server_id}/`)."
                    ),
                    color=discord.Color.red(),
                )
            )
            return

        # Find season directory (case-insensitive)
        seasons = [d for d in os.listdir(base) if os.path.isdir(os.path.join(base, d))]
        season_name = None
        for s in seasons:
            if re.match(r"^season\s+" + str(season_num) + r"$", s, re.IGNORECASE):
                season_name = s
                break

        if not season_name:
            await ctx.send(
                embed=discord.Embed(
                    title="Season Not Found",
                    description=f"Season {season_num} does not exist in server **{server_id}**.",
                    color=discord.Color.red(),
                )
            )
            return

        season_path = os.path.join(base, season_name)
        months = [
            d
            for d in os.listdir(season_path)
            if os.path.isdir(os.path.join(season_path, d)) and d.lower() != "overall"
        ]
        if not months:
            await ctx.send(
                embed=discord.Embed(
                    title="No Data Found",
                    description=f"No month data found in {season_name}.",
                    color=discord.Color.red(),
                )
            )
            return

        categories = aggregate_season_categories(season_path)
        if not categories:
            await ctx.send(
                embed=discord.Embed(
                    title="No Data Found",
                    description=f"No valid data found in {season_name}.",
                    color=discord.Color.red(),
                )
            )
            return

        buf = build_zip_bytes(categories)
        nickname = get_nickname(server_id)
        zip_name = f"sum_lb_{season_name.replace(' ', '_')}_{nickname or server_id}.zip"
        await ctx.send(file=discord.File(buf, filename=zip_name))


async def setup(bot: commands.Bot):
    await bot.add_cog(AdminImportCog(bot))