"""-nickname: assign a short nickname to a server ID.

Used purely for file naming in -get_lb, -sum_lb, and the automatic monthly
-decay task (e.g. "QS_S6M3.db" instead of a raw server ID), so archives are
easier to tell apart at a glance.

Prefix-only, admin-only (Elo Bot Manager role). No longer restricted to the
private/management guild - any server's admin can set their own server's
nickname (previously lived under cogs/private/, see that folder's other
commands for the private-guild-only equivalents).
"""

from __future__ import annotations

import discord
from discord.ext import commands

from db.elo_db import get_nickname, set_nickname
from utils import is_admin
from utils.logger import bot_log


class AdminEloNicknameCog(commands.Cog, name="AdminEloNickname"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(
        name="nickname",
        help="Set a server's nickname, used for -get_lb/-sum_lb/-decay file names (admin only).",
    )
    @commands.check(is_admin)
    async def nickname(
        self, ctx: commands.Context, server_id: str, *, name: str
    ) -> None:
        """
        Usage:
          -nickname <server_id> <name>
        """
        try:
            guild_id = int(server_id)
        except ValueError:
            await ctx.send(
                embed=discord.Embed(
                    title="Invalid Server ID",
                    description="`server_id` must be a numeric Discord server ID.",
                    color=discord.Color.red(),
                )
            )
            return

        name = name.strip()
        if not name or len(name) > 32:
            await ctx.send(
                embed=discord.Embed(
                    title="Invalid Nickname",
                    description="Nickname must be 1-32 characters.",
                    color=discord.Color.red(),
                )
            )
            return

        old_name = get_nickname(guild_id)
        set_nickname(guild_id, name)

        await ctx.send(
            embed=discord.Embed(
                title="Nickname Set",
                description=(
                    f"Server `{guild_id}` nickname: "
                    f"{f'`{old_name}` -> ' if old_name else ''}`{name}`"
                ),
                color=discord.Color.green(),
            )
        )

        await bot_log(
            ctx.bot,
            ctx.guild.id if ctx.guild else 0,
            "Command used: -nickname",
            level="INFO",
            fields={
                "User": f"{ctx.author.mention} ({ctx.author.id})",
                "Server": str(guild_id),
                "Nickname": name,
            },
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(AdminEloNicknameCog(bot))