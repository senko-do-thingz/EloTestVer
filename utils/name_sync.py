"""Keep opponent display names consistent across historical database records."""

from db.h2h_db import rename_opponent_in_h2h
from db.match_history_db import rename_opponent_in_match_history


def sync_against_names(cursor, old_name: str, new_name: str) -> None:
    """Rename opponent references in H2H and match history in one transaction.

    The caller owns the connection and must commit the transaction.
    """
    rename_opponent_in_h2h(cursor, old_name, new_name)
    rename_opponent_in_match_history(cursor, old_name, new_name)
