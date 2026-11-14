"""Deep integration tests for cogs/leaderboard.py's actual command flow
(LeaderboardView), as opposed to a pure-function-only test file.

These exercise the real DB round-trip (players/daily_streaks tables
created through db.elo_db's own migrations, same as production) with a
fake "ctx" standing in for a discord.ext.commands.Context, so
_render_leaderboard runs for real against a live sqlite connection.

Two things are stubbed out rather than exercised for real:

- LeaderboardView._rebuild_layout: building the actual Components V2
  tree (Container/Section/MediaGallery/TextDisplay) is discord.py UI
  plumbing, not leaderboard business logic, and asserting through it
  would just make these tests brittle to unrelated UI-library changes.
  It's replaced with a stub that records (filename, note) so the
  eligibility-note text can still be asserted precisely.
- utils.rendering.render_leaderboard_image: real Pillow rendering
  works fine locally, but stubbing it lets tests assert *whether* a
  fresh render happened (cache hit vs miss) and inspect the exact
  entries passed to it, without depending on font/image internals.

No async test runner is installed for this project (see
test_rendering.py / test_history_deep.py for the same convention), so
each test drives its async body with asyncio.run()-equivalent
(asyncio.get_event_loop().run_until_complete) directly instead of
using pytest-asyncio.
"""

from __future__ import annotations

import asyncio
import io
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db.elo_db as elo_db
from utils import lb_cache
import cogs.leaderboard as leaderboard_mod
from cogs.leaderboard import (
    LeaderboardView,
    LeaderboardMyRankButton,
    _effective_daily_streak,
    _footer_text,
)


# ---------------------------------------------------------------------------
# Fixtures / fakes
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(elo_db, "DB_FOLDER", str(tmp_path))
    elo_db._migrated_dbs.clear()
    yield


@pytest.fixture(autouse=True)
def isolated_cache():
    lb_cache.invalidate_all()
    yield
    lb_cache.invalidate_all()


@pytest.fixture(autouse=True)
def layout_capture(monkeypatch):
    """Stub LeaderboardView._rebuild_layout so tests can assert on the
    (filename, note) it would have built the UI with, without needing a
    real discord.ui.Container/Section tree."""
    captured: dict = {}

    def _fake_rebuild(self, filename, note):
        captured["filename"] = filename
        captured["note"] = note

    monkeypatch.setattr(LeaderboardView, "_rebuild_layout", _fake_rebuild)
    return captured


@pytest.fixture
def render_spy(monkeypatch):
    """Replace render_leaderboard_image with a spy that records every
    call's entries/title/page/total_pages and returns distinguishable
    bytes, so cache-hit-vs-miss and entry contents can be asserted."""
    calls = []

    def _fake_render(entries, *, title, page=None, total_pages=None, width=580):
        calls.append(
            {
                "entries": entries,
                "title": title,
                "page": page,
                "total_pages": total_pages,
            }
        )
        return io.BytesIO(f"FRESH-RENDER-{len(calls)}".encode())

    monkeypatch.setattr(leaderboard_mod, "render_leaderboard_image", _fake_render)
    return calls


class FakeGuild:
    def __init__(self, guild_id):
        self.id = guild_id


class FakeAuthor:
    def __init__(self, user_id):
        self.id = user_id


class FakeCtx:
    """Stand-in for commands.Context: only .guild.id / .author.id are read
    directly by LeaderboardView, and .send() is the plain-messageable
    branch of _send_or_edit (isinstance(..., discord.Interaction) is
    False for a Context)."""

    def __init__(self, guild_id, author_id):
        self.guild = FakeGuild(guild_id)
        self.author = FakeAuthor(author_id)
        self.sent_view = None
        self.sent_file = None
        self.sent_embed = None

    async def send(self, *, view=None, file=None, embed=None):
        self.sent_view = view
        self.sent_file = file
        self.sent_embed = embed
        return self


