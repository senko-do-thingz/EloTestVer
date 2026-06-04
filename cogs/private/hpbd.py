import discord
from discord.ext import commands

from utils.logger import bot_log
from utils.private_guard import is_private_allowed, send_not_available


class PrivateHpbdCog(commands.Cog, name="PrivateHpbd"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="hpbd", hidden=True)
    async def hpbd(self, ctx: commands.Context, user: discord.User, server_id: int):
        """Send a happy-birthday message to a user in another server's #general channel."""
        if not is_private_allowed(ctx.guild):
            await send_not_available(ctx)
            return

        guild = self.bot.get_guild(server_id)
        if guild is None:
            await ctx.send(
                embed=discord.Embed(
                    title="Server Not Found",
                    description=f"No server found with ID {server_id}.",
                    color=discord.Color.red(),
                )
            )
            return

        target_channel = None
        for channel in guild.text_channels:
            if channel.name == "general":
                target_channel = channel
                break

        if target_channel is None:
            await ctx.send(
                embed=discord.Embed(
                    title="Channel Not Found",
                    description="No #general channel exists in the target server.",
                    color=discord.Color.red(),
                )
            )
            return

        try:
            await target_channel.send(f"🥳 Happy birthday, {user.mention} 🎂")
            await ctx.message.add_reaction("🎉")
            await bot_log(
                ctx.bot,
                ctx.guild.id if ctx.guild else 0,
                "Command used: -hpbd",
                level="INFO",
                fields={
                    "User": f"{ctx.author.mention} ({ctx.author.id})",
                    "Target User": f"{user.mention} ({user.id})",
                    "Target Server": str(server_id),
                    "Sent To": f"#{target_channel.name}",
                },
            )
        except Exception:
            await ctx.send(
                embed=discord.Embed(
                    title="Failed to Send",
                    description="The bot could not send a message to the #general channel of the specified server.",
                    color=discord.Color.red(),
                )
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(PrivateHpbdCog(bot))