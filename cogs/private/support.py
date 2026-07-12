import discord
from discord.ext import commands

from db.elo_db import get_support_channel_id, set_support_channel_id
from utils import PRIVATE_GUILD_ID, is_admin
from utils.logger import bot_log


class PrivateSupportCog(commands.Cog, name="PrivateSupport"):
    """-support <channel_id>: configure the DM relay destination for /msg.

    Every time a user runs /msg in the bot's DMs, the message is relayed
    into a thread (named after the sender) under the channel configured
    here. Only usable on the private/management guild, same as -log.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(
        name="support",
        hidden=True,
        help="Set or clear the channel /msg relays DM messages into (private guild only).",
    )
    @commands.check(is_admin)
    async def support(
        self, ctx: commands.Context, *, channel: discord.TextChannel | None = None
    ):
        """
        Usage:
          -support <channel>   - set the support relay channel
          -support off         - clear / disable it
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
            set_support_channel_id(PRIVATE_GUILD_ID, None)
            await ctx.send(
                embed=discord.Embed(
                    title="Support Channel Cleared",
                    description="/msg will no longer relay DM messages anywhere.",
                    color=discord.Color.orange(),
                )
            )
            await bot_log(
                ctx.bot,
                ctx.guild.id,
                "Command used: -support",
                level="WARNING",
                fields={
                    "User": f"{ctx.author.mention} ({ctx.author.id})",
                    "Action": "Support channel **disabled**",
                },
            )
            return

        if channel is None:
            current_id = get_support_channel_id(PRIVATE_GUILD_ID)
            if current_id:
                ch = ctx.guild.get_channel(current_id)
                desc = f"Current support channel: {ch.mention if ch else str(current_id)}"
            else:
                desc = (
                    "No support channel is currently set.\n"
                    "Usage: `-support <channel>` or `-support off`."
                )
            await ctx.send(
                embed=discord.Embed(
                    title="Support Channel",
                    description=desc,
                    color=discord.Color.blue(),
                )
            )
            return

        set_support_channel_id(PRIVATE_GUILD_ID, channel.id)
        await ctx.send(
            embed=discord.Embed(
                title="Support Channel Set",
                description=(
                    f"/msg will now relay every DM message into a thread under "
                    f"{channel.mention}, named after the sender."
                ),
                color=discord.Color.green(),
            )
        )
        await bot_log(
            ctx.bot,
            ctx.guild.id,
            "Command used: -support",
            level="WARNING",
            fields={
                "User": f"{ctx.author.mention} ({ctx.author.id})",
                "Action": f"Support channel set to {channel.mention}",
            },
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(PrivateSupportCog(bot))