class FakeResponse:
    def __init__(self):
        self.sent_embed = None
        self.sent_ephemeral = None
        self.sent_content = None

    async def send_message(self, content=None, *, embed=None, ephemeral=False):
        self.sent_content = content
        self.sent_embed = embed
        self.sent_ephemeral = ephemeral


class FakeInteraction:
    """Only used for the LeaderboardMyRankButton permission-check test,
    whose "wrong user" branch calls interaction.response.send_message()
    directly and returns *before* reaching _send_or_edit's
    isinstance(discord.Interaction) check - so a plain fake is safe here."""

    def __init__(self, user_id):
        self.user = FakeAuthor(user_id)
        self.response = FakeResponse()


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _add_player(
    cursor,
    pid,
    name,
    *,
    elo=500,
    won=0,
    lost=0,
    games_month=0,
    win_streak=0,
):
    cursor.execute(
        "INSERT INTO players "
        "(id, name, elo, games_won, games_lost, games_played_this_month, win_streak) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (str(pid), name, elo, won, lost, games_month, win_streak),
    )


def _set_daily_streak(cursor, pid, streak, last_played_date):
    cursor.execute(
        "INSERT INTO daily_streaks (player_id, daily_streak, last_played_date) "
        "VALUES (?, ?, ?)",
        (str(pid), streak, last_played_date),
    )


def _today_str(offset_days=0):
    d = datetime.now(timezone.utc).date() - timedelta(days=offset_days)
    return d.strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Pure-function helpers
# ---------------------------------------------------------------------------


class TestEffectiveDailyStreakHelper:
    def test_no_last_played_date_returns_zero(self):
        assert _effective_daily_streak(5, None) == 0

    def test_played_today_keeps_streak(self):
        assert _effective_daily_streak(5, _today_str(0)) == 5

    def test_played_yesterday_still_within_grace_period(self):
        # day_gap == 1: haven't played *yet* today, but yesterday still counts.
        assert _effective_daily_streak(5, _today_str(1)) == 5

    def test_gap_of_two_days_zeroes_streak(self):
        assert _effective_daily_streak(5, _today_str(2)) == 0


class TestFooterText:
    def test_no_updated_no_note_returns_zero_width_placeholder(self):
        assert _footer_text(None, None) == "\u200b"

    def test_updated_only(self):
        now = datetime.now(timezone.utc)
        text = _footer_text(now, None)
        assert text.startswith("Last updated: <t:")
        assert "\n" not in text

    def test_updated_and_note_joined_with_newline(self):
        now = datetime.now(timezone.utc)
        text = _footer_text(now, "Play at least one set to be ranked")
        lines = text.split("\n")
        assert lines[0].startswith("Last updated: <t:")
        assert lines[1] == "Play at least one set to be ranked"


# ---------------------------------------------------------------------------
# Empty / not-found leaderboard
# ---------------------------------------------------------------------------


class TestEmptyLeaderboard:
    def test_no_players_at_all_shows_not_found(self):
        async def _body():
            ctx = FakeCtx(guild_id=1, author_id=1)
            view = LeaderboardView(ctx, category="elo")
            await view.update_leaderboard(ctx)
            assert ctx.sent_view is view
            assert ctx.sent_file is None

        run(_body())

    def test_players_exist_but_none_eligible_shows_not_found(self):
        # Elo category excludes anyone with zero games played, even if
        # they're registered with the default starting elo.
        async def _body():
            ctx = FakeCtx(guild_id=2, author_id=1)
            with elo_db.db_connection(2) as (conn, cursor):
                _add_player(cursor, 1, "Alice", elo=500, won=0, lost=0)
                conn.commit()

            view = LeaderboardView(ctx, category="elo")
            await view.update_leaderboard(ctx)
            assert ctx.sent_file is None

        run(_body())


# ---------------------------------------------------------------------------
# Category-by-category eligibility, sorting, and note text
# ---------------------------------------------------------------------------


