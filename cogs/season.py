"""Season/month configuration commands (admin only, slash-only).

- /months sets how many months a season can hold on this server.
- /season updates the server's current season/month, used by -decay,
  /info, and /history to tag and filter match history.
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from db.elo_db import get_max_month, set_max_month, set_season_month
from utils import is_admin_interaction


class SeasonCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        """Without this, an app_commands.CheckFailure from is_admin_interaction
        (used on /months and /season) is never caught, so the interaction is
        left unanswered and Discord shows "The application did not respond"
        instead of a proper permission-denied message."""
        if isinstance(error, app_commands.CheckFailure):
            embed = discord.Embed(
                title="Permission Denied",
                description="You do not have permission to use this admin command.",
                color=discord.Color.red(),
            )
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        raise error

    # ------------------------------------------------------------------
    # /months
    # ------------------------------------------------------------------

    @app_commands.command(
        name="months",
        description="Set the total number of months a season can have on this server (admin only).",
    )
    @app_commands.describe(
        total_months=(
            "Maximum number of months per season (e.g. 3 -> a season has "
            "month 1, 2, 3)"
        )
    )
    @app_commands.guild_only()
    @app_commands.check(is_admin_interaction)
    async def months(
        self,
        interaction: discord.Interaction,
        total_months: app_commands.Range[int, 1, 100],
    ) -> None:
        set_max_month(interaction.guild_id, total_months)
        embed = discord.Embed(
            title="Months Updated",
            description=(
                f"This server's seasons now hold **{total_months}** month"
                f"{'s' if total_months != 1 else ''} "
                f"(month 1 to month {total_months})."
            ),
            color=discord.Color.green(),
        )
        await interaction.response.send_message(embed=embed)

    # ------------------------------------------------------------------
    # /season
    # ------------------------------------------------------------------

    @app_commands.command(
        name="season",
        description="Update the current season and month for this server (admin only).",
    )
    @app_commands.describe(
        season="Season number",
        month="Month number within the season",
    )
    @app_commands.guild_only()
    @app_commands.check(is_admin_interaction)
    async def season(
        self,
        interaction: discord.Interaction,
        season: app_commands.Range[int, 1, 999],
        month: app_commands.Range[int, 1, 100],
    ) -> None:
        max_month = get_max_month(interaction.guild_id)
        if max_month is None:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="Months Not Set",
                    description=(
                        "This server has not set a month limit yet. "
                        "Use `/months` first to set the total number of "
                        "months a season can have."
                    ),
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
            return

        if month > max_month:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="Invalid Month",
                    description=(
                        f"This server's seasons only go up to month "
                        f"{max_month}. Choose a month between 1 and {max_month}."
                    ),
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
            return

        set_season_month(interaction.guild_id, season, month)
        embed = discord.Embed(
            title="Season Updated",
            description=f"This server is now set to **Season: {season}, Month: {month}**.",
            color=discord.Color.green(),
        )
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SeasonCog(bot))