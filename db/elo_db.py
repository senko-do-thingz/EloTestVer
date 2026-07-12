import json
import os
import re
import sqlite3
from contextlib import contextmanager

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
    _safe_alter(cursor, "ALTER TABLE settings ADD COLUMN max_month INTEGER DEFAULT NULL")
    _safe_alter(cursor, "ALTER TABLE settings ADD COLUMN current_season INTEGER DEFAULT 1")
    _safe_alter(cursor, "ALTER TABLE settings ADD COLUMN current_month INTEGER DEFAULT 1")
    _safe_alter(cursor, "ALTER TABLE match_history ADD COLUMN season INTEGER DEFAULT NULL")
    _safe_alter(cursor, "ALTER TABLE match_history ADD COLUMN month INTEGER DEFAULT NULL")

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
    """Set the total number of months a season can have on this server."""
    with db_connection(guild_id) as (conn, cursor):
        cursor.execute(
            """
            INSERT INTO settings (guild_id, max_month)
            VALUES (?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET max_month = excluded.max_month
            """,
            (guild_id, max_month),
        )
        conn.commit()


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