"""Tests for match history DB helpers."""

import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.match_history_db import (
    add_match_history_row,
    create_match_history_table,
    decrement_player_matches,
    find_latest_match_id_for_revert,
    get_player_matches_count,
    get_recent_match_history,
    increment_player_matches,
    next_match_id,
)


@pytest.fixture
def history_db():
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys=ON")
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE players (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            matches INTEGER DEFAULT 0
        )
        """
    )
    cur.execute("INSERT INTO players (id, name) VALUES ('1', 'Alice'), ('2', 'Bob')")
    create_match_history_table(cur)
    conn.commit()
    yield conn, cur
    conn.close()


class TestMatchHistory:
    def test_increment_and_count(self, history_db):
        conn, cur = history_db
        increment_player_matches(cur, "1")
        conn.commit()
        assert get_player_matches_count(cur, "1") == 1

    def test_add_and_fetch_recent(self, history_db):
        conn, cur = history_db
        match_id = next_match_id(cur)
        add_match_history_row(
            cur,
            match_id=match_id,
            player_id="1",
            opponent_name="Bob",
            wins=3,
            losses=1,
            elo_init=500,
            elo_final=520,
            played_at=1_700_000_000,
        )
        increment_player_matches(cur, "1")
        conn.commit()
        rows = get_recent_match_history(cur, "1", 5)
        assert len(rows) == 1
        assert rows[0].opponent_name == "Bob"
        assert rows[0].elo_delta == 20

    def test_find_and_revert_match(self, history_db):
        conn, cur = history_db
        match_id = next_match_id(cur)
        add_match_history_row(
            cur,
            match_id=match_id,
            player_id="1",
            opponent_name="Bob",
            wins=3,
            losses=1,
            elo_init=500,
            elo_final=520,
        )
        add_match_history_row(
            cur,
            match_id=match_id,
            player_id="2",
            opponent_name="Alice",
            wins=1,
            losses=3,
            elo_init=510,
            elo_final=490,
        )
        increment_player_matches(cur, "1")
        increment_player_matches(cur, "2")
        conn.commit()

        found = find_latest_match_id_for_revert(cur, "1", "2", "Alice", "Bob", 3, 1)
        assert found == match_id

        decrement_player_matches(cur, "1")
        decrement_player_matches(cur, "2")
        conn.commit()
        assert get_player_matches_count(cur, "1") == 0
