import discord
from discord.ext import commands

from db import get_log_channel_id, set_log_channel_id
from utils import PRIVATE_GUILD_ID, is_admin
from utils.logger import bot_log


class PrivateLogCog(commands.Cog, name="PrivateLog"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(
        name="log",
        hidden=True,
        help="Set or clear the log channel for bot activity logs (private guild only).",
    )
    @commands.check(is_admin)
    async def set_log_channel(
        self, ctx: commands.Context, *, channel: discord.TextChannel | None = None
    ):
        """
        Usage:
          -log <channel>   — set the log channel (ID, #channel, or name)
          -log off         — clear / disable the log channel
        """
        if ctx.guild is None or ctx.guild.id != PRIVATE_GUILD_ID:
            await ctx.send(
                embed=discord.Embed(
                    title="Not Available",
                    description="This command is only available in the private server.",
                    color=discord.Color.red(),
                )
            )
            return

        raw = ctx.message.content.strip()
        parts = raw.split(None, 1)
        if len(parts) > 1 and parts[1].strip().lower() == "off":
            set_log_channel_id(PRIVATE_GUILD_ID, None)
            await ctx.send(
                embed=discord.Embed(
                    title="Log Channel Cleared",
                    description="Bot logging has been disabled.",
                    color=discord.Color.orange(),
                )
            )
            await bot_log(
                ctx.bot,
                ctx.guild.id,
                "Command used: -log",
                level="WARNING",
                fields={
                    "User": f"{ctx.author.mention} ({ctx.author.id})",
                    "Action": "Log channel **disabled**",
                },
            )
            return

        if channel is None:
            current_id = get_log_channel_id(PRIVATE_GUILD_ID)
            if current_id:
                ch = ctx.guild.get_channel(current_id)
                desc = f"Current log channel: {ch.mention if ch else str(current_id)}"
            else:
                desc = "No log channel is currently set.\nUsage: `-log <channel>` or `-log off`."
            await ctx.send(
                embed=discord.Embed(
                    title="Log Channel",
                    description=desc,
                    color=discord.Color.blue(),
                )
            )
            return

        set_log_channel_id(PRIVATE_GUILD_ID, channel.id)
        await ctx.send(
            embed=discord.Embed(
                title="Log Channel Set",
                description=f"Bot logs will now be sent to {channel.mention}.",
                color=discord.Color.green(),
            )
        )
        await bot_log(
            ctx.bot,
            ctx.guild.id,
            "Command used: -log",
            level="WARNING",
            fields={
                "User": f"{ctx.author.mention} ({ctx.author.id})",
                "Action": f"Log channel set to {channel.mention}",
            },
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(PrivateLogCog(bot))