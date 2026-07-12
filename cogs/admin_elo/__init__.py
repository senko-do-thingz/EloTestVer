"""
cogs/admin_elo - split from the monolithic admin_elo.py.

Sub-modules
-----------
base         : AdminEloBaseCog  (shared helper: _remove_bot_reactions)
decay        : AdminEloDecayCog (decay, decay_setup, reset_e)
decay_auto   : AutoDecayCog (automatic monthly -decay task, see module
               docstring for the full Season/Month/Overall rollover rules)
nickname     : AdminEloNicknameCog (-nickname, admin-only, any server -
               moved here from cogs/private/ since it's no longer
               restricted to the private/management guild)
registration : AdminEloRegistrationCog (c_reg). register/rename (public +
               admin_o/admin_oid variants) live in cogs/registration.py -
               see that module's docstring for why this one doesn't
               duplicate them.
setters      : AdminEloSettersCog (set_e, set_eid, set_w, set_wid,
                                   set_l, set_lid, set_gid, set_stats)
"""

from __future__ import annotations

from discord.ext import commands


async def setup(bot: commands.Bot) -> None:
    from .decay import setup as setup_decay
    from .decay_auto import setup as setup_decay_auto
    from .nickname import setup as setup_nickname
    from .registration import setup as setup_registration
    from .setters import setup as setup_setters

    await setup_decay(bot)
    await setup_decay_auto(bot)
    await setup_nickname(bot)
    await setup_registration(bot)
    await setup_setters(bot)