class TestEloCategory:
    def test_zero_game_players_excluded_and_sorted_desc(self, render_spy):
        async def _body():
            ctx = FakeCtx(guild_id=10, author_id=99)  # invoker not registered
            with elo_db.db_connection(10) as (conn, cursor):
                _add_player(cursor, 1, "Low", elo=450, won=1, lost=1)
                _add_player(cursor, 2, "High", elo=600, won=3, lost=0)
                _add_player(cursor, 3, "Bench", elo=500, won=0, lost=0)  # excluded
                conn.commit()

            view = LeaderboardView(ctx, category="elo")
            await view.update_leaderboard(ctx)

            entries = render_spy[-1]["entries"]
            names = [e[1] for e in entries]
            assert names == ["High", "Low"]  # Bench excluded, High before Low

        run(_body())

    def test_unregistered_invoker_gets_no_note(self, render_spy, layout_capture):
        async def _body():
            ctx = FakeCtx(guild_id=11, author_id=99)  # not in players table
            with elo_db.db_connection(11) as (conn, cursor):
                _add_player(cursor, 1, "Alice", elo=500, won=1, lost=0)
                conn.commit()

            view = LeaderboardView(ctx, category="elo")
            await view.update_leaderboard(ctx)
            assert layout_capture["note"] is None

        run(_body())

    def test_registered_but_zero_games_gets_ranking_note(
        self, render_spy, layout_capture
    ):
        async def _body():
            ctx = FakeCtx(guild_id=12, author_id=1)
            with elo_db.db_connection(12) as (conn, cursor):
                _add_player(cursor, 1, "Alice", elo=500, won=0, lost=0)
                _add_player(cursor, 2, "Bob", elo=500, won=1, lost=0)
                conn.commit()

            view = LeaderboardView(ctx, category="elo")
            await view.update_leaderboard(ctx)
            assert layout_capture["note"] == "Play at least one set to be ranked"

        run(_body())

    def test_my_rank_not_ranked_embed_for_ineligible_target(self):
        async def _body():
            ctx = FakeCtx(guild_id=13, author_id=1)
            with elo_db.db_connection(13) as (conn, cursor):
                _add_player(cursor, 1, "Alice", elo=500, won=0, lost=0)
                _add_player(cursor, 2, "Bob", elo=500, won=1, lost=0)
                conn.commit()

            view = LeaderboardView(ctx, category="elo")
            view.jump_to_target = True
            await view.update_leaderboard(ctx)
            assert ctx.sent_file is None
            assert view.jump_to_target is False  # consumed even on the not-ranked path

        run(_body())


class TestWinsCategory:
    def test_zero_wins_excluded_note_and_sort(self, render_spy, layout_capture):
        async def _body():
            ctx = FakeCtx(guild_id=20, author_id=1)
            with elo_db.db_connection(20) as (conn, cursor):
                _add_player(cursor, 1, "Alice", won=0, lost=5)  # excluded + note
                _add_player(cursor, 2, "Bob", won=10, lost=1)
                _add_player(cursor, 3, "Carl", won=3, lost=1)
                conn.commit()

            view = LeaderboardView(ctx, category="wins")
            await view.update_leaderboard(ctx)

            entries = render_spy[-1]["entries"]
            assert [e[1] for e in entries] == ["Bob", "Carl"]
            assert layout_capture["note"] == "Win at least one game to be ranked"

        run(_body())


class TestLossesCategory:
    def test_zero_losses_excluded_note_and_sort(self, render_spy, layout_capture):
        async def _body():
            ctx = FakeCtx(guild_id=21, author_id=1)
            with elo_db.db_connection(21) as (conn, cursor):
                _add_player(cursor, 1, "Alice", won=5, lost=0)  # excluded + note
                _add_player(cursor, 2, "Bob", won=1, lost=8)
                _add_player(cursor, 3, "Carl", won=1, lost=2)
                conn.commit()

            view = LeaderboardView(ctx, category="losses")
            await view.update_leaderboard(ctx)

            entries = render_spy[-1]["entries"]
            assert [e[1] for e in entries] == ["Bob", "Carl"]
            assert layout_capture["note"] == "Lose at least one game to be ranked"

        run(_body())


