"""
cogs/roles - split from the monolithic roles.py.

Sub-modules
-----------
base     : RolesBaseCog  (shared helpers, league task tracking)
view     : RolesViewCog  (leagues, ladmin, lhelp)
edit     : RolesEditCog  (ledit)
manage   : RolesManageCog (ladd, lapply, lcancel, lclear, lannounce, lrannounce)
"""

from __future__ import annotations

from discord.ext import commands


async def setup(bot: commands.Bot) -> None:
    from .view import setup as setup_view
    from .edit import setup as setup_edit
    from .manage import setup as setup_manage

    await setup_view(bot)
    await setup_edit(bot)
    await setup_manage(bot)