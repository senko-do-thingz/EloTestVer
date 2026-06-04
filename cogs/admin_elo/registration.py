"""Player registration management commands (admin only)."""

from __future__ import annotations

import asyncio
import os

import discord
from discord.ext import commands

from config import YES_EMOJI, NO_EMOJI
from db.elo_db import db_connection, db_exists, get_db_path
from utils import is_admin, sync_league_roles
from utils.lb_cache import (
    invalidate as lb_cache_invalidate,
    schedule_rebuild as lb_cache_rebuild,
)

from .base import AdminEloBaseCog


class AdminEloRegistrationCog(AdminEloBaseCog):
    """Commands: c_reg, d_reg, did_reg, db_d."""

    @commands.command(
        name="c_reg",
        aliases=["c_registration"],
        help="Clear all registered player information (admin only).",
    )
    @commands.check(is_admin)
    async def c_reg(self, ctx: commands.Context) -> None:
        embed = discord.Embed(
            title="Confirm Clear All Registrations",
            description=(
                f"**This function affects all registered player data**, Are you sure "
                f"you want to clear all registered player information? React with "
                f"{YES_EMOJI} to confirm or {NO_EMOJI} to cancel within 5 minutes."
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
                    cursor.execute("SELECT id FROM players")
                    all_player_ids = [int(row[0]) for row in cursor.fetchall()]
                    cursor.execute("DELETE FROM players")
                    conn.commit()
                lb_cache_invalidate(ctx.guild.id)
                if all_player_ids:
                    await sync_league_roles(ctx.guild, player_ids=all_player_ids)
                embed = discord.Embed(
                    title="Registration Data Cleared",
                    description="All registered player information has been cleared.",
                    color=discord.Color.green(),
                )
            else:
                embed = discord.Embed(
                    title="Operation Canceled",
                    description="Clear player registrations has been canceled.",
                    color=discord.Color.red(),
                )
            await self._remove_bot_reactions(message)
            await message.edit(embed=embed)
        except asyncio.TimeoutError:
            await self._remove_bot_reactions(message)
            await message.edit(
                embed=discord.Embed(
                    title="Timeout",
                    description=(
                        "Clear player registrations confirmation timed out. Please try again."
                    ),
                    color=discord.Color.red(),
                )
            )

    @commands.command(
        name="d_reg",
        aliases=["d_registration"],
        help="Delete a player's registration (admin only).",
    )
    @commands.check(is_admin)
    async def d_reg(self, ctx: commands.Context, member: discord.Member) -> None:
        if not db_exists(ctx.guild.id):
            await ctx.send(
                embed=discord.Embed(
                    title="Error",
                    description="Database for this server does not exist.",
                    color=discord.Color.red(),
                )
            )
            return

        user_id = str(member.id)
        with db_connection(ctx.guild.id) as (conn, cursor):
            cursor.execute("SELECT name FROM players WHERE id=?", (user_id,))
            result = cursor.fetchone()

        if not result:
            await ctx.send(
                embed=discord.Embed(
                    title="Deletion Failed",
                    description=f"{member.name} is not registered in the database!",
                    color=discord.Color.red(),
                )
            )
            return

        player_name = result[0]
        embed = discord.Embed(
            title="Confirm Deletion",
            description=(
                f"Are you sure you want to delete {player_name}'s registration? "
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
                    cursor.execute("DELETE FROM players WHERE id=?", (user_id,))
                    conn.commit()
                lb_cache_invalidate(ctx.guild.id)
                lb_cache_rebuild(self.bot, ctx.guild.id)
                await sync_league_roles(ctx.guild, player_ids=[int(user_id)])
                embed = discord.Embed(
                    title="Registration Deleted",
                    description=f"{player_name}'s registration has been deleted.",
                    color=discord.Color.green(),
                )
            else:
                embed = discord.Embed(
                    title="Deletion Canceled",
                    description=f"Deletion of {player_name}'s registration has been canceled.",
                    color=discord.Color.red(),
                )
            await self._remove_bot_reactions(message)
            await message.edit(embed=embed)
        except asyncio.TimeoutError:
            await self._remove_bot_reactions(message)
            await message.edit(
                embed=discord.Embed(
                    title="Timeout",
                    description="Deletion confirmation timed out. Please try again.",
                    color=discord.Color.red(),
                )
            )

    @commands.command(
        name="did_reg",
        aliases=["did_registration"],
        help="Delete a player's registration using their Discord ID (admin only).",
    )
    @commands.check(is_admin)
    async def did_reg(self, ctx: commands.Context, discord_id: str) -> None:
        try:
            user_id = int(discord_id)
        except ValueError:
            await ctx.send(
                embed=discord.Embed(
                    title="Invalid ID",
                    description="Please provide a valid Discord user ID.",
                    color=discord.Color.red(),
                )
            )
            return

        with db_connection(ctx.guild.id) as (conn, cursor):
            cursor.execute("SELECT name FROM players WHERE id=?", (user_id,))
            player_data = cursor.fetchone()

        if not player_data:
            await ctx.send(
                embed=discord.Embed(
                    title="Deletion Failed",
                    description=f"No registered player found with ID {user_id}.",
                    color=discord.Color.red(),
                )
            )
            return

        player_name = player_data[0]
        embed = discord.Embed(
            title="Confirm Deletion",
            description=(
                f"Are you sure you want to delete {player_name}'s registration? "
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
                    cursor.execute("DELETE FROM players WHERE id=?", (user_id,))
                    conn.commit()
                # Bug #6 fix: invalidate + rebuild cache, matching d_reg behaviour
                lb_cache_invalidate(ctx.guild.id)
                lb_cache_rebuild(self.bot, ctx.guild.id)
                await sync_league_roles(ctx.guild, player_ids=[int(user_id)])
                embed = discord.Embed(
                    title="Registration Deleted",
                    description=f"Player {player_name} registration has been deleted.",
                    color=discord.Color.green(),
                )
            else:
                embed = discord.Embed(
                    title="Deletion Canceled",
                    description=f"Deletion of {player_name}'s registration has been canceled.",
                    color=discord.Color.red(),
                )
            await self._remove_bot_reactions(message)
            await message.edit(embed=embed)
        except asyncio.TimeoutError:
            await self._remove_bot_reactions(message)
            await message.edit(
                embed=discord.Embed(
                    title="Timeout",
                    description="Deletion confirmation timed out. Please try again.",
                    color=discord.Color.red(),
                )
            )

    async def db_d(self, ctx: commands.Context, server_id: int, record_id: int) -> None:
        """Delete a player record from any server's DB (called by private cogs)."""
        if not db_exists(server_id):
            await ctx.send(
                embed=discord.Embed(
                    title="Database Not Found",
                    description=(
                        f"No database found for server ID {server_id} "
                        f"(`{get_db_path(server_id).replace(os.sep, '/')}`)."
                    ),
                    color=discord.Color.red(),
                )
            )
            return

        with db_connection(server_id) as (conn, cursor):
            cursor.execute("SELECT * FROM players WHERE id=?", (record_id,))
            record = cursor.fetchone()

        if not record:
            await ctx.send(
                embed=discord.Embed(
                    title="Record Not Found",
                    description=(
                        f"No record found with ID {record_id} in the database "
                        f"for server ID {server_id}."
                    ),
                    color=discord.Color.red(),
                )
            )
            return

        embed = discord.Embed(
            title="Confirm Deletion",
            description=(
                f"Are you sure you want to delete the record with ID {record_id} from "
                f"the database for server ID {server_id}? "
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
                with db_connection(server_id) as (conn, cursor):
                    cursor.execute("DELETE FROM players WHERE id=?", (record_id,))
                    conn.commit()
                embed = discord.Embed(
                    title="Record Deleted",
                    description=(
                        f"The record with ID {record_id} has been deleted from the "
                        f"database for server ID {server_id}."
                    ),
                    color=discord.Color.green(),
                )
            else:
                embed = discord.Embed(
                    title="Deletion Canceled",
                    description=f"The deletion of the record with ID {record_id} has been canceled.",
                    color=discord.Color.red(),
                )
            await self._remove_bot_reactions(message)
            await message.edit(embed=embed)
        except asyncio.TimeoutError:
            await self._remove_bot_reactions(message)
            await message.edit(
                embed=discord.Embed(
                    title="Timeout",
                    description="Deletion confirmation timed out. Please try again.",
                    color=discord.Color.red(),
                )
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AdminEloRegistrationCog(bot))