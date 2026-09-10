import re

from discord.utils import get
import discord

# Allowed character ranges:
#   a-zA-Z0-9_   -> ASCII letters, digits, underscore
#   \u00C0-\u024F -> Latin Extended (accented letters: à á â ã ä å æ ç è é ê ...
#                    also covers most other Latin-script variants)
#   \u1E00-\u1EFF -> Latin Extended Additional (Vietnamese precomposed
#                    letters not covered above: ệ, ố, ắ, ữ, ỳ, ...)
#   \u0300-\u036F -> Combining diacritical marks (NFD decomposed accents)
#   \.            -> period
# CJK/Hangul (Chinese, Japanese, Korean) and all other scripts (Cyrillic,
# Greek, Arabic, emoji, ...) are blocked. CJK/Hangul used to be allowed, but
# the bundled font can't render every character in those scripts, so an
# unsupported one shows up as a "tofu" box wherever the name is drawn (e.g.
# the /history and /leaderboard images) instead of failing name validation.
_VALID_NAME_RE = re.compile(
    r"^["
    r"a-zA-Z0-9_ "
    r"\u00C0-\u024F"
    r"\u1E00-\u1EFF"
    r"\u0300-\u036F"
    r"\.]+$"
)


# Human-readable description of is_valid_name()'s rules, shared by the
# registration error messages and the -help / /help text for register/rename.
NAME_RULE_TEXT = (
    "Name must be 2-20 characters and only contain English letters, "
    "accented letters (e.g. a\u0300, u\u0308 - including Vietnamese and other Latin "
    "script variants), numbers, spaces, '.' and '_'. "
    "Names containing \"elobot\" are not "
    "allowed."
)


def is_valid_name(name: str) -> tuple[bool, str]:
    # Check if name contains "elobot" (case insensitive)
    if "elobot" in name.lower():
        return False, "banned"

    # Check length (keep existing UX expectations).
    if not (2 <= len(name) <= 20):
        return False, "invalid_length"

    # Allowed: ASCII letters/digits/underscore, Latin with diacritics
    # (Vietnamese and other Latin-script variants), dot (.) - everything
    # else, including CJK/Hangul, is rejected.
    if not _VALID_NAME_RE.match(name):
        return False, "invalid_format"

    return True, "valid"


def is_admin(ctx) -> bool:
    """Check if the user has the 'Elo Bot Manager' role."""
    return get(ctx.author.roles, name="Elo Bot Manager") is not None


def is_admin_interaction(interaction: discord.Interaction) -> bool:
    """App-command variant of ``is_admin``."""
    user = interaction.user
    if not isinstance(user, discord.Member):
        return False
    return get(user.roles, name="Elo Bot Manager") is not None