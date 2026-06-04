"""Shared helpers for all AdminElo sub-cogs."""

from __future__ import annotations

import discord
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

    async def _remove_bot_reactions(self, message: discord.Message) -> None:
        bot_user = getattr(self.bot, "user", None)
        if not bot_user:
            return
        for emoji in (YES_EMOJI, NO_EMOJI):
            try:
                await message.remove_reaction(emoji, bot_user)
            except Exception:
                pass