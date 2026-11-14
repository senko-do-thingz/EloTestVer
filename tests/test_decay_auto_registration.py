"""Regression tests for automatic season rollover and registration cleanup.

All database writes use temporary SQLite files; no real Discord calls occur.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import cogs.admin_elo.decay_auto as decay
import cogs.admin_elo.registration as registration
import db.elo_db as db


def run(coro):
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


@pytest.fixture
def gid(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_FOLDER", str(tmp_path))
    monkeypatch.setattr(db, "_migrated_dbs", set())
    monkeypatch.setattr(decay, "lb_cache_rebuild", Mock())
    monkeypatch.setattr(registration, "lb_cache_rebuild", Mock())
    return 111222333444555666


def add_player(guild_id, user_id, name, elo=500):
    with db.db_connection(guild_id) as (conn, cursor):
        cursor.execute(
            "INSERT INTO players(id, name, elo, games_played_this_month) "
            "VALUES (?, ?, ?, 2)", (str(user_id), name, elo),
        )
        conn.commit()


def count(guild_id):
    with db.db_connection(guild_id) as (_conn, cursor):
        cursor.execute("SELECT COUNT(*) FROM players")
        return cursor.fetchone()[0]


def bot_without_guild():
    return SimpleNamespace(get_guild=lambda _id: None)


def mock_side_effects(monkeypatch):
    for name in ("send_decay_db_backup", "post_decay_log", "bot_log"):
        monkeypatch.setattr(decay, name, AsyncMock())
    monkeypatch.setattr(
        decay, "export_month_snapshot",
        AsyncMock(return_value=(True, "Month saved")),
    )
    monkeypatch.setattr(
        decay, "export_season_overall",
        AsyncMock(return_value=(True, "Overall saved")),
    )
    monkeypatch.setattr(
        decay, "purge_old_match_history",
        AsyncMock(return_value="History saved"),
    )


def test_final_month_exports_before_clear_and_rolls_over(gid, monkeypatch):
    db.set_max_month(gid, 3)
    db.set_season_month(gid, 4, 3)
    add_player(gid, 101, "Dark Knight")
    add_player(gid, 102, "Sky")
    mock_side_effects(monkeypatch)
    order = []

    async def month(*_):
        assert count(gid) == 2
        order.append("month")
        return True, "Month saved"

    async def overall(*_):
        assert count(gid) == 2
        order.append("overall")
        return True, "Overall saved"

    monkeypatch.setattr(decay, "export_month_snapshot", month)
    monkeypatch.setattr(decay, "export_season_overall", overall)
    run(decay.run_monthly_decay_for_guild(bot_without_guild(), gid))

    assert order == ["month", "overall"]
    assert count(gid) == 0
    assert db.get_season_month(gid) == (5, 1)
    description = decay.post_decay_log.await_args.args[2].description
    assert "2 registered player(s)" in description
    assert "Month saved" in description
    assert "Overall saved" in description


def test_regular_month_does_not_call_c_reg(gid, monkeypatch):
    db.set_max_month(gid, 3)
    db.set_season_month(gid, 4, 2)
    add_player(gid, 101, "Dark Knight")
    mock_side_effects(monkeypatch)
    clear = AsyncMock()
    monkeypatch.setattr(decay, "clear_all_registrations", clear)

    run(decay.run_monthly_decay_for_guild(bot_without_guild(), gid))

    clear.assert_not_awaited()
    assert count(gid) == 1
    assert db.get_season_month(gid) == (4, 3)
    with db.db_connection(gid) as (_conn, cursor):
        cursor.execute("SELECT games_played_this_month FROM players")
        assert cursor.fetchone()[0] == 0
    decay.export_season_overall.assert_not_awaited()


def test_failed_c_reg_does_not_advance_season(gid, monkeypatch):
    db.set_max_month(gid, 1)
    db.set_season_month(gid, 2, 1)
    add_player(gid, 101, "Sky")
    mock_side_effects(monkeypatch)
    monkeypatch.setattr(
        decay, "clear_all_registrations",
        AsyncMock(side_effect=RuntimeError("cleanup failed")),
    )
    with pytest.raises(RuntimeError, match="cleanup failed"):
        run(decay.run_monthly_decay_for_guild(bot_without_guild(), gid))
    assert count(gid) == 1
    assert db.get_season_month(gid) == (2, 1)
    decay.post_decay_log.assert_not_awaited()


def test_empty_final_month_advances_without_errors(gid, monkeypatch):
    db.set_max_month(gid, 1)
    db.set_season_month(gid, 2, 1)
    mock_side_effects(monkeypatch)
    run(decay.run_monthly_decay_for_guild(bot_without_guild(), gid))
    assert db.get_season_month(gid) == (3, 1)
    assert "no registered players to clear" in (
        decay.post_decay_log.await_args.args[2].description
    )


def test_c_reg_idempotence_and_daily_streak_retention(gid):
    add_player(gid, 101, "Dark Knight")
    with db.db_connection(gid) as (conn, cursor):
        cursor.execute(
            "INSERT INTO daily_streaks(player_id, daily_streak) VALUES (?, ?)",
            ("101", 8),
        )
        conn.commit()
    first = run(registration.clear_all_registrations(bot_without_guild(), gid))
    second = run(registration.clear_all_registrations(bot_without_guild(), gid))
    assert first.players_cleared == 1
    assert second == registration.ClearRegistrationsResult(
        0, registration.RoleRemovalSummary(0, 0, 0)
    )
    with db.db_connection(gid) as (_conn, cursor):
        cursor.execute("SELECT daily_streak FROM daily_streaks WHERE player_id='101'")
        assert cursor.fetchone() == (8,)


def test_c_reg_does_not_touch_another_guild(gid):
    add_player(gid, 101, "Sky")
    add_player(gid + 1, 102, "Dark Knight")
    run(registration.clear_all_registrations(bot_without_guild(), gid))
    assert count(gid) == 0
    assert count(gid + 1) == 1


def test_c_reg_removes_league_roles_in_leaderboard_order(gid, monkeypatch):
    for user_id, elo in ((101, 900), (102, 600), (103, 300)):
        add_player(gid, user_id, f"Player {user_id}", elo)
    league, other = SimpleNamespace(id=777), SimpleNamespace(id=888)
    calls = []

    class Member:
        def __init__(self, user_id, roles):
            self.roles = roles
            self.user_id = user_id

        async def remove_roles(self, *roles, reason):
            calls.append(self.user_id)
            self.roles = [role for role in self.roles if role not in roles]

    members = {
        101: Member(101, [league, other]),
        102: Member(102, [league]),
        103: Member(103, [other]),
    }
    guild = SimpleNamespace(
        roles=[league, other], get_member=lambda user_id: members.get(user_id),
    )
    bot = SimpleNamespace(get_guild=lambda _id: guild)
    monkeypatch.setattr(registration, "_league_role_ids", lambda _id: {777})
    sleep = AsyncMock()
    monkeypatch.setattr(registration.asyncio, "sleep", sleep)

    result = run(registration.clear_all_registrations(bot, gid))

    assert result.roles == registration.RoleRemovalSummary(2, 0, 0)
    assert calls == [101, 102]
    assert members[101].roles == [other]
    assert members[103].roles == [other]
    assert sleep.await_count == 2
    assert count(gid) == 0


@pytest.mark.parametrize("choice, remaining, title", [
    ("confirm", 0, "All Registrations Cleared"),
    ("cancel", 1, "Clear Canceled"),
    ("timeout", 1, "Timeout"),
])
def test_manual_confirmation_cancel_timeout(gid, choice, remaining, title):
    add_player(gid, 101, "Dark Knight")
    message = SimpleNamespace(
        id=42, add_reaction=AsyncMock(), edit=AsyncMock(),
    )
    author = SimpleNamespace(id=123)
    ctx = SimpleNamespace(
        guild=SimpleNamespace(id=gid),
        author=author,
        send=AsyncMock(return_value=message),
    )
    reaction = SimpleNamespace(
        emoji=registration.YES_EMOJI if choice == "confirm" else registration.NO_EMOJI,
        message=message,
    )

    async def wait_for(event, timeout, check):
        assert event == "reaction_add"
        assert check(reaction, author)
        if choice == "timeout":
            raise asyncio.TimeoutError()
        return reaction, author

    bot = SimpleNamespace(
        get_guild=lambda _id: None, user=None, wait_for=wait_for,
    )
    cog = registration.AdminEloRegistrationCog(bot)
    run(registration.AdminEloRegistrationCog.c_reg.callback(cog, ctx))

    assert count(gid) == remaining
    assert message.add_reaction.await_count == 2
    assert message.edit.await_args.kwargs["embed"].title == title


def test_manual_c_reg_no_players_does_not_prompt(gid):
    ctx = SimpleNamespace(guild=SimpleNamespace(id=gid), send=AsyncMock())
    bot = SimpleNamespace(wait_for=AsyncMock())
    cog = registration.AdminEloRegistrationCog(bot)
    run(registration.AdminEloRegistrationCog.c_reg.callback(cog, ctx))
    bot.wait_for.assert_not_awaited()
    assert ctx.send.await_args.kwargs["embed"].title == "Nothing to Clear"
