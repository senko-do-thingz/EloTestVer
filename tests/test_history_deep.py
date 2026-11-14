"""Deep integration tests for cogs/history.py's actual command flow
(HistoryView / HistoryCog), as opposed to test_history.py which only
covers the pure helper functions (_previous_season_month,
_compute_rest_gaps_for_graph).

These exercise the real DB round-trip (players/settings/match_history
tables created through db.elo_db's own migrations, same as production)
with a fake "sink" target standing in for a discord.abc.Messageable /
discord.Member, so _render_history / _render_graph run for real.

No async test runner is installed for this project (see test_rendering.py
for the same convention), so each test drives its async body with
asyncio.run() directly instead of using pytest-asyncio.
"""

from __future__ import annotations

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db.elo_db as elo_db
from db.match_history_db import add_match_history_row, next_match_id
from cogs.history import HistoryCog, HistoryGraphToggleButton, HistoryView, PAGE_SIZE


class FakeGuild:
    def __init__(self, guild_id):
        self.id = guild_id


class FakeCtx:
    """Stand-in for a commands.Context/SlashContext: only .guild is read
    directly by HistoryView; everything else goes through the
    interaction_or_target argument passed to update_history()."""

    def __init__(self, guild_id):
        self.guild = FakeGuild(guild_id)


class FakeAvatar:
    async def read(self):
        return b"fake-avatar-bytes"


class FakeTarget:
    """Stand-in for a discord.Member: id/display_name/mention are read by
    HistoryView, display_avatar.read() is awaited for the rendered card."""

    def __init__(self, user_id, display_name="Player"):
        self.id = user_id
        self.display_name = display_name
        self.mention = f"<@{user_id}>"
        self.display_avatar = FakeAvatar()


class Sink:
    """Stand-in for the "plain messageable" branch of _send_or_edit /
    _send_error (i.e. neither discord.Interaction nor discord.Message).
    Captures whatever HistoryView tries to send so assertions can inspect
    it without needing real discord.py networking objects."""

    def __init__(self):
        self.sent_view = None
        self.sent_file = None
        self.sent_embed = None
        self.response = self  # Fake interaction response for button.defer()

    async def defer(self):
        pass

    async def send(self, *, view=None, file=None, embed=None):
        self.sent_view = view
        self.sent_file = file
        self.sent_embed = embed
        return self


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(elo_db, "DB_FOLDER", str(tmp_path))
    elo_db._migrated_dbs.clear()
    yield


def _register(cursor, player_id, name):
    cursor.execute(
        "INSERT INTO players (id, name) VALUES (?, ?)", (str(player_id), name)
    )


def _add_match(cursor, player_id, season, month, played_at, elo_init=500, elo_final=520, wins=1, losses=0):
    add_match_history_row(
        cursor,
        match_id=next_match_id(cursor),
        player_id=player_id,
        opponent_name="Bob",
        wins=wins,
        losses=losses,
        elo_init=elo_init,
        elo_final=elo_final,
        played_at=played_at,
        season=season,
        month=month,
    )


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class TestUnregisteredPlayer:
    def test_history_mode_shows_not_registered(self):
        async def _body():
            ctx = FakeCtx(guild_id=1)
            target = FakeTarget(999)
            view = HistoryView(cog=None, ctx=ctx, target=target, season=1, month=1)
            sink = Sink()
            await view.update_history(sink)
            assert sink.sent_embed is not None
            assert sink.sent_embed.title == "Player Not Registered"

        run(_body())

    def test_graph_mode_shows_not_registered(self):
        async def _body():
            ctx = FakeCtx(guild_id=1)
            target = FakeTarget(999)
            view = HistoryView(cog=None, ctx=ctx, target=target, season=1, month=1)
            view.mode = "graph"
            sink = Sink()
            await view.update_history(sink)
            assert sink.sent_embed is not None
            assert sink.sent_embed.title == "Player Not Registered"

        run(_body())


class TestEmptyHistory:
    def test_registered_but_no_matches_this_month(self):
        async def _body():
            ctx = FakeCtx(guild_id=2)
            target = FakeTarget(1, "Alice")
            with elo_db.db_connection(2) as (conn, cursor):
                _register(cursor, 1, "Alice")
                conn.commit()

            view = HistoryView(cog=None, ctx=ctx, target=target, season=1, month=1)
            sink = Sink()
            await view.update_history(sink)
            assert sink.sent_embed.title == "Match History"
            assert "no recorded matches" in sink.sent_embed.description

        run(_body())


