"""
Leaderboard image pre-render cache.

Cách hoạt động
--------------
- Cache lưu ảnh PNG (bytes) cho từng (guild_id, category, page).
- Khi Elo thay đổi (report confirm, admin edit, revert…), gọi
  ``invalidate(guild_id)`` để xóa cache của guild đó.
- Background task ``rebuild(bot, guild_id)`` sẽ render lại tất cả
  các page của tất cả categories và lưu vào cache.
- Khi user gọi /lb, ``get()`` trả về bytes từ cache ngay lập tức
  nếu có; nếu không có (lần đầu hoặc đang rebuild) trả về None và
  caller tự render bình thường.

Thread-safety
-------------
asyncio event loop là single-threaded nên dict thao tác bình thường.
render chạy trong thread pool (asyncio.to_thread) nên không block loop.
"""

from __future__ import annotations

import asyncio
import io
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import discord

logger = logging.getLogger(__name__)

# (guild_id, category, page) → PNG bytes
_cache: dict[tuple[int, str, int], bytes] = {}

# Giữ track task đang rebuild để tránh chạy 2 lần cùng lúc cho 1 guild
_rebuild_tasks: dict[int, asyncio.Task] = {}

CATEGORIES = ("elo", "wins", "losses", "win_rate", "games_played_this_month")
PER_PAGE = 10


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get(guild_id: int, category: str, page: int) -> bytes | None:
    """Trả về PNG bytes từ cache, hoặc None nếu chưa có."""
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
    """Lưu PNG bytes vào cache."""
    _cache[(guild_id, category, page)] = png_bytes
    logger.debug(
        "LB cache STORE guild=%s category=%s page=%d  (%d bytes)",
        guild_id, category, page, len(png_bytes),
    )


def invalidate(guild_id: int) -> None:
    """Xóa toàn bộ cache của một guild (gọi sau mỗi lần Elo thay đổi)."""
    keys = [k for k in _cache if k[0] == guild_id]
    for k in keys:
        del _cache[k]
    logger.debug("LB cache invalidated for guild %s (%d entries removed)", guild_id, len(keys))


def invalidate_all() -> None:
    """Xóa toàn bộ cache (dùng khi bot restart hoặc mass reset)."""
    _cache.clear()
    logger.debug("LB cache fully cleared")


# ---------------------------------------------------------------------------
# Rebuild helpers
# ---------------------------------------------------------------------------

def schedule_rebuild(bot: "discord.ext.commands.Bot", guild_id: int) -> None:
    """
    Lên lịch rebuild cache cho guild sau khi Elo thay đổi.

    Hủy task rebuild cũ nếu đang chạy (tránh race condition khi nhiều
    match được report liên tiếp).
    """
    old = _rebuild_tasks.get(guild_id)
    if old and not old.done():
        logger.debug("LB cache rebuild CANCELLED (old task) for guild %s — new rebuild scheduled", guild_id)
        old.cancel()
    logger.debug("LB cache rebuild SCHEDULED for guild %s", guild_id)
    task = asyncio.create_task(_rebuild_guild(bot, guild_id))
    _rebuild_tasks[guild_id] = task


async def _rebuild_guild(bot: "discord.ext.commands.Bot", guild_id: int) -> None:
    """Render lại tất cả các page của mọi category cho guild."""
    try:
        from db.elo_db import db_connection
        from utils.rendering import render_leaderboard_image
        from utils.lb_cache import put
        from utils import calculate_win_rate

        await asyncio.sleep(0)  # yield trước khi bắt đầu render

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
                        title=f"Lb - {title}",
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
    """Trả về list (name, stat_str) đã sort cho category, không phân trang."""
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