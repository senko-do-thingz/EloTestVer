"""
lb_view.py
----------
LBView: the main leaderboard paginator shown after selecting a season/month.
Also contains all UI components that belong to that view:
  - LBDropdown   (category selector)
  - PrevBtn / NextBtn  (pagination)
  - SearchPlayerBtn + PlayerSearchModal
  - NoDataView  (shown when a file is missing or empty)
"""

from __future__ import annotations

import os

import discord
from db.elo_db import db_connection
from utils import (
    get_most_played_with,
    get_opponent_games,
)

from utils.lb_archive import parse_leaderboard_lines, read_leaderboard_rows

from .helpers import build_leaderboard_embed

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FILE_MAP: dict[str, str] = {
    "elo": "data.txt",
    "wins": "w.txt",
    "losses": "l.txt",
    "win_rate": "wr.txt",
    "games_played_this_month": "games.txt",
}

CATEGORY_TITLES: dict[str, str] = {
    "elo": "Elo",
    "wins": "Wins",
    "losses": "Losses",
    "win_rate": "Win Rate",
    "games_played_this_month": "Monthly Games Played",
}


# ---------------------------------------------------------------------------
# Helper: "no data" view
# ---------------------------------------------------------------------------

class NoDataView(discord.ui.View):
    """Minimal view shown when a leaderboard file is missing or empty."""

    def __init__(self, lb_view: "LBView"):
        # Modified: synced timeout with LBView/-lb so the "Back" button here
        # also auto-disables instead of staying interactive indefinitely.
        super().__init__(timeout=3600.0)
        self.lb_view = lb_view
        self.add_item(_BackToLBButton(lb_view))

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True
        if self.lb_view.message:
            try:
                await self.lb_view.message.edit(view=self)
            except Exception:
                pass


class _BackToLBButton(discord.ui.Button):
    def __init__(self, lb_view: "LBView"):
        super().__init__(label="Back", style=discord.ButtonStyle.secondary)
        self.lb_view = lb_view

    async def callback(self, interaction: discord.Interaction):
        self.lb_view.category = "elo"
        self.lb_view.page = 0
        await self.lb_view.update(interaction)


# ---------------------------------------------------------------------------
# LBView components
# ---------------------------------------------------------------------------

