import json
import os
import re
import sqlite3
from contextlib import contextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import date, datetime

DB_FOLDER = "databases"


# ---------------------------------------------------------------------------
# Migration tracking: running ALTER TABLE on every get_database_connection()
# call is wasteful. Use a set to migrate each db path only once per process lifetime.
# ---------------------------------------------------------------------------
_migrated_dbs: set[str] = set()


def get_db_path(guild_id: int | str) -> str:
    """
    Resolve the SQLite database path for a guild/server.

    Uses the same matching idea as ``get_lb_root`` for ``lb/<server_id>/``:
    - ``databases/<guild_id>/*.db``
    - ``databases/<guild_id>.db``
    - ``databases/c_<guild_id>.db``
    - any ``databases/*`` file or subfolder whose name starts with the guild id

    If nothing exists yet, returns ``databases/<guild_id>.db`` (used when creating).
    """
    guild_id_str = str(guild_id)

    subdir = os.path.join(DB_FOLDER, guild_id_str)
    if os.path.isdir(subdir):
        for name in sorted(os.listdir(subdir)):
            if name.endswith(".db"):
                path = os.path.join(subdir, name)
                if os.path.isfile(path):
                    return path

    for candidate in (
        os.path.join(DB_FOLDER, f"{guild_id_str}.db"),
        os.path.join(DB_FOLDER, f"c_{guild_id_str}.db"),
    ):
        if os.path.isfile(candidate):
            return candidate

    if os.path.isdir(DB_FOLDER):
        for entry in sorted(os.listdir(DB_FOLDER)):
            full = os.path.join(DB_FOLDER, entry)
            if os.path.isfile(full) and entry.endswith(".db"):
                match = re.match(r"^(?:c_)?(\d+)", entry)
                if match and match.group(1) == guild_id_str:
                    return full
            if os.path.isdir(full):
                match = re.match(r"^(\d+)", entry)
                if match and match.group(1) == guild_id_str:
                    for name in sorted(os.listdir(full)):
                        if name.endswith(".db"):
                            path = os.path.join(full, name)
                            if os.path.isfile(path):
                                return path

    return os.path.join(DB_FOLDER, f"{guild_id_str}.db")


def list_all_db_paths() -> list[str]:
    """Every guild database file discovered under DB_FOLDER (both the
    ``databases/<guild_id>.db`` / ``databases/c_<guild_id>.db`` layout and
    the ``databases/<guild_id>/*.db`` subfolder layout).

    Used by the scheduled daily-streak refresh task to reach every guild's
    database directly by path, without needing to resolve each one through
    a live guild_id (some guild dbs may belong to servers the bot isn't
    currently a member of, e.g. the dev-mode / cross-server admin tools).
    """
    paths: list[str] = []
    if not os.path.isdir(DB_FOLDER):
        return paths
    for entry in sorted(os.listdir(DB_FOLDER)):
        full = os.path.join(DB_FOLDER, entry)
        if os.path.isfile(full) and entry.endswith(".db"):
            paths.append(full)
        elif os.path.isdir(full):
            for name in sorted(os.listdir(full)):
                if name.endswith(".db"):
                    sub_path = os.path.join(full, name)
                    if os.path.isfile(sub_path):
                        paths.append(sub_path)
    return paths


def db_exists(guild_id: int | str) -> bool:
    """Return True if a database file exists for this guild (after path resolution)."""
    return os.path.isfile(get_db_path(guild_id))


@contextmanager
def db_connection(guild_id: int):
    """
    Context manager that returns (conn, cursor) for a guild.
    Automatically closes when leaving the block, even on exception.

    Usage:
        with db_connection(guild_id) as (conn, cursor):
            cursor.execute(...)
            conn.commit()
    """
    conn, cursor = get_database_connection(guild_id)
    try:
        yield conn, cursor
    finally:
        conn.close()


def get_database_connection(guild_id: int):
    """
    Create (if not already present) and return (conn, cursor) for a guild.

    Call this manually when you need to keep a connection open across
    multiple steps (e.g. a long-running transaction). In normal cases, use
    the db_connection() context manager instead to avoid connection leaks.
    """
    if not os.path.exists(DB_FOLDER):
        os.makedirs(DB_FOLDER)

    db_path = get_db_path(guild_id)
    os.makedirs(os.path.dirname(db_path) or DB_FOLDER, exist_ok=True)

    conn = sqlite3.connect(db_path)
    # WAL mode: allows concurrent reads while writing, reduces contention
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    cursor = conn.cursor()

    # Only run migrations once per db path for the lifetime of the process
    if db_path not in _migrated_dbs:
        _run_migrations(conn, cursor)
        _migrated_dbs.add(db_path)

    return conn, cursor


