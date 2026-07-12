"""Scheduled task: once a day, right after UTC midnight, zero out
``daily_streak`` for any player who missed the previous UTC day entirely -
across every guild database, not just guilds the bot happens to be online
in at that exact moment.

This is the durable/proactive counterpart to
``db.elo_db.get_effective_daily_streak()`` (which only *displays* a stale
streak as 0 on read, without changing the stored value). With this task
running, the stored ``daily_streak`` value itself gets reset shortly after
day rollover, instead of only ever being corrected lazily when someone
happens to look it up.
"""

from __future__ import annotations

import datetime as dt

from discord.ext import commands, tasks

from db.elo_db import refresh_all_daily_streaks
from utils.logger import bot_log

_MIDNIGHT_UTC = dt.time(hour=0, minute=0, second=30, tzinfo=dt.timezone.utc)


class DailyStreakRefreshCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.refresh_daily_streaks.start()

    def cog_unload(self) -> None:
        self.refresh_daily_streaks.cancel()

    @tasks.loop(time=_MIDNIGHT_UTC)
    async def refresh_daily_streaks(self) -> None:
        dbs_processed, total_reset = refresh_all_daily_streaks()
        await bot_log(
            self.bot,
            0,
            "Daily streak refresh completed",
            level="INFO",
            fields={
                "Servers processed": str(dbs_processed),
                "Streaks reset": str(total_reset),
            },
        )

    @refresh_daily_streaks.before_loop
    async def before_refresh_daily_streaks(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(DailyStreakRefreshCog(bot))