"""Integration tests for guild data, ranks, archives, counting, and caching."""
from __future__ import annotations

import zipfile

import pytest

import db.elo_db as db
from db.counting_db import (
    increment_counting_stat, get_counting_leaderboard, reset_counting_stats,
)
from utils import lb_cache
from utils.lb_export import (
    CATEGORY_FILENAMES,
    build_leaderboard_categories,
    build_zip_bytes,
    write_leaderboard_files,
)
from utils.league import (
    get_leagues,
    pick_league_role_id_for_elo,
    set_leagues,
)


@pytest.fixture
def guild_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_FOLDER", str(tmp_path / "db"))
    monkeypatch.setattr(db, "_migrated_dbs", set())
    monkeypatch.chdir(tmp_path)
    lb_cache.invalidate_all()
    yield 100300500
    lb_cache.invalidate_all()


def add_player(guild_id, ident, name, elo=500, won=0, lost=0, monthly=0):
    with db.db_connection(guild_id) as (conn, cursor):
        cursor.execute(
            "INSERT INTO players(id,name,elo,games_won,games_lost,games_played_this_month) "
            "VALUES (?,?,?,?,?,?)",
            (str(ident), name, elo, won, lost, monthly),
        )
        conn.commit()


def test_guild_database_isolated_from_other_guild(guild_db):
    add_player(guild_db, 1, "First")
    add_player(guild_db + 1, 2, "Second")
    with db.db_connection(guild_db) as (_conn, cursor):
        assert cursor.execute("SELECT name FROM players").fetchall() == [("First",)]
    with db.db_connection(guild_db + 1) as (_conn, cursor):
        assert cursor.execute("SELECT name FROM players").fetchall() == [("Second",)]


def test_season_settings_and_month_rollover(guild_db):
    assert db.get_season_month(guild_db) == (1, 1)
    db.set_max_month(guild_db, 3)
    db.set_season_month(guild_db, 2, 3)
    with db.db_connection(guild_db) as (conn, cursor):
        result = db.advance_season_month_cursor(cursor, guild_db)
        conn.commit()
    assert result == (2, 3, 3, 1)
    assert db.get_season_month(guild_db) == (3, 1)


def test_month_advances_without_rollover(guild_db):
    db.set_max_month(guild_db, 3)
    with db.db_connection(guild_db) as (conn, cursor):
        result = db.advance_season_month_cursor(cursor, guild_db)
        conn.commit()
    assert result == (1, 1, 1, 2)


def test_devmode_is_persisted_per_server(guild_db):
    assert not db.get_dev_mode(guild_db)
    db.set_dev_mode(guild_db, True)
    assert db.get_dev_mode(guild_db)
    assert not db.get_dev_mode(guild_db + 1)
    db.set_dev_mode(guild_db, False)
    assert not db.get_dev_mode(guild_db)


@pytest.mark.parametrize("setter,getter", [
    (db.set_log_channel_id, db.get_log_channel_id),
    (db.set_support_channel_id, db.get_support_channel_id),
    (db.set_decay_log_channel_id, db.get_decay_log_channel_id),
])
def test_guild_channel_configuration(guild_db, setter, getter):
    assert getter(guild_db) is None
    setter(guild_db, 123456)
    assert getter(guild_db) == 123456
    assert getter(guild_db + 1) is None
    setter(guild_db, None)
    assert getter(guild_db) is None


def test_server_nickname(guild_db):
    assert db.get_nickname(guild_db) is None
    db.set_nickname(guild_db, "QS")
    assert db.get_nickname(guild_db) == "QS"


def test_support_thread_mapping_and_receipt(guild_db):
    assert db.get_support_thread_id(guild_db, 99) is None
    db.set_support_thread_id(guild_db, 99, 123)
    assert db.get_support_thread_id(guild_db, 99) == 123
    assert db.get_support_user_id_by_thread(guild_db, 123) == 99
    assert db.get_support_user_id_by_thread(guild_db, 555) is None
    db.set_support_notify_message_id(guild_db, 99, 789)
    assert db.get_support_notify_message_id(guild_db, 99) == 789
    db.set_support_notify_message_id(guild_db, 99, None)
    assert db.get_support_notify_message_id(guild_db, 99) is None


