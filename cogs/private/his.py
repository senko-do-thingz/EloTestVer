"""
Admin command to view history of a player on any server.
Usage: -his <server_id> <player_id> [season] [month]
Only works in the private management server.
"""

import discord
from discord.ext import commands

from cogs.history import HistoryView
from db.elo_db import db_connection, get_season_month
from db.match_history_db import get_months_with_history
from utils import is_admin, PRIVATE_GUILD_ID


class FakeUser:
    """Mock user object to satisfy HistoryView's dependencies."""
    def __init__(self, user_id: int):
        self.id = str(user_id)
        self.mention = f"<@{user_id}>"

    @property
    def display_avatar(self):
        class FakeAvatar:
            async def read(self):
                raise Exception("No avatar available")
        return FakeAvatar()


class FakeCtx:
    """Mock context object to supply the target server_id for HistoryView."""
    def __init__(self, guild_id: int):
        self.guild = discord.Object(id=guild_id)


class AdminHisCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(
        name="his",
        help="View match history of any player on any server (admin only).",
        usage="-his <server_id> <player_id> [season] [month]"
    )
    @commands.check(is_admin)
    async def his(self, ctx: commands.Context, server_id: int, player_id: int, season: int = None, month: int = None):
        """View the match history of a specific player on a specific server."""
        if ctx.guild is None or ctx.guild.id != PRIVATE_GUILD_ID:
            return

        # Fetch real user to get their avatar, fallback to FakeUser if not found
        try:
            target = await self.bot.fetch_user(player_id)
        except discord.NotFound:
            target = FakeUser(player_id)
        
        fake_ctx = FakeCtx(server_id)

        if season is None and month is None:
            season, month = get_season_month(server_id)
        elif season is not None and month is None:
            # If only season is provided, default to the latest month with data
            with db_connection(server_id) as (_conn, cursor):
                months = get_months_with_history(cursor, str(target.id), season)
            if not months:
                await ctx.send(embed=discord.Embed(
                    title="Match History",
                    description=f"No recorded matches in Season {season}.",
                    color=discord.Color.blue(),
                ))
                return
            month = max(months)

        view = HistoryView(
            cog=None,
            ctx=fake_ctx,
            target=target,
            season=season,
            month=month,
        )

        # Remove emojis from buttons to comply with the rules
        view.prev_button.emoji = None
        view.next_button.emoji = None
        view.refresh_button.emoji = None
        view.refresh_button.label = "Refresh"
        
        # update_history uses the provided context to send the message
        await view.update_history(ctx)

    @his.error
    async def his_error(self, ctx: commands.Context, error):
        """Error handler for the -his command."""
        if isinstance(error, commands.CheckFailure):
            # Ignore silently if not admin or not in private server
            pass
        elif isinstance(error, commands.MissingRequiredArgument):
            if ctx.guild and ctx.guild.id == PRIVATE_GUILD_ID:
                await ctx.send(embed=discord.Embed(
                    title="Missing Argument",
                    description="Usage: `-his <server_id> <player_id> [season] [month]`",
                    color=discord.Color.red(),
                ))
        elif isinstance(error, commands.BadArgument):
            if ctx.guild and ctx.guild.id == PRIVATE_GUILD_ID:
                await ctx.send(embed=discord.Embed(
                    title="Bad Argument",
                    description="Server ID and Player ID must be valid numbers.",
                    color=discord.Color.red(),
                ))


async def setup(bot: commands.Bot):
    await bot.add_cog(AdminHisCog(bot))
