"""/nickname: assign a short nickname to a server ID.

Used purely for file naming in -get_lb, -sum_lb, and the automatic monthly
-decay task (e.g. "QS_S6M3.db" instead of a raw server ID), so archives are
easier to tell apart at a glance.
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from db.elo_db import get_nickname, set_nickname
from utils.logger import bot_log
from utils.private_guard import is_private_allowed


class PrivateNicknameCog(commands.Cog, name="PrivateNickname"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="nickname",
        description="Set a server's nickname, used for -get_lb/-sum_lb/-decay file names.",
    )
    @app_commands.describe(
        server_id="The server (guild) ID to name",
        name="The nickname to use, e.g. 'QS'",
    )
    @app_commands.guild_only()
    async def nickname(
        self, interaction: discord.Interaction, server_id: str, name: str
    ) -> None:
        if not is_private_allowed(interaction.guild):
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="Not Available",
                    description="This command is only available in the private server.",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
            return

        try:
            guild_id = int(server_id)
        except ValueError:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="Invalid Server ID",
                    description="`server_id` must be a numeric Discord server ID.",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
            return

        name = name.strip()
        if not name or len(name) > 32:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="Invalid Nickname",
                    description="Nickname must be 1-32 characters.",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
            return

        old_name = get_nickname(guild_id)
        set_nickname(guild_id, name)

        await interaction.response.send_message(
            embed=discord.Embed(
                title="Nickname Set",
                description=(
                    f"Server `{guild_id}` nickname: "
                    f"{f'`{old_name}` -> ' if old_name else ''}`{name}`"
                ),
                color=discord.Color.green(),
            ),
            ephemeral=True,
        )

        await bot_log(
            self.bot,
            interaction.guild.id if interaction.guild else 0,
            "Command used: /nickname",
            level="INFO",
            fields={
                "User": f"{interaction.user} ({interaction.user.id})",
                "Server": str(guild_id),
                "Nickname": name,
            },
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(PrivateNicknameCog(bot))