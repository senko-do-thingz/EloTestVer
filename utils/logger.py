"""
Bot-level logging utility.

Sends structured log messages to a configured Discord text channel on the
private server.  Import ``bot_log`` and call it anywhere inside a cog after
the bot is ready.

Usage::

    from utils.logger import bot_log

    await bot_log(bot, guild_id, "Player registered", level="INFO",
                  fields={"name": "Senko", "elo": 500})
"""

import datetime
import traceback
from typing import Any

import discord

from db.elo_db import get_log_channel_id

# The guild that hosts the log channel (same constant used by lb_id / st).
PRIVATE_GUILD_ID = 1048217847198916618

_LEVEL_COLOR = {
    "INFO":    discord.Color.blue(),
    "WARNING": discord.Color.yellow(),
    "ERROR":   discord.Color.red(),
    "SUCCESS": discord.Color.green(),
}


async def bot_log(
    bot: discord.Client,
    source_guild_id: int,
    message: str,
    *,
    level: str = "INFO",
    fields: dict[str, Any] | None = None,
    exc: BaseException | None = None,
) -> None:
    """
    Send a log embed to the private server's configured log channel.

    Parameters
    ----------
    bot:              The discord.py Bot instance.
    source_guild_id:  The guild that triggered this log entry.
    message:          Short description of the event.
    level:            One of "INFO", "WARNING", "ERROR", "SUCCESS".
    fields:           Optional extra key-value pairs shown as embed fields.
    exc:              Optional exception; its traceback is appended.
    """
    channel_id = get_log_channel_id(PRIVATE_GUILD_ID)
    if channel_id is None:
        return  # Log channel not configured – silently skip.

    channel = bot.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except Exception:
            return

    color = _LEVEL_COLOR.get(level.upper(), discord.Color.light_grey())
    embed = discord.Embed(
        title=f"[{level.upper()}] {message}",
        color=color,
        timestamp=datetime.datetime.now(datetime.timezone.utc),
    )
    if source_guild_id != 0:
        embed.add_field(name="Source Guild", value=str(source_guild_id), inline=True)

    if fields:
        for k, v in fields.items():
            embed.add_field(name=k, value=str(v), inline=True)

    if exc is not None:
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        # Discord field value limit is 1024 chars
        if len(tb) > 1000:
            tb = tb[-1000:]
        embed.add_field(name="Traceback", value=f"```\n{tb}\n```", inline=False)

    try:
        await channel.send(embed=embed)
    except Exception:
        pass  # Never let logging itself crash the bot.