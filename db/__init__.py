"""
Database access layer.

All SQLite queries live in `db/`.
Command modules (cogs) must never use sqlite3 directly.
"""

from .elo_db import (  # noqa: F401
    clear_msg_ban,
    db_connection,
    db_exists,
    get_database_connection,
    get_db_path,
    get_decay_log_channel_id,
    get_dev_mode,
    get_language,
    get_log_channel_id,
    get_msg_ban_reason,
    get_nickname,
    get_support_channel_id,
    get_support_notify_message_id,
    get_support_thread_id,
    get_support_user_id_by_thread,
    list_all_guild_ids,
    set_decay_log_channel_id,
    set_dev_mode,
    set_log_channel_id,
    set_msg_ban,
    set_nickname,
    set_support_channel_id,
    set_support_notify_message_id,
    set_support_thread_id,
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