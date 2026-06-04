"""
Guard helpers for private commands.

Instead of locking private commands to a single hardcoded PRIVATE_GUILD_ID,
they are now accessible in any guild where devmode is ON.
The private guild (PRIVATE_GUILD_ID) retains permanent access regardless of
devmode state, since it is the management/logging server.
"""

from __future__ import annotations

import discord
from discord.ext import commands

from db.elo_db import get_dev_mode
from utils.logger import PRIVATE_GUILD_ID


def is_private_allowed(guild: discord.Guild | None) -> bool:
    """
    Return True if private commands may run in this guild.

    Access is granted when:
    - The guild is the permanent private/management server (PRIVATE_GUILD_ID), OR
    - The guild has devmode enabled in its settings.
    """
    if guild is None:
        return False
    if guild.id == PRIVATE_GUILD_ID:
        return True
    return get_dev_mode(guild.id)


async def send_not_available(ctx: commands.Context) -> None:
    """Send the standard 'not available in this server' embed."""
    await ctx.send(
        embed=discord.Embed(
            title="Not Available",
            description="This command is not available in this server.",
            color=discord.Color.red(),
        )
    )