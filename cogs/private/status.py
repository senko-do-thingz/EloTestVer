import platform
from datetime import datetime, timezone

import discord
from discord.ext import commands

from utils.logger import bot_log
from utils.private_guard import is_private_allowed, send_not_available


class PrivateStatusCog(commands.Cog, name="PrivateStatus"):
    """Private diagnostics command showing internal bot/process status.

    NOTE: this file used to be an exact copy-paste of gld.py (same class
    name "PrivateGldCog", same cog name "PrivateGld"), which made
    `bot.add_cog()` raise "Cog named 'PrivateGld' already loaded" whenever
    both extensions were loaded, crashing this extension on every startup.
    It has been rewritten as its own distinct command.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="status", hidden=True)
    async def status(self, ctx: commands.Context):
        """Show internal bot/process status (private guild only)."""
        if not is_private_allowed(ctx.guild):
            await send_not_available(ctx)
            return

        latency_ms = round(self.bot.latency * 1000) if self.bot.latency else 0
        current_time = datetime.now(timezone.utc).strftime("%d %b %Y, %H:%M:%S UTC")

        loaded_extensions = sorted(self.bot.extensions.keys())
        loaded_cogs = sorted(self.bot.cogs.keys())

        prefix_command_names = {cmd.qualified_name for cmd in self.bot.commands}
        tree_command_names = {cmd.qualified_name for cmd in self.bot.tree.get_commands()}
        command_count = len(prefix_command_names | tree_command_names)

        embed = discord.Embed(
            title="Bot Status",
            color=discord.Color.blue(),
        )
        embed.add_field(name="Servers", value=str(len(self.bot.guilds)), inline=True)
        embed.add_field(name="Latency", value=f"{latency_ms} ms", inline=True)
        embed.add_field(name="Commands", value=str(command_count), inline=True)
        embed.add_field(name="Extensions Loaded", value=str(len(loaded_extensions)), inline=True)
        embed.add_field(name="Cogs Loaded", value=str(len(loaded_cogs)), inline=True)
        embed.add_field(
            name="Python / discord.py",
            value=f"{platform.python_version()} / {discord.__version__}",
            inline=True,
        )
        embed.add_field(name="Bot Time (UTC)", value=current_time, inline=False)
        embed.add_field(
            name="Loaded Extensions",
            value="\n".join(f"`{ext}`" for ext in loaded_extensions) or "None",
            inline=False,
        )

        await ctx.send(embed=embed)

        await bot_log(
            ctx.bot,
            ctx.guild.id,
            "Command used: -status",
            level="INFO",
            fields={
                "User": f"{ctx.author.mention} ({ctx.author.id})",
            },
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(PrivateStatusCog(bot))