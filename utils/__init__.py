"""
Utilities layer.

Most cogs import symbols from `utils` for convenience.
Internally, implementations live in submodules to match the project diagram.
"""

from datetime import datetime, timezone
from .elo import calculate_elo, calculate_win_rate  # noqa: F401
from .guards import NAME_RULE_TEXT, is_admin, is_admin_interaction, is_valid_name  # noqa: F401
from db.h2h_db import (  # noqa: F401
    add_h2h_match,
    get_h2h_stats as get_head_to_head_stats,
    get_most_played_with,
    get_opponent_games,
    rename_opponent_in_h2h,
)
from db.match_history_db import rename_opponent_in_match_history
from .lb_archive import (  # noqa: F401
    LB_ARCHIVE_FILES,
    format_leaderboard_export_line,
    get_lb_root,
    parse_leaderboard_line,
    sync_discord_ids_to_lb_archives,
    update_lb_name,
)
from .lb_export import (  # noqa: F401
    aggregate_season_categories,
    build_leaderboard_categories,
    build_zip_bytes,
    write_leaderboard_files,
)
from .league import (  # noqa: F401
    add_league_announce_channel_id,
    format_league_stat_line,
    get_league_announce_channel_ids,
    get_league_announce_channels,
    get_leagues,
    pick_league_role_id_for_elo,
    remove_league_announce_channel_id,
    set_leagues,
    sync_league_roles,
)
from .rendering import (  # noqa: F401
    render_compare_image,
    render_compare_image_async,
    render_elo_graph_image,
    render_elo_graph_image_async,
    render_history_image,
    render_history_image_async,
    render_leaderboard_image,
    render_leaderboard_image_async,
    truncate_cjk_name,
)
from .elo_graph import (  # noqa: F401
    build_calendar_series,
    build_daily_series,
    build_match_series,
    calendar_axis_bounds,
    compute_month_averages,
    find_rest_gaps,
    rest_gap_fractions,
)
from .logger import bot_log, PRIVATE_GUILD_ID  # noqa: F401


def sync_against_names(cursor, old_name: str, new_name: str) -> None:
    """
    Rename opponent_name in h2h_stats and match_history when a player's
    display name changes.

    Caller is responsible for committing the transaction.
    """
    rename_opponent_in_h2h(cursor, old_name, new_name)
    rename_opponent_in_match_history(cursor, old_name, new_name)


def format_relative_time(dt: datetime) -> str:
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    diff = now - dt
    seconds = int(diff.total_seconds())
    if seconds < 0:
        seconds = 0
    
    if seconds < 60:
        return f"{seconds} seconds ago" if seconds > 1 else "just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} minutes ago" if minutes > 1 else "1 minute ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} hours ago" if hours > 1 else "1 hour ago"
    days = hours // 24
    return f"{days} days ago" if days > 1 else "1 day ago"