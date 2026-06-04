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
    parse_leaderboard_line,
)

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
        super().__init__(timeout=None)
        self.add_item(_BackToLBButton(lb_view))


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
            stats_data: dict[str, dict] = {}
            for category, filename in FILE_MAP.items():
                file_path = os.path.join(mpath, filename)
                if not os.path.exists(file_path):
                    continue
                with open(file_path, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                for line in lines:
                    stripped = line.strip()
                    if not stripped or stripped.lower().endswith("leaderboard"):
                        continue
                    parsed = parse_leaderboard_line(
                        stripped, win_rate=category == "win_rate"
                    )
                    if not parsed:
                        continue
                    name, value, _ = parsed
                    if search_name in name.lower():
                        stats_data.setdefault(name, {})[category] = value

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
                    f"- {n}" for n in list(stats_data.keys())[:10]
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
            player_name = list(stats_data.keys())[0]
            player_stats = stats_data[player_name]

            def get_stat_value(category: str):
                value = player_stats.get(category)
                fp = os.path.join(mpath, FILE_MAP[category])
                if value is not None:
                    return value, True
                return ("-", os.path.exists(fp))

            def get_rank_for_stat(category: str, value) -> int | None:
                fp = os.path.join(mpath, FILE_MAP[category])
                if not os.path.exists(fp):
                    return None
                with open(fp, "r", encoding="utf-8") as f:
                    raw = f.readlines()
                all_values = []
                for line in raw:
                    s = line.strip()
                    if not s or s.lower().endswith("leaderboard"):
                        continue
                    p = parse_leaderboard_line(s, win_rate=category == "win_rate")
                    if p:
                        all_values.append(p[1])
                if not all_values:
                    return None
                all_values.sort(reverse=True)
                return sum(1 for v in all_values if v > value) + 1

            elo, _ = get_stat_value("elo")
            wins, _ = get_stat_value("wins")
            losses, _ = get_stat_value("losses")
            games_played_this_month, _ = get_stat_value("games_played_this_month")
            win_rate, _ = get_stat_value("win_rate")

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
                    f"**🎮 Monthly Games Played:** {games_played_this_month}{_rank(monthly_rank)}"
                )

            if win_rate != "-":
                stat_lines.append(f"**📊 Win Rate:** {win_rate:.2f}%{_rank(win_rate_rank)}")
            else:
                stat_lines.append(f"**📊 Win Rate:** {win_rate}")

            # Most played against
            try:
                with db_connection(lb_guild_id) as (conn, cursor):
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
                stat_lines.append(f"**👥 Most Played Against:** {most_played_text}")
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
    ):
        super().__init__(timeout=None)
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

        # Back button is added by the caller (season_view.py) so it can close
        # back to the correct MonthView.  We reserve a slot by requiring callers
        # to call add_back_button() before update().
        self._back_btn: discord.ui.Button | None = None

        self.add_item(LBDropdown(self))
        self.add_item(PrevBtn(self))
        self.add_item(NextBtn(self))
        self.add_item(SearchPlayerBtn(self))

    def set_back_button(self, btn: discord.ui.Button):
        self._back_btn = btn
        self.add_item(btn)
        self.update_buttons()

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
        # children order: dropdown(0), prev(1), next(2), search(3), back(4)
        if len(self.children) > 2:
            self.children[1].disabled = self.page == 0
            self.children[2].disabled = self.page >= self.total_pages - 1

    # ── main render ─────────────────────────────────────────────────────────

    async def update(self, interaction: discord.Interaction):
        conn = None
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
                return

            players_data = [
                p
                for l in data_lines
                if (p := parse_leaderboard_line(l, win_rate=self.category == "win_rate"))
            ]
            players_data.sort(key=lambda x: x[1], reverse=True)

            self.total_pages = max(1, (len(players_data) - 1) // self.per_page + 1)
            start = self.page * self.per_page
            end = start + self.per_page

            # ── highlight current user ───────────────────────────────────────
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

            entries = []
            for i, (name, value, discord_id) in enumerate(
                players_data[start:end], start=start + 1
            ):
                stat_str = (
                    f"{value:.2f}" if self.category == "win_rate" else str(value)
                )
                highlight = (
                    discord_id == user_id
                    if discord_id
                    else (
                        user_registered_name is not None
                        and name.lower() == user_registered_name
                    )
                )
                entries.append((i, name, stat_str, highlight))

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
        finally:
            if conn:
                conn.close()