def _run_migrations(conn: sqlite3.Connection, cursor: sqlite3.Cursor) -> None:
    """Create tables and migrate the schema. Only called once per db."""

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS players (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            elo INTEGER DEFAULT 500,
            games_won INTEGER DEFAULT 0,
            games_lost INTEGER DEFAULT 0,
            games_played_this_month INTEGER DEFAULT 0,
            matches INTEGER DEFAULT 0,
            last_rename INTEGER DEFAULT 0,
            against TEXT DEFAULT '',
            win_streak INTEGER DEFAULT 0,
            highest_win_streak INTEGER DEFAULT 0
        )
        """
    )

    # daily_streaks is intentionally a separate table, keyed by Discord player
    # id and NOT a foreign key of players.id / not touched by "DELETE FROM
    # players" (-c_reg's unregister-everyone wipe). Unlike every other stat
    # (elo, wins, win_streak, ...), which is tied to a *registration* and is
    # expected to reset when a player unregisters, daily streak is meant to
    # track a Discord user's real-world play activity across
    # unregister/re-register cycles, so it survives -c_reg on purpose.
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_streaks (
            player_id TEXT PRIMARY KEY,
            daily_streak INTEGER DEFAULT 0,
            highest_daily_streak INTEGER DEFAULT 0,
            last_played_date TEXT DEFAULT NULL
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS settings (
            guild_id INTEGER PRIMARY KEY,
            language TEXT DEFAULT 'en',
            dev_mode INTEGER DEFAULT 0,
            league_announce_channel_id INTEGER DEFAULT NULL,
            league_announce_channel_ids TEXT DEFAULT NULL,
            max_month INTEGER DEFAULT NULL,
            current_season INTEGER DEFAULT 1,
            current_month INTEGER DEFAULT 1
        )
        """
    )

    from .h2h_db import create_h2h_table
    from .match_history_db import create_match_history_table

    create_h2h_table(cursor)
    create_match_history_table(cursor)

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS leagues (
            name TEXT PRIMARY KEY,
            min_elo INTEGER,
            max_elo INTEGER,
            min_kind TEXT DEFAULT 'n',
            max_kind TEXT DEFAULT 'n',
            role_id INTEGER,
            discovered INTEGER DEFAULT 0
        )
        """
    )

    # Backward-compatible migrations use IGNORE instead of repeated try/except
    _safe_alter(cursor, "ALTER TABLE players ADD COLUMN against TEXT DEFAULT ''")
    _safe_alter(cursor, "ALTER TABLE players ADD COLUMN matches INTEGER DEFAULT 0")
    _safe_alter(cursor, "ALTER TABLE leagues ADD COLUMN min_kind TEXT DEFAULT 'n'")
    _safe_alter(cursor, "ALTER TABLE leagues ADD COLUMN max_kind TEXT DEFAULT 'n'")
    _safe_alter(cursor, "ALTER TABLE leagues ADD COLUMN discovered INTEGER DEFAULT 0")
    _safe_alter(cursor, "ALTER TABLE settings ADD COLUMN dev_mode INTEGER DEFAULT 0")
    _safe_alter(cursor, "ALTER TABLE settings ADD COLUMN league_announce_channel_id INTEGER DEFAULT NULL")
    _safe_alter(cursor, "ALTER TABLE settings ADD COLUMN league_announce_channel_ids TEXT DEFAULT NULL")
    _safe_alter(cursor, "ALTER TABLE settings ADD COLUMN log_channel_id INTEGER DEFAULT NULL")
    _safe_alter(cursor, "ALTER TABLE players ADD COLUMN win_streak INTEGER DEFAULT 0")
    _safe_alter(cursor, "ALTER TABLE players ADD COLUMN highest_win_streak INTEGER DEFAULT 0")
    # One-time cleanup: earlier dev builds briefly added daily_streak columns
    # directly on players before this was split into its own table. Drop them
    # if present so stale per-registration data doesn't linger unused.
    for _col in ("daily_streak", "highest_daily_streak", "last_played_date"):
        try:
            cursor.execute(f"ALTER TABLE players DROP COLUMN {_col}")
        except sqlite3.OperationalError:
            pass
    _safe_alter(cursor, "ALTER TABLE settings ADD COLUMN max_month INTEGER DEFAULT NULL")
    _safe_alter(cursor, "ALTER TABLE settings ADD COLUMN current_season INTEGER DEFAULT 1")
    _safe_alter(cursor, "ALTER TABLE settings ADD COLUMN current_month INTEGER DEFAULT 1")
    _safe_alter(cursor, "ALTER TABLE match_history ADD COLUMN season INTEGER DEFAULT NULL")
    _safe_alter(cursor, "ALTER TABLE match_history ADD COLUMN month INTEGER DEFAULT NULL")
    _safe_alter(cursor, "ALTER TABLE settings ADD COLUMN support_channel_id INTEGER DEFAULT NULL")
    _safe_alter(cursor, "ALTER TABLE settings ADD COLUMN decay_log_channel_id INTEGER DEFAULT NULL")
    _safe_alter(cursor, "ALTER TABLE settings ADD COLUMN nickname TEXT DEFAULT NULL")

    # support_threads: maps a DM support user to the thread created for them
    # under the configured support channel (see -support / /msg). Keyed by
    # (guild_id, user_id) so the same physical database layout used
    # elsewhere still applies even though this feature is only ever used on
    # the private/management guild's database.
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS support_threads (
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            thread_id INTEGER NOT NULL,
            PRIMARY KEY (guild_id, user_id)
        )
        """
    )
    # notify_message_id: the last "your message has been sent" receipt the
    # bot sent this user in DMs. Replying to that specific message lets the
    # user continue the conversation without re-running /msg (see cogs/msg.py).
    _safe_alter(
        cursor, "ALTER TABLE support_threads ADD COLUMN notify_message_id INTEGER DEFAULT NULL"
    )

    # msg_bans: users blocked from using /msg, with the reason given by the
    # admin who ran -ban_msg. Keyed by (guild_id, user_id) like
    # support_threads, always looked up against PRIVATE_GUILD_ID.
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS msg_bans (
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            reason TEXT NOT NULL,
            PRIMARY KEY (guild_id, user_id)
        )
        """
    )

    # season_max_month_history: records every value /months has ever been
    # set to on this server, each tagged with the season it took effect
    # from (the server's current_season at the moment /months was run).
    # This lets date math (e.g. "Recorded on" in /stats expanded) work out
    # how many calendar months actually separate two Season/Months even if
    # an earlier season held a different number of months than the
    # server's current /months setting - see max_month_for_season() below.
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS season_max_month_history (
            guild_id INTEGER NOT NULL,
            effective_season INTEGER NOT NULL,
            max_month INTEGER NOT NULL,
            PRIMARY KEY (guild_id, effective_season)
        )
        """
    )
    # One-time backfill: a server that already had /months set before this
    # table existed has no history of its own yet. Seed a Season 1 entry
    # with its current setting so this server's existing seasons are all
    # still treated consistently (matching prior behavior) until the next
    # actual /months change starts recording real history.
    cursor.execute(
        "SELECT guild_id, max_month FROM settings WHERE max_month IS NOT NULL"
    )
    for _guild_id, _max_month in cursor.fetchall():
        cursor.execute(
            "SELECT 1 FROM season_max_month_history WHERE guild_id=? LIMIT 1",
            (_guild_id,),
        )
        if cursor.fetchone() is None:
            cursor.execute(
                "INSERT INTO season_max_month_history (guild_id, effective_season, max_month) "
                "VALUES (?, 1, ?)",
                (_guild_id, _max_month),
            )

    conn.commit()