def test_support_message_ban_lifecycle(guild_db):
    assert db.get_msg_ban_reason(guild_db, 99) is None
    assert not db.clear_msg_ban(guild_db, 99)
    db.set_msg_ban(guild_db, 99, "Spam")
    assert db.get_msg_ban_reason(guild_db, 99) == "Spam"
    db.set_msg_ban(guild_db, 99, "Repeated spam")
    assert db.get_msg_ban_reason(guild_db, 99) == "Repeated spam"
    assert db.clear_msg_ban(guild_db, 99)
    assert not db.clear_msg_ban(guild_db, 99)


def test_league_ranges_and_guild_persistence(guild_db):
    leagues = [
        ("Bronze", None, 499, "l", "n", 11, 1),
        ("Silver", 500, 999, "n", "n", 22, 1),
        ("Gold", 1000, None, "n", "h", 33, 1),
    ]
    set_leagues(guild_db, leagues)
    assert len(get_leagues(guild_db)) == 3
    effective = [row[:6] for row in get_leagues(guild_db)]
    assert pick_league_role_id_for_elo(effective, 300) == 11
    assert pick_league_role_id_for_elo(effective, 500) == 22
    assert pick_league_role_id_for_elo(effective, 999) == 22
    assert pick_league_role_id_for_elo(effective, 1200) == 33
    assert get_leagues(guild_db + 1) == []


def test_lb_cache_is_separated_by_guild_and_page(guild_db):
    lb_cache.put(guild_db, "elo", 0, b"one")
    lb_cache.put(guild_db, "elo", 1, b"two")
    lb_cache.put(guild_db + 1, "elo", 0, b"other")
    assert lb_cache.get(guild_db, "elo", 0) == b"one"
    assert lb_cache.get(guild_db, "elo", 1) == b"two"
    assert lb_cache.get(guild_db, "wins", 0) is None
    lb_cache.invalidate(guild_db)
    assert lb_cache.get(guild_db, "elo", 0) is None
    assert lb_cache.get(guild_db + 1, "elo", 0) == b"other"


def test_lb_cache_clear_all(guild_db):
    lb_cache.put(guild_db, "elo", 0, b"sample")
    lb_cache.invalidate_all()
    assert lb_cache.get(guild_db, "elo", 0) is None


def test_export_all_five_categories(guild_db):
    add_player(guild_db, 111111111111111111, "Sky", 800, 4, 1, 5)
    add_player(guild_db, 222222222222222222, "Dark Knight", 600, 1, 1, 2)
    categories = build_leaderboard_categories(guild_db)
    assert set(categories) == set(CATEGORY_FILENAMES)
    assert "Sky 800 111111111111111111" in categories["data.txt"]
    assert "Dark Knight 600 222222222222222222" in categories["data.txt"]
    assert "Sky 80.00" in categories["wr.txt"]

    zip_file = build_zip_bytes(categories)
    with zipfile.ZipFile(zip_file) as archive:
        assert set(archive.namelist()) == set(CATEGORY_FILENAMES)
        assert archive.read("w.txt").decode("utf-8") == categories["w.txt"]


def test_export_empty_guild_has_no_files(guild_db):
    assert build_leaderboard_categories(guild_db) == {}


def test_write_leaderboard_files_to_archive_directory(guild_db, tmp_path):
    root = tmp_path / "lb" / str(guild_db) / "Season 1" / "Month 1"
    write_leaderboard_files(str(root), {"data.txt": "Sky 600 123"})
    assert (root / "data.txt").read_text(encoding="utf-8") == "Sky 600 123"


def test_counting_stats_add_sort_limit_and_reset(guild_db):
    channel = 11
    increment_counting_stat(guild_db, channel, 101)
    increment_counting_stat(guild_db, channel, 101, 2)
    increment_counting_stat(guild_db, channel, 102, 2)
    increment_counting_stat(guild_db, channel + 1, 103, 7)
    assert get_counting_leaderboard(guild_db, channel) == [
        (101, 3), (102, 2)
    ]
    assert get_counting_leaderboard(guild_db, channel, limit=1) == [(101, 3)]
    reset_counting_stats(guild_db, channel)
    assert get_counting_leaderboard(guild_db, channel) == []
    assert get_counting_leaderboard(guild_db, channel + 1) == [(103, 7)]