class TestWinRateCategory:
    def test_under_ten_games_excluded(self, render_spy, layout_capture):
        async def _body():
            ctx = FakeCtx(guild_id=30, author_id=1)
            with elo_db.db_connection(30) as (conn, cursor):
                # Alice: 9 games total -> excluded, gets the ten-games note.
                _add_player(cursor, 1, "Alice", won=9, lost=0)
                # Bob: exactly 10 games -> included.
                _add_player(cursor, 2, "Bob", won=5, lost=5)
                conn.commit()

            view = LeaderboardView(ctx, category="win_rate")
            await view.update_leaderboard(ctx)

            entries = render_spy[-1]["entries"]
            assert [e[1] for e in entries] == ["Bob"]
            assert layout_capture["note"] == "Play at least ten games to be ranked"

        run(_body())

    def test_sorted_by_percentage_not_raw_win_count(self, render_spy):
        # Carl has far fewer absolute wins than Dan but a much higher
        # percentage - win_rate must rank by percentage, not by wins.
        async def _body():
            ctx = FakeCtx(guild_id=31, author_id=99)
            with elo_db.db_connection(31) as (conn, cursor):
                _add_player(cursor, 1, "Carl", won=9, lost=1)   # 90%
                _add_player(cursor, 2, "Dan", won=50, lost=50)  # 50%
                conn.commit()

            view = LeaderboardView(ctx, category="win_rate")
            await view.update_leaderboard(ctx)

            entries = render_spy[-1]["entries"]
            assert [e[1] for e in entries] == ["Carl", "Dan"]
            assert entries[0][2] == "90.00"

        run(_body())


class TestGamesPlayedThisMonthCategory:
    def test_sorted_by_monthly_games_independent_of_win_loss_totals(
        self, render_spy, layout_capture
    ):
        # Bob has more total wins/losses than Alice, but fewer *monthly*
        # games - the category must sort by games_played_this_month, not
        # by the overall won+lost total.
        async def _body():
            ctx = FakeCtx(guild_id=40, author_id=1)
            with elo_db.db_connection(40) as (conn, cursor):
                _add_player(cursor, 1, "Alice", won=1, lost=0, games_month=8)
                _add_player(cursor, 2, "Bob", won=20, lost=20, games_month=2)
                _add_player(cursor, 3, "Bench", won=0, lost=0, games_month=0)  # excluded
                conn.commit()

            view = LeaderboardView(ctx, category="games_played_this_month")
            await view.update_leaderboard(ctx)

            entries = render_spy[-1]["entries"]
            assert [e[1] for e in entries] == ["Alice", "Bob"]
            assert layout_capture["note"] is None  # eligible player needs no note

        run(_body())


class TestDailyStreakCategory:
    def test_stale_streak_zeroed_and_excluded_despite_nonzero_column(
        self, render_spy, layout_capture
    ):
        async def _body():
            ctx = FakeCtx(guild_id=50, author_id=1)
            with elo_db.db_connection(50) as (conn, cursor):
                _add_player(cursor, 1, "Alice", won=1, lost=0)
                _add_player(cursor, 2, "Bob", won=1, lost=0)
                # Alice's stored streak is nonzero but stale (3 days ago)
                # -> effective streak 0 -> excluded from the leaderboard.
                _set_daily_streak(cursor, 1, 7, _today_str(3))
                # Bob played yesterday -> still within the grace window.
                _set_daily_streak(cursor, 2, 4, _today_str(1))
                conn.commit()

            view = LeaderboardView(ctx, category="daily_streak")
            await view.update_leaderboard(ctx)

            entries = render_spy[-1]["entries"]
            assert [e[1] for e in entries] == ["Bob"]

        run(_body())

    def test_sorted_by_effective_streak_descending(self, render_spy):
        async def _body():
            ctx = FakeCtx(guild_id=51, author_id=99)
            with elo_db.db_connection(51) as (conn, cursor):
                _add_player(cursor, 1, "Low", won=1, lost=0)
                _add_player(cursor, 2, "High", won=1, lost=0)
                _set_daily_streak(cursor, 1, 2, _today_str(0))
                _set_daily_streak(cursor, 2, 9, _today_str(0))
                conn.commit()

            view = LeaderboardView(ctx, category="daily_streak")
            await view.update_leaderboard(ctx)

            entries = render_spy[-1]["entries"]
            assert [e[1] for e in entries] == ["High", "Low"]

        run(_body())