def _safe_alter(cursor: sqlite3.Cursor, sql: str) -> None:
    """Run ALTER TABLE, ignoring the error if the column already exists."""
    try:
        cursor.execute(sql)
    except sqlite3.OperationalError:
        pass


# ---------------------------------------------------------------------------
# Settings helpers - all use db_connection() to avoid leaks
# ---------------------------------------------------------------------------

def get_language(guild_id: int) -> str:
    with db_connection(guild_id) as (conn, cursor):
        cursor.execute("SELECT language FROM settings WHERE guild_id=?", (guild_id,))
        result = cursor.fetchone()
        return result[0] if result else "en"


def get_dev_mode(guild_id: int) -> bool:
    with db_connection(guild_id) as (conn, cursor):
        cursor.execute("SELECT dev_mode FROM settings WHERE guild_id=?", (guild_id,))
        row = cursor.fetchone()
        return bool(row and row[0])


def set_dev_mode(guild_id: int, enabled: bool) -> None:
    with db_connection(guild_id) as (conn, cursor):
        cursor.execute(
            """
            INSERT INTO settings (guild_id, language, dev_mode)
            VALUES (
                ?,
                COALESCE((SELECT language FROM settings WHERE guild_id=?), 'en'),
                ?
            )
            ON CONFLICT(guild_id) DO UPDATE SET dev_mode = excluded.dev_mode
            """,
            (guild_id, guild_id, 1 if enabled else 0),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Win streak helpers
#
# Win streak is a display-only stat: it never affects Elo math. A player's
# current streak increments on every match win and resets to 0 on a loss.
# The highest streak they have ever reached is kept separately so it can be
# shown even after the current streak resets.
# ---------------------------------------------------------------------------

def apply_win_streak(cursor, player_id: str | int, won: bool) -> tuple[int, int]:
    """Update a player's current/highest win streak after a match result.

    Returns the (win_streak, highest_win_streak) tuple after the update.
    """
    cursor.execute(
        "SELECT win_streak, highest_win_streak FROM players WHERE id=?",
        (str(player_id),),
    )
    row = cursor.fetchone()
    current = row[0] if row and row[0] is not None else 0
    highest = row[1] if row and row[1] is not None else 0

    if won:
        current += 1
        highest = max(highest, current)
    else:
        current = 0

    cursor.execute(
        "UPDATE players SET win_streak=?, highest_win_streak=? WHERE id=?",
        (current, highest, str(player_id)),
    )
    return current, highest


def get_win_streak(cursor, player_id: str | int) -> tuple[int, int]:
    """Return (win_streak, highest_win_streak) for a player, defaulting to (0, 0)."""
    cursor.execute(
        "SELECT win_streak, highest_win_streak FROM players WHERE id=?",
        (str(player_id),),
    )
    row = cursor.fetchone()
    if not row:
        return 0, 0
    return (row[0] or 0), (row[1] or 0)


# ---------------------------------------------------------------------------
# Daily streak helpers
#
# Daily streak tracks how many consecutive calendar days (UTC) in a row a
# player has played at least one set. It never affects Elo math. Every time
# a match is reported, both players' daily streak is updated:
# - Same UTC day as their last played date -> no change (already counted).
# - Exactly one UTC day after their last played date -> streak +1.
# - Any other gap (or first ever match) -> streak resets to 1.
# The highest streak ever reached is kept separately, same pattern as
# highest_win_streak.
#
# Unlike win_streak, this lives in its own daily_streaks table (keyed by
# Discord player id, not a foreign key of players.id) so it survives
# -c_reg / unregistering - it tracks a Discord user's activity, not a
# registration's stats.
# ---------------------------------------------------------------------------

def apply_daily_streak(
    cursor, player_id: str | int, played_at: "datetime" = None
) -> tuple[int, int]:
    """Update a player's current/highest daily streak after a match is played.

    Returns the (daily_streak, highest_daily_streak) tuple after the update.
    """
    from datetime import datetime, timezone

    if played_at is None:
        played_at = datetime.now(timezone.utc)
    today = played_at.strftime("%Y-%m-%d")

    cursor.execute(
        "SELECT daily_streak, highest_daily_streak, last_played_date FROM daily_streaks WHERE player_id=?",
        (str(player_id),),
    )
    row = cursor.fetchone()
    current = row[0] if row and row[0] is not None else 0
    highest = row[1] if row and row[1] is not None else 0
    last_played_date = row[2] if row else None

    if last_played_date == today:
        # Already played today - streak already accounted for.
        pass
    else:
        if last_played_date is not None:
            last_date = datetime.strptime(last_played_date, "%Y-%m-%d")
            day_gap = (played_at.date() - last_date.date()).days
        else:
            day_gap = None

        if day_gap == 1:
            current += 1
        else:
            current = 1
        highest = max(highest, current)

    cursor.execute(
        """
        INSERT INTO daily_streaks (player_id, daily_streak, highest_daily_streak, last_played_date)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(player_id) DO UPDATE SET
            daily_streak = excluded.daily_streak,
            highest_daily_streak = excluded.highest_daily_streak,
            last_played_date = excluded.last_played_date
        """,
        (str(player_id), current, highest, today),
    )
    return current, highest


def get_daily_streak(cursor, player_id: str | int) -> tuple[int, int]:
    """Return (daily_streak, highest_daily_streak) for a player, defaulting to (0, 0)."""
    cursor.execute(
        "SELECT daily_streak, highest_daily_streak FROM daily_streaks WHERE player_id=?",
        (str(player_id),),
    )
    row = cursor.fetchone()
    if not row:
        return 0, 0
    return (row[0] or 0), (row[1] or 0)


def get_effective_daily_streak(cursor, player_id: str | int) -> int:
    """Return the player's daily streak as it should be *displayed* right now.

    The stored ``daily_streak`` value is only updated when a match is
    reported, so a player who missed a day without playing would still show
    their old streak until their next match. This lazily zeroes the streak
    out (without needing a background/cron task) once more than one UTC day
    has passed since they last played: still within the grace period (played
    yesterday, haven't played yet today) shows the stored value; missing a
    full day shows 0.
    """
    from datetime import datetime, timezone

    cursor.execute(
        "SELECT daily_streak, last_played_date FROM daily_streaks WHERE player_id=?",
        (str(player_id),),
    )
    row = cursor.fetchone()
    if not row or not row[1]:
        return 0

    streak = row[0] or 0
    last_date = datetime.strptime(row[1], "%Y-%m-%d").date()
    today = datetime.now(timezone.utc).date()
    day_gap = (today - last_date).days

    if day_gap > 1:
        return 0
    return streak


def refresh_daily_streaks_for_db(db_path: str, *, today: "date | None" = None) -> int:
    """Zero out ``daily_streak`` for every player in one guild database whose
    streak is stale - i.e. they didn't play at all on the UTC day right
    before ``today``. Returns the number of rows reset.

    This is the durable counterpart to ``get_effective_daily_streak()``
    (which only *displays* a stale streak as 0 without touching the
    database). It's meant to be called once a day, right after UTC
    midnight, by the scheduled task in cogs/daily_streak_refresh.py - see
    ``refresh_all_daily_streaks()`` below, which runs this across every
    guild database.
    """
    from datetime import datetime, timedelta, timezone

    if today is None:
        today = datetime.now(timezone.utc).date()
    # Any daily_streaks row whose last_played_date is older than yesterday
    # means the player missed an entire UTC day without playing - reset it.
    # A last_played_date of exactly yesterday is still within the grace
    # period (they have all of "today" left to keep the streak alive).
    threshold = (today - timedelta(days=1)).isoformat()

    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE daily_streaks SET daily_streak = 0 "
            "WHERE last_played_date < ? AND daily_streak != 0",
            (threshold,),
        )
        reset_count = cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0
        conn.commit()
        return reset_count
    except sqlite3.OperationalError:
        # daily_streaks table doesn't exist in this db yet (e.g. a very old
        # db file that was never touched after this feature was added) -
        # nothing to refresh.
        return 0
    finally:
        conn.close()


