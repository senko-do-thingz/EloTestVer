"""Shared helpers and league-task tracking for all Roles sub-cogs."""

from __future__ import annotations

import asyncio

import discord
from discord.ext import commands

from config import YES_EMOJI, NO_EMOJI
from db.elo_db import db_connection
from utils import (
    get_league_announce_channel_ids,
    get_leagues,
    pick_league_role_id_for_elo,
    sync_league_roles,
)


LeagueRow = tuple[str, int | None, int | None, str, str, int | None, int]


class RolesBaseCog(commands.Cog):
    """Base class: league task tracking + all formatting/query helpers."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._pending_league_task: dict[tuple[int, int, int], asyncio.Event] = {}

    # ------------------------------------------------------------------
    # Error handler
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # League task helpers
    # ------------------------------------------------------------------

    def _league_task_key(self, ctx: commands.Context) -> tuple[int, int, int]:
        return (ctx.guild.id, ctx.channel.id, ctx.author.id)

    def _try_begin_league_task(self, ctx: commands.Context) -> asyncio.Event | None:
        key = self._league_task_key(ctx)
        if key in self._pending_league_task:
            return None
        ev = asyncio.Event()
        self._pending_league_task[key] = ev
        return ev

    def _end_league_task(self, ctx: commands.Context) -> None:
        self._pending_league_task.pop(self._league_task_key(ctx), None)

    # ------------------------------------------------------------------
    # Reaction helper
    # ------------------------------------------------------------------

    async def _remove_bot_reactions(self, message: discord.Message) -> None:
        bot_user = getattr(self.bot, "user", None)
        if not bot_user:
            return
        for emoji in (YES_EMOJI, NO_EMOJI):
            try:
                await message.remove_reaction(emoji, bot_user)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Formatting helpers
    # ------------------------------------------------------------------

    def _parse_league_name(self, name_raw: str) -> str:
        return (
            name_raw.replace("_", " ")
            if len(name_raw) >= 2 and "_" in name_raw
            else name_raw
        )

    def _format_league_display(
        self,
        role_by_id: dict[int, discord.Role],
        name: str,
        role_id: int | None,
    ) -> str:
        role = role_by_id.get(role_id) if role_id else None
        return role.mention if role else f"`{name}`"

    def _format_role_members(
        self, role: discord.Role | None, *, max_mentions: int = 25
    ) -> tuple[str, int]:
        if role is None:
            return "*Role not found.*", 0
        members = list(role.members)
        count = len(members)
        if count == 0:
            return "*No players in this league.*", 0
        mentions = [m.mention for m in members[:max_mentions]]
        extra = count - len(mentions)
        if extra > 0:
            mentions.append(f"*…and {extra} more*")
        return " ".join(mentions), count

    def _format_range(
        self,
        min_elo: int | None,
        max_elo: int | None,
        min_kind: str,
        max_kind: str,
    ) -> str:
        if min_kind == "l":
            return f"Lower - {max_elo}"
        if max_kind == "h":
            return f"{min_elo} - Higher"
        return f"{min_elo} - {max_elo}"

    def _effective_range(
        self,
        min_elo: int | None,
        max_elo: int | None,
        min_kind: str,
        max_kind: str,
    ) -> tuple[int, int]:
        low = -(10**18) if min_kind == "l" else int(min_elo)  # type: ignore[arg-type]
        high = 10**18 if max_kind == "h" else int(max_elo)  # type: ignore[arg-type]
        return low, high

    def _format_member_mentions_from_ids(
        self,
        user_ids: list[int],
        *,
        max_mentions: int | None = 25,
        one_per_line: bool = False,
        comma_wrap_every: int | None = None,
    ) -> tuple[str, int]:
        count = len(user_ids)
        if count == 0:
            return "*No players in this league.*", 0

        limit = count if max_mentions is None else min(max_mentions, count)
        slice_ids = user_ids[:limit]
        extra = count - len(slice_ids)

        if comma_wrap_every is not None and comma_wrap_every > 0:
            lines: list[str] = []
            for i in range(0, len(slice_ids), comma_wrap_every):
                chunk = slice_ids[i : i + comma_wrap_every]
                lines.append(", ".join(f"<@{uid}>" for uid in chunk))
            text = "\n".join(lines)
            if extra > 0:
                text += f"\n*…and {extra} more*"
            return text, count

        mentions = [f"<@{user_id}>" for user_id in slice_ids]
        if extra > 0:
            mentions.append(f"*…and {extra} more*")
        sep = "\n" if one_per_line else " "
        return sep.join(mentions), count

    def _population_notes_suffix(self, pop_stats: list[tuple[int, str]]) -> str:
        if not pop_stats:
            return "\n- Most populated: -\n- Least populated: -"
        most = max(pop_stats, key=lambda x: x[0])
        least = min(pop_stats, key=lambda x: x[0])
        lines = f"\n- Most populated: {most[1]} (**{most[0]}**)"
        if most[1] == least[1]:
            lines += "\n- Least populated: -"
        else:
            lines += f"\n- Least populated: {least[1]} (**{least[0]}**)"
        return lines

    def _league_announce_channels_lines(self, guild: discord.Guild) -> list[str]:
        ids = get_league_announce_channel_ids(guild.id)
        if not ids:
            return ["*None configured.*"]
        out: list[str] = []
        for cid in ids:
            ch = guild.get_channel(cid)
            if isinstance(ch, discord.TextChannel):
                out.append(f"- {ch.mention} (`{ch.name}`)")
            else:
                out.append(f"- Unknown or deleted channel (ID `{cid}`)")
        return out

    def _leagues_matching_name_query(
        self,
        leagues: list[LeagueRow],
        query: str,
    ) -> list[LeagueRow]:
        q = query.strip().lower()
        if not q:
            return []
        return [row for row in leagues if q in row[0].lower()]

    def _leagues_author_header_and_footer(
        self,
        ctx: commands.Context,
        guild: discord.Guild,
        leagues: list[LeagueRow],
    ) -> tuple[str, str | None]:
        role_by_id = {r.id: r for r in guild.roles}
        with db_connection(guild.id) as (conn, cursor):
            cursor.execute(
                "SELECT elo, games_won, games_lost FROM players WHERE id=?",
                (str(ctx.author.id),),
            )
            row = cursor.fetchone()

        if not row:
            header = f"**Your league:** - *({ctx.author.display_name} is not registered)*"
            footer = (
                "*Register with `-register <name>` to get a league by Elo after you "
                "play matches.*"
            )
            return header, footer

        elo, gw, gl = int(row[0]), int(row[1]), int(row[2])
        if gw + gl <= 0:
            header = "**Your league:** - *(no wins or losses yet)*"
            footer = (
                "*Play at least one set (record a win or loss) to be placed into a "
                "league by Elo.*"
            )
            return header, footer

        effective = [
            (n, mi, ma, mik, mak, rid)
            for (n, mi, ma, mik, mak, rid, _d) in leagues
            if rid is not None and rid in role_by_id
        ]
        rid = pick_league_role_id_for_elo(effective, elo)
        if rid is None:
            header = "**Your league:** - *(Elo is outside every configured range)*"
            footer = (
                "*Your Elo does not fall into any league bracket yet (or leagues have "
                "no valid roles on this server).*"
            )
            return header, footer

        lname = next(
            (n for n, _a, _b, _c, _d, r in effective if r == rid),
            "?",
        )
        role = role_by_id.get(rid)
        lab = role.mention if role else f"`{lname}`"
        header = f"**Your league:** {lab}"
        return header, None

    def _get_registered_players_grouped_by_target_league(
        self,
        guild: discord.Guild,
        leagues: list[LeagueRow],
    ) -> dict[int, list[int]]:
        role_by_id: dict[int, discord.Role] = {r.id: r for r in guild.roles}
        effective_leagues = [
            (name, min_elo, max_elo, min_kind, max_kind, role_id)
            for (name, min_elo, max_elo, min_kind, max_kind, role_id, _disc) in leagues
            if role_id is not None and role_id in role_by_id
        ]
        grouped: dict[int, list[int]] = {}
        if not effective_leagues:
            return grouped

        with db_connection(guild.id) as (conn, cursor):
            cursor.execute("SELECT id, elo, games_won, games_lost FROM players")
            rows = cursor.fetchall()

        for player_id_raw, elo_raw, gw_raw, gl_raw in rows:
            try:
                player_id = int(player_id_raw)
                elo = int(elo_raw)
                gw = int(gw_raw)
                gl = int(gl_raw)
            except Exception:
                continue
            if gw + gl <= 0:
                continue
            role_id = pick_league_role_id_for_elo(effective_leagues, elo)
            if role_id is None:
                continue
            grouped.setdefault(role_id, []).append(player_id)

        for role_id in grouped:
            grouped[role_id].sort()
        return grouped

    def _format_league_apply_delta(
        self,
        added: list[tuple[int, int]],
        removed: list[tuple[int, int]],
    ) -> str:
        lines_added: list[str] = []
        if added:
            for uid, rid in sorted(added, key=lambda x: (x[0], x[1])):
                lines_added.append(f"<@{uid}> -> <@&{rid}>")

        lines_removed: list[str] = []
        if removed:
            by_user: dict[int, list[int]] = {}
            for uid, rid in removed:
                by_user.setdefault(uid, []).append(rid)
            for uid in sorted(by_user.keys()):
                rids = sorted(set(by_user[uid]))
                roles_part = " ".join(f"<@&{r}>" for r in rids)
                lines_removed.append(f"<@{uid}>: {roles_part}")

        sec1 = "**Players who received a league role:**\n" + (
            "\n".join(lines_added) if lines_added else "*None.*"
        )
        sec2 = "**Players who had a league role removed:**\n" + (
            "\n".join(lines_removed) if lines_removed else "*None.*"
        )
        text = f"{sec1}\n\n{sec2}"
        if len(text) > 3500:
            text = text[:3500] + "\n*(truncated…)*"
        return text

    def _count_registered_players_with_league_roles(
        self, guild: discord.Guild, league_role_ids: set[int]
    ) -> int:
        if not league_role_ids:
            return 0
        with db_connection(guild.id) as (conn, cursor):
            cursor.execute("SELECT id FROM players")
            player_rows = cursor.fetchall()
        registered_ids: set[int] = set()
        for (player_id_raw,) in player_rows:
            try:
                registered_ids.add(int(player_id_raw))
            except Exception:
                continue
        count = 0
        for player_id in registered_ids:
            member = guild.get_member(player_id)
            if member is None:
                continue
            if any(role.id in league_role_ids for role in member.roles):
                count += 1
        return count

    async def _apply_leagues(
        self,
        guild: discord.Guild,
        *,
        mark_populated_leagues_discovered: bool = False,
    ) -> tuple[int, int, list[str], str]:
        updated, missing_from_db, added_log, removed_log = await sync_league_roles(guild)
        leagues = get_leagues(guild.id)
        grouped = self._get_registered_players_grouped_by_target_league(guild, leagues)
        assigned_count = sum(len(user_ids) for user_ids in grouped.values())

        if mark_populated_leagues_discovered:
            role_ids_to_discover = sorted(
                {
                    rid
                    for rid, user_ids in grouped.items()
                    if rid is not None and len(user_ids) >= 1
                }
            )
            if role_ids_to_discover:
                with db_connection(guild.id) as (conn, cursor):
                    cursor.executemany(
                        "UPDATE leagues SET discovered=1 WHERE role_id=?",
                        [(rid,) for rid in role_ids_to_discover],
                    )
                    conn.commit()

        details = self._format_league_apply_delta(added_log, removed_log)
        return updated, assigned_count, missing_from_db, details

    def _parse_league_lines(
        self, ctx: commands.Context, raw_lines: list[str]
    ) -> tuple[list[LeagueRow], list[str], list[str]]:
        bad_lines: list[str] = []
        missing_roles: list[str] = []
        leagues_to_store: list[LeagueRow] = []

        for ln in raw_lines:
            parts = ln.split()
            if len(parts) not in (3, 4):
                bad_lines.append(ln)
                continue

            if len(parts) == 3:
                name_raw, min_s, max_s = parts
                discovered = 0
            else:
                name_raw, min_s, max_s, disc_s = parts
                try:
                    discovered = int(disc_s)
                except ValueError:
                    bad_lines.append(ln)
                    continue
                if discovered not in (0, 1):
                    bad_lines.append(ln)
                    continue

            name = self._parse_league_name(name_raw)
            min_kind = "n"
            max_kind = "n"
            min_elo: int | None = None
            max_elo: int | None = None

            if min_s.lower() == "l":
                min_kind = "l"
                try:
                    max_elo = int(max_s)
                except ValueError:
                    bad_lines.append(ln)
                    continue
            elif max_s.lower() == "h":
                max_kind = "h"
                try:
                    min_elo = int(min_s)
                except ValueError:
                    bad_lines.append(ln)
                    continue
            else:
                try:
                    min_elo = int(min_s)
                    max_elo = int(max_s)
                except ValueError:
                    bad_lines.append(ln)
                    continue
                if min_elo > max_elo:
                    bad_lines.append(ln)
                    continue

            role = discord.utils.get(ctx.guild.roles, name=name)
            if not role:
                missing_roles.append(name)
                continue

            leagues_to_store.append(
                (name, min_elo, max_elo, min_kind, max_kind, role.id, discovered)
            )

        return leagues_to_store, bad_lines, missing_roles

    async def _ask_reply_number(
        self,
        ctx: commands.Context,
        prompt_embed: discord.Embed,
        *,
        min_value: int,
        max_value: int,
        timeout_s: float = 300.0,
        cancel_event: asyncio.Event | None = None,
    ) -> int | None:
        prompt_msg = await ctx.send(embed=prompt_embed)

        def check(m: discord.Message):
            return (
                m.author.id == ctx.author.id
                and m.channel.id == ctx.channel.id
                and m.reference is not None
                and m.reference.message_id == prompt_msg.id
            )

        message_task = asyncio.create_task(
            self.bot.wait_for("message", timeout=timeout_s, check=check)
        )
        cancel_task = (
            asyncio.create_task(cancel_event.wait())
            if cancel_event is not None
            else None
        )
        try:
            if cancel_task is None:
                reply: discord.Message = await message_task
            else:
                done, pending = await asyncio.wait(
                    {message_task, cancel_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for t in pending:
                    t.cancel()
                if (
                    cancel_task in done
                    and cancel_event is not None
                    and cancel_event.is_set()
                ):
                    await ctx.send(
                        embed=discord.Embed(
                            title="Canceled",
                            description="League task has been canceled.",
                            color=discord.Color.red(),
                        )
                    )
                    return None
                reply = message_task.result()
        except Exception:
            await ctx.send(
                embed=discord.Embed(
                    title="Timeout",
                    description="Timed out. Please try again.",
                    color=discord.Color.red(),
                )
            )
            return None

        try:
            n = int((reply.content or "").strip())
        except ValueError:
            await ctx.send(
                embed=discord.Embed(
                    title="Invalid Number",
                    description="Please reply with a valid number.",
                    color=discord.Color.red(),
                )
            )
            return None

        if n < min_value or n > max_value:
            await ctx.send(
                embed=discord.Embed(
                    title="Out of Range",
                    description=(
                        f"Please reply with a number between **{min_value}** and **{max_value}**."
                    ),
                    color=discord.Color.red(),
                )
            )
            return None

        return n