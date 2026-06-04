"""Stat setter commands (Elo, wins, losses, monthly games) — admin only."""

from __future__ import annotations

import asyncio

import discord
from discord.ext import commands

from config import YES_EMOJI, NO_EMOJI
from db.elo_db import db_connection
from utils import is_admin, sync_league_roles
from utils.lb_cache import (
    invalidate as lb_cache_invalidate,
    schedule_rebuild as lb_cache_rebuild,
)

from .base import AdminEloBaseCog


class AdminEloSettersCog(AdminEloBaseCog):
    """Commands: set_e, set_eid, set_w, set_wid, set_l, set_lid, set_gid, set_stats."""

    # ------------------------------------------------------------------
    # Elo setters
    # ------------------------------------------------------------------

    @commands.command(help="Set the Elo of a specific player (admin only).")
    @commands.check(is_admin)
    async def set_e(self, ctx: commands.Context, member: discord.Member, elo: int) -> None:
        with db_connection(ctx.guild.id) as (conn, cursor):
            cursor.execute("SELECT name FROM players WHERE id=?", (member.id,))
            player_data = cursor.fetchone()

        if not player_data:
            await ctx.send(
                embed=discord.Embed(
                    title="Error",
                    description=f"{member.name} is not registered.",
                    color=discord.Color.red(),
                )
            )
            return

        player_name = player_data[0]
        embed = discord.Embed(
            title="Confirm Elo Change",
            description=(
                f"Are you sure you want to set {player_name}'s Elo to {elo}? "
                f"React with {YES_EMOJI} to confirm or {NO_EMOJI} to cancel within 5 minutes."
            ),
            color=discord.Color.orange(),
        )
        message = await ctx.send(embed=embed)
        await message.add_reaction(YES_EMOJI)
        await message.add_reaction(NO_EMOJI)

        def check(reaction, user):
            return (
                user == ctx.author
                and str(reaction.emoji) in [YES_EMOJI, NO_EMOJI]
                and reaction.message.id == message.id
            )

        try:
            reaction, _ = await self.bot.wait_for(
                "reaction_add", timeout=300.0, check=check
            )
            if str(reaction.emoji) == YES_EMOJI:
                with db_connection(ctx.guild.id) as (conn, cursor):
                    cursor.execute(
                        "UPDATE players SET elo=? WHERE id=?", (elo, member.id)
                    )
                    conn.commit()
                lb_cache_invalidate(ctx.guild.id)
                lb_cache_rebuild(self.bot, ctx.guild.id)
                await sync_league_roles(ctx.guild)
                embed = discord.Embed(
                    title="Elo Updated",
                    description=f"{player_name}'s Elo has been set to {elo}.",
                    color=discord.Color.green(),
                )
            else:
                embed = discord.Embed(
                    title="Operation Canceled",
                    description="Set Elo operation has been canceled.",
                    color=discord.Color.red(),
                )
            await self._remove_bot_reactions(message)
            await message.edit(embed=embed)
        except asyncio.TimeoutError:
            await self._remove_bot_reactions(message)
            await message.edit(
                embed=discord.Embed(
                    title="Timeout",
                    description="Set Elo confirmation timed out. Please try again.",
                    color=discord.Color.red(),
                )
            )

    @commands.command(
        help="Set the Elo of a specific player using their Discord ID (admin only)."
    )
    @commands.check(is_admin)
    async def set_eid(self, ctx: commands.Context, discord_id: str, elo: int) -> None:
        try:
            user_id = int(discord_id)
        except ValueError:
            await ctx.send("Invalid Discord ID.")
            return

        with db_connection(ctx.guild.id) as (conn, cursor):
            cursor.execute("SELECT name FROM players WHERE id=?", (user_id,))
            player_name_row = cursor.fetchone()

        if not player_name_row:
            await ctx.send(f"No registered player found with ID {user_id}.")
            return

        player_name = player_name_row[0]
        embed = discord.Embed(
            title="Confirm Elo Change",
            description=(
                f"Are you sure you want to set {player_name}'s Elo to {elo}? "
                f"React with {YES_EMOJI} to confirm or {NO_EMOJI} to cancel within 5 minutes."
            ),
            color=discord.Color.orange(),
        )
        message = await ctx.send(embed=embed)
        await message.add_reaction(YES_EMOJI)
        await message.add_reaction(NO_EMOJI)

        def check(reaction, user):
            return (
                user == ctx.author
                and str(reaction.emoji) in [YES_EMOJI, NO_EMOJI]
                and reaction.message.id == message.id
            )

        try:
            reaction, _ = await self.bot.wait_for(
                "reaction_add", timeout=300.0, check=check
            )
            if str(reaction.emoji) == YES_EMOJI:
                with db_connection(ctx.guild.id) as (conn, cursor):
                    cursor.execute(
                        "UPDATE players SET elo=? WHERE id=?", (elo, user_id)
                    )
                    conn.commit()
                lb_cache_invalidate(ctx.guild.id)
                lb_cache_rebuild(self.bot, ctx.guild.id)
                await sync_league_roles(ctx.guild)
                embed = discord.Embed(
                    title="Elo Updated",
                    description=f"{player_name}'s Elo has been set to {elo}.",
                    color=discord.Color.green(),
                )
            else:
                embed = discord.Embed(
                    title="Operation Canceled",
                    description="Set Elo operation has been canceled.",
                    color=discord.Color.red(),
                )
            await self._remove_bot_reactions(message)
            await message.edit(embed=embed)
        except asyncio.TimeoutError:
            await self._remove_bot_reactions(message)
            await message.edit(
                embed=discord.Embed(
                    title="Timeout",
                    description="Elo update confirmation timed out. Please try again.",
                    color=discord.Color.red(),
                )
            )

    # ------------------------------------------------------------------
    # Win/loss setters
    # ------------------------------------------------------------------

    @commands.command(help="Set the win count of a specific player (admin only).")
    @commands.check(is_admin)
    async def set_w(self, ctx: commands.Context, member: discord.Member, wins: int) -> None:
        with db_connection(ctx.guild.id) as (conn, cursor):
            cursor.execute(
                "SELECT name, games_won, games_lost FROM players WHERE id=?",
                (member.id,),
            )
            data = cursor.fetchone()

        if not data:
            await ctx.send(
                embed=discord.Embed(
                    title="Player Not Found",
                    description=f"No record found for {member.name}.",
                    color=discord.Color.red(),
                )
            )
            return

        player_name = data[0]
        embed = discord.Embed(
            title="Confirm Action",
            description=(
                f"Are you sure you want to set {player_name}'s games won to {wins}? "
                f"React with {YES_EMOJI} to confirm or {NO_EMOJI} to cancel."
            ),
            color=discord.Color.orange(),
        )
        message = await ctx.send(embed=embed)
        await message.add_reaction(YES_EMOJI)
        await message.add_reaction(NO_EMOJI)

        def check(reaction, user):
            return (
                user == ctx.author
                and str(reaction.emoji) in [YES_EMOJI, NO_EMOJI]
                and reaction.message.id == message.id
            )

        try:
            reaction, _ = await self.bot.wait_for(
                "reaction_add", timeout=300.0, check=check
            )
            if str(reaction.emoji) == YES_EMOJI:
                with db_connection(ctx.guild.id) as (conn, cursor):
                    cursor.execute(
                        "UPDATE players SET games_won=? WHERE id=?", (wins, member.id)
                    )
                    conn.commit()
                lb_cache_invalidate(ctx.guild.id)
                lb_cache_rebuild(self.bot, ctx.guild.id)
                await sync_league_roles(ctx.guild)
                embed = discord.Embed(
                    title="Win Count Updated",
                    description=f"{player_name}'s games won has been set to {wins}.",
                    color=discord.Color.green(),
                )
            else:
                embed = discord.Embed(
                    title="Action Canceled",
                    description=f"Win count update for {player_name} has been canceled.",
                    color=discord.Color.red(),
                )
        except asyncio.TimeoutError:
            embed = discord.Embed(
                title="Timeout",
                description="Set win count confirmation timed out. Please try again.",
                color=discord.Color.red(),
            )
        await self._remove_bot_reactions(message)
        await message.edit(embed=embed)

    @commands.command(help="Set the loss count of a specific player (admin only).")
    @commands.check(is_admin)
    async def set_l(self, ctx: commands.Context, member: discord.Member, losses: int) -> None:
        with db_connection(ctx.guild.id) as (conn, cursor):
            cursor.execute(
                "SELECT name, games_won, games_lost FROM players WHERE id=?",
                (member.id,),
            )
            data = cursor.fetchone()

        if not data:
            await ctx.send(
                embed=discord.Embed(
                    title="Player Not Found",
                    description=f"No record found for {member.name}.",
                    color=discord.Color.red(),
                )
            )
            return

        player_name = data[0]
        embed = discord.Embed(
            title="Confirm Action",
            description=(
                f"Are you sure you want to set {player_name}'s games lost to {losses}? "
                f"React with {YES_EMOJI} to confirm or {NO_EMOJI} to cancel."
            ),
            color=discord.Color.orange(),
        )
        message = await ctx.send(embed=embed)
        await message.add_reaction(YES_EMOJI)
        await message.add_reaction(NO_EMOJI)

        def check(reaction, user):
            return (
                user == ctx.author
                and str(reaction.emoji) in [YES_EMOJI, NO_EMOJI]
                and reaction.message.id == message.id
            )

        try:
            reaction, _ = await self.bot.wait_for(
                "reaction_add", timeout=300.0, check=check
            )
            if str(reaction.emoji) == YES_EMOJI:
                with db_connection(ctx.guild.id) as (conn, cursor):
                    cursor.execute(
                        "UPDATE players SET games_lost=? WHERE id=?",
                        (losses, member.id),
                    )
                    conn.commit()
                lb_cache_invalidate(ctx.guild.id)
                lb_cache_rebuild(self.bot, ctx.guild.id)
                await sync_league_roles(ctx.guild)
                embed = discord.Embed(
                    title="Loss Count Updated",
                    description=f"{player_name}'s games lost has been set to {losses}.",
                    color=discord.Color.green(),
                )
            else:
                embed = discord.Embed(
                    title="Action Canceled",
                    description=f"Loss count update for {player_name} has been canceled.",
                    color=discord.Color.red(),
                )
        except asyncio.TimeoutError:
            embed = discord.Embed(
                title="Timeout",
                description="Set loss count confirmation timed out. Please try again.",
                color=discord.Color.red(),
            )
        await self._remove_bot_reactions(message)
        await message.edit(embed=embed)

    @commands.command(
        help="Set the win count of a specific player using their Discord ID (admin only)."
    )
    @commands.check(is_admin)
    async def set_wid(self, ctx: commands.Context, user_id: int, games_won: int) -> None:
        with db_connection(ctx.guild.id) as (conn, cursor):
            cursor.execute("SELECT name FROM players WHERE id=?", (user_id,))
            player_data = cursor.fetchone()

        if not player_data:
            await ctx.send(
                embed=discord.Embed(
                    title="Error",
                    description=f"No registered player found for ID {user_id}.",
                    color=discord.Color.red(),
                )
            )
            return

        player_name = player_data[0]
        embed = discord.Embed(
            title="Confirm Action",
            description=(
                f"Are you sure you want to set {player_name}'s games won to {games_won}? "
                f"React with {YES_EMOJI} to confirm or {NO_EMOJI} to cancel within 5 minutes."
            ),
            color=discord.Color.orange(),
        )
        message = await ctx.send(embed=embed)
        await message.add_reaction(YES_EMOJI)
        await message.add_reaction(NO_EMOJI)

        def check(reaction, user):
            return (
                user == ctx.author
                and str(reaction.emoji) in [YES_EMOJI, NO_EMOJI]
                and reaction.message.id == message.id
            )

        try:
            reaction, _ = await self.bot.wait_for(
                "reaction_add", timeout=300.0, check=check
            )
            if str(reaction.emoji) == YES_EMOJI:
                with db_connection(ctx.guild.id) as (conn, cursor):
                    cursor.execute(
                        "UPDATE players SET games_won=? WHERE id=?",
                        (games_won, user_id),
                    )
                    conn.commit()
                lb_cache_invalidate(ctx.guild.id)
                lb_cache_rebuild(self.bot, ctx.guild.id)
                await sync_league_roles(ctx.guild)
                embed = discord.Embed(
                    title="Win Count Updated",
                    description=f"{player_name}'s games won has been set to {games_won}.",
                    color=discord.Color.green(),
                )
            else:
                embed = discord.Embed(
                    title="Operation Canceled",
                    description="Win count update has been canceled.",
                    color=discord.Color.red(),
                )
            await self._remove_bot_reactions(message)
            await message.edit(embed=embed)
        except asyncio.TimeoutError:
            await self._remove_bot_reactions(message)
            await message.edit(
                embed=discord.Embed(
                    title="Timeout",
                    description="Win count update confirmation timed out. Please try again.",
                    color=discord.Color.red(),
                )
            )

    @commands.command(
        help="Set the loss count of a specific player using their Discord ID (admin only)."
    )
    @commands.check(is_admin)
    async def set_lid(self, ctx: commands.Context, user_id: int, games_lost: int) -> None:
        with db_connection(ctx.guild.id) as (conn, cursor):
            cursor.execute("SELECT name FROM players WHERE id=?", (user_id,))
            player_data = cursor.fetchone()

        if not player_data:
            await ctx.send(
                embed=discord.Embed(
                    title="Error",
                    description=f"No registered player found for ID {user_id}.",
                    color=discord.Color.red(),
                )
            )
            return

        player_name = player_data[0]
        embed = discord.Embed(
            title="Confirm Action",
            description=(
                f"Are you sure you want to set {player_name}'s games lost to {games_lost}? "
                f"React with {YES_EMOJI} to confirm or {NO_EMOJI} to cancel within 5 minutes."
            ),
            color=discord.Color.orange(),
        )
        message = await ctx.send(embed=embed)
        await message.add_reaction(YES_EMOJI)
        await message.add_reaction(NO_EMOJI)

        def check(reaction, user):
            return (
                user == ctx.author
                and str(reaction.emoji) in [YES_EMOJI, NO_EMOJI]
                and reaction.message.id == message.id
            )

        try:
            reaction, _ = await self.bot.wait_for(
                "reaction_add", timeout=300.0, check=check
            )
            if str(reaction.emoji) == YES_EMOJI:
                with db_connection(ctx.guild.id) as (conn, cursor):
                    cursor.execute(
                        "UPDATE players SET games_lost=? WHERE id=?",
                        (games_lost, user_id),
                    )
                    conn.commit()
                lb_cache_invalidate(ctx.guild.id)
                lb_cache_rebuild(self.bot, ctx.guild.id)
                await sync_league_roles(ctx.guild)
                embed = discord.Embed(
                    title="Loss Count Updated",
                    description=f"{player_name}'s games lost has been set to {games_lost}.",
                    color=discord.Color.green(),
                )
            else:
                embed = discord.Embed(
                    title="Operation Canceled",
                    description="Loss count update has been canceled.",
                    color=discord.Color.red(),
                )
            await self._remove_bot_reactions(message)
            await message.edit(embed=embed)
        except asyncio.TimeoutError:
            await self._remove_bot_reactions(message)
            await message.edit(
                embed=discord.Embed(
                    title="Timeout",
                    description="Loss count update confirmation timed out. Please try again.",
                    color=discord.Color.red(),
                )
            )

    # ------------------------------------------------------------------
    # Monthly games / full stats setters
    # ------------------------------------------------------------------

    @commands.command(
        help="Set the monthly game count for a player using their Discord ID (admin only)."
    )
    @commands.check(is_admin)
    async def set_gid(
        self, ctx: commands.Context, discord_id: int, games_played: int
    ) -> None:
        user_id = str(discord_id)
        with db_connection(ctx.guild.id) as (conn, cursor):
            cursor.execute(
                "SELECT id, name, games_won, games_lost FROM players WHERE id=?",
                (user_id,),
            )
            player_data = cursor.fetchone()

        if not player_data:
            await ctx.send(
                embed=discord.Embed(
                    title="Error",
                    description=f"No registered player found with Discord ID {user_id}.",
                    color=discord.Color.red(),
                )
            )
            return

        player_id, player_name, games_won, games_lost = player_data
        total_games = games_won + games_lost
        if games_played > total_games:
            await ctx.send(
                embed=discord.Embed(
                    title="Invalid Game Count",
                    description=(
                        f"The specified game count {games_played} exceeds the total "
                        f"games played {total_games}."
                    ),
                    color=discord.Color.red(),
                )
            )
            return

        embed = discord.Embed(
            title="Confirm Action",
            description=(
                f"Are you sure you want to set {player_name}'s monthly game count to "
                f"{games_played}?\nReact with {YES_EMOJI} to confirm or {NO_EMOJI} to "
                f"cancel within 5 minutes."
            ),
            color=discord.Color.orange(),
        )
        message = await ctx.send(embed=embed)
        await message.add_reaction(YES_EMOJI)
        await message.add_reaction(NO_EMOJI)

        def check(reaction, user):
            return (
                user == ctx.author
                and str(reaction.emoji) in [YES_EMOJI, NO_EMOJI]
                and reaction.message.id == message.id
            )

        try:
            reaction, _ = await self.bot.wait_for(
                "reaction_add", timeout=300.0, check=check
            )
            if str(reaction.emoji) == YES_EMOJI:
                with db_connection(ctx.guild.id) as (conn, cursor):
                    cursor.execute(
                        "UPDATE players SET games_played_this_month=? WHERE id=?",
                        (games_played, user_id),
                    )
                    conn.commit()
                lb_cache_invalidate(ctx.guild.id)
                lb_cache_rebuild(self.bot, ctx.guild.id)
                await sync_league_roles(ctx.guild)
                embed = discord.Embed(
                    title="Monthly Games Played Updated",
                    description=(
                        f"{player_name}'s monthly game count has been set to {games_played}."
                    ),
                    color=discord.Color.green(),
                )
            else:
                embed = discord.Embed(
                    title="Operation Canceled",
                    description="Game count update has been canceled.",
                    color=discord.Color.red(),
                )
            await self._remove_bot_reactions(message)
            await message.edit(embed=embed)
        except asyncio.TimeoutError:
            await self._remove_bot_reactions(message)
            await message.edit(
                embed=discord.Embed(
                    title="Timeout",
                    description="Game count update confirmation timed out. Please try again.",
                    color=discord.Color.red(),
                )
            )

    @commands.command(
        help=(
            "Set all stats (Elo, wins, losses, monthly games) for a player using their "
            "Discord ID (admin only)."
        )
    )
    @commands.check(is_admin)
    async def set_stats(
        self,
        ctx: commands.Context,
        discord_id: int,
        elo: int,
        games_won: int,
        games_lost: int,
        games_played: int,
    ) -> None:
        with db_connection(ctx.guild.id) as (conn, cursor):
            cursor.execute("SELECT name FROM players WHERE id=?", (discord_id,))
            player_data = cursor.fetchone()

        if not player_data:
            await ctx.send(
                embed=discord.Embed(
                    title="Error",
                    description=f"No registered player found with Discord ID {discord_id}.",
                    color=discord.Color.red(),
                )
            )
            return

        player_name = player_data[0]
        total_games = games_won + games_lost
        if games_played > total_games:
            await ctx.send(
                embed=discord.Embed(
                    title="Invalid Game Count",
                    description=(
                        f"The specified monthly game count {games_played} exceeds the "
                        f"total games played {total_games}."
                    ),
                    color=discord.Color.red(),
                )
            )
            return

        embed = discord.Embed(
            title="Confirm Stats Update",
            description=(
                f"Are you sure you want to update {player_name}'s stats?\n"
                f"🔥 **Elo**: {elo}\n"
                f"🏆 **Games Won**: {games_won}\n"
                f"💀 **Games Lost**: {games_lost}\n"
                f"🎮 **Monthly Games Played**: {games_played}\n\n"
                f"React with {YES_EMOJI} to confirm or {NO_EMOJI} to cancel within 5 minutes."
            ),
            color=discord.Color.orange(),
        )
        message = await ctx.send(embed=embed)
        await message.add_reaction(YES_EMOJI)
        await message.add_reaction(NO_EMOJI)

        def check(reaction, user):
            return (
                user == ctx.author
                and str(reaction.emoji) in [YES_EMOJI, NO_EMOJI]
                and reaction.message.id == message.id
            )

        try:
            reaction, _ = await self.bot.wait_for(
                "reaction_add", timeout=300.0, check=check
            )
            if str(reaction.emoji) == YES_EMOJI:
                with db_connection(ctx.guild.id) as (conn, cursor):
                    cursor.execute(
                        "UPDATE players SET elo=?, games_won=?, games_lost=?, "
                        "games_played_this_month=? WHERE id=?",
                        (elo, games_won, games_lost, games_played, discord_id),
                    )
                    conn.commit()
                lb_cache_invalidate(ctx.guild.id)
                lb_cache_rebuild(self.bot, ctx.guild.id)
                await sync_league_roles(ctx.guild)
                embed = discord.Embed(
                    title="Stats Updated",
                    description=(
                        f"{player_name}'s stats have been updated:\n"
                        f"🔥 **Elo**: {elo}\n"
                        f"🏆 **Games Won**: {games_won}\n"
                        f"💀 **Games Lost**: {games_lost}\n"
                        f"🎮 **Monthly Games Played**: {games_played}"
                    ),
                    color=discord.Color.green(),
                )
            else:
                embed = discord.Embed(
                    title="Operation Canceled",
                    description="Stats update has been canceled.",
                    color=discord.Color.red(),
                )
            await self._remove_bot_reactions(message)
            await message.edit(embed=embed)
        except asyncio.TimeoutError:
            await self._remove_bot_reactions(message)
            await message.edit(
                embed=discord.Embed(
                    title="Timeout",
                    description="Stats update confirmation timed out. Please try again.",
                    color=discord.Color.red(),
                )
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AdminEloSettersCog(bot))