"""
Utilities layer.

Most cogs import symbols from `utils` for convenience.
Internally, implementations live in submodules to match the project diagram.
"""

from .elo import calculate_elo, calculate_elo_detailed, calculate_win_rate  # noqa: F401
from .guards import NAME_MAX_LENGTH, NAME_MIN_LENGTH, NAME_RULE_TEXT, is_admin, is_admin_interaction, is_valid_name  # noqa: F401
from db.h2h_db import (  # noqa: F401
    add_h2h_match,
    get_h2h_stats as get_head_to_head_stats,
    get_most_played_with,
    get_opponent_games,
    rename_opponent_in_h2h,
)
from db.match_history_db import rename_opponent_in_match_history  # noqa: F401
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
from .logger import bot_log  # noqa: F401
from config import PRIVATE_GUILD_ID  # noqa: F401
from .name_sync import sync_against_names  # noqa: F401
from .time_format import format_relative_time  # noqa: F401
