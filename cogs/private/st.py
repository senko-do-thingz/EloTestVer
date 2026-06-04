import discord
from discord.ext import commands

from db.elo_db import db_connection, db_exists, get_db_path
from utils import (
    format_league_stat_line,
    get_leagues,
    get_most_played_with,
    get_opponent_games,
)
from utils.logger import bot_log
from utils.private_guard import is_private_allowed, send_not_available

STATS_ELO_LEADERBOARD_NOTE = (
    "*Play at least one set to be ranked on the Elo leaderboard.*"
)


class PrivateStCog(commands.Cog, name="PrivateSt"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _get_display_avatar_url(self, ctx: commands.Context, user_id: int) -> str | None:
        if ctx.guild:
            member = ctx.guild.get_member(user_id)
            if member is not None:
                return member.display_avatar.url
        try:
            fetched = await self.bot.fetch_user(user_id)
            return fetched.display_avatar.url
        except Exception:
            return None

    def _apply_stats_avatar(self, embed: discord.Embed, avatar_url: str | None) -> None:
        if avatar_url:
            embed.set_thumbnail(url=avatar_url)

    async def _safe_fetch_mention(self, user_id: int) -> str:
        try:
            user = await self.bot.fetch_user(user_id)
            return user.mention
        except Exception:
            return f"<@{user_id}>"

    async def _format_most_played_with(
        self, ctx: commands.Context, most_played_with: list[str], cursor, player_id: str
    ) -> str:
        if not most_played_with:
            return "Play at least 1 set to see your dream person"
        formatted_list = []
        for name in most_played_with:
            cursor.execute("SELECT id FROM players WHERE name = ?", (name,))
            row = cursor.fetchone()
            games = get_opponent_games(cursor, player_id, name)
            if row:
                uid = int(row[0])
                mention = await self._safe_fetch_mention(uid)
                formatted_list.append(f"**{name}** ({mention}) - {games} games")
            else:
                formatted_list.append(f"**{name}** - {games} games")
        return "; ".join(formatted_list)

    @commands.command(name="st", hidden=True)
    async def st(self, ctx: commands.Context, server_id: int, player_id: int):
        """View stats for a player in any server by their Discord ID (private guild only)."""
        if not is_private_allowed(ctx.guild):
            await send_not_available(ctx)
            return

        if not db_exists(server_id):
            db_path = get_db_path(server_id).replace("\\", "/")
            await ctx.send(
                embed=discord.Embed(
                    title="Database Not Found",
                    description=(
                        f"No database found for server ID **{server_id}** (`{db_path}`)."
                    ),
                    color=discord.Color.red(),
                )
            )
            return

        with db_connection(server_id) as (conn, cursor):
            cursor.execute(
                "SELECT id, name, games_won, games_lost, elo, games_played_this_month"
                " FROM players WHERE id = ?",
                (player_id,),
            )
            row = cursor.fetchone()
            if not row:
                await ctx.send(
                    embed=discord.Embed(
                        title="Player Not Found",
                        description=f"No player with Discord ID `{player_id}` in server `{server_id}`.",
                        color=discord.Color.red(),
                    )
                )
                return

            _uid, name, games_won, games_lost, elo, games_played_this_month = row
            player_id_str = str(player_id)
            total_games = games_won + games_lost
            win_rate = (games_won / total_games) * 100 if total_games > 0 else 0

            cursor.execute("SELECT COUNT(*) FROM players WHERE elo > ?", (elo,))
            rank = cursor.fetchone()[0] + 1

            leagues = get_leagues(server_id)
            league_line = format_league_stat_line(leagues, elo, games_won, games_lost, cursor)
            elo_line = f"🔥 Elo: {elo} (#{rank})" if total_games > 0 else "🔥 Elo: -"
            mention_inline = await self._safe_fetch_mention(player_id)
            header = f"**{name}** ({mention_inline})"

            if total_games > 0:
                most_played_with = get_most_played_with(cursor, player_id_str)
                if most_played_with:
                    most_played_text = await self._format_most_played_with(
                        ctx, most_played_with, cursor, player_id_str
                    )
                else:
                    most_played_text = "No data"
                tail = f"👥 Most Played: {most_played_text}"
            else:
                tail = STATS_ELO_LEADERBOARD_NOTE

            description = (
                f"{header}\n"
                f"🌐 Server: {server_id}\n"
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
        avatar_url = await self._get_display_avatar_url(ctx, player_id)
        self._apply_stats_avatar(embed, avatar_url)
        await ctx.send(embed=embed)

        await bot_log(
            ctx.bot,
            ctx.guild.id,
            "Command used: -st",
            level="INFO",
            fields={
                "User": f"{ctx.author.mention} ({ctx.author.id})",
                "Target Server": str(server_id),
                "Target Player": f"`{player_id}`",
                "Input": f"`{ctx.message.content[:200]}`",
            },
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(PrivateStCog(bot))