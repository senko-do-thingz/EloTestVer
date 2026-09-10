"""League management commands: ladd, lapply, lcancel, lclear, lannounce, lrannounce."""

from __future__ import annotations

import asyncio

import discord
from discord.ext import commands

from config import YES_EMOJI, NO_EMOJI
from db.elo_db import db_connection
from utils import (
    add_league_announce_channel_id,
    get_league_announce_channel_ids,
    get_leagues,
    is_admin,
    remove_league_announce_channel_id,
    set_leagues,
)

from .base import RolesBaseCog


class RolesManageCog(RolesBaseCog):
    """Commands: ladd, lapply, lcancel, lclear, lannounce, lrannounce."""

    @commands.command(
        name="ladd",
        help="Create league ranges and sync roles. Usage: -ladd <number>",
    )
    @commands.check(is_admin)
    async def ladd(self, ctx: commands.Context, number: int) -> None:
        if number <= 0 or number > 50:
            await ctx.send(
                embed=discord.Embed(
                    title="Invalid Number",
                    description="Use: `-ladd <number>` where number is between 1 and 50.",
                    color=discord.Color.red(),
                )
            )
            return

        cancel_event = self._try_begin_league_task(ctx)
        if cancel_event is None:
            await ctx.send(
                embed=discord.Embed(
                    title="League Task Already Running",
                    description=(
                        "You already have a league task running in this channel. "
                        "Use `-lcancel` to cancel it."
                    ),
                    color=discord.Color.red(),
                )
            )
            return

        example = "Silver l 599 1\nGold 600 699 1\nPlatinum 700 h 0"
        prompt = await ctx.send(
            embed=discord.Embed(
                title="League Setup",
                description=(
                    "Please **reply** to this message with exactly the number of lines you chose.\n"
                    "Each line must follow this format:\n"
                    "`league_name minimum_elo maximum_elo [discovered]`\n\n"
                    "Special values:\n"
                    "- Use `l` as `minimum_elo` to mean **Lower** (from `maximum_elo` and below)\n"
                    "- Use `h` as `maximum_elo` to mean **Higher** (from `minimum_elo` and above)\n\n"
                    "- `discovered` (optional): `1` = discovered, `0` = undiscovered (default: `0`)\n\n"
                    "League name:\n"
                    "- If it has **2+ characters**, `_` represents a space. Example: `Diamond_League` -> `Diamond League`\n\n"
                    "To cancel this setup, use `-lcancel`.\n\n"
                    f"Example:\n```\n{example}\n```"
                ),
                color=discord.Color.orange(),
            )
        )

        def check(m: discord.Message):
            return (
                m.author.id == ctx.author.id
                and m.channel.id == ctx.channel.id
                and m.reference is not None
                and m.reference.message_id == prompt.id
            )

        try:
            message_task = asyncio.create_task(
                self.bot.wait_for("message", timeout=300.0, check=check)
            )
            cancel_task = asyncio.create_task(cancel_event.wait())
            done, pending = await asyncio.wait(
                {message_task, cancel_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for t in pending:
                t.cancel()

            if cancel_task in done and cancel_event.is_set():
                await ctx.send(
                    embed=discord.Embed(
                        title="Canceled",
                        description="League task has been canceled.",
                        color=discord.Color.red(),
                    )
                )
                return

            reply: discord.Message = message_task.result()
        except Exception:
            await ctx.send(
                embed=discord.Embed(
                    title="Timeout",
                    description="Timed out. Please run `-ladd` again.",
                    color=discord.Color.red(),
                )
            )
            return
        finally:
            self._end_league_task(ctx)

        raw_lines = [
            ln.strip() for ln in (reply.content or "").splitlines() if ln.strip()
        ]
        if len(raw_lines) != number:
            await ctx.send(
                embed=discord.Embed(
                    title="Invalid Lines",
                    description=(
                        f"Expected exactly **{number}** line(s), "
                        f"but received **{len(raw_lines)}**."
                    ),
                    color=discord.Color.red(),
                )
            )
            return

        leagues_to_store, bad_lines, missing_roles = self._parse_league_lines(
            ctx, raw_lines
        )

        if bad_lines:
            await ctx.send(
                embed=discord.Embed(
                    title="Invalid League Lines",
                    description="These lines have an invalid format:\n"
                    + "\n".join(f"- `{x}`" for x in bad_lines),
                    color=discord.Color.red(),
                )
            )
            return

        if missing_roles:
            await ctx.send(
                embed=discord.Embed(
                    title="Missing League Roles",
                    description=(
                        "Could not find a server role for these leagues (skipped):\n"
                        + "\n".join(f"- `{x}`" for x in missing_roles)
                    ),
                    color=discord.Color.red(),
                )
            )

        if not leagues_to_store:
            await ctx.send(
                embed=discord.Embed(
                    title="No Valid Leagues",
                    description="No valid leagues to save.",
                    color=discord.Color.red(),
                )
            )
            return

        def _eff(row):
            _, min_elo, max_elo, min_kind, max_kind, _r, _d = row
            low = -(10**18) if min_kind == "l" else int(min_elo)
            high = 10**18 if max_kind == "h" else int(max_elo)
            return low, high

        sorted_rows = sorted(
            leagues_to_store,
            key=lambda r: (_eff(r)[0], _eff(r)[1], r[0].lower()),
        )
        overlaps: list[str] = []
        gaps: list[str] = []
        prev = None
        for row in sorted_rows:
            low, high = _eff(row)
            if prev is not None:
                _p_low, p_high, p_name = prev
                if low <= p_high:
                    overlaps.append(f"`{p_name}` overlaps `{row[0]}`")
                elif low > p_high + 1:
                    gaps.append(f"`{p_name}` -> `{row[0]}` (Elo {p_high + 1}–{low - 1} is unassigned)")
            prev = (low, high, row[0])

        if overlaps:
            await ctx.send(
                embed=discord.Embed(
                    title="Overlapping Leagues",
                    description=(
                        "Each league must be strictly separate (no shared Elo).\n"
                        "Fix these overlaps and try again:\n"
                        + "\n".join(f"- {x}" for x in overlaps)
                    ),
                    color=discord.Color.red(),
                )
            )
            return

        if gaps:
            await ctx.send(
                embed=discord.Embed(
                    title="Gap Between Leagues",
                    description=(
                        "Players with Elo in the gap will not be assigned any league.\n"
                        "Fix these gaps and try again:\n"
                        + "\n".join(f"- {x}" for x in gaps)
                    ),
                    color=discord.Color.red(),
                )
            )
            return

        set_leagues(ctx.guild.id, leagues_to_store)
        _u, assigned_count, missing_from_db, apply_details = await self._apply_leagues(
            ctx.guild
        )
        active_league_role_ids = {
            rid
            for (_name, _min, _max, _min_k, _max_k, rid, _d) in leagues_to_store
            if rid and ctx.guild.get_role(rid) is not None
        }
        if not active_league_role_ids:
            assigned_count = 0

        missing_all = sorted(set(missing_roles + missing_from_db))
        desc = f"Saved **{len(leagues_to_store)}** league(s).\n\n{apply_details}"
        if missing_all:
            desc += "\n\nLeague roles not found (skipped):\n" + "\n".join(
                f"- `{x}`" for x in missing_all
            )

        await ctx.send(
            embed=discord.Embed(
                title="League Setup Completed",
                description=desc,
                color=discord.Color.green(),
            )
        )

    @commands.command(
        name="lapply",
        help="Re-apply league roles for all registered players based on current Elo.",
    )
    @commands.check(is_admin)
    async def lapply(self, ctx: commands.Context) -> None:
        leagues = get_leagues(ctx.guild.id)
        if not leagues:
            await ctx.send(
                embed=discord.Embed(
                    title="No Leagues",
                    description="No leagues are configured. Use `-ladd <number>` first.",
                    color=discord.Color.red(),
                )
            )
            return

        _u, _assigned_count, missing_roles, apply_details = await self._apply_leagues(
            ctx.guild, mark_populated_leagues_discovered=True
        )
        desc = apply_details
        if missing_roles:
            desc += "\n\nLeague roles not found (skipped):\n" + "\n".join(
                f"- `{x}`" for x in sorted(set(missing_roles))
            )

        await ctx.send(
            embed=discord.Embed(
                title="League Apply Completed",
                description=desc,
                color=discord.Color.green(),
            )
        )

    @commands.command(
        name="lcancel",
        help="Cancel any running league task in this channel.",
    )
    @commands.check(is_admin)
    async def lcancel(self, ctx: commands.Context) -> None:
        key = self._league_task_key(ctx)
        ev = self._pending_league_task.get(key)
        if not ev:
            await ctx.send(
                embed=discord.Embed(
                    title="Nothing to Cancel",
                    description="You don't have an active league task in this channel.",
                    color=discord.Color.red(),
                )
            )
            return
        ev.set()

    @commands.command(
        name="lclear",
        help=(
            "Remove all league roles from players and clear all leagues from the bot data."
        ),
    )
    @commands.check(is_admin)
    async def lclear(self, ctx: commands.Context) -> None:
        cancel_event = self._try_begin_league_task(ctx)
        if cancel_event is None:
            await ctx.send(
                embed=discord.Embed(
                    title="League Task Already Running",
                    description=(
                        "You already have a league task running in this channel. "
                        "Use `-lcancel` to cancel it."
                    ),
                    color=discord.Color.red(),
                )
            )
            return

        leagues = get_leagues(ctx.guild.id)
        if not leagues:
            await ctx.send(
                embed=discord.Embed(
                    title="No Leagues",
                    description="No leagues are configured. Nothing to clear.",
                    color=discord.Color.red(),
                )
            )
            self._end_league_task(ctx)
            return

        confirm_embed = discord.Embed(
            title="Clear Leagues Confirmation",
            description=(
                f"**This will remove all league roles from players and clear "
                f"{len(leagues)} league(s).** "
                f"Are you sure? React with {YES_EMOJI} to confirm or {NO_EMOJI} to cancel "
                f"within 5 minutes."
            ),
            color=discord.Color.orange(),
        )
        message = await ctx.send(embed=confirm_embed)
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
        except asyncio.TimeoutError:
            await self._remove_bot_reactions(message)
            await message.edit(
                embed=discord.Embed(
                    title="Timeout",
                    description="League clear confirmation timed out. Please try again.",
                    color=discord.Color.red(),
                )
            )
            self._end_league_task(ctx)
            return

        if str(reaction.emoji) != YES_EMOJI:
            await self._remove_bot_reactions(message)
            await message.edit(
                embed=discord.Embed(
                    title="League Clear Canceled",
                    description="League clear has been canceled.",
                    color=discord.Color.red(),
                )
            )
            self._end_league_task(ctx)
            return

        await self._remove_bot_reactions(message)
        await message.edit(
            embed=discord.Embed(
                title="Clearing Leagues...",
                description="Removing league roles and clearing league data.",
                color=discord.Color.blue(),
            )
        )

        role_ids: list[int] = sorted(
            {rid for (_n, _mi, _ma, _mik, _mak, rid, _d) in leagues if rid}
        )
        role_by_id = {r.id: r for r in ctx.guild.roles}
        roles_to_remove: list[discord.Role] = [
            role_by_id[rid] for rid in role_ids if rid in role_by_id
        ]

        with db_connection(ctx.guild.id) as (conn, cursor):
            cursor.execute("SELECT id FROM players")
            player_rows = cursor.fetchall()

        semaphore = asyncio.Semaphore(5)
        removed_from_players = 0

        async def _clear_one(user_id_raw: str) -> int:
            if cancel_event.is_set():
                return 0
            try:
                user_id = int(user_id_raw)
            except Exception:
                return 0
            member = ctx.guild.get_member(user_id)
            if member is None:
                return 0
            to_remove = [r for r in roles_to_remove if r in member.roles]
            if not to_remove:
                return 0
            async with semaphore:
                if cancel_event.is_set():
                    return 0
                try:
                    await member.remove_roles(
                        *to_remove, reason="League clear: remove all league roles"
                    )
                    return 1
                except Exception:
                    return 0

        try:
            tasks = [_clear_one(row[0]) for row in player_rows]
            results = await asyncio.gather(*tasks, return_exceptions=False)
            removed_from_players = int(sum(results))
        finally:
            self._end_league_task(ctx)

        if cancel_event.is_set():
            await ctx.send(
                embed=discord.Embed(
                    title="Canceled",
                    description="League task has been canceled.",
                    color=discord.Color.red(),
                )
            )
            return

        set_leagues(ctx.guild.id, [])

        await ctx.send(
            embed=discord.Embed(
                title="Leagues Cleared",
                description=(
                    f"Removed league roles from **{removed_from_players}** player(s).\n"
                    f"Cleared **{len(leagues)}** league(s) from the bot data."
                ),
                color=discord.Color.green(),
            )
        )


    @commands.command(
        name="lannounce",
        help=(
            "Add a text channel for league promotion/demotion announcements. "
            "Usage: -lannounce <channel> - numeric ID, #channel-picker, mention, or name."
        ),
    )
    @commands.check(is_admin)
    async def lannounce(self, ctx: commands.Context, *, channel: discord.TextChannel) -> None:
        prior = get_league_announce_channel_ids(ctx.guild.id)
        if channel.id in prior:
            lines = self._league_announce_channels_lines(ctx.guild)
            await ctx.send(
                embed=discord.Embed(
                    title="Already Configured",
                    description=(
                        f"{channel.mention} is already a league announcement channel.\n\n"
                        "**Current announcement channels:**\n" + "\n".join(lines)
                    ),
                    color=discord.Color.blurple(),
                )
            )
            return

        add_league_announce_channel_id(ctx.guild.id, channel.id)
        lines = self._league_announce_channels_lines(ctx.guild)
        await ctx.send(
            embed=discord.Embed(
                title="Announcement Channel Added",
                description=(
                    f"{channel.mention} will receive league promotion/demotion messages "
                    "(alongside any other channels already configured).\n\n"
                    "**Current announcement channels:**\n" + "\n".join(lines)
                ),
                color=discord.Color.green(),
            )
        )

    @commands.command(
        name="lrannounce",
        help=(
            "Remove a text channel from league announcement destinations. "
            "Usage: same channel formats as `-lannounce`."
        ),
    )
    @commands.check(is_admin)
    async def lrannounce(self, ctx: commands.Context, *, channel: discord.TextChannel) -> None:
        removed = remove_league_announce_channel_id(ctx.guild.id, channel.id)
        if not removed:
            await ctx.send(
                embed=discord.Embed(
                    title="Channel Not in List",
                    description=(
                        f"{channel.mention} is not configured as a league announcement channel."
                    ),
                    color=discord.Color.red(),
                )
            )
            return

        lines = self._league_announce_channels_lines(ctx.guild)
        await ctx.send(
            embed=discord.Embed(
                title="Announcement Channel Removed",
                description=(
                    f"Removed {channel.mention} from league announcements.\n\n"
                    "**Remaining channels:**\n" + "\n".join(lines)
                ),
                color=discord.Color.green(),
            )
        )

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RolesManageCog(bot))