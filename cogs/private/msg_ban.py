"""-ban_msg / -unban_msg: ban or unban a user from using /msg.

Both commands are restricted to the private/management guild, same as
-support. A banned user gets a rejection message (with the reason) the next
time they try /msg, instead of having their message relayed. Unbanning also
proactively DMs the user, since there's no equivalent "next attempt" moment
to surface that message on.
"""

from __future__ import annotations

import discord
from discord.ext import commands

from db.elo_db import clear_msg_ban, set_msg_ban
from utils import PRIVATE_GUILD_ID, is_admin
from utils.logger import bot_log


_NO_REASON_TEXT = "-"


class PrivateMsgBanCog(commands.Cog, name="PrivateMsgBan"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @staticmethod
    def _sanitize_reason(reason: str | None) -> str:
        """Strip out every double-quote character from the given reason
        (so it can't break out of the surrounding message formatting) and
        fall back to a plain "-" when nothing - or only whitespace - is
        left, since the reason is optional on both -ban_msg and
        -unban_msg."""
        if reason is None:
            return _NO_REASON_TEXT
        cleaned = reason.replace('"', "").strip()
        return cleaned if cleaned else _NO_REASON_TEXT

    @commands.command(
        name="ban_msg",
        hidden=True,
        help="Ban a user from using /msg (private guild only).",
    )
    @commands.check(is_admin)
    async def ban_msg(
        self, ctx: commands.Context, discord_id: int, *, reason: str | None = None
    ) -> None:
        """Usage: -ban_msg <discord_id> [reason]"""
        if ctx.guild is None or ctx.guild.id != PRIVATE_GUILD_ID:
            await ctx.send(
                embed=discord.Embed(
                    title="Not Available",
                    description="This command is only available in the private server.",
                    color=discord.Color.red(),
                )
            )
            return

        reason = self._sanitize_reason(reason)
        set_msg_ban(PRIVATE_GUILD_ID, discord_id, reason)

        await ctx.send(
            embed=discord.Embed(
                title="User Banned",
                description=(
                    f"<@{discord_id}> (`{discord_id}`) can no longer use `/msg`.\n"
                    f"Reason: {reason}"
                ),
                color=discord.Color.orange(),
            )
        )
        await bot_log(
            ctx.bot,
            ctx.guild.id,
            "Command used: -ban_msg",
            level="WARNING",
            fields={
                "User": f"{ctx.author.mention} ({ctx.author.id})",
                "Target": f"<@{discord_id}> ({discord_id})",
                "Reason": reason,
            },
        )

    @commands.command(
        name="unban_msg",
        hidden=True,
        help="Unban a user from using /msg (private guild only).",
    )
    @commands.check(is_admin)
    async def unban_msg(
        self, ctx: commands.Context, discord_id: int, *, reason: str | None = None
    ) -> None:
        """Usage: -unban_msg <discord_id> [reason]"""
        if ctx.guild is None or ctx.guild.id != PRIVATE_GUILD_ID:
            await ctx.send(
                embed=discord.Embed(
                    title="Not Available",
                    description="This command is only available in the private server.",
                    color=discord.Color.red(),
                )
            )
            return

        reason = self._sanitize_reason(reason)
        was_banned = clear_msg_ban(PRIVATE_GUILD_ID, discord_id)

        await ctx.send(
            embed=discord.Embed(
                title="User Unbanned" if was_banned else "User Was Not Banned",
                description=(
                    f"<@{discord_id}> (`{discord_id}`) can use `/msg` again.\n"
                    f"Reason: {reason}"
                ),
                color=discord.Color.green() if was_banned else discord.Color.blue(),
            )
        )

        # Best-effort DM: let the user know directly, since (unlike the ban
        # message) there's no /msg attempt to react to and surface this on.
        try:
            user = self.bot.get_user(discord_id) or await self.bot.fetch_user(discord_id)
            await user.send(
                f"**Ban Lifted**\nYour ban from this command has ended.\nReason: {reason}"
            )
        except Exception:
            pass

        await bot_log(
            ctx.bot,
            ctx.guild.id,
            "Command used: -unban_msg",
            level="WARNING",
            fields={
                "User": f"{ctx.author.mention} ({ctx.author.id})",
                "Target": f"<@{discord_id}> ({discord_id})",
                "Reason": reason,
            },
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(PrivateMsgBanCog(bot))