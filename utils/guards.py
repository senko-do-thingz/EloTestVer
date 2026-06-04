import re

from discord.utils import get

# Allowed character ranges:
#   a-zA-Z0-9_   → ASCII letters, digits, underscore
#   \u00C0-\u024F → Latin Extended (chữ có dấu: à á â ã ä å æ ç è é ê ...)
#   \u0300-\u036F → Combining diacritical marks (NFD decomposed accents)
#   \u4E00-\u9FFF → CJK Unified Ideographs (chữ Hán/Trung cơ bản)
#   \u3400-\u4DBF → CJK Extension A
#   \uF900-\uFAFF → CJK Compatibility Ideographs
#   \u3040-\u309F → Hiragana (Japanese)
#   \u30A0-\u30FF → Katakana (Japanese)
#   \uAC00-\uD7AF → Hangul Syllables (Korean)
#   \u1100-\u11FF → Hangul Jamo
#   \u3130-\u318F → Hangul Compatibility Jamo
#   \.            → dấu chấm
# Tất cả script khác (Cyrillic, Greek, Arabic, emoji, ...) đều bị chặn.
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