class TestPagination:
    def test_exact_multiple_of_page_size_does_not_create_empty_extra_page(self):
        # 20 matches, PAGE_SIZE=10 -> should be exactly 2 pages, not 3.
        async def _body():
            ctx = FakeCtx(guild_id=3)
            target = FakeTarget(1, "Alice")
            with elo_db.db_connection(3) as (conn, cursor):
                _register(cursor, 1, "Alice")
                for i in range(2 * PAGE_SIZE):
                    _add_match(cursor, "1", 1, 1, played_at=1_700_000_000 + i)
                conn.commit()

            view = HistoryView(cog=None, ctx=ctx, target=target, season=1, month=1)
            sink = Sink()
            await view.update_history(sink)
            assert view.total_pages == 2
            assert view.prev_button.disabled is True
            assert view.next_button.disabled is False

        run(_body())

    def test_page_argument_beyond_range_is_clamped_not_error(self):
        # Requesting a page far past the end must clamp instead of
        # returning an empty page / erroring - guards against a stale
        # page number surviving a -revert that shrinks the month's match
        # count between button presses.
        async def _body():
            ctx = FakeCtx(guild_id=4)
            target = FakeTarget(1, "Alice")
            with elo_db.db_connection(4) as (conn, cursor):
                _register(cursor, 1, "Alice")
                _add_match(cursor, "1", 1, 1, played_at=1_700_000_000)
                conn.commit()

            view = HistoryView(cog=None, ctx=ctx, target=target, season=1, month=1, page=50)
            sink = Sink()
            await view.update_history(sink)
            assert view.page == 0
            assert view.total_pages == 1

        run(_body())

    def test_last_page_partial_shows_next_disabled(self):
        async def _body():
            ctx = FakeCtx(guild_id=5)
            target = FakeTarget(1, "Alice")
            with elo_db.db_connection(5) as (conn, cursor):
                _register(cursor, 1, "Alice")
                for i in range(PAGE_SIZE + 3):  # 13 matches -> pages of 10, 3
                    _add_match(cursor, "1", 1, 1, played_at=1_700_000_000 + i)
                conn.commit()

            view = HistoryView(cog=None, ctx=ctx, target=target, season=1, month=1, page=1)
            sink = Sink()
            await view.update_history(sink)
            assert view.total_pages == 2
            assert view.page == 1
            assert view.next_button.disabled is True
            assert view.prev_button.disabled is False

        run(_body())


class TestStreakLabelsAcrossPages:
    def test_streak_spanning_page_boundary_uses_full_month(self):
        # 12 wins in a row, newest first across 2 pages (10 + 2). The
        # streak label (drawn on the most-recent row of the run) must
        # reflect the true length (12), computed from the full month -
        # not be undercounted because it spans a page boundary. We can't
        # inspect the rendered image directly here, so we assert via
        # compute_streak_labels reused the same way _render_history does,
        # over the same full-month row set _render_history queries.
        from db.match_history_db import (
            compute_streak_labels,
            get_full_match_history_by_season_month,
        )

        with elo_db.db_connection(6) as (conn, cursor):
            _register(cursor, 1, "Alice")
            for i in range(12):
                _add_match(cursor, "1", 1, 1, played_at=1_700_000_000 + i, wins=1, losses=0)
            conn.commit()
            full_rows = get_full_match_history_by_season_month(cursor, "1", 1, 1)

        labels = compute_streak_labels(full_rows)
        # Most-recent row (index 0, DESC order) should carry the full streak length.
        assert labels[0] == 12


class FakePromptMessage:
    """Stand-in for the `discord.Message` prompt sent by _prompt_for_month:
    tracks every embed it gets edited with so tests can assert on the
    re-prompt behavior without a real Discord message."""

    def __init__(self, message_id=1):
        self.id = message_id
        self.edits = []

    async def edit(self, embed=None, **kwargs):
        self.edits.append(embed)


class FakePromptCtx:
    def __init__(self, prompt_message):
        self._prompt_message = prompt_message

    async def send(self, embed=None):
        return self._prompt_message


class FakeReplyMessage:
    def __init__(self, content):
        self.content = content


class FakeBotWaitFor:
    """Stand-in for commands.Bot.wait_for: yields queued replies in order
    (or raises asyncio.TimeoutError for a queued `TimeoutError` sentinel),
    bypassing the real `check` predicate since these tests only cover
    _prompt_for_month's own validation/re-prompt loop, not the Discord
    message-matching logic."""

    def __init__(self, queued_replies):
        self._queue = list(queued_replies)

    async def wait_for(self, event, timeout, check):
        item = self._queue.pop(0)
        if item is TimeoutError:
            raise asyncio.TimeoutError()
        return item


class FakeInteractionUser:
    def __init__(self, user_id):
        self.id = user_id


class FakeInteractionChannel:
    def __init__(self, channel_id):
        self.id = channel_id


class FakeInteraction:
    def __init__(self):
        self.user = FakeInteractionUser(42)
        self.channel = FakeInteractionChannel(7)