class TestWinStreakCategory:
    def test_zero_streak_excluded_and_sorted_desc(self, render_spy, layout_capture):
        async def _body():
            ctx = FakeCtx(guild_id=60, author_id=1)
            with elo_db.db_connection(60) as (conn, cursor):
                _add_player(cursor, 1, "Alice", won=3, lost=1, win_streak=0)  # excluded
                _add_player(cursor, 2, "Bob", won=5, lost=0, win_streak=5)
                _add_player(cursor, 3, "Carl", won=2, lost=0, win_streak=2)
                conn.commit()

            view = LeaderboardView(ctx, category="win_streak")
            await view.update_leaderboard(ctx)

            entries = render_spy[-1]["entries"]
            assert [e[1] for e in entries] == ["Bob", "Carl"]
            assert (
                layout_capture["note"] == "Win at least one game in a row to be ranked"
            )

        run(_body())


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


class TestPagination:
    def _seed_players(self, cursor, count):
        for i in range(count):
            _add_player(cursor, i + 1, f"P{i + 1}", elo=1000 - i, won=1, lost=0)

    def test_exact_three_pages_last_page_partial(self, render_spy):
        # 25 eligible players, per_page=10 -> pages of 10, 10, 5.
        async def _body():
            ctx = FakeCtx(guild_id=70, author_id=999)
            with elo_db.db_connection(70) as (conn, cursor):
                self._seed_players(cursor, 25)
                conn.commit()

            view = LeaderboardView(ctx, category="elo", page=2)
            await view.update_leaderboard(ctx)
            assert view.total_pages == 3
            assert len(render_spy[-1]["entries"]) == 5
            assert view.next_button.disabled is True
            assert view.prev_button.disabled is False

        run(_body())

    def test_first_page_prev_disabled_next_enabled(self, render_spy):
        async def _body():
            ctx = FakeCtx(guild_id=71, author_id=999)
            with elo_db.db_connection(71) as (conn, cursor):
                self._seed_players(cursor, 15)
                conn.commit()

            view = LeaderboardView(ctx, category="elo", page=0)
            await view.update_leaderboard(ctx)
            assert view.total_pages == 2
            assert view.prev_button.disabled is True
            assert view.next_button.disabled is False

        run(_body())

    def test_page_beyond_range_clamps_to_last_page_not_zero(self, render_spy):
        # Unlike history.py's pagination (which clamps a stale/out-of-range
        # page back to 0), the leaderboard clamps down to the *last valid*
        # page - `self.page = self.total_pages - 1`. Pin that behavior down
        # explicitly since it's easy to accidentally "fix" into matching
        # history's convention.
        async def _body():
            ctx = FakeCtx(guild_id=72, author_id=999)
            with elo_db.db_connection(72) as (conn, cursor):
                self._seed_players(cursor, 12)  # 2 pages (10, 2)
                conn.commit()

            view = LeaderboardView(ctx, category="elo", page=50)
            await view.update_leaderboard(ctx)
            assert view.total_pages == 2
            assert view.page == 1  # last valid page, not 0

        run(_body())


