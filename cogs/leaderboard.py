import io
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from db.elo_db import db_connection, db_exists, get_db_path
from utils import calculate_win_rate, render_leaderboard_image
from utils.lb_cache import get as cache_get, LB_TITLE_PREFIX
import logging

logger = logging.getLogger(__name__)

# Container accent color, equivalent to the old embed.color
ACCENT_COLOR = discord.Color.blue()


def _footer_text(last_updated: datetime | None, note: str | None) -> str:
    """Build the 'Last updated' line + note. Uses <t:...:R> so Discord
    updates the relative time client-side, without the bot re-editing the message."""
    lines = []
    if last_updated:
        lines.append(f"Last updated: <t:{int(last_updated.timestamp())}:R>")
    if note:
        lines.append(note)
    return "\n".join(lines) if lines else "\u200b"


class LeaderboardDropdown(discord.ui.Select):
    def __init__(self, custom_view):
        options = [
            discord.SelectOption(
                label="🏆 Elo", value="elo", description="Players with the highest Elo"
            ),
            discord.SelectOption(
                label="🥇 Wins", value="wins", description="Players with the most wins"
            ),
            discord.SelectOption(
                label="💀 Losses",
                value="losses",
                description="Players with the most losses",
            ),
            discord.SelectOption(
                label="📊 Win Rate",
                value="win_rate",
                description="Players with the highest win rate",
            ),
            discord.SelectOption(
                label="🎮 Monthly Games Played",
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
        self.custom_view = custom_view

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


class LeaderboardMyRankButton(discord.ui.Button):
    def __init__(self, custom_view):
        super().__init__(style=discord.ButtonStyle.secondary, label="My Rank")
        self.custom_view = custom_view

    async def callback(self, interaction: discord.Interaction):
        # Only the user who ran the command can use this button.
        if interaction.user.id != self.custom_view.ctx.author.id:
            await interaction.response.send_message(
                "You cannot perform this action.", ephemeral=True
            )
            return
        self.custom_view.jump_to_target = True
        await self.custom_view.update_leaderboard(interaction)


class LeaderboardRefreshButton(discord.ui.Button):
    def __init__(self, custom_view):
        super().__init__(style=discord.ButtonStyle.secondary, emoji="🔄")
        self.custom_view = custom_view

    async def callback(self, interaction: discord.Interaction):
        self.custom_view.last_updated = datetime.now(timezone.utc)
        await self.custom_view.update_leaderboard(interaction)


# Modified: LeaderboardView is now a LayoutView (Components V2) instead of View +
# embed, so the Refresh button can be placed as an accessory INSIDE the Container
# (the colored border, like an OwO card) instead of floating in a separate action row.
class LeaderboardView(discord.ui.LayoutView):
    def __init__(
        self,
        ctx,
        category: str = "elo",
        page: int = 0,
        *,
        db_guild_id: int | None = None,
    ):
        super().__init__(timeout=3600.0)
        self.ctx = ctx
        self.db_guild_id = db_guild_id if db_guild_id is not None else ctx.guild.id
        self.category = category
        self.page = page
        self.per_page = 10
        self.total_pages = 1
        # Bug fix: `id` is a TEXT-affinity column, so SQLite always stores/
        # returns player ids as str, even when an int was bound at INSERT.
        # Comparing that str to a raw int ctx.author.id (as this used to do)
        # was always False in Python, so the "highlight me" / My Rank logic
        # never actually matched anyone. Normalize to str once here instead.
        self.target_user_id = str(ctx.author.id)
        self.last_updated = datetime.now(timezone.utc)
        self.message = None
        # Set by LeaderboardMyRankButton; consumed once by the next
        # _render_leaderboard() call to jump to the page the user is on.
        self.jump_to_target = False

        # Interactive items are created once and keep the same instance across
        # each layout re-render (only their attachment point in the row/container changes).
        self.dropdown = LeaderboardDropdown(self)
        self.prev_button = PreviousPageButton(self)
        self.next_button = NextPageButton(self)
        self.my_rank_button = LeaderboardMyRankButton(self)
        self.refresh_button = LeaderboardRefreshButton(self)

    async def on_timeout(self) -> None:
        for item in (
            self.dropdown,
            self.prev_button,
            self.next_button,
            self.my_rank_button,
            self.refresh_button,
        ):
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass

    def _title_prefix(self) -> str:
        return LB_TITLE_PREFIX

    def _dev_note(self, note_text: str | None) -> str | None:
        if self.db_guild_id != self.ctx.guild.id:
            dev = f"Dev Mode - Leaderboards from Server {self.db_guild_id}"
            return f"{note_text}\n{dev}" if note_text else dev
        return note_text

    def _rebuild_layout(self, filename: str, note: str | None) -> None:
        """Rebuild the whole layout: Container (leaderboard image + Refresh
        inside the frame) + dropdown action row + Prev/Next action row."""
        self.clear_items()

        container = discord.ui.Container(accent_color=ACCENT_COLOR)
        container.add_item(
            discord.ui.MediaGallery(discord.MediaGalleryItem(media=f"attachment://{filename}"))
        )
        container.add_item(discord.ui.Separator())
        container.add_item(
            discord.ui.Section(
                discord.ui.TextDisplay(_footer_text(self.last_updated, note)),
                accessory=self.refresh_button,
            )
        )
        self.add_item(container)

        self.add_item(discord.ui.ActionRow(self.dropdown))
        self.add_item(
            discord.ui.ActionRow(self.prev_button, self.next_button, self.my_rank_button)
        )

    async def update_leaderboard(self, interaction_or_ctx):
        with db_connection(self.db_guild_id) as (conn, cursor):
            await self._render_leaderboard(interaction_or_ctx, cursor)

    async def _send_or_edit(self, interaction_or_ctx, file: discord.File) -> None:
        if isinstance(interaction_or_ctx, discord.Interaction):
            await interaction_or_ctx.response.edit_message(view=self, attachments=[file])
            if interaction_or_ctx.message:
                self.message = interaction_or_ctx.message
            else:
                try:
                    self.message = await interaction_or_ctx.original_response()
                except Exception:
                    pass
        else:
            self.message = await interaction_or_ctx.send(view=self, file=file)

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
        display_players: list[tuple[int, str, int]] = []

        if self.category == "elo":
            title = "Elo"
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
            title = "Wins"
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
            title = "Losses"
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
            title = "Win Rate"
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
            title = "Monthly Games Played"
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

        if self.jump_to_target:
            self.jump_to_target = False
            target_index = next(
                (
                    i
                    for i, (pid, _, _) in enumerate(display_players)
                    if str(pid) == self.target_user_id
                ),
                None,
            )
            if target_index is None:
                embed = discord.Embed(
                    title="Not Ranked",
                    description="You are not currently ranked in this leaderboard category.",
                    color=discord.Color.red(),
                )
                if isinstance(interaction_or_ctx, discord.Interaction):
                    await interaction_or_ctx.response.send_message(embed=embed, ephemeral=True)
                else:
                    await interaction_or_ctx.send(embed=embed)
                return
            self.page = target_index // self.per_page

        self.total_pages = max(1, (len(display_players) - 1) // self.per_page + 1)
        if self.page >= self.total_pages:
            self.page = self.total_pages - 1
        start = self.page * self.per_page
        end = start + self.per_page
        page_players = display_players[start:end]

        # Check whether the user is on this page (to highlight them). Cast
        # pid to str defensively - legacy rows inserted before the id column
        # settled on TEXT affinity could otherwise come back as int and
        # silently fail this comparison, which used to suppress the
        # highlight and (incorrectly) allow the cached, unhighlighted image
        # to be served instead.
        target_in_page = any(str(pid) == self.target_user_id for pid, _, _ in page_players)

        self.prev_button.disabled = self.page == 0
        self.next_button.disabled = self.page >= self.total_pages - 1
        # Grey out My Rank once the invoker's own row is already visible.
        self.my_rank_button.disabled = target_in_page

        filename = "leaderboard.png"

        # --- Use cache if the user isn't on this page (no highlight needed) ---
        use_cache = (
            not target_in_page
            and self.db_guild_id == self.ctx.guild.id  # don't cache cross-server lb
        )

        if use_cache:
            cached = cache_get(self.db_guild_id, self.category, self.page)
            if cached is not None:
                logger.debug(
                    "LB serve  HIT  guild=%s category=%s page=%d user=%s",
                    self.db_guild_id, self.category, self.page, self.target_user_id,
                )
                file = discord.File(io.BytesIO(cached), filename=filename)
                self._rebuild_layout(filename, self._dev_note(note_text))
                await self._send_or_edit(interaction_or_ctx, file)
                return
            else:
                logger.debug(
                    "LB serve  MISS guild=%s category=%s page=%d user=%s - rendering fresh",
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
            entries.append((rank, name, stat_str, str(pid) == self.target_user_id))

        buf = render_leaderboard_image(
            entries,
            title=f"{self._title_prefix()}{title}",
            page=self.page + 1,
            total_pages=self.total_pages,
        )
        file = discord.File(buf, filename=filename)
        self._rebuild_layout(filename, self._dev_note(note_text))
        await self._send_or_edit(interaction_or_ctx, file)


class LeaderboardCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.hybrid_command(
        name="leaderboard",
        aliases=["lb"],
        help="Show the top 10 players on the leaderboard with sorting options.",
        description="Show the top 10 players on the leaderboard with sorting options.",
        with_app_command=True,
    )
    @app_commands.guild_only()
    async def leaderboard(self, ctx: commands.Context):
        view = LeaderboardView(ctx)
        await view.update_leaderboard(ctx)


async def setup(bot: commands.Bot):
    await bot.add_cog(LeaderboardCog(bot))