class LBDropdown(discord.ui.Select):
    def __init__(self, view: "LBView"):
        options = [
            discord.SelectOption(
                label="🏆 Top Elo",
                value="elo",
                description="Players with the highest Elo",
            ),
            discord.SelectOption(
                label="🥇 Top Wins",
                value="wins",
                description="Players with the most wins",
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
        ]
        if view.month.lower() != "overall":
            options.append(
                discord.SelectOption(
                    label="🎮 Top Monthly Games Played",
                    value="games_played_this_month",
                    description="Players with the most monthly games played",
                )
            )
        super().__init__(
            placeholder="Select a leaderboard...",
            min_values=1,
            max_values=1,
            options=options,
        )
        self._view = view

    async def callback(self, interaction: discord.Interaction):
        self._view.category = self.values[0]
        self._view.page = 0
        await self._view.update(interaction)


class PrevBtn(discord.ui.Button):
    def __init__(self, view: "LBView"):
        super().__init__(label="Prev", style=discord.ButtonStyle.secondary, disabled=True)
        self._view = view

    async def callback(self, interaction: discord.Interaction):
        self._view.page -= 1
        await self._view.update(interaction)


class NextBtn(discord.ui.Button):
    def __init__(self, view: "LBView"):
        super().__init__(label="Next", style=discord.ButtonStyle.secondary)
        self._view = view

    async def callback(self, interaction: discord.Interaction):
        self._view.page += 1
        await self._view.update(interaction)


class MyRankBtn(discord.ui.Button):
    def __init__(self, view: "LBView"):
        super().__init__(label="My Rank", style=discord.ButtonStyle.secondary)
        self._view = view

    async def callback(self, interaction: discord.Interaction):
        # Only the user who ran the command can use this button.
        if interaction.user.id != self._view.invoker_id:
            await interaction.response.send_message(
                "You cannot perform this action.", ephemeral=True
            )
            return
        self._view.jump_to_target = True
        await self._view.update(interaction)


class SearchPlayerBtn(discord.ui.Button):
    def __init__(self, view: "LBView"):
        super().__init__(label="Search Player", style=discord.ButtonStyle.primary)
        self._view = view

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(PlayerSearchModal(self._view))


# ---------------------------------------------------------------------------
# Player search modal
# ---------------------------------------------------------------------------

class PlayerSearchModal(discord.ui.Modal, title="Search Player Stats"):
    player_name = discord.ui.TextInput(
        label="Player Name",
        placeholder="Enter player name to search...",
        required=True,
        max_length=50,
    )

    def __init__(self, view: "LBView"):
        super().__init__()
        self.view = view

    async def on_submit(self, interaction: discord.Interaction):
        search_name = self.player_name.value.strip().lower()
        mpath = self.view.mpath
        lb_guild_id = self.view.lb_guild_id

        try:
            # ── Gather stats for matching players ───────────────────────────
            # Group rows by stable Discord ID when available so historical
            # duplicate names cannot combine two different players' stats.
            # If a legacy category has no ID, use its name only when exactly
            # one known ID shares that name in this archive directory.
            archive_rows: list[tuple[str, str, int | float, str | None]] = []
            ids_by_name: dict[str, set[str]] = {}
            for category, filename in FILE_MAP.items():
                file_path = os.path.join(mpath, filename)
                if not os.path.exists(file_path):
                    continue
                for name, value, discord_id in read_leaderboard_rows(
                    file_path, win_rate=category == "win_rate"
                ):
                    if search_name in name.lower():
                        archive_rows.append((category, name, value, discord_id))
                        if discord_id:
                            ids_by_name.setdefault(name.casefold(), set()).add(discord_id)

            stats_data: dict[str, dict] = {}
            display_names: dict[str, str] = {}
            player_ids: dict[str, str | None] = {}
            for category, name, value, discord_id in archive_rows:
                if discord_id is None:
                    candidates = ids_by_name.get(name.casefold(), set())
                    if len(candidates) == 1:
                        discord_id = next(iter(candidates))
                key = f"id:{discord_id}" if discord_id else f"name:{name.casefold()}"
                stats_data.setdefault(key, {})[category] = value
                display_names[key] = name
                player_ids[key] = discord_id

            if not stats_data:
                await interaction.response.send_message(
                    embed=discord.Embed(
                        title="Player Not Found",
                        description=f"No player found with name containing `{search_name}`.",
                        color=discord.Color.red(),
                    ),
                    ephemeral=True,
                )
                return

            if len(stats_data) > 1:
                matches_list = "\n".join(
                    f"- {display_names[key]}" for key in list(stats_data)[:10]
                )
                if len(stats_data) > 10:
                    matches_list += f"\n... and {len(stats_data) - 10} more"
                await interaction.response.send_message(
                    embed=discord.Embed(
                        title="Multiple Players Found",
                        description=(
                            f"Found {len(stats_data)} players:\n{matches_list}\n\n"
                            "Please be more specific."
                        ),
                        color=discord.Color.orange(),
                    ),
                    ephemeral=True,
                )
                return

            # ── Single player found ─────────────────────────────────────────
            player_key = next(iter(stats_data))
            player_name = display_names[player_key]
            player_id = player_ids[player_key]
            player_stats = stats_data[player_key]

            def get_stat_value(category: str):
                # Search only uses the value; the old availability flag was unused.
                return player_stats.get(category, "-")

            def get_rank_for_stat(category: str, value) -> int | None:
                fp = os.path.join(mpath, FILE_MAP[category])
                values = [
                    stat for _, stat, _ in read_leaderboard_rows(
                        fp, win_rate=category == "win_rate"
                    )
                ]
                if not values:
                    return None
                return sum(1 for stat in values if stat > value) + 1

            elo = get_stat_value("elo")
            wins = get_stat_value("wins")
            losses = get_stat_value("losses")
            games_played_this_month = get_stat_value("games_played_this_month")
            win_rate = get_stat_value("win_rate")

            elo_rank = get_rank_for_stat("elo", elo) if elo != "-" else None
            wins_rank = get_rank_for_stat("wins", wins) if wins != "-" else None
            losses_rank = get_rank_for_stat("losses", losses) if losses != "-" else None
            win_rate_rank = (
                get_rank_for_stat("win_rate", win_rate) if win_rate != "-" else None
            )

            def _rank(r):
                return f" (#{r})" if r is not None else ""

            stat_lines = [
                f"**🔥 Elo:** {elo}{_rank(elo_rank)}",
                f"**🏆 Wins:** {wins}{_rank(wins_rank)}",
                f"**💀 Losses:** {losses}{_rank(losses_rank)}",
            ]
            if self.view.month.lower() != "overall":
                monthly_rank = (
                    get_rank_for_stat("games_played_this_month", games_played_this_month)
                    if games_played_this_month != "-"
                    else None
                )
                stat_lines.append(
                    f"**🎮 Monthly Games:** {games_played_this_month}{_rank(monthly_rank)}"
                )

            if win_rate != "-":
                stat_lines.append(f"**📊 Win Rate:** {win_rate:.2f}%{_rank(win_rate_rank)}")
            else:
                stat_lines.append(f"**📊 Win Rate:** {win_rate}")

            # Most played against
            try:
                with db_connection(lb_guild_id) as (conn, cursor):
                    if player_id:
                        cursor.execute("SELECT id FROM players WHERE id = ?", (player_id,))
                    else:
                        cursor.execute(
                            "SELECT id FROM players WHERE name = ?", (player_name,)
                        )
                    id_row = cursor.fetchone()
                    if id_row:
                        pid = str(id_row[0])
                        mpw = get_most_played_with(cursor, pid)
                        most_played_text = (
                            await self.view._format_most_played_with(mpw, cursor, pid)
                            if mpw
                            else "-"
                        )
                    else:
                        most_played_text = "-"
                stat_lines.append(f"**👥 Most Played:** {most_played_text}")
            except Exception:
                pass

            embed = discord.Embed(
                title=f"All Stats for {player_name}",
                description="\n".join(stat_lines),
                color=discord.Color.green(),
            )
            embed.set_footer(text=f"From {self.view.season} / {self.view.month}")
            await interaction.response.send_message(embed=embed, ephemeral=True)

        except Exception as e:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="Error",
                    description=f"An error occurred: {str(e)}",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )


