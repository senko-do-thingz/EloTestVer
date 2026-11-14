"""Coverage of the seasonal leaderboard archive format.

Multi-word names are a documented known issue; xfail cases become regular
tests when the proposed -lbs parser/rename fix is implemented.
"""
import pytest
from utils.lb_archive import parse_leaderboard_line, update_lb_name


@pytest.mark.parametrize("line, expected", [
    ("Sky 850 123456789012345678", ("Sky", 850, "123456789012345678")),
    ("Sky 850", ("Sky", 850, None)),
    ("", None),
    ("Malformed", None),
    ("Elo Leaderboard", None),
])
def test_single_word_and_invalid_archive_lines(line, expected):
    assert parse_leaderboard_line(line) == expected


def test_win_rate_parsing():
    assert parse_leaderboard_line(
        "Sky 75.50 123456789012345678", win_rate=True
    ) == ("Sky", 75.5, "123456789012345678")


@pytest.mark.xfail(strict=True, reason="Known -lbs parser bug with spaces in names")
@pytest.mark.parametrize("line, win_rate, expected", [
    ("Dark Knight 850 123456789012345678", False,
     ("Dark Knight", 850, "123456789012345678")),
    ("Dark Knight 850", False, ("Dark Knight", 850, None)),
    ("Dark Knight 75.50 123456789012345678", True,
     ("Dark Knight", 75.5, "123456789012345678")),
    ("Player 2 Junior 123 123456789012345678", False,
     ("Player 2 Junior", 123, "123456789012345678")),
    ("Nguyen Van A 600", False, ("Nguyen Van A", 600, None)),
])
def test_multi_word_names_known_issue(line, win_rate, expected):
    assert parse_leaderboard_line(line, win_rate=win_rate) == expected


@pytest.mark.xfail(strict=True, reason="Known archive rename bug with multi-word names")
def test_multi_word_archive_rename_known_issue(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    folder = tmp_path / "lb" / "123" / "Season 1" / "Month 1"
    folder.mkdir(parents=True)
    f = folder / "data.txt"
    f.write_text("Dark Knight 850\nSky 700\n", encoding="utf-8")
    update_lb_name(123, "Dark Knight", "Dark Guardian")
    assert f.read_text(encoding="utf-8") == "Dark Guardian 850\nSky 700\n"
