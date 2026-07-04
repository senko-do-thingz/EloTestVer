"""
cogs/admin_elo - split from the monolithic admin_elo.py.

Sub-modules
-----------
base         : AdminEloBaseCog  (shared helper: _remove_bot_reactions)
decay        : AdminEloDecayCog (decay, reset_e)
registration : no-op stub. register/rename (public + admin_o/admin_oid
               variants) live in cogs/registration.py - see that module's
               docstring for why this one no longer duplicates them.
setters      : AdminEloSettersCog (set_e, set_eid, set_w, set_wid,
                                   set_l, set_lid, set_gid, set_stats)
"""

from __future__ import annotations

from discord.ext import commands


async def setup(bot: commands.Bot) -> None:
    from .decay import setup as setup_decay
    from .registration import setup as setup_registration
    from .setters import setup as setup_setters

    await setup_decay(bot)
    await setup_registration(bot)
    await setup_setters(bot)