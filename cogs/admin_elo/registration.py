"""
cogs/admin_elo/registration.py

This used to contain a full duplicate of cogs/registration.py's
RegistrationCog (register, rename, register_o, rename_o, register_oid,
rename_oid). Because both files defined a cog class named "RegistrationCog",
whichever loaded first "won" and the other failed with a silent
"Cog named 'RegistrationCog' already loaded" error during startup - so one
of the two copies was always dead code, and it was easy to lose bugfixes
made to whichever copy didn't happen to load.

cogs/registration.py is the single source of truth for register/rename
(both the public commands and their admin_o/admin_oid variants).

This module now houses AdminEloRegistrationCog with the bulk/registration-
clearing admin commands referenced in cogs/help.py and elobot.py's logging
allowlist: c_reg is implemented below. d_reg/did_reg/db_d are still not
implemented anywhere in the codebase.

clear_all_registrations() is factored out so the -decay command
(cogs/admin_elo/decay.py) can run the exact same wipe automatically at
season end, with no separate reaction confirmation, instead of requiring
the admin to run -c_reg by hand afterwards.

Before the players table is wiped, clear_all_registrations() also strips
every league role from every registered member, one member at a time in
leaderboard order (highest Elo first), with a short delay between members
so a large server does not trip Discord's role-edit rate limit. The result
reports how many members had their league role removed successfully,
how many failed (permission/API error), and how many could no longer be
found in the guild (e.g. they left).
"""

from __future__ import annotations

import asyncio
import logging
from typing import NamedTuple

import discord
from discord.ext import commands

from config import YES_EMOJI, NO_EMOJI
from db.elo_db import db_connection
from utils import is_admin
from utils.lb_cache import (
    invalidate as lb_cache_invalidate,
    schedule_rebuild as lb_cache_rebuild,
)
from utils.league import get_leagues

from .base import AdminEloBaseCog

logger = logging.getLogger(__name__)

# Delay (seconds) between each member's role removal during the c_reg
# league-role cleanup. Keeps a large guild from bursting the Discord
# role-edit rate limit the way the -decay full sync already does
# (see utils/league.py _FULL_SYNC_DELAY).
_ROLE_REMOVAL_DELAY = 0.5


class RoleRemovalSummary(NamedTuple):
    """Outcome of stripping league roles from every registered member."""

    removed: int
    failed: int
    not_found: int


class ClearRegistrationsResult(NamedTuple):
    """Outcome of clear_all_registrations()."""

    players_cleared: int
    roles: RoleRemovalSummary


def _league_role_ids(guild_id: int) -> set[int]:
    """Return every role_id configured on any league for guild_id."""
    leagues = get_leagues(guild_id)
    return {
        role_id
        for (_name, _min_elo, _max_elo, _min_kind, _max_kind, role_id, _discovered)
        in leagues
        if role_id is not None
    }


async def _remove_league_roles_in_leaderboard_order(
    bot: commands.Bot, guild_id: int
) -> RoleRemovalSummary:
    """Strip every league role from every registered member, one member at
    a time in leaderboard order (Elo descending), pausing briefly between
    members to avoid overloading the Discord API.

    Must be called before the players table is wiped, since it reads the
    player list (and their Elo order) straight from the DB.
    """
    guild = bot.get_guild(guild_id)
    if guild is None:
        return RoleRemovalSummary(0, 0, 0)

    league_role_ids = _league_role_ids(guild_id)
    if not league_role_ids:
        return RoleRemovalSummary(0, 0, 0)

    role_by_id = {r.id: r for r in guild.roles}
    league_roles = [role_by_id[rid] for rid in league_role_ids if rid in role_by_id]
    if not league_roles:
        return RoleRemovalSummary(0, 0, 0)

    with db_connection(guild_id) as (_conn, cursor):
        cursor.execute("SELECT id FROM players ORDER BY elo DESC")
        player_ids = [row[0] for row in cursor.fetchall()]

    removed = 0
    failed = 0
    not_found = 0

    for player_id_raw in player_ids:
        try:
            user_id = int(player_id_raw)
        except (TypeError, ValueError):
            not_found += 1
            continue

        member = guild.get_member(user_id)
        if member is None:
            try:
                member = await guild.fetch_member(user_id)
            except discord.NotFound:
                not_found += 1
                continue
            except discord.HTTPException:
                failed += 1
                await asyncio.sleep(_ROLE_REMOVAL_DELAY)
                continue

        to_remove = [r for r in league_roles if r in member.roles]
        if not to_remove:
            # Nothing to strip for this member - no API call needed, so no
            # delay either, just move on to the next one in the ranking.
            continue

        try:
            await member.remove_roles(
                *to_remove, reason="c_reg: clear all registrations"
            )
            removed += 1
        except discord.HTTPException:
            logger.exception(
                "Failed to remove league role(s) from member %s during c_reg",
                user_id,
            )
            failed += 1

        await asyncio.sleep(_ROLE_REMOVAL_DELAY)

    return RoleRemovalSummary(removed, failed, not_found)


