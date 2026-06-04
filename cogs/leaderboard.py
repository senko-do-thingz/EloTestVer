import io

import discord
from discord.ext import commands

from db.elo_db import db_connection, db_exists, get_db_path
from utils import calculate_win_rate, render_leaderboard_image
from utils.lb_cache import get as cache_get
import logging

logger = logging.getLogger(__name__)


def _build_leaderboard_embed(
    title: str,
    entries: list[tuple[int, str, str] | tuple[int, str, str, bool]],
    *,
    page: int | None = None,
    total_pages: int | None = None,
    note: str | None = None,
) -> tuple[discord.Embed, discord.File]:
    filename = "leaderboard.png"

    # Thử lấy từ cache trước (chỉ có khi KHÔNG có highlight cá nhân)
    # Cache rebuild không biết user nào highlight nên ta render fresh khi cần
    buf = render_leaderboard_image(entries, title=title, page=page, total_pages=total_pages)

    file = discord.File(buf, filename=filename)
    embed = discord.Embed(color=discord.Color.blue())
    embed.set_image(url=f"attachment://{filename}")
    if note:
        embed.set_footer(text=note)
    return embed, file


def _build_embed_from_bytes(
    png_bytes: bytes,
    *,
    note: str | None = None,
) -> tuple[discord.Embed, discord.File]:
    """Tạo embed trực tiếp từ cached PNG bytes, không cần render lại."""
    filename = "leaderboard.png"
    file = discord.File(io.BytesIO(png_bytes), filename=filename)
    embed = discord.Embed(color=discord.Color.blue())
    embed.set_image(url=f"attachment://{filename}")
    if note:
        embed.set_footer(text=note)
    return embed, file


class LeaderboardDropdown(discord.ui.Select):
    def __init__(self, view):
        options = [
            discord.SelectOption(
                label="🏆 Top Elo", value="elo", description="Players with the highest Elo"
            ),
            discord.SelectOption(
                label="🥇 Top Wins", value="wins", description="Players with the most wins"
            ),
            discord.SelectOption(
                label="💀 Top Losses",
                value="losses",
                description="Players with the most losses",
            ),
            discord.SelectOption(
                label="📊 Top Win Rate",
                value="win_rate",
                description="Players with the highest win rate",
            ),
            discord.SelectOption(
                label="🎮 Top Monthly Games Played",
                value="games_played_this_month",
                description="Players with the most monthly games played",
            ),
        ]
        super().__init__(
            placeholder="Select a leaderboard...",
            min_values=1,
            max_values=1,
            options=options,
        )
        self.custom_view = view

    async def callback(self, interaction: discord.Interaction):
        self.custom_view.category = self.values[0]
        self.custom_view.page = 0
        await self.custom_view.update_leaderboard(interaction)


class PreviousPageButton(discord.ui.Button):
    def __init__(self, custom_view):
        super().__init__(style=discord.ButtonStyle.secondary, label="Prev", disabled=True)
        self.custom_view = custom_view

    async def callback(self, interaction: discord.Interaction):
        self.custom_view.page -= 1
        await self.custom_view.update_leaderboard(interaction)


class NextPageButton(discord.ui.Button):
    def __init__(self, custom_view):
        super().__init__(style=discord.ButtonStyle.secondary, label="Next", disabled=False)
        self.custom_view = custom_view

    async def callback(self, interaction: discord.Interaction):
        self.custom_view.page += 1
        await self.custom_view.update_leaderboard(interaction)


