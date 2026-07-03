"""League viewing commands: leagues, ladmin, lhelp."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from utils import get_leagues, is_admin

from .base import RolesBaseCog


class RolesViewCog(RolesBaseCog):
    """Commands: leagues, ladmin, lhelp."""

    @commands.hybrid_command(
        name="leagues",
        description="Show league roles, Elo ranges, and player counts.",
        help=(
            "Show league roles, Elo ranges, and player counts. "
            "Usage: -leagues | -leagues <server_id> | -leagues <name> (substring match)"
        ),
        with_app_command=True,
    )
    @app_commands.describe(league_query="Server ID and/or league name substring (optional)")
    @app_commands.guild_only()
    async def leagues(self, ctx: commands.Context, *, league_query: str = "") -> None:
        target_guild = ctx.guild
        q = league_query.strip()
        tokens = q.split(None, 1)
        if tokens and tokens[0].isdigit():
            server_id = int(tokens[0])
            resolved = self.bot.get_guild(server_id)
            if resolved is None:
                await ctx.send(
                    embed=discord.Embed(
                        title="Server Not Found",
                        description=(
                            f"Bot is not in a server with ID `{server_id}`, or that ID is invalid."
                        ),
                        color=discord.Color.red(),
                    )
                )
                return
            target_guild = resolved
            q = tokens[1].strip() if len(tokens) > 1 else ""

        leagues = get_leagues(target_guild.id)
        if not leagues:
            label = (
                f" (server `{target_guild.id}`)"
                if target_guild.id != ctx.guild.id
                else ""
            )
            await ctx.send(
                embed=discord.Embed(
                    title="No Leagues",
                    description=(
                        f"No leagues have been set up yet{label}. Use `-ladd <number>` first."
                    ),
                    color=discord.Color.red(),
                )
            )
            return

        if q:
            await self._leagues_detail_by_name(ctx, leagues, q)
            return

        viewing_other = target_guild.id != ctx.guild.id
        if viewing_other:
            author_header = (
                f"*Viewing leagues from **{discord.utils.escape_markdown(target_guild.name)}**"
                f" (`{target_guild.id}`)*"
            )
            author_footer = None
        else:
            author_header, author_footer = self._leagues_author_header_and_footer(
                ctx, ctx.guild, leagues
            )

        role_by_id = {r.id: r for r in target_guild.roles}
        grouped_by_target = self._get_registered_players_grouped_by_target_league(
            target_guild, leagues
        )
        lines: list[str] = []
        pop_stats: list[tuple[int, str]] = []
        hidden_count = 0

        for name, min_elo, max_elo, min_kind, max_kind, role_id, discovered in reversed(leagues):
            role = role_by_id.get(role_id) if role_id else None
            role_display = role.mention if role else f"`{name}`"
            range_display = self._format_range(min_elo, max_elo, min_kind, max_kind)

            if not discovered:
                hidden_count += 1
                continue

            member_ids = grouped_by_target.get(role_id, []) if role_id else []
            _mentions, member_count = self._format_member_mentions_from_ids(member_ids)
            if member_count > 0:
                pop_stats.append((member_count, role_display))
            lines.append(
                f"{role_display}: **{range_display}** - **{member_count}** player(s)"
            )

        body = "\n".join(lines) if lines else "*No leagues discovered yet.*"
        if hidden_count > 0:
            body += (
                f"\n\n*There are still {hidden_count} league(s) left to discover"
                " - keep playing!*"
            )

        body += "\n\n**Population notes**" + self._population_notes_suffix(pop_stats)

        usage_notes = (
            "\n\n**How to list players in one league**\n"
            "*Use* `-leagues <league_name>`\n"
            "*Do **not** put* `@` *or role mentions in* `<league_name>`*; You will ping "
            "everyone with that role or treat it as a mention.*"
        )
        tail = f"\n\n{author_footer}" if author_footer else ""
        description = f"{author_header}\n\n{body}{usage_notes}{tail}"

        await ctx.send(
            embed=discord.Embed(
                title="Leagues",
                description=description,
                color=discord.Color.blurple(),
            )
        )

    @commands.command(
        name="ladmin",
        help=(
            "Admin view: show all leagues (discovered and undiscovered). "
            "Usage: -ladmin | -ladmin <server_id>"
        ),
    )
    @commands.check(is_admin)
    async def ladmin(self, ctx: commands.Context, *, args: str = "") -> None:
        target_guild = ctx.guild
        tokens = args.strip().split(None, 1)
        if tokens and tokens[0].isdigit():
            server_id = int(tokens[0])
            resolved = self.bot.get_guild(server_id)
            if resolved is None:
                await ctx.send(
                    embed=discord.Embed(
                        title="Server Not Found",
                        description=(
                            f"Bot is not in a server with ID `{server_id}`, or that ID is invalid."
                        ),
                        color=discord.Color.red(),
                    )
                )
                return
            target_guild = resolved

        leagues = get_leagues(target_guild.id)
        if not leagues:
            await ctx.send(
                embed=discord.Embed(
                    title="No Leagues",
                    description=(
                        "No leagues are configured. Use `-ladd <number>` to set them up."
                    ),
                    color=discord.Color.red(),
                )
            )
            return

        role_by_id = {r.id: r for r in target_guild.roles}
        grouped = self._get_registered_players_grouped_by_target_league(
            target_guild, leagues
        )
        lines: list[str] = []
        for name, min_elo, max_elo, min_kind, max_kind, role_id, discovered in reversed(leagues):
            role_display = self._format_league_display(role_by_id, name, role_id)
            range_display = self._format_range(min_elo, max_elo, min_kind, max_kind)
            status = "✅ discovered" if discovered else "🔒 undiscovered"
            member_ids = grouped.get(role_id, []) if role_id else []
            lines.append(
                f"{role_display}: **{range_display}** - **{len(member_ids)}** player(s) [{status}]"
            )

        description = "\n".join(lines)
        if target_guild.id != ctx.guild.id:
            description = (
                f"*Viewing from **{discord.utils.escape_markdown(target_guild.name)}**"
                f" (`{target_guild.id}`)*\n\n{description}"
            )

        await ctx.send(
            embed=discord.Embed(
                title="Leagues (Admin View)",
                description=description,
                color=discord.Color.blurple(),
            )
        )

    @commands.command(name="lhelp", help="Show help for all league-related commands.")
    async def lhelp(self, ctx: commands.Context) -> None:
        # ── Commands ──────────────────────────────────────────────────
        embed = discord.Embed(
            title="League Commands Help",
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="Commands",
            value=(
                "- `-ladd <number>` : Create league ranges and sync roles\n"
                "- `-lapply` : Re-apply league roles for all registered players\n"
                "- `-lcancel` : Cancel any running league task in this channel\n"
                "- `-lclear` : Remove all league roles from players and clear all leagues\n"
                "- `-lannounce <channel_name>` : Set a channel for league promotion/demotion announcements\n"
                "- `-ledit` : Edit league boundaries\n"
                "- `-leagues` : Show discovered leagues and their Elo ranges\n"
                "- `-ladmin` : Show all leagues"
            ),
            inline=False,
        )

        # ── Required setup ────────────────────────────────────────────
        embed.add_field(
            name="Required setup",
            value=(
                "- To start using this feature, grant the bot permission by doing: "
                "`Server Settings` → `Roles` → `Elo Bot` → search and enable `Manage Roles`.\n"
                "- Then create the server roles for your leagues (e.g. `Diamond`, `Gold`, etc.)."
            ),
            inline=False,
        )

        # ── Input format for -ladd reply ──────────────────────────────
        embed.add_field(
            name="Input format for `-ladd` reply",
            value=(
                "- `league_name minimum_elo maximum_elo [discovered]`\n"
                "- Use `l` as `minimum_elo` for **Lower** (from `maximum_elo` and below)\n"
                "- Use `h` as `maximum_elo` for **Higher** (from `minimum_elo` and above)\n"
                "- `discovered` is optional: `1` = discovered, `0` = undiscovered (default: `0`)"
            ),
            inline=False,
        )

        # ── League name (role name) ───────────────────────────────────
        embed.add_field(
            name="League name (role name)",
            value=(
                "- If the league name has **2+ characters**, `_` represents a space.\n"
                "  Example: `Diamond_League 900 h` matches a role named `Diamond League`."
            ),
            inline=False,
        )

        # ── Examples ──────────────────────────────────────────────────
        embed.add_field(
            name="Examples",
            value=(
                "```\n"
                "Silver l 599 1\n"
                "Gold 600 699 1\n"
                "Platinum 700 h 0\n"
                "```"
            ),
            inline=False,
        )

        await ctx.send(embed=embed)

    # ------------------------------------------------------------------
    # Internal: detail view for a single league by name
    # ------------------------------------------------------------------

    async def _leagues_detail_by_name(
        self,
        ctx: commands.Context,
        leagues,
        query: str,
    ) -> None:
        matches = self._leagues_matching_name_query(leagues, query)
        if not matches:
            await ctx.send(
                embed=discord.Embed(
                    title="League Not Found",
                    description=f"No league found with a name containing `{query.strip()}`.",
                    color=discord.Color.red(),
                )
            )
            return

        if len(matches) > 1:
            opts: list[str] = []
            for row in matches:
                lname, _mi, _ma, _mik, _mak, _rid, disc = row
                disc_tag = " *(undiscovered)*" if not disc else ""
                opts.append(f"- `{lname}`{disc_tag}")
            embed = discord.Embed(
                title="Multiple Leagues Found",
                description=(
                    f"Found **{len(matches)}** leagues matching `{query.strip()}`:\n\n"
                    + "\n".join(opts)
                ),
                color=discord.Color.yellow(),
            )
            embed.add_field(
                name="",
                value="Please be more specific (closer match to one league name).",
                inline=False,
            )
            await ctx.send(embed=embed)
            return

        row = matches[0]
        name, min_elo, max_elo, min_kind, max_kind, role_id, discovered = row
        if not discovered:
            await ctx.send(
                embed=discord.Embed(
                    title="League Not Found",
                    description=(
                        f"No **discovered** league matches `{query.strip()}`. "
                        "Undiscovered leagues cannot be viewed with this command yet."
                    ),
                    color=discord.Color.red(),
                )
            )
            return

        role_by_id = {r.id: r for r in ctx.guild.roles}
        role = role_by_id.get(role_id) if role_id else None
        role_display = role.mention if role else f"`{name}`"
        range_display = self._format_range(min_elo, max_elo, min_kind, max_kind)

        grouped = self._get_registered_players_grouped_by_target_league(ctx.guild, leagues)
        member_ids = grouped.get(role_id, []) if role_id else []
        member_block, member_count = self._format_member_mentions_from_ids(
            member_ids, max_mentions=None, comma_wrap_every=5
        )

        desc = (
            f"{role_display}\n"
            f"**Elo range:** {range_display}\n"
            f"**Players ({member_count}):**\n{member_block}"
        )
        if len(desc) > 3900:
            desc = desc[:3900] + "\n\n*(truncated…)*"

        await ctx.send(
            embed=discord.Embed(
                title="League",
                description=desc,
                color=discord.Color.blurple(),
            )
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RolesViewCog(bot))