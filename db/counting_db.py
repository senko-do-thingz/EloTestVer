import contextlib
import os
import sqlite3


@contextlib.contextmanager
def counting_db(guild_id: int):
    """
    Context manager that yields a (conn, cursor) pair for the per-guild
    counting database, ensuring the connection is always closed on exit.

    Database files follow the naming convention ``c_<guild_id>.db`` and are
    stored inside the ``databases/<guild_id>_*/`` directory structure that the
    rest of the bot uses.  The file is placed directly inside ``databases/``
    (flat layout) so callers that already know the path can still find it.

    Usage::

        with counting_db(guild.id) as (conn, cursor):
            cursor.execute(...)
            conn.commit()
    """
    db_folder = "databases"
    db_path = f"{db_folder}/c_{guild_id}.db"

    os.makedirs(db_folder, exist_ok=True)

    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        _ensure_schema(cursor)
        conn.commit()
        yield conn, cursor
    finally:
        conn.close()


def _ensure_schema(cursor: sqlite3.Cursor) -> None:
    """Create counting tables if they do not yet exist.

    Uses individual execute() calls instead of executescript() because
    executescript() issues an implicit COMMIT before running, which would
    commit any in-progress transaction on the caller's connection.
    """
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS channels (
            channel_id INTEGER PRIMARY KEY,
            current    INTEGER NOT NULL DEFAULT 1,
            last_user  INTEGER
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS cooldowns (
            user_id    INTEGER PRIMARY KEY,
            last_count INTEGER NOT NULL
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS counting_stats (
            channel_id   INTEGER NOT NULL,
            user_id      INTEGER NOT NULL,
            total_counts INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (channel_id, user_id)
        )
        """
    )


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def increment_counting_stat(
    guild_id: int, channel_id: int, user_id: int, delta: int = 1
) -> None:
    """Increment the valid-count tally for *user_id* in *channel_id*."""
    with counting_db(guild_id) as (conn, cursor):
        cursor.execute(
            """
            INSERT INTO counting_stats (channel_id, user_id, total_counts)
            VALUES (?, ?, ?)
            ON CONFLICT(channel_id, user_id)
            DO UPDATE SET total_counts = total_counts + ?
            """,
            (channel_id, user_id, delta, delta),
        )
        conn.commit()


def get_counting_leaderboard(
    guild_id: int, channel_id: int, limit: int = 10
) -> list[tuple[int, int]]:
    """
    Return the top *limit* counters for *channel_id*, sorted by count desc.
    Each row is ``(user_id, total_counts)``.
    """
    with counting_db(guild_id) as (conn, cursor):
        cursor.execute(
            """
            SELECT user_id, total_counts
            FROM counting_stats
            WHERE channel_id = ?
            ORDER BY total_counts DESC, user_id ASC
            LIMIT ?
            """,
            (channel_id, limit),
        )
        return cursor.fetchall()


def reset_counting_stats(guild_id: int, channel_id: int) -> None:
    """Delete all counting stats for *channel_id* (e.g. after a rule violation)."""
    with counting_db(guild_id) as (conn, cursor):
        cursor.execute(
            "DELETE FROM counting_stats WHERE channel_id = ?",
            (channel_id,),
        )
        conn.commit()