"""Reusable parsing for compact game-set scores.

Keep this independent of Discord so both report command formats can share
identical numeric parsing while their validation and error messages stay in
cogs.reporting.
"""

import re


def parse_combined_score(score: str) -> tuple[int, int] | None:
    """Parse 31, 3 1, 3-1, or 3:1 using the original single-digit rules."""
    match = re.match(r"(\\d)\\s*(\\d)$", score) or re.match(
        r"(\\d)\\s*[-:]\\s*(\\d)$", score
    )
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))
