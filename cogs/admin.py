import discord
from discord.ext import commands

from utils import is_admin


class AdminCog(commands.Cog):
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
        help="Send a message to a specific channel in another server (admin only)."
    )
    @commands.check(is_admin)
    async def say(
        self, ctx: commands.Context, server_id: int, channel_name: str, *, message: str
    ):
        """
        Send a message to a specific channel.
        Usage: -say <server_id> <channel_name> <message>
        Example: -say 123456789 general Hello everyone!
        """
        try:
            # Get the target guild
            target_guild = self.bot.get_guild(server_id)
            if not target_guild:
                await ctx.send(
                    embed=discord.Embed(
                        title="Server Not Found",
                        description=f"Bot is not in server with ID {server_id}.",
                        color=discord.Color.red(),
                    )
                )
                return

            # Find the channel by name (case-insensitive)
            target_channel = None
            for channel in target_guild.text_channels:
                if channel.name.lower() == channel_name.lower():
                    target_channel = channel
                    break

            if not target_channel:
                await ctx.send(
                    embed=discord.Embed(
                        title="Channel Not Found",
                        description=f"Channel `{channel_name}` not found in server {target_guild.name}.",
                        color=discord.Color.red(),
                    )
                )
                return

            # Check if bot has permission to send messages in that channel
            if not target_channel.permissions_for(target_guild.me).send_messages:
                await ctx.send(
                    embed=discord.Embed(
                        title="Permission Denied",
                        description=f"Bot does not have permission to send messages in {target_channel.mention}.",
                        color=discord.Color.red(),
                    )
                )
                return

            # Check for attachments
            files = []
            if ctx.message.attachments:
                for attachment in ctx.message.attachments:
                    files.append(await attachment.to_file())

            # Send the message with attachments if any
            await target_channel.send(message, files=files)

            await ctx.send(
                embed=discord.Embed(
                    title="Message Sent",
                    description=f"Message sent to {target_channel.mention} in server **{target_guild.name}**.",
                    color=discord.Color.green(),
                )
            )

        except Exception as e:
            await ctx.send(
                embed=discord.Embed(
                    title="Error",
                    description=f"An error occurred: {str(e)}",
                    color=discord.Color.red(),
                )
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(AdminCog(bot))
