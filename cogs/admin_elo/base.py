"""Shared helpers for all AdminElo sub-cogs."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from config import YES_EMOJI, NO_EMOJI


class AdminEloBaseCog(commands.Cog):
    """Base class providing shared utilities for admin Elo commands."""

    def __init__(self, bot: commands.Bot) -> None:
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

    async def cog_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        """Slash-command counterpart of cog_command_error.

        Without this, an app_commands.CheckFailure (e.g. is_admin_interaction
        rejecting a non-admin user) is never caught, so the interaction is
        left unanswered and Discord shows "The application did not respond"
        instead of a proper permission-denied message.
        """
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

    async def _remove_bot_reactions(self, message: discord.Message) -> None:
        bot_user = getattr(self.bot, "user", None)
        if not bot_user:
            return
        for emoji in (YES_EMOJI, NO_EMOJI):
            try:
                await message.remove_reaction(emoji, bot_user)
            except Exception:
                pass