# ---------------------------------------------------------------------------
# LBView
# ---------------------------------------------------------------------------

class LBView(discord.ui.View):
    """Paginated leaderboard view for a specific season/month."""

    def __init__(
        self,
        season: str,
        month: str,
        mpath: str,
        months: list[str],
        lb_guild_id: int,
        *,
        category: str = "elo",
        bot=None,
        invoker_id: int | None = None,
    ):
        # Modified: timeout synced with LeaderboardView (-lb) so -lbs buttons
        # also auto-disable after inactivity instead of staying active forever.
        super().__init__(timeout=3600.0)
        self.season = season
        self.month = month
        self.mpath = mpath
        self.months = months
        self.lb_guild_id = lb_guild_id
        self.category = category
        self.page = 0
        self.per_page = 10
        self.total_pages = 1
        self.bot = bot
        # ID of the user who originally ran the -lbs command; only they may
        # use the My Rank button.
        self.invoker_id = invoker_id
        self.message: discord.Message | None = None
        # Set by MyRankBtn; consumed once by the next update() call to jump
        # to the page the invoking user is currently ranked on.
        self.jump_to_target = False

        # Back button is added by the caller (season_view.py) so it can close
        # back to the correct MonthView.  We reserve a slot by requiring callers
        # to call add_back_button() before update().
        self._back_btn: discord.ui.Button | None = None

        self.add_item(LBDropdown(self))
        self.add_item(PrevBtn(self))
        self.add_item(NextBtn(self))
        self.my_rank_button = MyRankBtn(self)
        self.add_item(self.my_rank_button)
        self.add_item(SearchPlayerBtn(self))

    def set_back_button(self, btn: discord.ui.Button):
        self._back_btn = btn
        self.add_item(btn)
        self.update_buttons()

    async def on_timeout(self) -> None:
        # Modified: mirror LeaderboardView.on_timeout() so -lbs pagination
        # controls also become non-interactive after the view times out.
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass

    # ── helpers ─────────────────────────────────────────────────────────────

    async def _safe_fetch_mention(self, user_id) -> str:
        try:
            user = await self.bot.fetch_user(int(user_id))
            return user.mention
        except Exception:
            return f"<@{user_id}>"

    async def _format_most_played_with(
        self, most_played_with: list, cursor, player_id: str
    ) -> str:
        if not most_played_with:
            return "Play at least 1 set to see your dream person"
        parts = []
        for name in most_played_with:
            cursor.execute("SELECT id FROM players WHERE name = ?", (name,))
            row = cursor.fetchone()
            games = get_opponent_games(cursor, player_id, name)
            if row:
                mention = await self._safe_fetch_mention(int(row[0]))
                parts.append(f"**{name}** - {games} games ({mention})")
            else:
                parts.append(f"**{name}** - {games} games")
        return "; ".join(parts)

    def get_file_path(self, category: str) -> str:
        return os.path.join(self.mpath, FILE_MAP.get(category, "data.txt"))

    def update_buttons(self):
        # children order: dropdown(0), prev(1), next(2), my rank(3), search(4), back(5)
        if len(self.children) > 2:
            self.children[1].disabled = self.page == 0
            self.children[2].disabled = self.page >= self.total_pages - 1

    # ── main render ─────────────────────────────────────────────────────────

    async def update(self, interaction: discord.Interaction):
        try:
            # Guard: overall month can't show games_played_this_month
            if self.month.lower() == "overall" and self.category == "games_played_this_month":
                self.category = "elo"
                self.page = 0

            file_path = self.get_file_path(self.category)

            # ── file missing ────────────────────────────────────────────────
            if not os.path.exists(file_path):
                await interaction.response.edit_message(
                    embed=discord.Embed(
                        title="No Data Available",
                        description="No data available, press back and select another option.",
                        color=discord.Color.orange(),
                    ),
                    view=NoDataView(self),
                )
                self.message = interaction.message
                return

            # ── read file ───────────────────────────────────────────────────
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    raw_lines = f.readlines()
                if not raw_lines:
                    raise ValueError("Empty leaderboard file.")
            except Exception:
                await interaction.response.edit_message(
                    embed=discord.Embed(
                        title="No Data Available",
                        description="No data available, press back and select another option.",
                        color=discord.Color.orange(),
                    ),
                    view=NoDataView(self),
                )
                self.message = interaction.message
                return

            # ── parse lines ─────────────────────────────────────────────────
            data_lines = [
                l.strip()
                for l in raw_lines
                if l.strip() and not l.strip().lower().endswith("leaderboard")
            ]
            if not data_lines:
                await interaction.response.edit_message(
                    embed=discord.Embed(
                        title="No Data Available",
                        description="No data available, press back and select another option.",
                        color=discord.Color.orange(),
                    ),
                    view=NoDataView(self),
                )
                self.message = interaction.message
                return

            players_data = parse_leaderboard_lines(
                data_lines, win_rate=self.category == "win_rate"
            )
            players_data.sort(key=lambda x: x[1], reverse=True)

            # ── identify current user (for highlighting + My Rank) ────────────
            user_id = str(interaction.user.id)
            user_registered_name = None
            try:
                with db_connection(self.lb_guild_id) as (conn, cursor):
                    cursor.execute(
                        "SELECT name FROM players WHERE id = ?", (user_id,)
                    )
                    row = cursor.fetchone()
                    if row:
                        user_registered_name = row[0].lower()
            except Exception:
                pass

            def _is_target(name: str, discord_id: str | None) -> bool:
                return (
                    discord_id == user_id
                    if discord_id
                    else (
                        user_registered_name is not None
                        and name.lower() == user_registered_name
                    )
                )

            # ── identify the command invoker (for greying out My Rank) ────────
            # This is deliberately separate from the "current viewer" lookup
            # above: prev/next/dropdown can be used by anyone, but only the
            # invoker can use My Rank, so the button's disabled state must
            # always reflect the invoker's presence on the page, not the
            # presence of whoever happens to be clicking right now.
            invoker_id_str = str(self.invoker_id) if self.invoker_id is not None else None
            if invoker_id_str is not None and invoker_id_str == user_id:
                invoker_registered_name = user_registered_name
            else:
                invoker_registered_name = None
                if invoker_id_str is not None:
                    try:
                        with db_connection(self.lb_guild_id) as (conn, cursor):
                            cursor.execute(
                                "SELECT name FROM players WHERE id = ?", (invoker_id_str,)
                            )
                            row = cursor.fetchone()
                            if row:
                                invoker_registered_name = row[0].lower()
                    except Exception:
                        pass

            def _is_invoker(name: str, discord_id: str | None) -> bool:
                if invoker_id_str is None:
                    return False
                return (
                    discord_id == invoker_id_str
                    if discord_id
                    else (
                        invoker_registered_name is not None
                        and name.lower() == invoker_registered_name
                    )
                )

            if self.jump_to_target:
                self.jump_to_target = False
                target_index = next(
                    (
                        i
                        for i, (name, _, discord_id) in enumerate(players_data)
                        if _is_target(name, discord_id)
                    ),
                    None,
                )
                if target_index is None:
                    await interaction.response.send_message(
                        embed=discord.Embed(
                            title="Not Ranked",
                            description=(
                                "You are not currently ranked in this leaderboard category."
                            ),
                            color=discord.Color.red(),
                        ),
                        ephemeral=True,
                    )
                    return
                self.page = target_index // self.per_page

            self.total_pages = max(1, (len(players_data) - 1) // self.per_page + 1)
            if self.page >= self.total_pages:
                self.page = self.total_pages - 1
            start = self.page * self.per_page
            end = start + self.per_page

            entries = []
            for i, (name, value, discord_id) in enumerate(
                players_data[start:end], start=start + 1
            ):
                stat_str = (
                    f"{value:.2f}" if self.category == "win_rate" else str(value)
                )
                entries.append((i, name, stat_str, _is_target(name, discord_id)))

            # Grey out My Rank once the invoker's own row is already visible.
            self.my_rank_button.disabled = any(
                _is_invoker(name, discord_id)
                for name, _, discord_id in players_data[start:end]
            )

            title_suffix = CATEGORY_TITLES.get(self.category, "Elo")
            embed, file = build_leaderboard_embed(
                f"{self.season} / {self.month} - Top {title_suffix}",
                entries,
                page=self.page + 1,
                total_pages=self.total_pages,
            )
            self.update_buttons()
            await interaction.response.edit_message(
                embed=embed, attachments=[file], view=self
            )
            # Modified: keep a message reference so on_timeout() can disable
            # the buttons in place, matching LeaderboardView's behaviour.
            self.message = interaction.message

        except Exception as e:
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    embed=discord.Embed(
                        title="Error",
                        description=f"An error occurred: {str(e)}",
                        color=discord.Color.red(),
                    ),
                    ephemeral=True,
                )