# ---------------------------------------------------------------------------
# My Rank jump-to-page
# ---------------------------------------------------------------------------


class TestMyRankJump:
    def test_jump_lands_on_targets_page(self, render_spy):
        async def _body():
            ctx = FakeCtx(guild_id=80, author_id=15)  # P15 -> index 14 -> page 1
            with elo_db.db_connection(80) as (conn, cursor):
                for i in range(25):
                    _add_player(cursor, i + 1, f"P{i + 1}", elo=1000 - i, won=1, lost=0)
                conn.commit()

            view = LeaderboardView(ctx, category="elo")
            view.jump_to_target = True
            await view.update_leaderboard(ctx)
            assert view.page == 1
            names = [e[1] for e in render_spy[-1]["entries"]]
            assert "P15" in names

        run(_body())

    def test_jump_flag_is_consumed_after_use(self, render_spy):
        async def _body():
            ctx = FakeCtx(guild_id=81, author_id=1)
            with elo_db.db_connection(81) as (conn, cursor):
                _add_player(cursor, 1, "Alice", won=1, lost=0)
                conn.commit()

            view = LeaderboardView(ctx, category="elo")
            view.jump_to_target = True
            await view.update_leaderboard(ctx)
            assert view.jump_to_target is False

            # Move away, then refresh again without setting jump_to_target -
            # page must NOT snap back to the target's page.
            view.page = 0
            await view.update_leaderboard(ctx)
            assert view.page == 0

        run(_body())


class TestMyRankButtonPermission:
    def test_wrong_user_gets_ephemeral_denial_and_state_unchanged(self):
        async def _body():
            ctx = FakeCtx(guild_id=90, author_id=1)  # command invoker is user 1
            view = LeaderboardView(ctx, category="elo")
            button = LeaderboardMyRankButton(view)

            interaction = FakeInteraction(user_id=999)  # a different user
            await button.callback(interaction)

            assert interaction.response.sent_ephemeral is True
            assert interaction.response.sent_embed is None  # plain content message
            assert interaction.response.sent_content == "You cannot perform this action."
            assert view.jump_to_target is False  # never set

        run(_body())


# ---------------------------------------------------------------------------
# Highlight / cache interaction
# ---------------------------------------------------------------------------


class TestHighlightDisablesMyRank:
    def test_target_on_page_disables_my_rank_button(self, render_spy):
        async def _body():
            ctx = FakeCtx(guild_id=100, author_id=2)
            with elo_db.db_connection(100) as (conn, cursor):
                _add_player(cursor, 1, "Alice", elo=600, won=1, lost=0)
                _add_player(cursor, 2, "Bob", elo=500, won=1, lost=0)
                conn.commit()

            view = LeaderboardView(ctx, category="elo")
            await view.update_leaderboard(ctx)
            assert view.my_rank_button.disabled is True

            entries = render_spy[-1]["entries"]
            bob_entry = next(e for e in entries if e[1] == "Bob")
            assert bob_entry[3] is True  # highlight flag

        run(_body())

    def test_target_not_on_page_leaves_my_rank_enabled(self, render_spy):
        async def _body():
            ctx = FakeCtx(guild_id=101, author_id=999)  # not registered
            with elo_db.db_connection(101) as (conn, cursor):
                _add_player(cursor, 1, "Alice", elo=600, won=1, lost=0)
                conn.commit()

            view = LeaderboardView(ctx, category="elo")
            await view.update_leaderboard(ctx)
            assert view.my_rank_button.disabled is False

        run(_body())


