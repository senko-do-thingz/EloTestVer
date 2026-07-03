"""
Database access layer.

All SQLite queries live in `db/`.
Command modules (cogs) must never use sqlite3 directly.
"""

from .elo_db import (  # noqa: F401
    db_connection,
    db_exists,
    get_database_connection,
    get_db_path,
    get_dev_mode,
    get_language,
    get_log_channel_id,
    set_dev_mode,
    set_log_channel_id,
)
from .h2h_db import (  # noqa: F401
    add_h2h_match,
    clear_legacy_against_column,
    get_h2h_stats,
    get_most_played_with,
    get_opponent_games,
    has_legacy_against_data,
    migrate_legacy_against_column,
    rename_opponent_in_h2h,
)
from .counting_db import (  # noqa: F401
    counting_db,
    get_counting_leaderboard,
    increment_counting_stat,
    reset_counting_stats,
)