def refresh_all_daily_streaks() -> tuple[int, int]:
    """Run ``refresh_daily_streaks_for_db()`` across every guild database
    found under DB_FOLDER. Returns (databases_processed, total_rows_reset).
    """
    from datetime import datetime, timezone

    today = datetime.now(timezone.utc).date()
    dbs_processed = 0
    total_reset = 0
    for path in list_all_db_paths():
        dbs_processed += 1
        total_reset += refresh_daily_streaks_for_db(path, today=today)
    return dbs_processed, total_reset


# ---------------------------------------------------------------------------
# Season / month helpers
#
# ``max_month`` is the total number of months a season can hold on this
# server, set once via /months (e.g. 3 -> month 1, 2, 3 per season). Every
# server starts at season 1, month 1 until an admin changes it with /season.
# ---------------------------------------------------------------------------

def get_max_month(guild_id: int) -> int | None:
    """Return the configured max month per season, or None if /months was never run."""
    with db_connection(guild_id) as (conn, cursor):
        cursor.execute("SELECT max_month FROM settings WHERE guild_id=?", (guild_id,))
        row = cursor.fetchone()
        return row[0] if row and row[0] is not None else None


def set_max_month(guild_id: int, max_month: int) -> None:
    """Set the total number of months a season can have on this server.

    Also records this change in season_max_month_history, tagged with the
    server's current season at the time of the call, so a later change
    (e.g. moving from 3 to 5 months per season starting Season 6) doesn't
    retroactively make earlier seasons look like they had 5 months too -
    see max_month_for_season().
    """
    with db_connection(guild_id) as (conn, cursor):
        cursor.execute(
            "SELECT current_season FROM settings WHERE guild_id=?", (guild_id,)
        )
        row = cursor.fetchone()
        current_season = row[0] if row and row[0] is not None else 1

        cursor.execute(
            """
            INSERT INTO settings (guild_id, max_month)
            VALUES (?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET max_month = excluded.max_month
            """,
            (guild_id, max_month),
        )
        cursor.execute(
            """
            INSERT INTO season_max_month_history (guild_id, effective_season, max_month)
            VALUES (?, ?, ?)
            ON CONFLICT(guild_id, effective_season) DO UPDATE SET max_month = excluded.max_month
            """,
            (guild_id, current_season, max_month),
        )
        conn.commit()


