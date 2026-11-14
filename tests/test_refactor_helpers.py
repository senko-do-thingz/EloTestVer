"""Behavioral regression coverage for helpers extracted from Discord cogs.

The refactor must not change match-score validation, archive parsing,
relative time labels or the package-level import interface.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import utils
from cogs.reporting import ReportingCog
from utils import name_sync, time_format
from utils.lb_archive import parse_leaderboard_lines, read_leaderboard_rows
from utils.score_parsing import parse_combined_score


def run(coro):
    """Reuse a synchronous test loop to avoid disturbing other test modules."""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


@pytest.mark.parametrize(
    "score, expected",
    [
        ("31", (3, 1)),
        ("4 2", (4, 2)),
        ("4-2", (4, 2)),
        ("4: 2", (4, 2)),
        ("0:1", (0, 1)),
        ("2-2", (2, 2)),
        ("", None),
        (" 3-1", None),
        ("3-1 ", None),
        ("33-1", None),
        ("13-2", None),
        ("3-1-0", None),
        ("3.1", None),
    ],
)
def test_compact_score_parser_preserves_legacy_rules(score, expected):
    assert parse_combined_score(score) == expected


def test_admin_score_parser_still_strips_outer_whitespace():
    assert ReportingCog(None)._parse_combined_score(" 3-1 ") == (3, 1)
    assert ReportingCog(None)._parse_combined_score("3: 1") == (3, 1)


def test_member_report_parser_with_combined_score_and_discord_fetch():
    winner = SimpleNamespace(id=123)
    loser = SimpleNamespace(id=456)
    bot = SimpleNamespace(fetch_user=AsyncMock(return_value=loser))
    result = run(
        ReportingCog(bot)._parse_score_args(winner, "31", "<@456>", None)
    )
    assert result == (winner, loser, 3, 1)
    bot.fetch_user.assert_awaited_once_with(456)


def test_member_report_parser_can_reverse_combined_winner():
    reporter = SimpleNamespace(id=123)
    opponent = SimpleNamespace(id=456)
    bot = SimpleNamespace(fetch_user=AsyncMock(return_value=opponent))
    assert run(
        ReportingCog(bot)._parse_score_args(reporter, "1-3", "<@456>", None)
    ) == (opponent, reporter, 3, 1)


def test_member_report_parser_keeps_explicit_score_format():
    player1 = SimpleNamespace(id=123)
    player2 = SimpleNamespace(id=456)
    cog = ReportingCog(None)
    assert run(cog._parse_score_args(player1, "3", "1", player2)) == (
        player1, player2, 3, 1
    )
    assert run(cog._parse_score_args(player1, "1", "4", player2)) == (
        player2, player1, 4, 1
    )


@pytest.mark.parametrize(
    "score, expected_error",
    [
        ("2-2", "Scores cannot be equal."),
        ("bad", "Invalid score format."),
        (" 3-1", "Invalid score format."),
    ],
)
def test_report_score_errors_are_unchanged(score, expected_error):
    user = SimpleNamespace(id=123)
    result = run(
        ReportingCog(None)._parse_score_args(user, score, "<@456>", None)
    )
    assert isinstance(result, str)
    assert result.startswith(expected_error)


def test_archive_row_parser_skips_headers_and_invalid_records():
    entries = [
        "Elo Leaderboard\n",
        "\n",
        "Dark Knight 850 123456789012345678\n",
        "Bad Row\n",
        "Nguyễn Văn A 700\n",
    ]
    assert parse_leaderboard_lines(entries) == [
        ("Dark Knight", 850, "123456789012345678"),
        ("Nguyễn Văn A", 700, None),
    ]


def test_archive_file_reader_handles_missing_file(tmp_path):
    assert read_leaderboard_rows(str(tmp_path / "missing.txt")) == []


def test_archive_file_reader_supports_win_rate_and_legacy_names(tmp_path):
    archive = tmp_path / "wr.txt"
    archive.write_text(
        "Win Rate Leaderboard\n\n"
        "Dark Knight 75.50 123456789012345678\n"
        "Nguyễn Văn A 50.00\n",
        encoding="utf-8",
    )
    assert read_leaderboard_rows(str(archive), win_rate=True) == [
        ("Dark Knight", 75.5, "123456789012345678"),
        ("Nguyễn Văn A", 50.0, None),
    ]


def test_package_imports_still_export_moved_helpers():
    assert utils.sync_against_names is name_sync.sync_against_names
    assert utils.format_relative_time is time_format.format_relative_time


def test_name_sync_delegates_both_updates_in_order(monkeypatch):
    calls = []
    monkeypatch.setattr(
        name_sync, "rename_opponent_in_h2h",
        lambda cursor, old, new: calls.append(("h2h", cursor, old, new)),
    )
    monkeypatch.setattr(
        name_sync, "rename_opponent_in_match_history",
        lambda cursor, old, new: calls.append(("history", cursor, old, new)),
    )
    fake_cursor = object()
    utils.sync_against_names(fake_cursor, "Dark Knight", "Dark Guardian")
    assert calls == [
        ("h2h", fake_cursor, "Dark Knight", "Dark Guardian"),
        ("history", fake_cursor, "Dark Knight", "Dark Guardian"),
    ]


def test_relative_time_formatting_is_backwards_compatible():
    now = datetime.now(timezone.utc)
    assert utils.format_relative_time(now) == "just now"
    assert utils.format_relative_time(now - timedelta(days=2)) == "2 days ago"
    assert utils.format_relative_time(
        (now - timedelta(days=2)).replace(tzinfo=None)
    ) == "2 days ago"
