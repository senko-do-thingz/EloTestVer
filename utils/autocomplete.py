"""Shared Discord slash autocomplete helpers."""

from __future__ import annotations

import discord
from discord import app_commands

from db.elo_db import db_connection


async def registered_name_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    if interaction.guild is None:
        return []
    needle = (current or "").strip()
    with db_connection(interaction.guild.id) as (_conn, cursor):
        if needle:
            cursor.execute(
                """
                SELECT name FROM players
                WHERE name LIKE ? COLLATE NOCASE
                ORDER BY name ASC
                LIMIT 25
                """,
                (f"%{needle}%",),
            )
        else:
            cursor.execute(
                "SELECT name FROM players ORDER BY name ASC LIMIT 25"
            )
        rows = cursor.fetchall()
    return [app_commands.Choice(name=row[0], value=row[0]) for row in rows]


def _top_elo_players(guild_id: int, *, limit: int = 5) -> list[tuple[str, str, int]]:
    """Return up to *limit* (id, name, elo) tuples for players ranked by Elo,
    only counting players who have played at least one set."""
    with db_connection(guild_id) as (_conn, cursor):
        cursor.execute(
            """
            SELECT id, name, elo FROM players
            WHERE (games_won + games_lost) > 0
            ORDER BY elo DESC
            LIMIT ?
            """,
            (limit,),
        )
        return cursor.fetchall()


async def stats_top_elo_name_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    """Suggest the top 5 names on the Elo leaderboard (filtered by *current* if typed).
    Elo is intentionally not shown in the suggestion label."""
    if interaction.guild is None:
        return []
    needle = (current or "").strip().lower()
    choices = []
    for _uid, name, _elo in _top_elo_players(interaction.guild.id, limit=5):
        if needle and needle not in name.lower():
            continue
        choices.append(app_commands.Choice(name=name, value=name))
    return choices


async def command_name_autocomplete(
    interaction: discord.Interaction,
    current: str,
    *,
    names: list[str],
) -> list[app_commands.Choice[str]]:
    needle = (current or "").strip().lower().lstrip("-").lstrip("/")
    matches = [name for name in names if needle in name.lower()] if needle else names
    return [
        app_commands.Choice(name=name, value=name)
        for name in matches[:25]
    ]