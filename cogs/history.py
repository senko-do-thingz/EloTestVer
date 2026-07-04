from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from db.elo_db import db_connection, get_win_streak
from db.match_history_db import count_player_match_history, get_recent_match_history
from utils import render_history_image_async

# One page shows 10 matches; at most 30 matches are ever stored per player
# (see MAX_HISTORY_PER_PLAYER / trim_match_history in db/match_history_db.py),
# so there are never more than 3 pages to page through.
PAGE_SIZE = 10
MAX_PAGES = 3

ACCENT_COLOR = discord.Color.dark_grey()


def _footer_text(last_updated: datetime) -> str:
    """Build the 'Last updated' line, matching the leaderboard/stats footer
    style: <t:...:R> lets Discord update the relative time client-side."""
    return f"Last updated: <t:{int(last_updated.timestamp())}:R>"


class HistoryPreviousPageButton(discord.ui.Button):
    def __init__(self, custom_view: "HistoryView"):
        super().__init__(style=discord.ButtonStyle.secondary, label="Prev", disabled=True)
        self.custom_view = custom_view

    async def callback(self, interaction: discord.Interaction):
        self.custom_view.page -= 1
        await self.custom_view.update_history(interaction)


class HistoryNextPageButton(discord.ui.Button):
    def __init__(self, custom_view: "HistoryView"):
        super().__init__(style=discord.ButtonStyle.secondary, label="Next", disabled=False)
        self.custom_view = custom_view

    async def callback(self, interaction: discord.Interaction):
        self.custom_view.page += 1
        await self.custom_view.update_history(interaction)


class HistoryRefreshButton(discord.ui.Button):
    def __init__(self, custom_view: "HistoryView"):
        super().__init__(style=discord.ButtonStyle.secondary, emoji="🔄")
        self.custom_view = custom_view

    async def callback(self, interaction: discord.Interaction):
        self.custom_view.last_updated = datetime.now(timezone.utc)
        await self.custom_view.update_history(interaction)


# HistoryView is a LayoutView (Components V2), mirroring the leaderboard/stats
# pattern: Refresh sits INSIDE the colored border as a Section accessory
# (like an OwO card), with Prev/Next in their own action row underneath.
class HistoryView(discord.ui.LayoutView):
    def __init__(
        self,
        cog: "HistoryCog",
        ctx: commands.Context,
        target: discord.abc.User,
        page: int = 0,
    ):
        super().__init__(timeout=3600.0)
        self.cog = cog
        self.ctx = ctx
        self.target = target
        self.page = page
        self.total_pages = 1
        self.last_updated = datetime.now(timezone.utc)
        self.message = None

        self.prev_button = HistoryPreviousPageButton(self)
        self.next_button = HistoryNextPageButton(self)
        self.refresh_button = HistoryRefreshButton(self)

    async def on_timeout(self) -> None:
        for item in (self.prev_button, self.next_button, self.refresh_button):
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass

    def _rebuild_layout(self, filename: str) -> None:
        """Rebuild the whole layout: Container (the history image, which now
        carries the avatar + name as part of the title) + Prev/Next action row."""
        self.clear_items()

        container = discord.ui.Container(accent_color=ACCENT_COLOR)
        container.add_item(
            discord.ui.MediaGallery(discord.MediaGalleryItem(media=f"attachment://{filename}"))
        )
        container.add_item(discord.ui.Separator())
        container.add_item(
            discord.ui.Section(
                discord.ui.TextDisplay(_footer_text(self.last_updated)),
                accessory=self.refresh_button,
            )
        )
        self.add_item(container)
        self.add_item(discord.ui.ActionRow(self.prev_button, self.next_button))

    async def update_history(self, interaction_or_ctx) -> None:
        with db_connection(self.ctx.guild.id) as (_conn, cursor):
            await self._render_history(interaction_or_ctx, cursor)

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

    async def _send_error(self, interaction_or_ctx, embed: discord.Embed) -> None:
        if isinstance(interaction_or_ctx, discord.Interaction):
            if interaction_or_ctx.response.is_done():
                await interaction_or_ctx.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction_or_ctx.response.send_message(embed=embed, ephemeral=True)
        else:
            await interaction_or_ctx.send(embed=embed)

    async def _render_history(self, interaction_or_ctx, cursor) -> None:
        cursor.execute("SELECT name FROM players WHERE id=?", (self.target.id,))
        row = cursor.fetchone()
        if not row:
            await self._send_error(
                interaction_or_ctx,
                discord.Embed(
                    title="Player Not Registered",
                    description=(
                        f"{self.target.mention} is not registered. "
                        "Use `-register <name>` to sign up!"
                    ),
                    color=discord.Color.red(),
                ),
            )
            return

        player_name = row[0]
        total_matches = count_player_match_history(cursor, self.target.id)

        if total_matches <= 0:
            await self._send_error(
                interaction_or_ctx,
                discord.Embed(
                    title="Match History",
                    description=f"**{player_name}** has no recorded matches yet.",
                    color=discord.Color.blue(),
                ),
            )
            return

        self.total_pages = max(1, min(MAX_PAGES, (total_matches - 1) // PAGE_SIZE + 1))
        self.page = max(0, min(self.page, self.total_pages - 1))

        offset = self.page * PAGE_SIZE
        history_rows = get_recent_match_history(cursor, self.target.id, PAGE_SIZE, offset)
        _current_streak, highest_streak = get_win_streak(cursor, self.target.id)

        self.prev_button.disabled = self.page == 0
        self.next_button.disabled = self.page >= self.total_pages - 1

        # Render match history as an image table (colored win/loss accent bar
        # + DATE/OPPONENT/W/L/+-/INIT/FINAL grid), ClashPerk log style.
        image_rows = [
            (
                datetime.fromtimestamp(r.played_at, tz=timezone.utc).strftime("%m-%d"),
                r.opponent_name,
                r.wins,
                r.losses,
                r.elo_delta,
                r.elo_init,
                r.elo_final,
            )
            for r in history_rows
        ]
        avatar_bytes = await self._fetch_avatar_bytes()
        buf = await render_history_image_async(
            image_rows,
            title=f"{player_name}'s Match History",
            summary=(
                f"Page {self.page + 1}/{self.total_pages} - "
                f"showing {len(history_rows)} of {total_matches} match(es)"
            ),
            highest_streak=highest_streak,
            avatar_bytes=avatar_bytes,
        )
        filename = "history.png"
        file = discord.File(buf, filename=filename)

        self._rebuild_layout(filename)
        await self._send_or_edit(interaction_or_ctx, file)

    async def _fetch_avatar_bytes(self) -> bytes | None:
        try:
            return await self.target.display_avatar.read()
        except Exception:
            return None


class HistoryCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _history_impl(
        self, ctx: commands.Context, name: discord.Member | None
    ) -> None:
        target = name if name is not None else ctx.author
        view = HistoryView(self, ctx, target)
        await view.update_history(ctx)

    @commands.hybrid_command(
        name="history",
        description="Show your recent match history, or another player's by mentioning them.",
        with_app_command=True,
    )
    @app_commands.describe(
        name="Player whose match history to view (defaults to yourself)"
    )
    @app_commands.guild_only()
    async def history(
        self, ctx: commands.Context, name: discord.Member | None = None
    ):
        await self._history_impl(ctx, name)


async def setup(bot: commands.Bot):
    await bot.add_cog(HistoryCog(bot))