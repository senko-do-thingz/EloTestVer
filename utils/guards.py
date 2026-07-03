import re

from discord.utils import get
import discord

# Allowed character ranges:
#   a-zA-Z0-9_   → ASCII letters, digits, underscore
#   \u00C0-\u024F → Latin Extended (accented letters: à á â ã ä å æ ç è é ê ...)
#   \u0300-\u036F → Combining diacritical marks (NFD decomposed accents)
#   \u4E00-\u9FFF → CJK Unified Ideographs (basic Chinese/Han characters)
#   \u3400-\u4DBF → CJK Extension A
#   \uF900-\uFAFF → CJK Compatibility Ideographs
#   \u3040-\u309F → Hiragana (Japanese)
#   \u30A0-\u30FF → Katakana (Japanese)
#   \uAC00-\uD7AF → Hangul Syllables (Korean)
#   \u1100-\u11FF → Hangul Jamo
#   \u3130-\u318F → Hangul Compatibility Jamo
#   \.            → period
# All other scripts (Cyrillic, Greek, Arabic, emoji, ...) are blocked.
_VALID_NAME_RE = re.compile(
    r"^["
    r"a-zA-Z0-9_"
    r"\u00C0-\u024F"
    r"\u0300-\u036F"
    r"\u4E00-\u9FFF"
    r"\u3400-\u4DBF"
    r"\uF900-\uFAFF"
    r"\u3040-\u309F"
    r"\u30A0-\u30FF"
    r"\uAC00-\uD7AF"
    r"\u1100-\u11FF"
    r"\u3130-\u318F"
    r"\.]+$"
)


# Human-readable description of is_valid_name()'s rules, shared by the
# registration error messages and the -help / /help text for register/rename.
NAME_RULE_TEXT = (
    "Name must be 2-25 characters and only contain English letters, "
    "accented letters (e.g. à, ü), Chinese/Japanese/Korean characters, "
    "numbers, '.' and '_'. Names containing \"elobot\" are not allowed."
)


def is_valid_name(name: str) -> tuple[bool, str]:
    # Check if name contains "elobot" (case insensitive)
    if "elobot" in name.lower():
        return False, "banned"

    # Check length (keep existing UX expectations).
    if not (2 <= len(name) <= 25):
        return False, "invalid_length"

    # Allowed: ASCII letters/digits/underscore, Latin with diacritics,
    # CJK (Chinese/Japanese Kanji), Hiragana, Katakana, Korean Hangul,
    # dot (.) — everything else is rejected.
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