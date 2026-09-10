"""
Leaderboard image pre-render cache.

How it works
------------
- The cache stores PNG images (bytes) for each (guild_id, category, page).
- When Elo changes (report confirm, admin edit, revert...), call
  ``invalidate(guild_id)`` to clear that guild's cache.
- The background task ``rebuild(bot, guild_id)`` re-renders all pages of
  all categories and stores them in the cache.
- When a user calls /lb, ``get()`` returns the bytes from the cache
  immediately if present; if not (first time or still rebuilding) it
  returns None and the caller renders it normally.

Thread-safety
-------------
The asyncio event loop is single-threaded so dict operations are safe as-is.
Rendering runs in a thread pool (asyncio.to_thread) so it doesn't block the loop.
"""

from __future__ import annotations

import asyncio
import io
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import discord

logger = logging.getLogger(__name__)

# (guild_id, category, page) -> PNG bytes
_cache: dict[tuple[int, str, int], bytes] = {}

# Track in-progress rebuild tasks to avoid running two at once for the same guild
_rebuild_tasks: dict[int, asyncio.Task] = {}

CATEGORIES = ("elo", "wins", "losses", "win_rate", "games_played_this_month")
PER_PAGE = 10

# Must match LeaderboardView._title_prefix() in cogs/leaderboard.py so a
# cache-served page is visually indistinguishable from a freshly rendered one.
LB_TITLE_PREFIX = "Leaderboard - "


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get(guild_id: int, category: str, page: int) -> bytes | None:
    """Return PNG bytes from the cache, or None if not present."""
    result = _cache.get((guild_id, category, page))
    if result is not None:
        logger.debug(
            "LB cache HIT  guild=%s category=%s page=%d  (%d bytes)",
            guild_id, category, page, len(result),
        )
    else:
        logger.debug(
            "LB cache MISS guild=%s category=%s page=%d",
            guild_id, category, page,
        )
    return result


def put(guild_id: int, category: str, page: int, png_bytes: bytes) -> None:
    """Store PNG bytes in the cache."""
    _cache[(guild_id, category, page)] = png_bytes
    logger.debug(
        "LB cache STORE guild=%s category=%s page=%d  (%d bytes)",
        guild_id, category, page, len(png_bytes),
    )


def invalidate(guild_id: int) -> None:
    """Clear the entire cache for a guild (call this after every Elo change)."""
    keys = [k for k in _cache if k[0] == guild_id]
    for k in keys:
        del _cache[k]
    logger.debug("LB cache invalidated for guild %s (%d entries removed)", guild_id, len(keys))


def invalidate_all() -> None:
    """Clear the entire cache (used on bot restart or mass reset)."""
    _cache.clear()
    logger.debug("LB cache fully cleared")


# ---------------------------------------------------------------------------
# Rebuild helpers
# ---------------------------------------------------------------------------

def schedule_rebuild(bot: "discord.ext.commands.Bot", guild_id: int) -> None:
    """
    Schedule a cache rebuild for the guild after an Elo change.

    Cancels the previous rebuild task if it's still running (avoids a race
    condition when several matches are reported back-to-back).
    """
    old = _rebuild_tasks.get(guild_id)
    if old and not old.done():
        logger.debug("LB cache rebuild CANCELLED (old task) for guild %s - new rebuild scheduled", guild_id)
        old.cancel()
    logger.debug("LB cache rebuild SCHEDULED for guild %s", guild_id)
    task = asyncio.create_task(_rebuild_guild(bot, guild_id))
    _rebuild_tasks[guild_id] = task


async def _rebuild_guild(bot: "discord.ext.commands.Bot", guild_id: int) -> None:
    """Re-render all pages of every category for the guild."""
    try:
        from db.elo_db import db_connection
        from utils.rendering import render_leaderboard_image
        from utils.lb_cache import put
        from utils import calculate_win_rate

        await asyncio.sleep(0)  # yield before starting to render

        for category in CATEGORIES:
            try:
                entries_all = _fetch_entries(guild_id, category)
            except Exception:
                logger.exception("LB cache: failed to fetch entries for guild %s cat %s", guild_id, category)
                continue

            if not entries_all:
                continue

            total_pages = max(1, (len(entries_all) - 1) // PER_PAGE + 1)
            title = _category_title(category)

            for page in range(total_pages):
                start = page * PER_PAGE
                page_entries = [
                    (start + i + 1, name, stat)
                    for i, (name, stat) in enumerate(entries_all[start:start + PER_PAGE])
                ]
                try:
                    buf: io.BytesIO = await asyncio.to_thread(
                        render_leaderboard_image,
                        page_entries,
                        title=f"{LB_TITLE_PREFIX}{title}",
                        page=page + 1,
                        total_pages=total_pages,
                    )
                    put(guild_id, category, page, buf.read())
                except Exception:
                    logger.exception(
                        "LB cache: render failed guild %s cat %s page %d", guild_id, category, page
                    )

        logger.debug("LB cache rebuilt for guild %s", guild_id)

    except asyncio.CancelledError:
        logger.debug("LB cache rebuild cancelled for guild %s (superseded by newer rebuild)", guild_id)
    except Exception:
        logger.exception("LB cache: unexpected error rebuilding guild %s", guild_id)


def _fetch_entries(guild_id: int, category: str) -> list[tuple[str, str]]:
    """Return a sorted list of (name, stat_str) for the category, unpaginated."""
    from db.elo_db import db_connection
    from utils import calculate_win_rate

    with db_connection(guild_id) as (conn, cursor):
        if category == "elo":
            cursor.execute(
                "SELECT name, elo, games_won, games_lost FROM players ORDER BY elo DESC"
            )
            return [
                (name, str(elo))
                for name, elo, won, lost in cursor.fetchall()
                if won + lost > 0
            ]

        elif category == "wins":
            cursor.execute(
                "SELECT name, games_won FROM players ORDER BY games_won DESC"
            )
            return [(name, str(won)) for name, won in cursor.fetchall() if won > 0]

        elif category == "losses":
            cursor.execute(
                "SELECT name, games_lost FROM players ORDER BY games_lost DESC"
            )
            return [(name, str(lost)) for name, lost in cursor.fetchall() if lost > 0]

        elif category == "win_rate":
            cursor.execute("SELECT name, games_won, games_lost FROM players")
            eligible = [
                (name, calculate_win_rate(won, lost))
                for name, won, lost in cursor.fetchall()
                if won + lost >= 10
            ]
            eligible.sort(key=lambda x: x[1], reverse=True)
            return [(name, f"{wr:.2f}") for name, wr in eligible]

        elif category == "games_played_this_month":
            cursor.execute(
                "SELECT name, games_played_this_month, games_won, games_lost "
                "FROM players ORDER BY games_played_this_month DESC"
            )
            return [
                (name, str(games))
                for name, games, won, lost in cursor.fetchall()
                if won + lost > 0
            ]

    return []


def _category_title(category: str) -> str:
    return {
        "elo": "Top Elo",
        "wins": "Top Wins",
        "losses": "Top Losses",
        "win_rate": "Top Win Rate",
        "games_played_this_month": "Top Monthly Games Played",
    }.get(category, category)