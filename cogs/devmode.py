import discord
from discord.ext import commands

from db.elo_db import get_dev_mode, set_dev_mode
from utils import is_admin
from utils.logger import bot_log


class UtilsCog(commands.Cog, name="Utils"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_command_error(self, ctx: commands.Context, error: Exception) -> None:
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
        name="devmode",
        help="Toggle dev mode for report formula comparison in -rp (admin only).",
    )
    @commands.check(is_admin)
    async def devmode(self, ctx: commands.Context):
        new_state = not get_dev_mode(ctx.guild.id)
        set_dev_mode(ctx.guild.id, new_state)
        state_str = "enabled" if new_state else "disabled"
        await ctx.send(f"devmode is {state_str}")
        await bot_log(
            ctx.bot,
            ctx.guild.id if ctx.guild else 0,
            "Command used: -devmode",
            level="WARNING",
            fields={
                "User": f"{ctx.author.mention} ({ctx.author.id})",
                "Server": f"{ctx.guild.name} ({ctx.guild.id})" if ctx.guild else "DM",
                "New State": state_str,
            },
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(UtilsCog(bot))