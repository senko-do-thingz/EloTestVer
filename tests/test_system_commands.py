"""Command validation, access control, autocompletion, and schedule tests."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import db.elo_db as db
from cogs.reporting import ReportingCog
from utils import autocomplete
from utils.command_registry import lookup_command_help
from utils.elo_decay import next_scheduled_decay_run, season_end_notice
from utils.guards import (
    NAME_MAX_LENGTH, NAME_MIN_LENGTH, is_admin, is_valid_name,
)
from utils.private_guard import is_private_allowed
from utils import private_guard


def run(coro):
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


@pytest.fixture
def guild_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_FOLDER", str(tmp_path))
    monkeypatch.setattr(db, "_migrated_dbs", set())
    return 100200300


@pytest.mark.parametrize("name", [
    "Senko", "Dark Knight", "Nguyen Van A", "Nguyễn Văn A",
    "Café", "Mr.X", "A_B", "A1", "Play er 02",
])
def test_registration_accepts_valid_names(name):
    assert is_valid_name(name) == (True, "valid")


@pytest.mark.parametrize("name, reason_prefix", [
    ("A", "too_short:"),
    ("X" * 21, "too_long:"),
    ("   ", "only_spaces"),
    ("TheEloBotFan", "banned"),
    ("Hi!", "invalid_chars:"),
    ("山田", "invalid_chars:"),
    ("Hi😀", "invalid_chars:"),
])
def test_registration_rejects_invalid_names(name, reason_prefix):
    ok, reason = is_valid_name(name)
    assert not ok
    assert reason.startswith(reason_prefix)


def test_name_length_boundaries():
    assert NAME_MIN_LENGTH == 2
    assert NAME_MAX_LENGTH == 20
    assert is_valid_name("X" * NAME_MIN_LENGTH)[0]
    assert is_valid_name("X" * NAME_MAX_LENGTH)[0]


@pytest.mark.parametrize("score,expected", [
    ("31", (3, 1)), ("4-2", (4, 2)), ("3:1", (3, 1)),
    ("4 2", (4, 2)), ("0:1", (0, 1)),
    (" 4-3 ", (4, 3)), ("2-2", (2, 2)),
    ("", None), ("x", None), ("3/x", None), ("100", None),
])
def test_reporting_combined_score_parser(score, expected):
    assert ReportingCog(bot=None)._parse_combined_score(score) == expected


@pytest.mark.parametrize("arguments,expected", [
    (("31", "999"), (123, 3, 1, 999)),
    (("4-2", "999"), (123, 4, 2, 999)),
    (("3", "1", "999"), (123, 3, 1, 999)),
    (("bad", "999"), None),
    (("31", "not-id"), None),
    (("3", "bad", "999"), None),
    (("1",), None),
    ((), None),
])
def test_admin_report_argument_parsing(arguments, expected):
    assert ReportingCog(bot=None)._parse_ad_rp_args(123, arguments) == expected


def test_pending_match_locks_are_per_guild_and_per_player():
    cog = ReportingCog(bot=None)
    assert cog._acquire_pending_report_lock(1, {1, 2})
    assert not cog._acquire_pending_report_lock(1, {2, 3})
    assert cog._acquire_pending_report_lock(2, {2, 3})
    assert cog._acquire_pending_report_lock(1, {4})
    cog._release_pending_report_lock(1, {1, 2})
    assert cog._acquire_pending_report_lock(1, {2, 3})
    assert not cog._acquire_pending_report_lock(2, {3})


def test_admin_guard_requires_exact_manager_role():
    def ctx(*names):
        return SimpleNamespace(author=SimpleNamespace(
            roles=[SimpleNamespace(name=name) for name in names]
        ))
    assert is_admin(ctx("Member", "Elo Bot Manager"))
    assert not is_admin(ctx("Member"))
    assert not is_admin(ctx("elo bot manager"))


def test_private_access_is_scoped_to_management_or_dev_mode(monkeypatch):
    monkeypatch.setattr(private_guard, "get_dev_mode", lambda _id: False)
    assert not is_private_allowed(None)
    assert not is_private_allowed(SimpleNamespace(id=987654321))
    assert is_private_allowed(SimpleNamespace(id=private_guard.PRIVATE_GUILD_ID))
    monkeypatch.setattr(private_guard, "get_dev_mode", lambda _id: True)
    assert is_private_allowed(SimpleNamespace(id=987654321))


@pytest.mark.parametrize("source,expected", [
    (datetime(2026, 11, 14, tzinfo=timezone.utc),
     datetime(2026, 12, 1, 0, 10, tzinfo=timezone.utc)),
    (datetime(2026, 12, 31, tzinfo=timezone.utc),
     datetime(2027, 1, 1, 0, 10, tzinfo=timezone.utc)),
    (datetime(2026, 11, 1, 0, 9, tzinfo=timezone.utc),
     datetime(2026, 11, 1, 0, 10, tzinfo=timezone.utc)),
    (datetime(2026, 11, 1, 0, 10, tzinfo=timezone.utc),
     datetime(2026, 12, 1, 0, 10, tzinfo=timezone.utc)),
])
def test_next_decay_schedule(source, expected):
    assert next_scheduled_decay_run(source) == expected


def test_season_end_notice_mentions_auto_clear_only_when_requested():
    automatic = season_end_notice(8, auto_clear_registrations=True)
    manual = season_end_notice(8)
    assert "has been run automatically" in automatic
    assert "Use" in manual


def test_command_help_lookup_accepts_prefix_and_alias():
    entry = lookup_command_help("report")
    assert entry is not None
    assert lookup_command_help("  -REPORT ") is entry
    assert lookup_command_help("/report") is entry
    assert lookup_command_help("unknown-command") is None


def test_registered_autocomplete_handles_no_guild():
    context = SimpleNamespace(guild=None)
    assert run(autocomplete.registered_name_autocomplete(context, "Senko")) == []
    assert run(autocomplete.stats_top_elo_name_autocomplete(context, "S")) == []


def test_registered_autocomplete_matches_substrings(guild_db):
    with db.db_connection(guild_db) as (conn, cursor):
        cursor.executemany(
            "INSERT INTO players(id, name) VALUES (?, ?)",
            [("1", "Dark Knight"), ("2", "Sky Runner"), ("3", "Night Owl")],
        )
        conn.commit()
    context = SimpleNamespace(guild=SimpleNamespace(id=guild_db))
    matches = run(autocomplete.registered_name_autocomplete(context, "night"))
    assert [choice.name for choice in matches] == ["Dark Knight", "Night Owl"]


def test_command_name_autocomplete_limits_to_discord_maximum():
    context = SimpleNamespace()
    names = [f"name_{i:03}" for i in range(40)]
    result = run(autocomplete.command_name_autocomplete(context, "", names=names))
    assert len(result) == 25
    filtered = run(autocomplete.command_name_autocomplete(
        context, "-NAME_01", names=names
    ))
    assert len(filtered) == 10


def test_top_elo_autocomplete_excludes_unplayed_players(guild_db):
    with db.db_connection(guild_db) as (conn, cursor):
        cursor.executemany(
            "INSERT INTO players(id, name, elo, games_won) VALUES (?, ?, ?, ?)",
            [("1", "Dark Knight", 1000, 2),
             ("2", "Bench", 2000, 0),
             ("3", "Sky Runner", 800, 1)],
        )
        conn.commit()
    context = SimpleNamespace(guild=SimpleNamespace(id=guild_db))
    result = run(autocomplete.stats_top_elo_name_autocomplete(context, ""))
    assert [choice.name for choice in result] == ["Dark Knight", "Sky Runner"]


def test_bot_token_validation(monkeypatch):
    from config import get_discord_token
    monkeypatch.delenv("DISCORD_TOKEN", raising=False)
    with pytest.raises(ValueError, match="DISCORD_TOKEN"):
        get_discord_token()
    monkeypatch.setenv("DISCORD_TOKEN", "test-token-not-real")
    assert get_discord_token() == "test-token-not-real"
