"""Match history viewing, paginated per season/month.

- `-history` (prefix, no arguments): shows the invoking user's own history
  for this server's current season/month. This is the only prefix form.
- `/history` (slash): supports viewing another player and filtering by
  season/month.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from db.elo_db import db_connection, get_season_month
from db.match_history_db import (
    count_player_match_history_by_season_month,
    get_best_streak_by_season_month,
    get_match_history_by_season_month,
    get_months_with_history,
)
from utils import render_history_image_async
from utils.slash_ctx import SlashContext

# One page shows 10 matches. Match history storage is unbounded (see
# db/match_history_db.py), so the number of pages is simply derived from
# however many matches are actually stored - no artificial page cap.
PAGE_SIZE = 10

ACCENT_COLOR = discord.Color.dark_grey()

# How long a user has to reply with a month number when /history season:<n>
# matches more than one month (see HistoryCog._prompt_for_month below).
MONTH_REPLY_TIMEOUT = 300.0


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
        ctx: SlashContext | commands.Context,
        target: discord.abc.User,
        season: int,
        month: int,
        page: int = 0,
    ):
        super().__init__(timeout=3600.0)
        self.cog = cog
        self.ctx = ctx
        self.target = target
        self.season = season
        self.month = month
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

    async def update_history(self, interaction_or_target) -> None:
        with db_connection(self.ctx.guild.id) as (_conn, cursor):
            await self._render_history(interaction_or_target, cursor)

    async def _send_or_edit(self, interaction_or_target, file: discord.File) -> None:
        if isinstance(interaction_or_target, discord.Interaction):
            await interaction_or_target.response.edit_message(view=self, attachments=[file])
            if interaction_or_target.message:
                self.message = interaction_or_target.message
            else:
                try:
                    self.message = await interaction_or_target.original_response()
                except Exception:
                    pass
        elif isinstance(interaction_or_target, discord.Message):
            # Editing an existing prompt message (e.g. the "reply with a month
            # number" prompt) directly into the history view, instead of
            # sending a brand new message.
            await interaction_or_target.edit(view=self, attachments=[file])
            self.message = interaction_or_target
        else:
            self.message = await interaction_or_target.send(view=self, file=file)

    async def _send_error(self, interaction_or_target, embed: discord.Embed) -> None:
        if isinstance(interaction_or_target, discord.Interaction):
            if interaction_or_target.response.is_done():
                await interaction_or_target.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction_or_target.response.send_message(embed=embed, ephemeral=True)
        elif isinstance(interaction_or_target, discord.Message):
            await interaction_or_target.edit(embed=embed, view=None, attachments=[])
        else:
            await interaction_or_target.send(embed=embed)

    async def _render_history(self, interaction_or_target, cursor) -> None:
        cursor.execute("SELECT name FROM players WHERE id=?", (self.target.id,))
        row = cursor.fetchone()
        if not row:
            await self._send_error(
                interaction_or_target,
                discord.Embed(
                    title="Player Not Registered",
                    description=(
                        f"{self.target.mention} is not registered. "
                        "Use `-register <n>` to sign up!"
                    ),
                    color=discord.Color.red(),
                ),
            )
            return

        player_name = row[0]
        total_matches = count_player_match_history_by_season_month(
            cursor, self.target.id, self.season, self.month
        )

        if total_matches <= 0:
            await self._send_error(
                interaction_or_target,
                discord.Embed(
                    title="Match History",
                    description=(
                        f"**{player_name}** has no recorded matches in "
                        f"Season {self.season}, Month {self.month}."
                    ),
                    color=discord.Color.blue(),
                ),
            )
            return

        self.total_pages = max(1, (total_matches - 1) // PAGE_SIZE + 1)
        self.page = max(0, min(self.page, self.total_pages - 1))

        offset = self.page * PAGE_SIZE
        history_rows = get_match_history_by_season_month(
            cursor, self.target.id, self.season, self.month, PAGE_SIZE, offset
        )
        # Month-scoped Best Streak: computed purely from this season/month's
        # match_history rows, deliberately separate from the player-level
        # win_streak/highest_win_streak fields that get_win_streak() reads
        # (those are shared with /stats and track an all-time running streak).
        highest_streak = get_best_streak_by_season_month(
            cursor, self.target.id, self.season, self.month
        )

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
            season_month_text=f"Season: {self.season}, Month: {self.month}",
            year_text=f"Year: {datetime.now(timezone.utc).year}",
        )
        filename = "history.png"
        file = discord.File(buf, filename=filename)

        self._rebuild_layout(filename)
        await self._send_or_edit(interaction_or_target, file)

    async def _fetch_avatar_bytes(self) -> bytes | None:
        try:
            return await self.target.display_avatar.read()
        except Exception:
            return None


class HistoryCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _prompt_for_month(
        self,
        ctx: SlashContext,
        interaction: discord.Interaction,
        season: int,
        months: list[int],
    ) -> tuple[discord.Message, int] | None:
        """Send a prompt listing the months with data in `season` and wait for
        the user to reply with the month number they want (mirrors the
        -register "reply to this message" flow). Returns (prompt_message,
        chosen_month), or None if the flow was cancelled/timed out (the
        prompt message is already updated to reflect that in that case)."""
        months_list = ", ".join(str(m) for m in months)
        prompt = await ctx.send(
            embed=discord.Embed(
                title="Select Month",
                description=(
                    f"Season {season} has data in month(s): {months_list}.\n"
                    "Please reply to this message with the month number you want to view."
                ),
                color=discord.Color.orange(),
            )
        )

        def check(m: discord.Message) -> bool:
            return (
                m.author.id == interaction.user.id
                and m.channel.id == interaction.channel.id
                and m.reference is not None
                and m.reference.message_id == prompt.id
            )

        try:
            reply: discord.Message = await self.bot.wait_for(
                "message", timeout=MONTH_REPLY_TIMEOUT, check=check
            )
        except asyncio.TimeoutError:
            await prompt.edit(embed=discord.Embed(
                title="Timeout",
                description="Timed out. Please try again.",
                color=discord.Color.red(),
            ))
            return None

        month_text = (reply.content or "").strip()
        try:
            chosen_month = int(month_text)
        except ValueError:
            await prompt.edit(embed=discord.Embed(
                title="Invalid Month",
                description="No valid month number provided. Please try again.",
                color=discord.Color.red(),
            ))
            return None

        return prompt, chosen_month

    async def _history_impl(
        self,
        interaction: discord.Interaction,
        name: discord.Member | None,
        season: int | None,
        month: int | None,
    ) -> None:
        ctx = SlashContext(interaction)
        target = name if name is not None else interaction.user
        guild_id = interaction.guild_id

        # Neither season nor month given -> default to this server's current
        # season/month (same behavior whether or not `name` was given).
        if season is None and month is None:
            current_season, current_month = get_season_month(guild_id)
            view = HistoryView(self, ctx, target, current_season, current_month)
            await view.update_history(ctx)
            return

        # Only `month` given -> pair it with the server's current season.
        if season is None and month is not None:
            current_season, _current_month = get_season_month(guild_id)
            view = HistoryView(self, ctx, target, current_season, month)
            await view.update_history(ctx)
            return

        # Only `season` given -> figure out which month(s) have data.
        if season is not None and month is None:
            with db_connection(guild_id) as (_conn, cursor):
                months = get_months_with_history(cursor, target.id, season)

            if not months:
                await ctx.send(
                    embed=discord.Embed(
                        title="Match History",
                        description=f"No recorded matches for **{target.display_name}** in Season {season}.",
                        color=discord.Color.blue(),
                    )
                )
                return

            if len(months) == 1:
                view = HistoryView(self, ctx, target, season, months[0])
                await view.update_history(ctx)
                return

            # Multiple months with data -> ask which one, reply-to-update.
            result = await self._prompt_for_month(ctx, interaction, season, months)
            if result is None:
                return
            prompt_message, chosen_month = result
            view = HistoryView(self, ctx, target, season, chosen_month)
            await view.update_history(prompt_message)
            return

        # Both season and month given -> show it directly.
        view = HistoryView(self, ctx, target, season, month)
        await view.update_history(ctx)

    @commands.command(
        name="history",
        help="Show your own match history for this server's current season/month.",
    )
    @commands.guild_only()
    async def history_prefix(self, ctx: commands.Context) -> None:
        """The only prefix form of this command: `-history`, no arguments.
        Always shows the invoking user's own history for the server's
        current season/month. Filtering by player/season/month, or viewing
        another player's history, is only available via the `/history`
        slash command."""
        current_season, current_month = get_season_month(ctx.guild.id)
        view = HistoryView(self, ctx, ctx.author, current_season, current_month)
        await view.update_history(ctx)

    @app_commands.command(
        name="history",
        description="Show your recent match history, or another player's by mentioning them.",
    )
    @app_commands.describe(
        name="Player whose match history to view (defaults to yourself)",
        season="Season number to view (defaults to this server's current season)",
        month="Month number to view (defaults to this server's current month)",
    )
    @app_commands.guild_only()
    async def history(
        self,
        interaction: discord.Interaction,
        name: discord.Member | None = None,
        season: app_commands.Range[int, 1, 999] | None = None,
        month: app_commands.Range[int, 1, 100] | None = None,
    ):
        await self._history_impl(interaction, name, season, month)


async def setup(bot: commands.Bot):
    await bot.add_cog(HistoryCog(bot))