async def clear_all_registrations(
    bot: commands.Bot, guild_id: int
) -> ClearRegistrationsResult:
    """Delete every registered player for guild_id. players is the parent
    table; h2h_stats and match_history both declare ON DELETE CASCADE on
    player_id, and the connection enables PRAGMA foreign_keys=ON, so this
    single DELETE also wipes each cleared player's head-to-head stats and
    match history without needing separate statements.

    Before the DELETE, every member's league role(s) are stripped (see
    _remove_league_roles_in_leaderboard_order) - otherwise a member would
    keep wearing a league role for a player that no longer exists in the DB.

    Returns a ClearRegistrationsResult. players_cleared is 0 and roles is
    all-zero if there was nothing to clear, in which case no DB write and
    no role removal happens.
    """
    with db_connection(guild_id) as (_conn, cursor):
        cursor.execute("SELECT COUNT(*) FROM players")
        player_count = cursor.fetchone()[0]

    if not player_count:
        return ClearRegistrationsResult(0, RoleRemovalSummary(0, 0, 0))

    role_summary = await _remove_league_roles_in_leaderboard_order(bot, guild_id)

    with db_connection(guild_id) as (conn, cursor):
        cursor.execute("DELETE FROM players")
        conn.commit()

    lb_cache_invalidate(guild_id)
    lb_cache_rebuild(bot, guild_id)
    return ClearRegistrationsResult(player_count, role_summary)


class AdminEloRegistrationCog(AdminEloBaseCog):
    """Commands: c_reg."""

    @commands.command(
        name="c_reg",
        help="Clear all registered player data (admin only).",
    )
    @commands.check(is_admin)
    async def c_reg(self, ctx: commands.Context) -> None:
        with db_connection(ctx.guild.id) as (_conn, cursor):
            cursor.execute("SELECT COUNT(*) FROM players")
            player_count = cursor.fetchone()[0]

        if not player_count:
            await ctx.send(
                embed=discord.Embed(
                    title="Nothing to Clear",
                    description="There are no registered players to clear.",
                    color=discord.Color.blue(),
                )
            )
            return

        embed = discord.Embed(
            title="Confirm Clear All Registrations",
            description=(
                f"**This will permanently delete all {player_count} registered "
                f"player(s)**, along with their match history and head-to-head "
                f"stats, and remove their league role(s). This cannot be undone. "
                f"React with {YES_EMOJI} to confirm or {NO_EMOJI} to cancel "
                f"within 5 minutes."
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
                result = await clear_all_registrations(self.bot, ctx.guild.id)
                embed.title = "All Registrations Cleared"
                embed.description = (
                    f"{result.players_cleared} registered player(s) and their "
                    "stats have been permanently deleted.\n\n"
                    f"**League role cleanup:** {result.roles.removed} member(s) "
                    f"had their league role removed, {result.roles.failed} "
                    f"failed (permission/API error), {result.roles.not_found} "
                    "could not be found in the server."
                )
                embed.color = discord.Color.green()
            else:
                embed.title = "Clear Canceled"
                embed.description = "Clearing all registered player data has been canceled."
                embed.color = discord.Color.red()

            await self._remove_bot_reactions(message)
            await message.edit(embed=embed)
        except asyncio.TimeoutError:
            await self._remove_bot_reactions(message)
            await message.edit(
                embed=discord.Embed(
                    title="Timeout",
                    description="Clear-all confirmation timed out. Please try again.",
                    color=discord.Color.red(),
                )
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AdminEloRegistrationCog(bot))