"""
Utilities layer.

Most cogs import symbols from `utils` for convenience.
Internally, implementations live in submodules to match the project diagram.
"""

from .elo import calculate_elo, calculate_win_rate  # noqa: F401
from .guards import is_admin, is_valid_name  # noqa: F401
from db.h2h_db import (  # noqa: F401
    add_h2h_match,
    get_h2h_stats as get_head_to_head_stats,
    get_most_played_with,
    get_opponent_games,
    rename_opponent_in_h2h,
)
from .lb_archive import (  # noqa: F401
    LB_ARCHIVE_FILES,
    format_leaderboard_export_line,
    get_lb_root,
    parse_leaderboard_line,
    sync_discord_ids_to_lb_archives,
    update_lb_name,
)
from .league import (  # noqa: F401
    add_league_announce_channel_id,
    format_league_stat_line,
    get_league_announce_channel_ids,
    get_leagues,
    pick_league_role_id_for_elo,
    remove_league_announce_channel_id,
    set_leagues,
    sync_league_roles,
)
from .rendering import render_leaderboard_image  # noqa: F401
from .logger import bot_log, PRIVATE_GUILD_ID  # noqa: F401


def sync_against_names(cursor, old_name: str, new_name: str) -> None:
    """
    Rename opponent_name in h2h_stats when a player's display name changes.

    Caller is responsible for committing the transaction.
    """
    rename_opponent_in_h2h(cursor, old_name, new_name)