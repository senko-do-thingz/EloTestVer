"""
cogs/seasonal_lb - seasonal leaderboard browser.

Sub-modules
-----------
helpers      : sort_seasons, sort_months, build_leaderboard_embed  (no Cog)
base         : SeasonalLBBaseCog  (_open_lbs_picker, _build_season_view)
picker       : SeasonalLBPickerCog  (lbs, lbs_id)
season_view  : SeasonView, MonthView, MonthBackBtn, MonthDropdown, LBBackBtn  (UI, no Cog)
lb_view      : LBView + all LB UI components  (UI, no Cog)
"""

from __future__ import annotations

from discord.ext import commands


async def setup(bot: commands.Bot) -> None:
    from .picker import setup as setup_picker

    await setup_picker(bot)