def get_max_month_history(guild_id: int) -> list[tuple[int, int]]:
    """Return every recorded /months change for this server, as
    (effective_season, max_month) pairs sorted by effective_season
    ascending. The value recorded at a given effective_season applies to
    every season from then on, until the next recorded entry - see
    max_month_for_season()."""
    with db_connection(guild_id) as (conn, cursor):
        cursor.execute(
            "SELECT effective_season, max_month FROM season_max_month_history "
            "WHERE guild_id=? ORDER BY effective_season ASC",
            (guild_id,),
        )
        return cursor.fetchall()


def max_month_for_season(guild_id: int, season: int) -> int | None:
    """Return how many months `season` actually held on this server,
    based on the recorded /months history rather than assuming every
    season held the server's current max_month setting.

    Falls back to the earliest recorded setting for any season before the
    first recorded change, and to the plain current max_month if this
    server has no history at all (e.g. /months was never run).
    """
    history = get_max_month_history(guild_id)
    if not history:
        return get_max_month(guild_id)

    applicable = None
    for effective_season, max_month in history:
        if effective_season <= season:
            applicable = max_month
        else:
            break
    return applicable if applicable is not None else history[0][1]


def get_season_month(guild_id: int) -> tuple[int, int]:
    """Return (current_season, current_month) for this server, defaulting to (1, 1)."""
    with db_connection(guild_id) as (conn, cursor):
        cursor.execute(
            "SELECT current_season, current_month FROM settings WHERE guild_id=?",
            (guild_id,),
        )
        row = cursor.fetchone()
        if not row:
            return 1, 1
        season = row[0] if row[0] is not None else 1
        month = row[1] if row[1] is not None else 1
        return season, month


