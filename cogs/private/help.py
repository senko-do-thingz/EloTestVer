"""
private/help.py
~~~~~~~~~~~~~~~
Completely replaces the -help command from cogs/help.py with a version
that appends private commands when invoked inside the private guild.

Load ORDER matters: this cog must be loaded AFTER cogs.help so that
bot.remove_command("help") / add_command here wins.
elobot.py auto-discovers cogs via pkgutil — private sub-package modules
are loaded by the explicit loop in elobot.py (see load_extensions).
"""

import discord
from discord.ext import commands

from db import get_seasonal_report_enabled
from utils.logger import PRIVATE_GUILD_ID


class PrivateHelpCog(commands.Cog, name="PrivateHelp"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Remove the help registered by HelpCog so this one takes over.
        bot.remove_command("help")

    @commands.command(name="help", help="Show available commands.")
    async def help_command(self, ctx: commands.Context):
        is_private = ctx.guild is not None and ctx.guild.id == PRIVATE_GUILD_ID

        commands_list = [
            ("-qna", "Show frequently asked questions."),
            ("-register <name>", "Register with a custom name and initial 500 Elo."),
            ("-rename <new_name>", "Change your name (7-day cooldown)."),
            ("-report / -rp <@player1> <score> <@player2>", "Report a match result."),
        ]
        if get_seasonal_report_enabled(ctx.guild.id):
            commands_list.append(
                (
                    "-seasonal_report / -srp <@player1> <score> <@player2>",
                    "Report a seasonal match (1.5x Elo). Same usage as -report.",
                ),
            )
        commands_list.extend(
            [
                (
                    "-leaderboard / lb",
                    "Show the leaderboard of players based on various stats.",
                ),
                ("-lbs", "Show the leaderboards for each season."),
                (
                    "-stats / -stats <player>",
                    "Show your stats / Show the mentioned player's stats. <player> can be @mention, Discord ID, or registered name.",
                ),
                (
                    "-compare / -cp <player1> <player2>",
                    "Compare two players. <player> can be @mention, Discord ID, or registered name.",
                ),
                (
                    "-leagues / -leagues <name>",
                    "Show discovered leagues, Elo ranges, and player counts. "
                    "Add a name (substring match) to list everyone in that league.",
                ),
                ("-help", "Show this help message."),
                ("-adhelp", "Show the admin help message."),
            ]
        )

        if is_private:
            commands_list.extend(
                [
                    (
                        "-lb_id <server_id>",
                        "View the leaderboard of another server by its guild ID.",
                    ),
                    (
                        "-gld <server_id>",
                        "View + export the full leaderboard of another server (no filters). "
                        "Sends a zip file and an interactive leaderboard.\n"
                        "*(Dev Mode — requires Developer Mode enabled to copy Server ID)*",
                    ),
                    (
                        "-st <server_id> <player_id>",
                        "View stats of a player in another server by their Discord ID.",
                    ),
                    (
                        "-log [<channel> | off]",
                        "Set or clear the log channel for bot activity logs.",
                    ),
                    (
                        "-hpbd <@user> <server_id>",
                        "Send a happy-birthday message to a user in another server's #general.",
                    ),
                ]
            )

        def create_embed():
            title = "📜 Help - General Commands"
            if is_private:
                title += " (+ Private Commands)"
            embed = discord.Embed(
                title=title,
                description="Available commands:",
                color=discord.Color.blue(),
            )
            for name, desc in commands_list:
                embed.add_field(name=name, value=desc, inline=False)
            return embed

        await ctx.send(embed=create_embed())


async def setup(bot: commands.Bot):
    await bot.add_cog(PrivateHelpCog(bot))