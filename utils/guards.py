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

# Per-character test used by _find_invalid_chars to pinpoint exactly which
# characters in a name are not allowed.
_VALID_CHAR_RE = re.compile(
    r"["
    r"a-zA-Z0-9_ "
    r"\u00C0-\u024F"
    r"\u1E00-\u1EFF"
    r"\u0300-\u036F"
    r"\."
    r"]"
)

# Min/max name length constants, used by is_valid_name and exposed for
# error messages.
NAME_MIN_LENGTH = 2
NAME_MAX_LENGTH = 20


# Human-readable description of is_valid_name()'s rules, shared by the
# registration error messages and the -help / /help text for register/rename.
NAME_RULE_TEXT = (
    f"Name must be {NAME_MIN_LENGTH}-{NAME_MAX_LENGTH} characters and only contain English letters, "
    "accented letters (e.g. a\u0300, u\u0308 - including Vietnamese and other Latin "
    "script variants), numbers, spaces, '.' and '_'. "
    "Names containing \"elobot\" are not "
    "allowed."
)


def _find_invalid_chars(name: str) -> list[str]:
    """Return a deduplicated, order-preserving list of characters in ``name``
    that are NOT allowed by the naming rules. Used to show users exactly
    which characters caused their name to be rejected, rather than forcing
    them to guess from a generic rule description."""
    seen: set[str] = set()
    result: list[str] = []
    for ch in name:
        if ch not in seen and not _VALID_CHAR_RE.match(ch):
            seen.add(ch)
            result.append(ch)
    return result


def is_valid_name(name: str) -> tuple[bool, str]:
    """Validate a player display name.

    Returns ``(True, "valid")`` on success, or ``(False, reason)`` on
    failure where ``reason`` is a structured string that encodes the
    specific violation:

    - ``"banned"``              - name contains "elobot"
    - ``"too_short:<len>"``     - name is shorter than NAME_MIN_LENGTH
    - ``"too_long:<len>"``      - name is longer than NAME_MAX_LENGTH
    - ``"invalid_chars:<chars>"`` - name contains characters not in the
      allowed set; ``<chars>`` is the offending characters joined together
    - ``"only_spaces"``         - name is nothing but whitespace
    """
    # Check if name contains "elobot" (case insensitive)
    if "elobot" in name.lower():
        return False, "banned"

    # Check length (keep existing UX expectations).
    if len(name) < NAME_MIN_LENGTH:
        return False, f"too_short:{len(name)}"
    if len(name) > NAME_MAX_LENGTH:
        return False, f"too_long:{len(name)}"

    # Names that are entirely whitespace pass the regex but are useless.
    if not name.strip():
        return False, "only_spaces"

    # Allowed: ASCII letters/digits/underscore, Latin with diacritics
    # (Vietnamese and other Latin-script variants), dot (.) - everything
    # else, including CJK/Hangul, is rejected.
    if not _VALID_NAME_RE.match(name):
        bad_chars = _find_invalid_chars(name)
        return False, f"invalid_chars:{''.join(bad_chars)}"

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