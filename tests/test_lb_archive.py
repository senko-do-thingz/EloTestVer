"""Regression tests for the complete seasonal leaderboard archive workflow.

The same archive parser is used by -lbs, /lbs, -lbs_id, expanded stats,
season aggregation, archive ID backfill, and player rename operations.
"""
from pathlib import Path

import pytest

from utils.lb_archive import (
    format_leaderboard_export_line,
    get_lb_root,
    parse_leaderboard_line,
    sync_discord_ids_to_lb_archives,
    update_lb_name,
)
from utils.lb_export import aggregate_season_categories


DISCORD_ID = "123456789012345678"
OTHER_ID = "987654321098765432"


@pytest.mark.parametrize("line,expected", [
    ("Sky 850 " + DISCORD_ID, ("Sky", 850, DISCORD_ID)),
    ("Sky 850", ("Sky", 850, None)),
    ("Dark Knight 850 " + DISCORD_ID, ("Dark Knight", 850, DISCORD_ID)),
    ("Dark Knight 850", ("Dark Knight", 850, None)),
    ("Player 2 Junior 123 " + DISCORD_ID, ("Player 2 Junior", 123, DISCORD_ID)),
    ("Nguyễn Văn A 600", ("Nguyễn Văn A", 600, None)),
    ("Nguyễn Văn A 600 " + DISCORD_ID, ("Nguyễn Văn A", 600, DISCORD_ID)),
    ("Mr. X_II 0 " + DISCORD_ID, ("Mr. X_II", 0, DISCORD_ID)),
    ("Dark Knight -20 " + DISCORD_ID, ("Dark Knight", -20, DISCORD_ID)),
    ("  Dark   Knight   850   " + DISCORD_ID + " ",
     ("Dark   Knight", 850, DISCORD_ID)),
    ("Name With 2 Numbers 3 4", ("Name With 2 Numbers 3", 4, None)),
    ("", None),
    ("Malformed", None),
    ("Elo Leaderboard", None),
    ("Win Rate Leaderboard", None),
    ("Dark Knight invalid " + DISCORD_ID, None),
    ("Dark Knight 3.4 " + DISCORD_ID, None),
    ("Dark Knight 3.4", None),
])
def test_integer_archives_with_legacy_and_modern_names(line, expected):
    assert parse_leaderboard_line(line) == expected


@pytest.mark.parametrize("line,expected", [
    ("Sky 75.50 " + DISCORD_ID, ("Sky", 75.5, DISCORD_ID)),
    ("Dark Knight 75.50 " + DISCORD_ID,
     ("Dark Knight", 75.5, DISCORD_ID)),
    ("Dark Knight 75.50", ("Dark Knight", 75.5, None)),
    ("Nguyễn Văn A 100.00 " + DISCORD_ID,
     ("Nguyễn Văn A", 100.0, DISCORD_ID)),
    ("Player 2 Junior 0.00", ("Player 2 Junior", 0.0, None)),
    ("Dark Knight nan " + DISCORD_ID, None),
    ("Dark Knight inf " + DISCORD_ID, None),
    ("Dark Knight -inf", None),
    ("Dark Knight not-a-number", None),
])
def test_win_rate_archive_parsing(line, expected):
    assert parse_leaderboard_line(line, win_rate=True) == expected


@pytest.mark.parametrize("filename,win_rate,value", [
    ("data.txt", False, 850), ("w.txt", False, 9),
    ("l.txt", False, 4), ("wr.txt", True, 69.23),
    ("games.txt", False, 13),
])
def test_all_five_archive_categories(filename, win_rate, value, tmp_path):
    file_path = tmp_path / filename
    numeric = "69.23" if win_rate else str(value)
    file_path.write_text(
        f"{filename} Leaderboard\n\nDark Knight {numeric} {DISCORD_ID}\n",
        encoding="utf-8",
    )
    lines = [
        parse_leaderboard_line(row, win_rate=win_rate)
        for row in file_path.read_text(encoding="utf-8").splitlines()
    ]
    assert [r for r in lines if r] == [("Dark Knight", value, DISCORD_ID)]


def test_archive_export_format_can_omit_legacy_id():
    assert format_leaderboard_export_line("Dark Knight", 850, DISCORD_ID) == (
        f"Dark Knight 850 {DISCORD_ID}"
    )
    assert format_leaderboard_export_line("Dark Knight", 850, None) == "Dark Knight 850"


