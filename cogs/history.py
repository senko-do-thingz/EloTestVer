"""Match history viewing, paginated per season/month.

- `-history` (prefix, no arguments): shows the invoking user's own history
  for this server's current season/month. This is the only prefix form.
- `/history` (slash): supports viewing another player and filtering by
  season/month.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from db.elo_db import db_connection, get_season_month
from db.match_history_db import (
    compute_streak_labels,
    count_player_match_history,
    count_player_match_history_by_season_month,
    get_full_match_history_by_season_month,
    get_match_history_by_season_month,
    get_months_with_history,
)
from utils import (
    build_calendar_series,
    build_match_series,
    calendar_axis_bounds,
    compute_month_averages,
    find_rest_gaps,
    render_elo_graph_image_async,
    render_history_image_async,
    rest_gap_fractions,
    truncate_cjk_name,
)
from utils.slash_ctx import SlashContext

# One page shows 10 matches. Match history storage is unbounded (see
# db/match_history_db.py), so the number of pages is simply derived from
# however many matches are actually stored - no artificial page cap.
PAGE_SIZE = 10

ACCENT_COLOR = discord.Color.dark_grey()

# How long a user has to reply with a month number when /history season:<n>
# matches more than one month (see HistoryCog._prompt_for_month below).
MONTH_REPLY_TIMEOUT = 300.0


def _compute_axis_bounds(elo_values: list[int]) -> tuple[int, int]:
    """Y-axis bounds for the elo graph: default 300-1000, extended outward
    (rounded to the nearest 50 for clean gridlines) to cover any actual elo
    value outside that range, instead of letting rendering.py clamp the
    drawn line to the default range (see Q1: elo above 1000, while rare,
    does happen roughly once a season)."""
    axis_min, axis_max = 300, 1000
    if not elo_values:
        return axis_min, axis_max
    lo, hi = min(elo_values), max(elo_values)
    if lo < axis_min:
        axis_min = (lo // 50) * 50
    if hi > axis_max:
        axis_max = -(-hi // 50) * 50  # ceil to nearest 50
    return int(axis_min), int(axis_max)


def _compute_rest_gaps_for_graph(
    rows_chronological: list,
    *,
    anchor_date: date | None = None,
) -> list[tuple[float, float]]:
    """Build the ``rest_gaps`` list passed to render_elo_graph_image_async()
    for the -graph card, pulled out of HistoryCog's graph-rendering path so
    it can be unit-tested without mocking Discord interactions/context.

    ``anchor_date`` must be the REAL previous-month carry-over date (the
    module-level ``anchor_date`` variable in the caller, i.e. None unless
    there actually is a previous month with recorded matches) - NOT the
    synthetic "before first match" anchor used for ``series_anchor_date``
    when there is no real previous month. Passing the synthetic anchor
    here would make find_rest_gaps() fabricate a rest period ending at a
    day that was never actually played (see find_rest_gaps()'s own
    docstring for why that distinction matters).

    Returns an empty list when there's no calendar axis to position a
    gap against (calendar_axis_bounds() returns None), same as when
    there simply aren't any qualifying gaps.
    """
    axis_bounds = calendar_axis_bounds(rows_chronological, anchor_date=anchor_date)
    if axis_bounds is None:
        return []
    axis_start, axis_end = axis_bounds
    total_days = (axis_end - axis_start).days + 1
    gaps = find_rest_gaps(rows_chronological, anchor_date=anchor_date)
    return rest_gap_fractions(gaps, axis_start=axis_start, total_days=total_days)


def _previous_season_month(
    cursor, player_id: str | int, season: int, month: int
) -> tuple[int, int] | None:
    """Resolve the single (season, month) immediately adjacent to the given
    one - i.e. (season, month - 1), or the previous season's last
    configured month (see /months) when month == 1.

    This deliberately does NOT walk back further through empty months or
    seasons: if a player skipped the immediately preceding month/season
    entirely (no matches at all), the graph's "vs last month" comparison
    is simply unavailable - the caller shows "No data available" instead
    of silently anchoring to whatever older month happens to have data.

    Returns None when there is no adjacent slot to check (season 1,
    month 1) or when that adjacent slot has no recorded matches for this
    player.
    """
    if month > 1:
        candidate_season, candidate_month = season, month - 1
    else:
        if season <= 1:
            return None
        cursor.execute("SELECT max_month FROM settings LIMIT 1")
        row = cursor.fetchone()
        max_month = row[0] if row and row[0] is not None else None
        if max_month is None:
            # No /months configuration to know the previous season's last
            # month from - nothing safe to anchor to.
            return None
        candidate_season, candidate_month = season - 1, max_month

    has_data = count_player_match_history_by_season_month(
        cursor, player_id, candidate_season, candidate_month
    ) > 0
    if not has_data:
        return None
    return candidate_season, candidate_month


def _footer_text(last_updated: datetime) -> str:
    """Build the 'Last updated' line, matching the leaderboard/stats footer
    style: <t:...:R> lets Discord update the relative time client-side."""
    return f"Last updated: <t:{int(last_updated.timestamp())}:R>"


class HistoryPreviousPageButton(discord.ui.Button):
    def __init__(self, custom_view: "HistoryView"):
        super().__init__(style=discord.ButtonStyle.secondary, label="Prev", disabled=True)
        self.custom_view = custom_view

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        self.custom_view.page -= 1
        await self.custom_view.update_history(interaction)


class HistoryNextPageButton(discord.ui.Button):
    def __init__(self, custom_view: "HistoryView"):
        super().__init__(style=discord.ButtonStyle.secondary, label="Next", disabled=False)
        self.custom_view = custom_view

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        self.custom_view.page += 1
        await self.custom_view.update_history(interaction)


class HistoryRefreshButton(discord.ui.Button):
    def __init__(self, custom_view: "HistoryView"):
        super().__init__(style=discord.ButtonStyle.secondary, emoji="🔄")
        self.custom_view = custom_view

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        self.custom_view.last_updated = datetime.now(timezone.utc)
        await self.custom_view.update_history(interaction)


class HistoryGraphToggleButton(discord.ui.Button):
    """Switches HistoryView between the table view ("History") and the
    Elo line-chart view ("Graph"). The label flips to "Overview" while on
    the graph so tapping it again returns to the original History table.

    Switching INTO graph mode requires the target player to have played
    at least one set (ever, not just in the currently viewed season/month)
    - otherwise there is nothing meaningful to plot. A player with zero
    match_history rows at all gets a plain ephemeral message instead of
    the graph card; this is separate from (and checked before) the
    existing "no matches in this season/month" embed in _render_graph,
    which still applies once a player has played somewhere but not in
    the specific season/month currently being viewed.
    """

    def __init__(self, custom_view: "HistoryView"):
        super().__init__(style=discord.ButtonStyle.primary, label="Graph")
        self.custom_view = custom_view

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        view = self.custom_view
        if view.mode == "history":
            with db_connection(view.ctx.guild.id) as (_conn, cursor):
                played_any_set = count_player_match_history(cursor, view.target.id) > 0
            if not played_any_set:
                await interaction.followup.send(
                    "You need to play at least 1 set to use this feature.",
                    ephemeral=True,
                )
                return

        view.mode = "graph" if view.mode == "history" else "history"
        self.label = "Overview" if view.mode == "graph" else "Graph"
        if view.mode == "history":
            # Always land back on page 0 rather than wherever the table was
            # left before switching to the graph - the graph view doesn't
            # expose or track a page position, so silently resuming an old
            # one would be confusing rather than a deliberate "remember my
            # place" feature.
            view.page = 0
        await view.update_history(interaction)


class HistoryLegendButton(discord.ui.Button):
    def __init__(self):
        super().__init__(style=discord.ButtonStyle.secondary, label="Legend")

    async def callback(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="Graph Color Legend",
            description=(
                "**Green Line**: Winning streaks (Elo gain).\n"
                "**Red Line**: Losing streaks (Elo loss).\n"
                "**Dashed Cyan Line**: Your Elo history from the previous month (for comparison).\n"
                "**Light Blue Shaded Area**: Rest periods (multi-day gaps where no matches were played).\n\n"
                "**Example:**\n"
                "- If you win 3 matches in a row -> the line goes up and is colored green.\n"
                "- If you lose 2 matches -> the line goes down and is colored red.\n"
                "- If you don't play for 5 days -> that gap is highlighted with a light blue background."
            ),
            color=discord.Color.blue(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


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
        # "history" shows the paginated match table; "graph" shows the Elo
        # line chart for the whole season/month. Toggled by graph_button.
        self.mode = "history"

        self.prev_button = HistoryPreviousPageButton(self)
        self.next_button = HistoryNextPageButton(self)
        self.refresh_button = HistoryRefreshButton(self)
        self.graph_button = HistoryGraphToggleButton(self)
        self.legend_button = HistoryLegendButton()

    async def on_timeout(self) -> None:
        for item in (self.prev_button, self.next_button, self.refresh_button, self.graph_button, self.legend_button):
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
        if self.mode == "graph":
            # Pagination doesn't apply to the whole-month graph view, so hide
            # Prev/Next entirely instead of just disabling them.
            self.add_item(discord.ui.ActionRow(self.graph_button, self.legend_button))
        else:
            self.add_item(discord.ui.ActionRow(self.prev_button, self.next_button, self.graph_button))

    async def update_history(self, interaction_or_target) -> None:
        with db_connection(self.ctx.guild.id) as (_conn, cursor):
            if self.mode == "graph":
                await self._render_graph(interaction_or_target, cursor)
            else:
                await self._render_history(interaction_or_target, cursor)

    async def _send_or_edit(self, interaction_or_target, file: discord.File) -> None:
        if isinstance(interaction_or_target, discord.Interaction):
            if interaction_or_target.response.is_done():
                await interaction_or_target.edit_original_response(view=self, attachments=[file])
            else:
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
                        "Use `-register <name>` to register"
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

        # Streak labels (the small number drawn next to each row) and the
        # "Best Streak" badge must both be computed from the FULL month,
        # not just this page - a streak spanning a page boundary would
        # otherwise look shorter than it really is on every page it
        # touches, and would disagree with /export history's numbers,
        # which already use the full month.
        full_month_rows = get_full_match_history_by_season_month(
            cursor, self.target.id, self.season, self.month
        )
        full_streak_labels = compute_streak_labels(full_month_rows)
        page_streak_labels = full_streak_labels[offset : offset + len(history_rows)]
        highest_streak = max(
            (label for label in full_streak_labels if label is not None), default=0
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
            title=f"{truncate_cjk_name(player_name)}'s Match History",
            summary=(
                f"Page {self.page + 1}/{self.total_pages} - "
                f"showing {len(history_rows)} of {total_matches} match(es)"
            ),
            highest_streak=highest_streak,
            avatar_bytes=avatar_bytes,
            season_month_text=f"Season: {self.season}, Month: {self.month}",
            year_text=f"Year: {datetime.now(timezone.utc).year}",
            streak_labels=page_streak_labels,
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

    async def _render_graph(self, interaction_or_target, cursor) -> None:
        cursor.execute("SELECT name FROM players WHERE id=?", (self.target.id,))
        row = cursor.fetchone()
        if not row:
            await self._send_error(
                interaction_or_target,
                discord.Embed(
                    title="Player Not Registered",
                    description=(
                        f"{self.target.mention} is not registered. "
                        "Use `-register <name>` to register"
                    ),
                    color=discord.Color.red(),
                ),
            )
            return

        player_name = row[0]

        # Full month, chronological (oldest first) - both the averages and
        # the daily series are built from this same set of actual matches.
        full_month_rows_desc = get_full_match_history_by_season_month(
            cursor, self.target.id, self.season, self.month
        )
        if not full_month_rows_desc:
            await self._send_error(
                interaction_or_target,
                discord.Embed(
                    title="Elo Graph",
                    description=(
                        f"**{player_name}** has no recorded matches in "
                        f"Season {self.season}, Month {self.month}."
                    ),
                    color=discord.Color.blue(),
                ),
            )
            return

        rows_chronological = list(reversed(full_month_rows_desc))
        averages = compute_month_averages(rows_chronological)

        # Previous month's carry-over anchor point (the elo the player
        # carried INTO this month, before any -decay/-reset adjustment) and
        # previous month's averages, for the blue "vs last month" figures.
        anchor_elo = None
        anchor_date = None
        prev_averages = None
        prev_month_label = None
        compare_series: list[tuple[str, int]] | None = None
        prev = _previous_season_month(cursor, self.target.id, self.season, self.month)
        if prev is not None:
            prev_season, prev_month = prev
            prev_rows_desc = get_full_match_history_by_season_month(
                cursor, self.target.id, prev_season, prev_month
            )
            if prev_rows_desc:
                anchor_row = prev_rows_desc[0]  # most recent = last match of prev month
                anchor_elo = anchor_row.elo_final
                anchor_date = datetime.fromtimestamp(
                    anchor_row.played_at, tz=timezone.utc
                ).date()
                prev_averages = compute_month_averages(list(reversed(prev_rows_desc)))
                prev_month_label = f"S{prev_season}M{prev_month}"

                # The previous month's OWN match series, drawn as the
                # dashed "vs last month" comparison line - anchored at
                # ITS carry-over elo (two months back) the same way the
                # current month's line is, so both lines start the same
                # way. Also computed per SET, not per calendar day.
                prev_prev = _previous_season_month(
                    cursor, self.target.id, prev_season, prev_month
                )
                prev_anchor_elo = None
                prev_anchor_date = None
                if prev_prev is not None:
                    pp_season, pp_month = prev_prev
                    pp_rows_desc = get_full_match_history_by_season_month(
                        cursor, self.target.id, pp_season, pp_month
                    )
                    if pp_rows_desc:
                        pp_anchor_row = pp_rows_desc[0]
                        prev_anchor_elo = pp_anchor_row.elo_final
                        prev_anchor_date = datetime.fromtimestamp(
                            pp_anchor_row.played_at, tz=timezone.utc
                        ).date()
                compare_series = build_match_series(
                    list(reversed(prev_rows_desc)),
                    anchor_elo=prev_anchor_elo,
                    anchor_date=prev_anchor_date,
                )

        # The elo the line should start FROM: the real previous month's
        # carry-over elo when we have one (anchor_elo above), otherwise
        # fall back to this month's own first match's elo_init - the
        # elo the player already had before that first match - so the
        # graph still visibly starts from a real value instead of
        # jumping straight to the first match's post-match result (e.g.
        # a brand new season/month with no prior data). This fallback is
        # a separate variable from anchor_elo/anchor_date on purpose: it
        # must never be used as prev_final_elo below, since there is no
        # actual previous month to compare against in that case.
        series_anchor_elo = anchor_elo
        series_anchor_date = anchor_date
        anchor_before_first_match = False
        if series_anchor_elo is None:
            series_anchor_elo = rows_chronological[0].elo_init
            anchor_before_first_match = True

        # x-axis runs on a real calendar timeline (last day of the
        # previous month through the last day of this month), with one
        # point per SET (match) actually played - see
        # build_calendar_series(). No "not played" days are fabricated
        # in the elo line itself; axis_ticks separately pre-generates a
        # label for every calendar day (including days with no match
        # yet) so the full month is laid out ahead of time.
        series, axis_ticks = build_calendar_series(
            rows_chronological,
            anchor_elo=series_anchor_elo,
            anchor_date=series_anchor_date,
            anchor_before_first_match=anchor_before_first_match,
        )

        # Multi-day stretches with no match played ("rest periods"),
        # shaded on the chart so a genuine break in play is visually
        # distinct from an ordinary played stretch of the line. Uses the
        # REAL previous-month carry-over date (``anchor_date``), not
        # ``series_anchor_date``, since a synthetic "before first match"
        # anchor is not an actual day of activity and must not be
        # treated as one here - see _compute_rest_gaps_for_graph().
        rest_gaps = _compute_rest_gaps_for_graph(
            rows_chronological, anchor_date=anchor_date
        )

        # The y-axis defaults to 300-1000, but must never clamp a real elo
        # value that falls outside that range (see Q1): extend the axis to
        # cover the actual data instead of hard-clamping the drawn line.
        elo_values = [point[1] for point in series]
        if compare_series:
            elo_values.extend(point[1] for point in compare_series)
        axis_min, axis_max = _compute_axis_bounds(elo_values)

        avatar_bytes = await self._fetch_avatar_bytes()
        buf = await render_elo_graph_image_async(
            series,
            title=f"{truncate_cjk_name(player_name)}'s Elo Graph",
            avatar_bytes=avatar_bytes,
            season_month_text=f"Season: {self.season}, Month: {self.month}",
            year_text=f"Year: {datetime.now(timezone.utc).year}",
            avg_win=averages.avg_win,
            avg_loss=averages.avg_loss,
            avg_gain=averages.avg_gain,
            final_elo=averages.final_elo,
            prev_avg_win=prev_averages.avg_win if prev_averages else None,
            prev_avg_loss=prev_averages.avg_loss if prev_averages else None,
            prev_avg_gain=prev_averages.avg_gain if prev_averages else None,
            prev_final_elo=anchor_elo,
            prev_month_label=prev_month_label,
            no_prev_data=prev is None,
            compare_series=compare_series,
            axis_ticks=axis_ticks,
            rest_gaps=rest_gaps,
            axis_min=axis_min,
            axis_max=axis_max,
        )
        filename = "elo_graph.png"
        file = discord.File(buf, filename=filename)

        # Pagination doesn't apply to the whole-month graph view.
        self.prev_button.disabled = True
        self.next_button.disabled = True

        self._rebuild_layout(filename)
        await self._send_or_edit(interaction_or_target, file)


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
        prompt message is already updated to reflect that in that case).

        A reply that isn't a valid integer, or isn't one of the offered
        `months`, re-prompts on the SAME message (fresh MONTH_REPLY_TIMEOUT
        window each time) instead of ending the flow - cheaper for the user
        than re-running the whole /history command for a typo. `months` is
        expected to already be exactly the set of months this player has
        data in for `season` (get_months_with_history), so there is no
        "valid month with no data" case to special-case here."""
        months_list = ", ".join(str(m) for m in months)
        months_set = set(months)
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

        while True:
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
                chosen_month = None

            if chosen_month is None or chosen_month not in months_set:
                await prompt.edit(embed=discord.Embed(
                    title="Invalid Month",
                    description=(
                        f"'{month_text}' isn't one of the months with data: "
                        f"{months_list}.\nPlease reply to this message with a "
                        "valid month number."
                    ),
                    color=discord.Color.red(),
                ))
                continue

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