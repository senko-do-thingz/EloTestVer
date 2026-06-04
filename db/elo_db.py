import json
import os
import re
import sqlite3
from contextlib import contextmanager

DB_FOLDER = "databases"


# ---------------------------------------------------------------------------
# Migration tracking: chạy ALTER TABLE mỗi lần get_database_connection() là
# lãng phí. Dùng set để chỉ migrate mỗi db path một lần trong lifetime process.
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
    Context manager trả về (conn, cursor) cho guild.
    Tự động close khi thoát khỏi block, kể cả khi có exception.

    Dùng:
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
    Tạo (nếu chưa có) và trả về (conn, cursor) cho guild.

    Gọi thủ công khi cần giữ connection qua nhiều bước (vd: long-running
    transaction). Trong các trường hợp thông thường, dùng db_connection()
    context manager để tránh connection leak.
    """
    if not os.path.exists(DB_FOLDER):
        os.makedirs(DB_FOLDER)

    db_path = get_db_path(guild_id)
    os.makedirs(os.path.dirname(db_path) or DB_FOLDER, exist_ok=True)

    conn = sqlite3.connect(db_path)
    # WAL mode: cho phép đọc đồng thời với ghi, giảm contention
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    cursor = conn.cursor()

    # Chỉ chạy migration một lần mỗi db path trong suốt lifetime của process
    if db_path not in _migrated_dbs:
        _run_migrations(conn, cursor)
        _migrated_dbs.add(db_path)

    return conn, cursor


def _run_migrations(conn: sqlite3.Connection, cursor: sqlite3.Cursor) -> None:
    """Tạo bảng và migrate schema. Chỉ được gọi một lần mỗi db."""

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS players (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            elo INTEGER DEFAULT 500,
            games_won INTEGER DEFAULT 0,
            games_lost INTEGER DEFAULT 0,
            games_played_this_month INTEGER DEFAULT 0,
            last_rename INTEGER DEFAULT 0,
            against TEXT DEFAULT ''
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS settings (
            guild_id INTEGER PRIMARY KEY,
            language TEXT DEFAULT 'en',
            seasonal_report_enabled INTEGER DEFAULT 0,
            dev_mode INTEGER DEFAULT 0,
            league_announce_channel_id INTEGER DEFAULT NULL,
            league_announce_channel_ids TEXT DEFAULT NULL
        )
        """
    )

    from .h2h_db import create_h2h_table

    create_h2h_table(cursor)

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

    # Backward-compatible migrations dùng IGNORE thay vì try/except lặp lại
    _safe_alter(cursor, "ALTER TABLE players ADD COLUMN against TEXT DEFAULT ''")
    _safe_alter(cursor, "ALTER TABLE leagues ADD COLUMN min_kind TEXT DEFAULT 'n'")
    _safe_alter(cursor, "ALTER TABLE leagues ADD COLUMN max_kind TEXT DEFAULT 'n'")
    _safe_alter(cursor, "ALTER TABLE leagues ADD COLUMN discovered INTEGER DEFAULT 0")
    _safe_alter(cursor, "ALTER TABLE settings ADD COLUMN seasonal_report_enabled INTEGER DEFAULT 0")
    _safe_alter(cursor, "ALTER TABLE settings ADD COLUMN dev_mode INTEGER DEFAULT 0")
    _safe_alter(cursor, "ALTER TABLE settings ADD COLUMN league_announce_channel_id INTEGER DEFAULT NULL")
    _safe_alter(cursor, "ALTER TABLE settings ADD COLUMN league_announce_channel_ids TEXT DEFAULT NULL")
    _safe_alter(cursor, "ALTER TABLE settings ADD COLUMN log_channel_id INTEGER DEFAULT NULL")

    conn.commit()


def _safe_alter(cursor: sqlite3.Cursor, sql: str) -> None:
    """Thực thi ALTER TABLE, bỏ qua nếu column đã tồn tại."""
    try:
        cursor.execute(sql)
    except sqlite3.OperationalError:
        pass


# ---------------------------------------------------------------------------
# Settings helpers – đều dùng db_connection() để không leak
# ---------------------------------------------------------------------------

def get_language(guild_id: int) -> str:
    with db_connection(guild_id) as (conn, cursor):
        cursor.execute("SELECT language FROM settings WHERE guild_id=?", (guild_id,))
        result = cursor.fetchone()
        return result[0] if result else "en"


def get_seasonal_report_enabled(guild_id: int) -> bool:
    with db_connection(guild_id) as (conn, cursor):
        cursor.execute(
            "SELECT seasonal_report_enabled FROM settings WHERE guild_id=?",
            (guild_id,),
        )
        row = cursor.fetchone()
        return bool(row and row[0])


def set_seasonal_report_enabled(guild_id: int, enabled: bool) -> None:
    with db_connection(guild_id) as (conn, cursor):
        cursor.execute(
            """
            INSERT INTO settings (guild_id, language, seasonal_report_enabled)
            VALUES (?, COALESCE((SELECT language FROM settings WHERE guild_id=?), 'en'), ?)
            ON CONFLICT(guild_id) DO UPDATE SET seasonal_report_enabled = excluded.seasonal_report_enabled
            """,
            (guild_id, guild_id, 1 if enabled else 0),
        )
        conn.commit()


def get_dev_mode(guild_id: int) -> bool:
    with db_connection(guild_id) as (conn, cursor):
        cursor.execute("SELECT dev_mode FROM settings WHERE guild_id=?", (guild_id,))
        row = cursor.fetchone()
        return bool(row and row[0])


def set_dev_mode(guild_id: int, enabled: bool) -> None:
    with db_connection(guild_id) as (conn, cursor):
        cursor.execute(
            """
            INSERT INTO settings (guild_id, language, seasonal_report_enabled, dev_mode)
            VALUES (
                ?,
                COALESCE((SELECT language FROM settings WHERE guild_id=?), 'en'),
                COALESCE((SELECT seasonal_report_enabled FROM settings WHERE guild_id=?), 0),
                ?
            )
            ON CONFLICT(guild_id) DO UPDATE SET dev_mode = excluded.dev_mode
            """,
            (guild_id, guild_id, guild_id, 1 if enabled else 0),
        )
        conn.commit()


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