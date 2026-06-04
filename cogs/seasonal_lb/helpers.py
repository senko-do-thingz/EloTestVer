import re
import discord
from utils import render_leaderboard_image


# ---------------------------------------------------------------------------
# Sorting helpers
# ---------------------------------------------------------------------------

def natural_key(s: str) -> list:
    """Sort key that orders strings with embedded numbers naturally."""
    return [int(c) if c.isdigit() else c.lower() for c in re.split(r"(\d+)", s)]


def sort_seasons(season_list: list[str]) -> list[str]:
    """Sort seasons in natural order (Season 1, Season 2, …)."""
    return sorted(season_list, key=natural_key)


def sort_months(month_list: list[str]) -> list[str]:
    """Sort months naturally, placing 'overall' last."""
    return sorted(
        month_list,
        key=lambda m: (m.lower() == "overall", natural_key(m)),
    )


# ---------------------------------------------------------------------------
# Embed builder
# ---------------------------------------------------------------------------

def build_leaderboard_embed(
    title: str,
    entries: list[tuple[int, str, str] | tuple[int, str, str, bool]],
    *,
    page: int | None = None,
    total_pages: int | None = None,
    note: str | None = None,
) -> tuple[discord.Embed, discord.File]:
    filename = "leaderboard.png"
    file = discord.File(
        render_leaderboard_image(entries, title=title, page=page, total_pages=total_pages),
        filename=filename,
    )
    embed = discord.Embed(color=discord.Color.blue())
    embed.set_image(url=f"attachment://{filename}")
    if note:
        embed.set_footer(text=note)
    return embed, file