def get_season_month_cursor(cursor, guild_id: int) -> tuple[int, int]:
    """Cursor-based variant of get_season_month(), for use inside an already
    open transaction (e.g. while reporting a match) to avoid nested connections."""
    cursor.execute(
        "SELECT current_season, current_month FROM settings WHERE guild_id=?",
        (guild_id,),
    )
    row = cursor.fetchone()
    if not row:
        return 1, 1
    season = row[0] if row[0] is not None else 1
    month = row[1] if row[1] is not None else 1
    return season, month


def set_season_month(guild_id: int, season: int, month: int) -> None:
    """Directly set the current season/month for this server (used by /season)."""
    with db_connection(guild_id) as (conn, cursor):
        cursor.execute(
            """
            INSERT INTO settings (guild_id, current_season, current_month)
            VALUES (?, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                current_season = excluded.current_season,
                current_month = excluded.current_month
            """,
            (guild_id, season, month),
        )
        conn.commit()


def advance_season_month_cursor(cursor, guild_id: int) -> tuple[int, int, int, int]:
    """Advance the current month by 1 (used by -decay), rolling over into the
    next season when the month would exceed the server's configured max_month.

    Runs on an already-open cursor so it can be part of a larger transaction
    (caller is responsible for committing). Returns
    (old_season, old_month, new_season, new_month).
    """
    cursor.execute(
        "SELECT max_month, current_season, current_month FROM settings WHERE guild_id=?",
        (guild_id,),
    )
    row = cursor.fetchone()
    max_month = row[0] if row and row[0] is not None else None
    old_season = row[1] if row and row[1] is not None else 1
    old_month = row[2] if row and row[2] is not None else 1

    new_month = old_month + 1
    new_season = old_season
    if max_month is not None and new_month > max_month:
        new_season += 1
        new_month = 1

    cursor.execute(
        """
        INSERT INTO settings (guild_id, current_season, current_month)
        VALUES (?, ?, ?)
        ON CONFLICT(guild_id) DO UPDATE SET
            current_season = excluded.current_season,
            current_month = excluded.current_month
        """,
        (guild_id, new_season, new_month),
    )
    return old_season, old_month, new_season, new_month


