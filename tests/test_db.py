"""
Tests for database layer (db/elo_db.py, db/h2h_db.py).
Uses temporary in-memory / temp-file SQLite so production data is untouched.
Run: pytest tests/test_db.py -v
"""
import sys
import os
import sqlite3
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from db.h2h_db import (
    create_h2h_table,
    get_h2h_stats,
    add_h2h_match,
    get_most_played_with,
    rename_opponent_in_h2h,
    migrate_legacy_against_column,
    has_legacy_against_data,
    parse_against_entries,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def in_memory_db():
    """Fresh in-memory SQLite with h2h_stats and a minimal players table."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys=ON")
    cur = conn.cursor()

    # players table (needed for FK constraint)
    cur.execute(
        """
        CREATE TABLE players (
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
    create_h2h_table(cur)
    conn.commit()
    yield conn, cur
    conn.close()


@pytest.fixture
def guild_db(tmp_path, monkeypatch):
    """
    Patch DB_FOLDER to a temp dir, then use get_database_connection normally.
    Returns (guild_id, tmp_path).
    """
    import db.elo_db as elo_db

    monkeypatch.setattr(elo_db, "DB_FOLDER", str(tmp_path))
    monkeypatch.setattr(elo_db, "_migrated_dbs", set())

    guild_id = 111222333
    yield guild_id, tmp_path


# ---------------------------------------------------------------------------
# parse_against_entries
# ---------------------------------------------------------------------------

class TestParseAgainstEntries:
    def test_empty_string(self):
        assert list(parse_against_entries("")) == []

    def test_none_like(self):
        assert list(parse_against_entries(None)) == []  # type: ignore[arg-type]

    def test_single_entry(self):
        result = list(parse_against_entries("Alice, 3, 1"))
        assert result == [("Alice", 3, 1)]

    def test_multiple_entries(self):
        result = list(parse_against_entries("Alice, 3, 1; Bob, 0, 5"))
        assert result == [("Alice", 3, 1), ("Bob", 0, 5)]

    def test_malformed_segment_skipped(self):
        result = list(parse_against_entries("Alice, 3, 1; bad_entry; Bob, 2, 2"))
        assert len(result) == 2

    def test_non_int_values_skipped(self):
        result = list(parse_against_entries("Alice, abc, 1"))
        assert result == []


# ---------------------------------------------------------------------------
# h2h_stats CRUD
# ---------------------------------------------------------------------------

class TestH2HStats:
    def test_get_nonexistent_returns_zeros(self, in_memory_db):
        _, cur = in_memory_db
        assert get_h2h_stats(cur, "player1", "Alice") == (0, 0)

    def test_add_and_retrieve(self, in_memory_db):
        conn, cur = in_memory_db
        # Insert player first (FK)
        cur.execute("INSERT INTO players (id, name) VALUES ('p1', 'Player1')")
        add_h2h_match(cur, "p1", "Alice", wins=3, losses=1)
        conn.commit()
        assert get_h2h_stats(cur, "p1", "Alice") == (3, 1)

    def test_add_accumulates(self, in_memory_db):
        conn, cur = in_memory_db
        cur.execute("INSERT INTO players (id, name) VALUES ('p1', 'Player1')")
        add_h2h_match(cur, "p1", "Alice", wins=2, losses=0)
        add_h2h_match(cur, "p1", "Alice", wins=1, losses=1)
        conn.commit()
        assert get_h2h_stats(cur, "p1", "Alice") == (3, 1)

    def test_get_most_played_with(self, in_memory_db):
        conn, cur = in_memory_db
        cur.execute("INSERT INTO players (id, name) VALUES ('p1', 'Player1')")
        add_h2h_match(cur, "p1", "Alice", wins=5, losses=3)  # 8 games
        add_h2h_match(cur, "p1", "Bob", wins=2, losses=1)    # 3 games
        conn.commit()
        result = get_most_played_with(cur, "p1")
        assert result == ["Alice"]

    def test_rename_opponent(self, in_memory_db):
        conn, cur = in_memory_db
        cur.execute("INSERT INTO players (id, name) VALUES ('p1', 'Player1')")
        add_h2h_match(cur, "p1", "OldName", wins=4, losses=2)
        conn.commit()
        rename_opponent_in_h2h(cur, "OldName", "NewName")
        conn.commit()
        assert get_h2h_stats(cur, "p1", "OldName") == (0, 0)
        assert get_h2h_stats(cur, "p1", "NewName") == (4, 2)

    def test_rename_merges_existing(self, in_memory_db):
        conn, cur = in_memory_db
        cur.execute("INSERT INTO players (id, name) VALUES ('p1', 'Player1')")
        add_h2h_match(cur, "p1", "OldName", wins=2, losses=1)
        add_h2h_match(cur, "p1", "NewName", wins=1, losses=0)
        conn.commit()
        rename_opponent_in_h2h(cur, "OldName", "NewName")
        conn.commit()
        w, l = get_h2h_stats(cur, "p1", "NewName")
        assert w == 3  # 2 + 1
        assert l == 1  # 1 + 0


# ---------------------------------------------------------------------------
# Migration: legacy against TEXT → h2h_stats
# ---------------------------------------------------------------------------

class TestMigrateLegacyAgainst:
    def test_migration_writes_rows(self, in_memory_db):
        conn, cur = in_memory_db
        cur.execute(
            "INSERT INTO players (id, name, against) VALUES ('p1', 'P1', 'Alice, 3, 1; Bob, 0, 2')"
        )
        conn.commit()
        assert has_legacy_against_data(cur)
        players_done, rows_written = migrate_legacy_against_column(cur)
        conn.commit()
        assert players_done == 1
        assert rows_written == 2
        assert get_h2h_stats(cur, "p1", "Alice") == (3, 1)
        assert get_h2h_stats(cur, "p1", "Bob") == (0, 2)

    def test_no_legacy_data(self, in_memory_db):
        conn, cur = in_memory_db
        assert not has_legacy_against_data(cur)
        players_done, rows_written = migrate_legacy_against_column(cur)
        assert players_done == 0 and rows_written == 0


# ---------------------------------------------------------------------------
# elo_db – get_language / settings (uses real guild_db fixture)
# ---------------------------------------------------------------------------

class TestEloDbSettings:
    def test_default_language(self, guild_db):
        from db.elo_db import get_language
        guild_id, _ = guild_db
        lang = get_language(guild_id)
        # No settings row yet → default 'en'
        assert lang == "en"

    def test_dev_mode_default_false(self, guild_db):
        from db.elo_db import get_dev_mode
        guild_id, _ = guild_db
        assert get_dev_mode(guild_id) is False

    def test_set_and_get_dev_mode(self, guild_db):
        from db.elo_db import set_dev_mode, get_dev_mode
        guild_id, _ = guild_db
        set_dev_mode(guild_id, True)
        assert get_dev_mode(guild_id) is True
        set_dev_mode(guild_id, False)
        assert get_dev_mode(guild_id) is False

    def test_set_and_get_log_channel(self, guild_db):
        from db.elo_db import set_log_channel_id, get_log_channel_id
        guild_id, _ = guild_db
        assert get_log_channel_id(guild_id) is None
        set_log_channel_id(guild_id, 987654321)
        assert get_log_channel_id(guild_id) == 987654321

    def test_db_created_on_disk(self, guild_db):
        from db.elo_db import get_language, get_db_path
        guild_id, tmp_path = guild_db
        get_language(guild_id)  # triggers DB creation
        db_path = get_db_path(guild_id)
        assert os.path.isfile(db_path)