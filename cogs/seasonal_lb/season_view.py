"""
season_view.py
--------------
Season -> Month -> LB navigation views.

Flow:
  SeasonView  (dropdown of seasons)
    └─ SeasonDropdown.callback
         └─ MonthView  (dropdown of months + Back button)
              └─ MonthDropdown.callback
                   └─ LBView  (paginated leaderboard)
                        └─ LBBackBtn.callback  -> back to MonthView, image stays
"""

from __future__ import annotations

import os

import discord

from .helpers import sort_months
from .lb_view import LBView


# ---------------------------------------------------------------------------
# Season picker
# ---------------------------------------------------------------------------

class SeasonView(discord.ui.View):
    def __init__(
        self, seasons: list[str], base: str, lb_guild_id: int, invoker_id: int | None = None
    ):
        super().__init__(timeout=None)
        self.add_item(SeasonDropdown(seasons, base, lb_guild_id, invoker_id))


class SeasonDropdown(discord.ui.Select):
    def __init__(
        self, seasons: list[str], base: str, lb_guild_id: int, invoker_id: int | None = None
    ):
        opts = [discord.SelectOption(label=s, value=s) for s in seasons]
        super().__init__(placeholder="Select a season...", options=opts)
        self.base = base
        self.lb_guild_id = lb_guild_id
        self.invoker_id = invoker_id

    async def callback(self, interaction: discord.Interaction):
        season = self.values[0]
        spath = os.path.join(self.base, season)
        months = sort_months(
            [d for d in os.listdir(spath) if os.path.isdir(os.path.join(spath, d))]
        )

        if not months:
            await interaction.response.edit_message(
                embed=discord.Embed(
                    title="No Months Found",
                    description=f"No months in {season}.",
                    color=discord.Color.red(),
                ),
                view=None,
            )
            return

        await interaction.response.edit_message(
            embed=discord.Embed(
                title=f"Select a Month in {season}",
                description="Choose a month to view its leaderboard.",
                color=discord.Color.blue(),
            ),
            view=MonthView(season, months, self.base, self.lb_guild_id, self.invoker_id),
        )


# ---------------------------------------------------------------------------
# Month picker
# ---------------------------------------------------------------------------

class MonthView(discord.ui.View):
    def __init__(
        self,
        season: str,
        months: list[str],
        base: str,
        lb_guild_id: int,
        invoker_id: int | None = None,
    ):
        super().__init__(timeout=None)
        self.add_item(MonthDropdown(season, months, base, lb_guild_id, invoker_id))
        self.add_item(MonthBackBtn(base, lb_guild_id, invoker_id))


class MonthBackBtn(discord.ui.Button):
    """Go back to the season picker."""

    def __init__(self, base: str, lb_guild_id: int, invoker_id: int | None = None):
        super().__init__(label="Back", style=discord.ButtonStyle.secondary)
        self.base = base
        self.lb_guild_id = lb_guild_id
        self.invoker_id = invoker_id

    async def callback(self, interaction: discord.Interaction):
        from .base import SeasonalLBBaseCog  # late import to avoid circular

        season_view, embed = SeasonalLBBaseCog._build_season_view(
            self.base, self.lb_guild_id, self.invoker_id
        )
        if season_view is None:
            await interaction.response.edit_message(embed=embed, view=None)
        else:
            await interaction.response.edit_message(embed=embed, view=season_view)


class MonthDropdown(discord.ui.Select):
    def __init__(
        self,
        season: str,
        months: list[str],
        base: str,
        lb_guild_id: int,
        invoker_id: int | None = None,
    ):
        opts = [discord.SelectOption(label=m, value=m) for m in months]
        super().__init__(placeholder="Select a month...", options=opts)
        self.season = season
        self.months = months
        self.base = base
        self.lb_guild_id = lb_guild_id
        self.invoker_id = invoker_id

    async def callback(self, interaction: discord.Interaction):
        bot = interaction.client
        month = self.values[0]
        mpath = os.path.join(self.base, self.season, month)

        if not os.path.exists(os.path.join(mpath, "data.txt")):
            await interaction.response.edit_message(
                embed=discord.Embed(
                    title="Coming Soon",
                    description="This leaderboard is not available yet. Stay tuned!",
                    color=discord.Color.orange(),
                ),
                view=MonthView(
                    self.season, self.months, self.base, self.lb_guild_id, self.invoker_id
                ),
            )
            return

        try:
            lb_view = LBView(
                self.season,
                month,
                mpath,
                self.months,
                self.lb_guild_id,
                category="elo",
                bot=bot,
                invoker_id=self.invoker_id,
            )
            lb_view.set_back_button(
                LBBackBtn(
                    self.season, self.months, self.base, self.lb_guild_id, self.invoker_id
                )
            )
            await lb_view.update(interaction)
        except Exception as e:
            await interaction.response.edit_message(
                embed=discord.Embed(
                    title="Error",
                    description=str(e),
                    color=discord.Color.red(),
                ),
                view=None,
            )


# ---------------------------------------------------------------------------
# Back button inside LBView  ->  removes embed/view, keeps the image
# ---------------------------------------------------------------------------

class LBBackBtn(discord.ui.Button):
    """
    Pressing Back from the leaderboard view removes the embed and interactive
    controls but leaves the leaderboard image attachment visible in the message.
    """

    def __init__(
        self,
        season: str,
        months: list[str],
        base: str,
        lb_guild_id: int,
        invoker_id: int | None = None,
    ):
        super().__init__(label="Back", style=discord.ButtonStyle.secondary)
        self.season = season
        self.months = months
        self.base = base
        self.lb_guild_id = lb_guild_id
        self.invoker_id = invoker_id

    async def callback(self, interaction: discord.Interaction):
        # Bug fix: this used to just strip the embed/view (edit_message(embed=None,
        # view=None)) without touching the attachment, so the leaderboard image
        # from LBView stayed on the message and the user was left with a bare
        # picture and no controls. Rebuild the MonthView menu (mirrors
        # SeasonDropdown.callback / MonthBackBtn) and clear the stale image so
        # Back actually returns to the original -lbs month picker.
        await interaction.response.edit_message(
            embed=discord.Embed(
                title=f"Select a Month in {self.season}",
                description="Choose a month to view its leaderboard.",
                color=discord.Color.blue(),
            ),
            view=MonthView(self.season, self.months, self.base, self.lb_guild_id, self.invoker_id),
            attachments=[],
        )