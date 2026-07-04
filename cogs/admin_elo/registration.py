"""
cogs/admin_elo/registration.py

This used to contain a full duplicate of cogs/registration.py's
RegistrationCog (register, rename, register_o, rename_o, register_oid,
rename_oid). Because both files defined a cog class named "RegistrationCog",
whichever loaded first "won" and the other failed with a silent
"Cog named 'RegistrationCog' already loaded" error during startup - so one
of the two copies was always dead code, and it was easy to lose bugfixes
made to whichever copy didn't happen to load.

cogs/registration.py is the single source of truth for register/rename
(both the public commands and their admin_o/admin_oid variants). This
module is kept as a no-op stub purely so cogs/admin_elo/__init__.py's
`await setup_registration(bot)` call still has something to import; it adds
no cog itself.

Per the admin_elo package layout, this module is *intended* to eventually
house AdminEloRegistrationCog with c_reg/d_reg/did_reg/db_d (bulk/registration
-clearing admin commands referenced in cogs/help.py and elobot.py's logging
allowlist) - those are not yet implemented anywhere in the codebase.
"""

from discord.ext import commands


async def setup(bot: commands.Bot) -> None:
    # No cog to add - see module docstring.
    return