class LeaderboardView(discord.ui.View):
    def __init__(
        self,
        ctx,
        category: str = "elo",
        page: int = 0,
        *,
        db_guild_id: int | None = None,
    ):
        super().__init__()
        self.ctx = ctx
        self.db_guild_id = db_guild_id if db_guild_id is not None else ctx.guild.id
        self.category = category
        self.page = page
        self.per_page = 10
        self.total_pages = 1
        self.target_user_id = ctx.author.id
        self.add_item(LeaderboardDropdown(self))
        self.add_item(PreviousPageButton(self))
        self.add_item(NextPageButton(self))

    def _title_prefix(self) -> str:
        return "Lb - "

    def _dev_note(self, note_text: str | None) -> str | None:
        if self.db_guild_id != self.ctx.guild.id:
            dev = f"Dev Mode - Leaderboards from Server {self.db_guild_id}"
            return f"{note_text}\n{dev}" if note_text else dev
        return note_text

    async def update_leaderboard(self, interaction_or_ctx):
        with db_connection(self.db_guild_id) as (conn, cursor):
            await self._render_leaderboard(interaction_or_ctx, cursor)

    async def _render_leaderboard(self, interaction_or_ctx, cursor) -> None:
        cursor.execute(
            "SELECT name, elo, games_won, games_lost FROM players WHERE id=?",
            (self.target_user_id,),
        )
        target_row = cursor.fetchone()
        target_registered = target_row is not None
        target_name = target_row[0] if target_row else ""
        target_elo = target_row[1] if target_row else 0
        target_wins = target_row[2] if target_row else 0
        target_losses = target_row[3] if target_row else 0
        target_total_games = target_wins + target_losses

        note_text = None
        title = ""

        display_players: list[tuple[int, str, float]] = []
        if self.category == "elo":
            title = "Top Elo"
            cursor.execute(
                "SELECT id, name, elo, games_won, games_lost FROM players ORDER BY elo DESC"
            )
            for pid, name, elo, won, lost in cursor.fetchall():
                if won + lost == 0:
                    continue
                display_players.append((pid, name, elo))

            target_eligible = (target_total_games > 0)
            if target_registered and not target_eligible:
                note_text = "Play at least one set to be ranked"

        elif self.category == "wins":
            title = "Top Wins"
            cursor.execute(
                "SELECT id, name, games_won FROM players ORDER BY games_won DESC"
            )
            for pid, name, won in cursor.fetchall():
                if won == 0:
                    continue
                display_players.append((pid, name, won))

            target_eligible = (target_wins > 0)
            if target_registered and not target_eligible:
                note_text = "Win at least one game to be ranked"

        elif self.category == "losses":
            title = "Top Losses"
            cursor.execute(
                "SELECT id, name, games_lost FROM players ORDER BY games_lost DESC"
            )
            for pid, name, lost in cursor.fetchall():
                if lost == 0:
                    continue
                display_players.append((pid, name, lost))

            target_eligible = (target_losses > 0)
            if target_registered and not target_eligible:
                note_text = "Lose at least one game to be ranked"

        elif self.category == "win_rate":
            title = "Top Win Rate"
            cursor.execute(
                "SELECT id, name, games_won, games_lost FROM players"
            )
            eligible = []
            for pid, name, won, lost in cursor.fetchall():
                if won + lost < 10:
                    continue
                eligible.append((pid, name, calculate_win_rate(won, lost)))
            eligible.sort(key=lambda x: x[2], reverse=True)
            display_players = eligible

            target_eligible = (target_total_games >= 10)
            if target_registered and not target_eligible:
                note_text = "Play at least ten games to be ranked"

        elif self.category == "games_played_this_month":
            title = "Top Monthly Games Played"
            cursor.execute(
                "SELECT id, name, games_played_this_month, games_won, games_lost "
                "FROM players ORDER BY games_played_this_month DESC"
            )
            for pid, name, games, won, lost in cursor.fetchall():
                if won + lost == 0:
                    continue
                display_players.append((pid, name, games))

            target_eligible = target_total_games > 0
            if target_registered and not target_eligible:
                note_text = "Play at least one set to be ranked"

        if not display_players:
            embed = discord.Embed(
                title="Leaderboard Not Found",
                description="No one has yet qualified for this ranking, be the first to do so!",
                color=discord.Color.red(),
            )
            if isinstance(interaction_or_ctx, discord.Interaction):
                await interaction_or_ctx.response.send_message(embed=embed, ephemeral=True)
            else:
                await interaction_or_ctx.send(embed=embed)
            return

        self.total_pages = max(1, (len(display_players) - 1) // self.per_page + 1)
        if self.page >= self.total_pages:
            self.page = self.total_pages - 1
        start = self.page * self.per_page
        end = start + self.per_page
        page_players = display_players[start:end]

        # Kiểm tra xem user có trong trang này không (để highlight)
        target_in_page = any(pid == self.target_user_id for pid, _, _ in page_players)

        self.children[1].disabled = self.page == 0
        self.children[2].disabled = self.page >= self.total_pages - 1

        # --- Dùng cache nếu user không nằm trong trang (không cần highlight) ---
        # Cache được rebuild không có highlight; khi user có trong trang thì
        # phải render fresh để thêm highlight row đúng người.
        use_cache = (
            not target_in_page
            and self.db_guild_id == self.ctx.guild.id  # không cache cross-server lb
        )

        if use_cache:
            cached = cache_get(self.db_guild_id, self.category, self.page)
            if cached is not None:
                logger.debug(
                    "LB serve  HIT  guild=%s category=%s page=%d user=%s",
                    self.db_guild_id, self.category, self.page, self.target_user_id,
                )
                embed, file = _build_embed_from_bytes(cached, note=self._dev_note(note_text))
                if isinstance(interaction_or_ctx, discord.Interaction):
                    await interaction_or_ctx.response.edit_message(
                        embed=embed, attachments=[file], view=self
                    )
                else:
                    await interaction_or_ctx.send(embed=embed, file=file, view=self)
                return
            else:
                logger.debug(
                    "LB serve  MISS guild=%s category=%s page=%d user=%s — rendering fresh",
                    self.db_guild_id, self.category, self.page, self.target_user_id,
                )
        else:
            logger.debug(
                "LB serve  SKIP-CACHE guild=%s category=%s page=%d user=%s (highlight or cross-server)",
                self.db_guild_id, self.category, self.page, self.target_user_id,
            )

        # --- Cache miss or user in page → render fresh ---
        entries = []
        for i, (pid, name, stat) in enumerate(page_players):
            rank = start + i + 1
            stat_str = f"{stat:.2f}" if self.category == "win_rate" else str(stat)
            entries.append((rank, name, stat_str, pid == self.target_user_id))

        embed, file = _build_leaderboard_embed(
            f"{self._title_prefix()}{title}",
            entries,
            page=self.page + 1,
            total_pages=self.total_pages,
            note=self._dev_note(note_text),
        )

        if isinstance(interaction_or_ctx, discord.Interaction):
            await interaction_or_ctx.response.edit_message(
                embed=embed, attachments=[file], view=self
            )
        else:
            await interaction_or_ctx.send(embed=embed, file=file, view=self)


class LeaderboardCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(help="Show the top 10 players on the leaderboard with sorting options.")
    async def leaderboard(self, ctx: commands.Context):
        view = LeaderboardView(ctx)
        await view.update_leaderboard(ctx)

    @commands.command(
        name="lb",
        help="Show the top 10 players on the leaderboard with sorting options (alias for leaderboard).",
    )
    async def lb(self, ctx: commands.Context):
        await self.leaderboard(ctx)


async def setup(bot: commands.Bot):
    await bot.add_cog(LeaderboardCog(bot))