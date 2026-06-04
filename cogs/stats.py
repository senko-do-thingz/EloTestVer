import re
import typing

import discord
from discord.ext import commands

from db.elo_db import db_connection, db_exists, get_db_path
from utils import (
    format_league_stat_line,
    get_leagues,
    get_most_played_with,
    get_opponent_games,
)

STATS_ELO_LEADERBOARD_NOTE = (
    "*Play at least one set to be ranked on the Elo leaderboard.*"
)


class StatsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _get_display_avatar_url(
        self,
        ctx: commands.Context,
        user: typing.Union[discord.User, discord.Member, None],
        user_id: int | None,
    ) -> str | None:
        if isinstance(user, (discord.User, discord.Member)):
            return user.display_avatar.url
        if user_id is None:
            return None
        if ctx.guild:
            member = ctx.guild.get_member(user_id)
            if member is not None:
                return member.display_avatar.url
        try:
            fetched = await self.bot.fetch_user(user_id)
            return fetched.display_avatar.url
        except Exception:
            return None

    def _apply_stats_avatar(
        self, embed: discord.Embed, avatar_url: str | None
    ) -> None:
        if avatar_url:
            embed.set_thumbnail(url=avatar_url)

    async def _safe_fetch_mention(
        self, user_obj_or_id: typing.Union[discord.User, discord.Member, int]
    ) -> str:
        try:
            if isinstance(user_obj_or_id, (discord.User, discord.Member)):
                return user_obj_or_id.mention
            user_id = int(user_obj_or_id)
            user = await self.bot.fetch_user(user_id)
            return user.mention
        except Exception:
            return f"<@{user_obj_or_id}>"

    async def _format_most_played_with(
        self,
        ctx: commands.Context,
        most_played_with: list[str],
        cursor,
        player_id: str,
    ) -> str:
        """Format most played with list with bold names, mentions, and games count."""
        if not most_played_with:
            return "Play at least 1 set to see your dream person"

        formatted_list = []
        for name in most_played_with:
            # Get user ID from database
            cursor.execute("SELECT id FROM players WHERE name = ?", (name,))
            row = cursor.fetchone()
            games = get_opponent_games(cursor, player_id, name)
            if row:
                user_id = int(row[0])
                mention = await self._safe_fetch_mention(user_id)
                formatted_list.append(f"**{name}** ({mention}) - {games} games")
            else:
                # If user not found in database, just show name in bold
                formatted_list.append(f"**{name}** - {games} games")

        return "; ".join(formatted_list)

    def _get_player_data(
        self,
        cursor,
        *,
        user_id: int | None = None,
        search_name: str | None = None,
    ) -> list[tuple]:
        """
        Fetch player row(s) from the database.

        Provide exactly one of *user_id* or *search_name*.

        Always returns a ``list[tuple]`` where each tuple is
        ``(id, name, games_won, games_lost, elo, games_played_this_month)``:
        - Empty list  → no match found
        - 1-item list → exact match (or single partial match)
        - N-item list → multiple partial matches (callers show disambiguation)

        Callers can uniformly check ``len(result)`` without needing
        ``isinstance`` guards.
        """
        if user_id is not None:
            cursor.execute(
                "SELECT id, name, games_won, games_lost, elo, games_played_this_month"
                " FROM players WHERE id = ?",
                (user_id,),
            )
            row = cursor.fetchone()
            return [row] if row else []

        if search_name is not None:
            cursor.execute(
                "SELECT id, name, games_won, games_lost, elo, games_played_this_month"
                " FROM players WHERE name LIKE ?",
                (f"%{search_name}%",),
            )
            return cursor.fetchall()

        raise ValueError("Provide either user_id or search_name")

    @commands.command(help="View player stats. Usage: -stats [@member or name]")
    async def stats(
        self, ctx: commands.Context, *, target: typing.Optional[str] = None
    ):
        user = None
        search_name = None
        had_target = target is not None  # whether caller provided an explicit target

        # resolve target: member, mention, or name search
        if target is None:
            user = ctx.author
        else:
            try:
                user = await commands.MemberConverter().convert(ctx, target)
            except commands.BadArgument:
                mention_match = re.match(r"<@!?(\d+)>", target)
                if mention_match:
                    uid = int(mention_match.group(1))
                    user = ctx.guild.get_member(uid)
                    if not user:
                        try:
                            user = await self.bot.fetch_user(uid)
                        except discord.NotFound:
                            await ctx.send(
                                embed=discord.Embed(
                                    title="User Not Found",
                                    description="Could not find the mentioned user.",
                                    color=discord.Color.red(),
                                )
                            )
                            return
                else:
                    search_name = target
                    user = None

        with db_connection(ctx.guild.id) as (conn, cursor):
            await self._stats_impl(ctx, cursor, user, search_name, target, had_target)

    async def _stats_impl(
        self,
        ctx: commands.Context,
        cursor,
        user,
        search_name,
        target,
        had_target: bool,
    ) -> None:
        data_from_search = False
        user_id_for_mention = None
        player_id: str | None = None

        if search_name:
            matches = self._get_player_data(cursor, search_name=search_name)
            if not matches:
                await ctx.send(
                    embed=discord.Embed(
                        title="Player Not Found",
                        description=f"No player found with the name `{search_name}`.",
                        color=discord.Color.red(),
                    )
                )
                return
            if len(matches) == 1:
                (
                    user_id_for_mention,
                    name,
                    games_won,
                    games_lost,
                    elo,
                    games_played_this_month,
                ) = matches[0]
                player_id = str(user_id_for_mention)
                user = ctx.guild.get_member(user_id_for_mention) or (
                    await self.bot.fetch_user(user_id_for_mention) if True else None
                )
                data_from_search = True
            else:
                player_list = []
                for i, m in enumerate(matches, start=1):
                    uid, pname = m[0], m[1]
                    member = ctx.guild.get_member(uid)
                    if member:
                        mention_text = member.mention
                    else:
                        try:
                            mention_text = (await self.bot.fetch_user(uid)).mention
                        except Exception:
                            mention_text = f"<@{uid}>"
                    player_list.append(f"{i}. {pname} ({mention_text})")
                embed = discord.Embed(
                    title="Multiple Players Found",
                    description=f"Found {len(matches)} players with name containing '{search_name}':\n\n"
                    + "\n".join(player_list),
                    color=discord.Color.yellow(),
                )
                embed.add_field(
                    name="",
                    value="Please be more specific, use IDs or mention the player with @",
                    inline=False,
                )
                await ctx.send(embed=embed)
                return

        if not data_from_search:
            if user is None:
                await ctx.send(
                    embed=discord.Embed(
                        title="Error",
                        description="Could not find the user.",
                        color=discord.Color.red(),
                    )
                )
                return
            row = self._get_player_data(cursor, user_id=user.id)
            if not row:
                mention_text = (
                    user.mention if hasattr(user, "mention") else f"<@{user.id}>"
                )
                await ctx.send(
                    embed=discord.Embed(
                        title="Player Not Registered",
                        description=f"{mention_text} is not registered. Use `-register <name>` to sign up!",
                        color=discord.Color.red(),
                    )
                )
                return
            _uid, name, games_won, games_lost, elo, games_played_this_month = row[0]
            player_id = str(user.id)
            # only include mention inline if caller explicitly provided a target
            if had_target:
                user_id_for_mention = user.id

        total_games = games_won + games_lost
        win_rate = (games_won / total_games) * 100 if total_games > 0 else 0

        cursor.execute("SELECT COUNT(*) FROM players WHERE elo > ?", (elo,))
        rank = cursor.fetchone()[0] + 1

        leagues = get_leagues(ctx.guild.id)
        league_line = format_league_stat_line(
            leagues, elo, games_won, games_lost, cursor
        )
        if total_games <= 0:
            elo_line = "🔥 Elo: -"
        else:
            elo_line = f"🔥 Elo: {elo} (#{rank})"

        # inline mention next to the player header (only when caller provided target)
        mention_inline = ""
        if user_id_for_mention:
            mention_inline = await self._safe_fetch_mention(
                user if user is not None else user_id_for_mention
            )

        header = f"**{name}**"
        if mention_inline:
            header = f"{header} ({mention_inline})"

        if total_games > 0 and player_id:
            most_played_with = get_most_played_with(cursor, player_id)
            if most_played_with:
                most_played_text = await self._format_most_played_with(
                    ctx, most_played_with, cursor, player_id
                )
            else:
                most_played_text = "Play at least 1 set to see your dream person"
            tail = f"👥 Most Played: {most_played_text}"
        else:
            tail = STATS_ELO_LEADERBOARD_NOTE

        description = (
            f"{header}\n"
            f"{elo_line}\n"
            f"{league_line}\n"
            f"🏆 Games Won: {games_won}\n"
            f"💀 Games Lost: {games_lost}\n"
            f"🎮 Monthly Games Played: {games_played_this_month}\n"
            f"📊 Win Rate: {win_rate:.2f}%\n"
            f"{tail}"
        )

        embed = discord.Embed(
            title="Player Statistics",
            description=description,
            color=discord.Color.blue(),
        )
        avatar_url = await self._get_display_avatar_url(
            ctx,
            user if isinstance(user, (discord.User, discord.Member)) else None,
            int(player_id) if player_id else None,
        )
        self._apply_stats_avatar(embed, avatar_url)
        await ctx.send(embed=embed)

    @commands.command(help="View player stats by Discord ID.")
    async def stats_id(self, ctx: commands.Context, discord_id: str):
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

        mention_inline = await self._safe_fetch_mention(user_id)

        with db_connection(ctx.guild.id) as (conn, cursor):
            row = self._get_player_data(cursor, user_id=user_id)

            if not row:
                await ctx.send(
                    embed=discord.Embed(
                        title="Player Not Registered",
                        description=f"{mention_inline} is not registered. Use `-register <name>` to sign up!",
                        color=discord.Color.red(),
                    )
                )
                return

            _uid, name, games_won, games_lost, elo, games_played_this_month = row[0]
            player_id = str(user_id)
            total_games = games_won + games_lost
            win_rate = (games_won / total_games) * 100 if total_games > 0 else 0
            cursor.execute("SELECT COUNT(*) FROM players WHERE elo > ?", (elo,))
            rank = cursor.fetchone()[0] + 1

            leagues = get_leagues(ctx.guild.id)
            league_line = format_league_stat_line(
                leagues, elo, games_won, games_lost, cursor
            )
            if total_games <= 0:
                elo_line = "🔥 Elo: -"
            else:
                elo_line = f"🔥 Elo: {elo} (#{rank})"

            header = f"**{name}**"
            if mention_inline:
                header = f"{header} ({mention_inline})"

            if total_games > 0:
                most_played_with = get_most_played_with(cursor, player_id)
                if most_played_with:
                    most_played_text = await self._format_most_played_with(
                        ctx, most_played_with, cursor, player_id
                    )
                else:
                    most_played_text = "Play at least 1 set to see your dream person"
                tail = f"👥 Most Played: {most_played_text}"
            else:
                tail = STATS_ELO_LEADERBOARD_NOTE

            description = (
                f"{header}\n"
                f"{elo_line}\n"
                f"{league_line}\n"
                f"🏆 Games Won: {games_won}\n"
                f"💀 Games Lost: {games_lost}\n"
                f"🎮 Monthly Games Played: {games_played_this_month}\n"
                f"📊 Win Rate: {win_rate:.2f}%\n"
                f"{tail}"
            )

        embed = discord.Embed(
            title="Player Statistics",
            description=description,
            color=discord.Color.blue(),
        )
        avatar_url = await self._get_display_avatar_url(ctx, None, user_id)
        self._apply_stats_avatar(embed, avatar_url)
        await ctx.send(embed=embed)



async def setup(bot: commands.Bot):
    await bot.add_cog(StatsCog(bot))