class TestCacheBehavior:
    def test_cache_hit_skips_render_returns_cached_bytes_verbatim(self, render_spy):
        async def _body():
            ctx = FakeCtx(guild_id=110, author_id=999)  # not on the page
            with elo_db.db_connection(110) as (conn, cursor):
                _add_player(cursor, 1, "Alice", elo=600, won=1, lost=0)
                conn.commit()

            lb_cache.put(110, "elo", 0, b"PRECACHED-BYTES")

            view = LeaderboardView(ctx, category="elo")
            await view.update_leaderboard(ctx)

            assert render_spy == []  # render never invoked
            assert ctx.sent_file.fp.read() == b"PRECACHED-BYTES"

        run(_body())

    def test_cache_miss_renders_fresh_and_does_not_populate_cache(self, render_spy):
        # Note: writing to the cache only happens via lb_cache's async
        # rebuild task (triggered elsewhere after a match report/revert),
        # never from the leaderboard command's own render path - a fresh
        # render here must NOT leave anything behind in the cache.
        async def _body():
            ctx = FakeCtx(guild_id=111, author_id=999)
            with elo_db.db_connection(111) as (conn, cursor):
                _add_player(cursor, 1, "Alice", elo=600, won=1, lost=0)
                conn.commit()

            view = LeaderboardView(ctx, category="elo")
            await view.update_leaderboard(ctx)

            assert len(render_spy) == 1
            assert lb_cache.get(111, "elo", 0) is None

        run(_body())

    def test_target_in_page_skips_cache_even_when_populated(self, render_spy):
        async def _body():
            ctx = FakeCtx(guild_id=112, author_id=1)  # Alice IS on the page
            with elo_db.db_connection(112) as (conn, cursor):
                _add_player(cursor, 1, "Alice", elo=600, won=1, lost=0)
                conn.commit()

            lb_cache.put(112, "elo", 0, b"STALE-CACHED-BYTES")

            view = LeaderboardView(ctx, category="elo")
            await view.update_leaderboard(ctx)

            assert len(render_spy) == 1  # rendered fresh, cache ignored
            assert ctx.sent_file.fp.read() != b"STALE-CACHED-BYTES"

        run(_body())

    def test_cross_server_dev_mode_skips_cache_even_when_populated(self, render_spy):
        async def _body():
            # Viewing guild 120's leaderboard while the command context's
            # own guild is 121 (dev-mode cross-server lookup).
            ctx = FakeCtx(guild_id=121, author_id=999)
            with elo_db.db_connection(120) as (conn, cursor):
                _add_player(cursor, 1, "Alice", elo=600, won=1, lost=0)
                conn.commit()

            lb_cache.put(120, "elo", 0, b"STALE-CACHED-BYTES")

            view = LeaderboardView(ctx, category="elo", db_guild_id=120)
            await view.update_leaderboard(ctx)

            assert len(render_spy) == 1
            assert ctx.sent_file.fp.read() != b"STALE-CACHED-BYTES"

        run(_body())


class TestDevModeNote:
    def test_dev_note_appended_for_cross_server_view(self, render_spy, layout_capture):
        async def _body():
            ctx = FakeCtx(guild_id=131, author_id=999)
            with elo_db.db_connection(130) as (conn, cursor):
                _add_player(cursor, 1, "Alice", elo=600, won=1, lost=0)
                conn.commit()

            view = LeaderboardView(ctx, category="elo", db_guild_id=130)
            await view.update_leaderboard(ctx)

            assert layout_capture["note"] == "Dev Mode - Leaderboards from Server 130"

        run(_body())

    def test_dev_note_combines_with_eligibility_note(self, render_spy, layout_capture):
        async def _body():
            ctx = FakeCtx(guild_id=141, author_id=1)
            with elo_db.db_connection(140) as (conn, cursor):
                _add_player(cursor, 1, "Alice", elo=500, won=0, lost=0)  # ineligible
                _add_player(cursor, 2, "Bob", elo=500, won=1, lost=0)
                conn.commit()

            view = LeaderboardView(ctx, category="elo", db_guild_id=140)
            await view.update_leaderboard(ctx)

            note = layout_capture["note"]
            assert note.startswith("Play at least one set to be ranked\n")
            assert note.endswith("Dev Mode - Leaderboards from Server 140")

        run(_body())