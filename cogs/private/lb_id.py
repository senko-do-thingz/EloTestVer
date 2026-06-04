import discord
from discord.ext import commands

from db.elo_db import db_exists, get_db_path
from utils import PRIVATE_GUILD_ID
from utils.logger import bot_log

LB_ID_ALLOWED_GUILD_ID = PRIVATE_GUILD_ID


class PrivateLbIdCog(commands.Cog, name="PrivateLbId"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="lb_id", hidden=True)
    async def lb_id(self, ctx: commands.Context, server_id: int):
        """View leaderboard for another server by guild ID (private guild only)."""
        if ctx.guild is None or ctx.guild.id != LB_ID_ALLOWED_GUILD_ID:
            await ctx.send(
                embed=discord.Embed(
                    title="Not Available",
                    description="This command is not available in this server.",
                    color=discord.Color.red(),
                )
            )
            return

        if not db_exists(server_id):
            db_path = get_db_path(server_id).replace("\\", "/")
            await ctx.send(
                embed=discord.Embed(
                    title="Database Not Found",
                    description=(
                        f"No database found for server ID **{server_id}** (`{db_path}`)."
                    ),
                    color=discord.Color.red(),
                )
            )
            return

        lb_cog = self.bot.cogs.get("LeaderboardCog")
        if lb_cog is None:
            await ctx.send(
                embed=discord.Embed(
                    title="Error",
                    description="LeaderboardCog is not loaded.",
                    color=discord.Color.red(),
                )
            )
            return

        from cogs.leaderboard import LeaderboardView
        view = LeaderboardView(ctx, db_guild_id=server_id)
        await view.update_leaderboard(ctx)

        await bot_log(
            ctx.bot,
            ctx.guild.id,
            "Command used: -lb_id",
            level="INFO",
            fields={
                "User": f"{ctx.author.mention} ({ctx.author.id})",
                "Target Server": str(server_id),
                "Input": f"`{ctx.message.content[:200]}`",
            },
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(PrivateLbIdCog(bot))