def get_log_channel_id(guild_id: int) -> int | None:
    with db_connection(guild_id) as (conn, cursor):
        cursor.execute(
            "SELECT log_channel_id FROM settings WHERE guild_id=?", (guild_id,)
        )
        row = cursor.fetchone()
        return row[0] if row and row[0] is not None else None


def set_log_channel_id(guild_id: int, channel_id: int | None) -> None:
    with db_connection(guild_id) as (conn, cursor):
        cursor.execute(
            """
            INSERT INTO settings (guild_id, log_channel_id)
            VALUES (?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET log_channel_id = excluded.log_channel_id
            """,
            (guild_id, channel_id),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Support channel / thread mapping helpers (-support, /msg)
# ---------------------------------------------------------------------------

def get_support_channel_id(guild_id: int) -> int | None:
    """Return the configured destination channel for DM support relays."""
    with db_connection(guild_id) as (conn, cursor):
        cursor.execute(
            "SELECT support_channel_id FROM settings WHERE guild_id=?", (guild_id,)
        )
        row = cursor.fetchone()
        return row[0] if row and row[0] is not None else None


def set_support_channel_id(guild_id: int, channel_id: int | None) -> None:
    with db_connection(guild_id) as (conn, cursor):
        cursor.execute(
            """
            INSERT INTO settings (guild_id, support_channel_id)
            VALUES (?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET support_channel_id = excluded.support_channel_id
            """,
            (guild_id, channel_id),
        )
        conn.commit()


def get_support_thread_id(guild_id: int, user_id: int) -> int | None:
    """Return the thread ID previously created for this user's DM support
    conversation, or None if no thread has been created yet."""
    with db_connection(guild_id) as (conn, cursor):
        cursor.execute(
            "SELECT thread_id FROM support_threads WHERE guild_id=? AND user_id=?",
            (guild_id, user_id),
        )
        row = cursor.fetchone()
        return row[0] if row else None


def set_support_thread_id(guild_id: int, user_id: int, thread_id: int) -> None:
    with db_connection(guild_id) as (conn, cursor):
        cursor.execute(
            """
            INSERT INTO support_threads (guild_id, user_id, thread_id)
            VALUES (?, ?, ?)
            ON CONFLICT(guild_id, user_id) DO UPDATE SET thread_id = excluded.thread_id
            """,
            (guild_id, user_id, thread_id),
        )
        conn.commit()


def get_support_user_id_by_thread(guild_id: int, thread_id: int) -> int | None:
    """Reverse of get_support_thread_id: given a support thread, return the
    ID of the user it was created for, or None if it isn't a known support
    thread. Used to relay staff replies in the thread back to the user."""
    with db_connection(guild_id) as (conn, cursor):
        cursor.execute(
            "SELECT user_id FROM support_threads WHERE guild_id=? AND thread_id=?",
            (guild_id, thread_id),
        )
        row = cursor.fetchone()
        return row[0] if row else None


def get_msg_ban_reason(guild_id: int, user_id: int) -> str | None:
    """Return the reason this user was banned from /msg, or None if they
    aren't currently banned."""
    with db_connection(guild_id) as (conn, cursor):
        cursor.execute(
            "SELECT reason FROM msg_bans WHERE guild_id=? AND user_id=?",
            (guild_id, user_id),
        )
        row = cursor.fetchone()
        return row[0] if row else None


def set_msg_ban(guild_id: int, user_id: int, reason: str) -> None:
    """Ban a user from using /msg (see -ban_msg)."""
    with db_connection(guild_id) as (conn, cursor):
        cursor.execute(
            """
            INSERT INTO msg_bans (guild_id, user_id, reason)
            VALUES (?, ?, ?)
            ON CONFLICT(guild_id, user_id) DO UPDATE SET reason = excluded.reason
            """,
            (guild_id, user_id, reason),
        )
        conn.commit()


def clear_msg_ban(guild_id: int, user_id: int) -> bool:
    """Unban a user from /msg (see -unban_msg). Returns True if they were
    actually banned, False if there was nothing to clear."""
    with db_connection(guild_id) as (conn, cursor):
        cursor.execute(
            "DELETE FROM msg_bans WHERE guild_id=? AND user_id=?",
            (guild_id, user_id),
        )
        conn.commit()
        return cursor.rowcount > 0


def get_support_notify_message_id(guild_id: int, user_id: int) -> int | None:
    """Return the ID of the last support "message sent" receipt the bot sent
    this user, or None if there isn't one yet. Used to check whether an
    incoming DM reply is continuing that conversation."""
    with db_connection(guild_id) as (conn, cursor):
        cursor.execute(
            "SELECT notify_message_id FROM support_threads WHERE guild_id=? AND user_id=?",
            (guild_id, user_id),
        )
        row = cursor.fetchone()
        return row[0] if row and row[0] is not None else None


def set_support_notify_message_id(guild_id: int, user_id: int, message_id: int | None) -> None:
    """Remember the receipt message so a later reply to it can be relayed.
    Pass message_id=None to clear it (e.g. once the 30-minute session
    times out), so a stray reply to the old receipt no longer relays.
    Requires a support_threads row to already exist (i.e. a thread was
    already created for this user), since it just updates that row."""
    with db_connection(guild_id) as (conn, cursor):
        cursor.execute(
            """
            UPDATE support_threads SET notify_message_id = ?
            WHERE guild_id = ? AND user_id = ?
            """,
            (message_id, guild_id, user_id),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Decay log channel helper (-decay_setup)
# ---------------------------------------------------------------------------

def get_decay_log_channel_id(guild_id: int) -> int | None:
    """Return the per-server channel where automatic monthly -decay change
    logs are posted, or None if -decay_setup was never run on this server."""
    with db_connection(guild_id) as (conn, cursor):
        cursor.execute(
            "SELECT decay_log_channel_id FROM settings WHERE guild_id=?", (guild_id,)
        )
        row = cursor.fetchone()
        return row[0] if row and row[0] is not None else None


def set_decay_log_channel_id(guild_id: int, channel_id: int | None) -> None:
    with db_connection(guild_id) as (conn, cursor):
        cursor.execute(
            """
            INSERT INTO settings (guild_id, decay_log_channel_id)
            VALUES (?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET decay_log_channel_id = excluded.decay_log_channel_id
            """,
            (guild_id, channel_id),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Server nickname helper (-nickname)
#
# Used purely for naming files sent by -get_lb, -sum_lb and the automatic
# -decay task, so archives are easier to tell apart than a raw server ID.
# ---------------------------------------------------------------------------

def get_nickname(guild_id: int) -> str | None:
    with db_connection(guild_id) as (conn, cursor):
        cursor.execute("SELECT nickname FROM settings WHERE guild_id=?", (guild_id,))
        row = cursor.fetchone()
        return row[0] if row and row[0] else None


def set_nickname(guild_id: int, name: str) -> None:
    with db_connection(guild_id) as (conn, cursor):
        cursor.execute(
            """
            INSERT INTO settings (guild_id, nickname)
            VALUES (?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET nickname = excluded.nickname
            """,
            (guild_id, name),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Guild-id enumeration, used by the automatic monthly -decay task to walk
# every known guild database (mirrors list_all_db_paths()'s file discovery,
# but returns the guild id instead of the file path).
# ---------------------------------------------------------------------------

def list_all_guild_ids() -> list[int]:
    """Return every guild ID discovered under DB_FOLDER (both the
    ``databases/<guild_id>.db`` / ``databases/c_<guild_id>.db`` layout and
    the ``databases/<guild_id>/*.db`` subfolder layout), based on
    list_all_db_paths()'s file discovery.

    Used by the automatic monthly -decay task to walk every known guild
    database without needing to resolve each one through a live guild_id.
    """
    guild_ids: set[int] = set()
    for path in list_all_db_paths():
        # The guild id is either the containing subfolder's name
        # (databases/<guild_id>/x.db) or the leading digits of the file
        # name itself (databases/<guild_id>.db / databases/c_<guild_id>.db).
        parent = os.path.basename(os.path.dirname(path))
        if parent.isdigit():
            guild_ids.add(int(parent))
            continue
        filename = os.path.basename(path)
        match = re.match(r"^(?:c_)?(\d+)", filename)
        if match:
            guild_ids.add(int(match.group(1)))
    return sorted(guild_ids)