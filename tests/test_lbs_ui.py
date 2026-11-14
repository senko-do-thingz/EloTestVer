"""Offline UI and cross-feature integration tests for seasonal leaderboards.

Discord interactions and image rendering are mocked, but the seasonal view,
archive files, database lookups, and paginator/search behavior are real.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

import db.elo_db as db
import cogs.seasonal_lb.lb_view as lb_view
from cogs.seasonal_lb.base import SeasonalLBBaseCog
from cogs.seasonal_lb.lb_view import LBView, PlayerSearchModal
from cogs.stats import (
    _find_player_name_in_archive,
    _get_rank_for_stat,
    _read_player_stat,
)

ID1 = "123456789012345678"
ID2 = "987654321098765432"


def run(coro):
    """Reuse the test event loop so older tests retain get_event_loop support."""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_FOLDER", str(tmp_path / "databases"))
    monkeypatch.setattr(db, "_migrated_dbs", set())
    return 987654321


def write_categories(directory, name="Dark Knight", player_id=ID1):
    directory.mkdir(parents=True, exist_ok=True)
    data = {
        "data.txt": "850",
        "w.txt": "7",
        "l.txt": "3",
        "wr.txt": "70.00",
        "games.txt": "10",
    }
    for filename, value in data.items():
        (directory / filename).write_text(
            f"{filename} Leaderboard\n\n{name} {value} {player_id}\n",
            encoding="utf-8",
        )


def fake_interaction(user_id):
    response = SimpleNamespace(
        edit_message=AsyncMock(),
        send_message=AsyncMock(),
        is_done=lambda: False,
    )
    return SimpleNamespace(
        user=SimpleNamespace(id=int(user_id)),
        response=response,
        message=SimpleNamespace(id=111),
    )


def test_archived_stats_lookup_returns_full_name_by_discord_id(tmp_path):
    folder = tmp_path / "Season 1" / "Month 1"
    write_categories(folder, "Nguyễn Văn A")
    assert _find_player_name_in_archive(str(folder), int(ID1)) == "Nguyễn Văn A"
    assert _read_player_stat(str(folder), "data.txt", ID1) == 850
    assert _read_player_stat(str(folder), "wr.txt", ID1, win_rate=True) == 70.0
    assert _read_player_stat(str(folder), "data.txt", ID2) is None
    assert _get_rank_for_stat(str(folder), "data.txt", 850) == 1


def test_season_picker_discovers_folders_containing_archives(tmp_path):
    base = tmp_path / "lb" / "987"
    write_categories(base / "Season 2" / "Month 1")
    write_categories(base / "Season 10" / "Month 1")

    async def exercise():
        view, embed = SeasonalLBBaseCog._build_season_view(str(base), 987, int(ID1))
        assert view is not None
        assert [option.label for option in view.children[0].options] == [
            "Season 2", "Season 10"
        ]
        assert embed.title == "Select a Season"

    run(exercise())


def test_lbs_renders_multiword_names_and_jumps_to_correct_page(
    tmp_path, monkeypatch, isolated_db,
):
    archive = tmp_path / "Season 1" / "Month 1"
    archive.mkdir(parents=True)
    ids = [str(123456789012345000 + n) for n in range(15)]
    lines = [
        f"Dark Knight {n:02} {1000-n} {player_id}"
        for n, player_id in enumerate(ids)
    ]
    (archive / "data.txt").write_text(
        "Elo Leaderboard\n\n" + "\n".join(lines),
        encoding="utf-8",
    )
    captured = []

    def mock_render(title, entries, **kwargs):
        captured.append((title, entries, kwargs))
        return discord.Embed(title="test"), object()

    monkeypatch.setattr(lb_view, "build_leaderboard_embed", mock_render)

    async def exercise():
        view = LBView(
            "Season 1", "Month 1", str(archive), ["Month 1"],
            isolated_db, invoker_id=int(ids[12]),
        )
        view.jump_to_target = True
        interaction = fake_interaction(ids[12])
        await view.update(interaction)

        assert view.total_pages == 2
        assert view.page == 1
        assert len(captured) == 1
        entries = captured[0][1]
        assert [entry[1] for entry in entries] == [
            f"Dark Knight {n:02}" for n in range(10, 15)
        ]
        assert entries[2] == (13, "Dark Knight 12", "988", True)
        assert view.my_rank_button.disabled
        interaction.response.edit_message.assert_awaited_once()

    run(exercise())


def test_search_modal_merges_legacy_and_identified_categories(
    tmp_path, isolated_db,
):
    folder = tmp_path / "Season 1" / "Month 1"
    write_categories(folder)
    # Some legacy archive files have no Discord ID.
    (folder / "wr.txt").write_text("Dark Knight 70.00\n", encoding="utf-8")

    async def exercise():
        view = LBView(
            "Season 1", "Month 1", str(folder), ["Month 1"],
            isolated_db, invoker_id=int(ID1),
        )
        modal = PlayerSearchModal(view)
        modal.player_name._value = "knight"
        interaction = fake_interaction(ID1)
        await modal.on_submit(interaction)
        interaction.response.send_message.assert_awaited_once()
        kwargs = interaction.response.send_message.await_args.kwargs
        assert kwargs["ephemeral"]
        embed = kwargs["embed"]
        assert embed.title == "All Stats for Dark Knight"
        assert "850" in embed.description
        assert "70.00%" in embed.description
        assert "Monthly Games" in embed.description

    run(exercise())


def test_search_modal_does_not_combine_two_players_with_reused_name(
    tmp_path, isolated_db,
):
    folder = tmp_path / "Season 1" / "Month 1"
    write_categories(folder)
    for filename in ("data.txt", "w.txt"):
        path = folder / filename
        with path.open("a", encoding="utf-8") as f:
            f.write(f"Dark Knight 400 {ID2}\n")

    async def exercise():
        view = LBView(
            "Season 1", "Month 1", str(folder), ["Month 1"],
            isolated_db, invoker_id=int(ID1),
        )
        modal = PlayerSearchModal(view)
        modal.player_name._value = "Dark Knight"
        interaction = fake_interaction(ID1)
        await modal.on_submit(interaction)
        embed = interaction.response.send_message.await_args.kwargs["embed"]
        assert embed.title == "Multiple Players Found"
        assert "Found 2 players" in embed.description

    run(exercise())


def test_legacy_my_rank_falls_back_to_registered_multiword_name(
    tmp_path, monkeypatch, isolated_db,
):
    folder = tmp_path / "Month 1"
    folder.mkdir()
    (folder / "data.txt").write_text(
        "Dark Knight 900\nSky Runner 800\n", encoding="utf-8",
    )
    with db.db_connection(isolated_db) as (conn, cursor):
        cursor.execute(
            "INSERT INTO players(id,name,elo) VALUES (?,?,?)",
            (ID1, "Sky Runner", 800),
        )
        conn.commit()

    captured = []
    monkeypatch.setattr(
        lb_view, "build_leaderboard_embed",
        lambda title, entries, **kwargs: (
            captured.append(entries) or discord.Embed(title="test"), object()
        ),
    )

    async def exercise():
        view = LBView(
            "Season 1", "Month 1", str(folder), ["Month 1"],
            isolated_db, invoker_id=int(ID1),
        )
        view.jump_to_target = True
        await view.update(fake_interaction(ID1))
        assert view.page == 0
        assert captured[0][1] == (2, "Sky Runner", "800", True)

    run(exercise())