def test_get_lb_root_accepts_named_guild_folder(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    folder = tmp_path / "lb" / "123_My Server"
    folder.mkdir(parents=True)
    assert Path(get_lb_root(123)).samefile(folder)


def test_archive_rename_updates_all_categories_and_preserves_id(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    month = tmp_path / "lb" / "123" / "Season 1" / "Month 1"
    overall = tmp_path / "lb" / "123" / "Season 1" / "Overall"
    month.mkdir(parents=True)
    overall.mkdir(parents=True)
    names = ["data.txt", "w.txt", "l.txt", "wr.txt", "games.txt"]
    for folder in (month, overall):
        for filename in names:
            value = "75.500" if filename == "wr.txt" else "850"
            (folder / filename).write_text(
                f"Leaderboard\nDark Knight {value} {DISCORD_ID}\n"
                f"Dark Knight {value} {OTHER_ID}\n"
                f"Dark Knight {value}\n"
                "Sky 500\n",
                encoding="utf-8",
            )
    update_lb_name(123, "Dark Knight", "Dark Guardian", discord_id=DISCORD_ID)
    for folder in (month, overall):
        for filename in names:
            content = (folder / filename).read_text(encoding="utf-8")
            value = "75.500" if filename == "wr.txt" else "850"
            assert content.startswith("Leaderboard\n")
            assert f"Dark Guardian {value} {DISCORD_ID}\n" in content
            assert f"Dark Knight {value} {OTHER_ID}\n" in content
            assert f"Dark Guardian {value}\n" in content
            assert "Sky 500\n" in content


def test_archive_rename_legacy_two_column_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    folder = tmp_path / "lb" / "123" / "Season 1" / "Month 1"
    folder.mkdir(parents=True)
    file_path = folder / "data.txt"
    file_path.write_text("Dark Knight 850\nSky 700\n", encoding="utf-8")
    update_lb_name(123, "Dark Knight", "Dark Guardian")
    assert file_path.read_text(encoding="utf-8") == "Dark Guardian 850\nSky 700\n"


def test_archive_rename_preserves_windows_line_endings(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    folder = tmp_path / "lb" / "123" / "Season 1" / "Month 1"
    folder.mkdir(parents=True)
    path = folder / "data.txt"
    path.write_bytes(b"Elo Leaderboard\r\nDark Knight 900 123456789012345678\r\n")
    update_lb_name(123, "Dark Knight", "Dark Guardian", discord_id=DISCORD_ID)
    assert path.read_bytes() == (
        b"Elo Leaderboard\r\nDark Guardian 900 123456789012345678\r\n"
    )


def test_archive_rename_missing_folder_is_no_op(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    update_lb_name(111, "Old Name", "New Name")


def test_backfill_ids_in_all_five_categories_without_changing_other_ids(
    tmp_path,
):
    folder = tmp_path / "lb" / "123" / "Season 2" / "Month 1"
    folder.mkdir(parents=True)
    for filename in ("data.txt", "w.txt", "l.txt", "wr.txt", "games.txt"):
        value = "50.500" if filename == "wr.txt" else "400"
        (folder / filename).write_text(
            f"{filename} Leaderboard\n\n"
            f"Dark Knight {value}\n"
            f"Dark Knight {value} {OTHER_ID}\n"
            f"Sky {value} {DISCORD_ID}\n",
            encoding="utf-8",
        )
    result = sync_discord_ids_to_lb_archives(
        str(tmp_path / "lb" / "123"), {"dark knight": DISCORD_ID}
    )
    assert result == (5, 5, 1)
    for filename in ("data.txt", "w.txt", "l.txt", "wr.txt", "games.txt"):
        value = "50.500" if filename == "wr.txt" else "400"
        text = (folder / filename).read_text(encoding="utf-8")
        assert f"Dark Knight {value} {DISCORD_ID}" in text
        assert f"Dark Knight {value} {OTHER_ID}" in text
        assert f"Sky {value} {DISCORD_ID}" in text


def test_backfill_ids_is_idempotent(tmp_path):
    folder = tmp_path / "Season 1"
    folder.mkdir()
    file_path = folder / "data.txt"
    file_path.write_text("Dark Knight 500\n", encoding="utf-8")
    mapping = {"dark knight": DISCORD_ID}
    assert sync_discord_ids_to_lb_archives(str(folder), mapping) == (1, 1, 1)
    assert sync_discord_ids_to_lb_archives(str(folder), mapping) == (0, 0, 0)


def test_season_aggregation_uses_id_across_multiword_renames(tmp_path):
    season = tmp_path / "Season 1"
    first = season / "Month 1"
    second = season / "Month 2"
    first.mkdir(parents=True)
    second.mkdir()
    (first / "data.txt").write_text(
        f"Dark Knight 700 {DISCORD_ID}\nSky 300 {OTHER_ID}\n", encoding="utf-8"
    )
    (second / "data.txt").write_text(
        f"Dark Guardian 800 {DISCORD_ID}\nSky 400 {OTHER_ID}\n",
        encoding="utf-8",
    )
    (first / "w.txt").write_text(
        f"Dark Knight 3 {DISCORD_ID}\nSky 1 {OTHER_ID}\n", encoding="utf-8"
    )
    (second / "w.txt").write_text(
        f"Dark Guardian 5 {DISCORD_ID}\nSky 2 {OTHER_ID}\n", encoding="utf-8"
    )
    (first / "l.txt").write_text(
        f"Dark Knight 1 {DISCORD_ID}\nSky 3 {OTHER_ID}\n", encoding="utf-8"
    )
    (second / "l.txt").write_text(
        f"Dark Guardian 2 {DISCORD_ID}\nSky 2 {OTHER_ID}\n", encoding="utf-8"
    )
    combined = aggregate_season_categories(str(season))
    assert f"Dark Guardian 1500 {DISCORD_ID}" in combined["data.txt"]
    assert f"Dark Guardian 8 {DISCORD_ID}" in combined["w.txt"]
    assert f"Dark Guardian 3 {DISCORD_ID}" in combined["l.txt"]
    assert f"Dark Guardian 72.73 {DISCORD_ID}" in combined["wr.txt"]


def test_legacy_season_aggregation_keeps_multiword_name(tmp_path):
    season = tmp_path / "Season 2"
    month = season / "Month 1"
    month.mkdir(parents=True)
    (month / "data.txt").write_text("Dark Knight 600\n", encoding="utf-8")
    (month / "w.txt").write_text("Dark Knight 3\n", encoding="utf-8")
    (month / "l.txt").write_text("Dark Knight 1\n", encoding="utf-8")
    combined = aggregate_season_categories(str(season))
    assert "Dark Knight 600" in combined["data.txt"]
    assert "Dark Knight 75.00" in combined["wr.txt"]