class TestPromptForMonthValidation:
    """Season 1 has data in months 1 and 3 (skipping 2 is impossible in
    practice since -decay only ever advances the current month by 1 - see
    review discussion - but the validation itself must still hold for
    whatever `months` list get_months_with_history() actually returns)."""

    def test_valid_reply_on_first_try_returns_immediately(self):
        async def _body():
            prompt = FakePromptMessage()
            cog = HistoryCog(bot=FakeBotWaitFor([FakeReplyMessage("3")]))
            result = await cog._prompt_for_month(
                FakePromptCtx(prompt), FakeInteraction(), season=1, months=[1, 3]
            )
            assert result == (prompt, 3)
            assert prompt.edits == []  # no error re-prompt needed

        run(_body())

    def test_non_integer_reply_reprompts_then_accepts_valid_reply(self):
        async def _body():
            prompt = FakePromptMessage()
            cog = HistoryCog(
                bot=FakeBotWaitFor([FakeReplyMessage("banana"), FakeReplyMessage("1")])
            )
            result = await cog._prompt_for_month(
                FakePromptCtx(prompt), FakeInteraction(), season=1, months=[1, 3]
            )
            assert result == (prompt, 1)
            assert len(prompt.edits) == 1
            assert prompt.edits[0].title == "Invalid Month"

        run(_body())

    def test_valid_integer_but_not_in_offered_months_reprompts(self):
        # 2 is a syntactically valid month number but season 1 has no data
        # in month 2 (only 1 and 3) - must re-prompt, not silently accept it.
        async def _body():
            prompt = FakePromptMessage()
            cog = HistoryCog(
                bot=FakeBotWaitFor([FakeReplyMessage("2"), FakeReplyMessage("3")])
            )
            result = await cog._prompt_for_month(
                FakePromptCtx(prompt), FakeInteraction(), season=1, months=[1, 3]
            )
            assert result == (prompt, 3)
            assert len(prompt.edits) == 1
            assert prompt.edits[0].title == "Invalid Month"
            assert "2" in prompt.edits[0].description

        run(_body())

    def test_repeated_invalid_replies_keep_reprompting_until_valid(self):
        async def _body():
            prompt = FakePromptMessage()
            cog = HistoryCog(
                bot=FakeBotWaitFor(
                    [
                        FakeReplyMessage("nope"),
                        FakeReplyMessage("99"),
                        FakeReplyMessage("1"),
                    ]
                )
            )
            result = await cog._prompt_for_month(
                FakePromptCtx(prompt), FakeInteraction(), season=1, months=[1, 3]
            )
            assert result == (prompt, 1)
            assert len(prompt.edits) == 2

        run(_body())

    def test_timeout_ends_flow_with_none(self):
        async def _body():
            prompt = FakePromptMessage()
            cog = HistoryCog(bot=FakeBotWaitFor([TimeoutError]))
            result = await cog._prompt_for_month(
                FakePromptCtx(prompt), FakeInteraction(), season=1, months=[1, 3]
            )
            assert result is None
            assert prompt.edits[0].title == "Timeout"

        run(_body())

    def test_timeout_after_invalid_reply_still_ends_flow_with_none(self):
        async def _body():
            prompt = FakePromptMessage()
            cog = HistoryCog(
                bot=FakeBotWaitFor([FakeReplyMessage("nope"), TimeoutError])
            )
            result = await cog._prompt_for_month(
                FakePromptCtx(prompt), FakeInteraction(), season=1, months=[1, 3]
            )
            assert result is None
            assert prompt.edits[0].title == "Invalid Month"
            assert prompt.edits[1].title == "Timeout"

        run(_body())


class TestGraphTogglePageReset:
    def test_switching_from_graph_back_to_history_resets_to_page_0(self):
        async def _body():
            ctx = FakeCtx(guild_id=8)
            target = FakeTarget(1, "Alice")
            with elo_db.db_connection(8) as (conn, cursor):
                _register(cursor, 1, "Alice")
                for i in range(PAGE_SIZE + 3):
                    _add_match(cursor, "1", 1, 1, played_at=1_700_000_000 + i)
                conn.commit()

            view = HistoryView(cog=None, ctx=ctx, target=target, season=1, month=1, page=1)
            view.mode = "graph"  # simulate having already been on page 1, then switched to graph
            button = HistoryGraphToggleButton(view)

            sink = Sink()
            await button.callback(sink)

            assert view.mode == "history"
            assert view.page == 0

        run(_body())


class TestGraphEligibility:
    def test_graph_mode_with_matches_in_other_month_but_not_this_one(self):
        # Player has played before (elsewhere), but not in the requested
        # season/month -> should get the "no recorded matches" embed for
        # THIS month, not silently show another month's data.
        async def _body():
            ctx = FakeCtx(guild_id=7)
            target = FakeTarget(1, "Alice")
            with elo_db.db_connection(7) as (conn, cursor):
                _register(cursor, 1, "Alice")
                _add_match(cursor, "1", 1, 1, played_at=1_700_000_000)
                conn.commit()

            view = HistoryView(cog=None, ctx=ctx, target=target, season=1, month=2)
            view.mode = "graph"
            sink = Sink()
            await view.update_history(sink)
            assert sink.sent_embed.title == "Elo Graph"
            assert "no recorded matches" in sink.sent_embed.description

        run(_body())