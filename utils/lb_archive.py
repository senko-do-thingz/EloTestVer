"""Shared reader and maintenance helpers for seasonal leaderboard archives.

Archive records have one of these whitespace-delimited layouts:

    <player name> <stat>
    <player name> <stat> <discord id>

Player names may contain spaces, numbers, and accented Latin characters.
Only the numeric stat and optional Discord ID are positional fields.
"""

import math
import os
import re


def _is_discord_id(value: str) -> bool:
    return value.isdigit() and len(value) >= 17


def _split_leaderboard_fields(
    line: str, *, win_rate: bool = False
) -> tuple[str, str, str | None] | None:
    """Split archive fields from the right without splitting a player name.

    Return the original numeric token as a string so maintenance operations
    can preserve formatting, for example 75.500 rather than 75.50.
    """
    stripped = line.strip()
    if not stripped or stripped.lower().endswith("leaderboard"):
        return None

    try:
        name, last_field = stripped.rsplit(None, 1)
    except ValueError:
        return None

    discord_id = None
    if _is_discord_id(last_field):
        try:
            # An ID is present only if the preceding token is a valid stat.
            # Existing archives without IDs remain supported.
            possible_name, possible_value = name.rsplit(None, 1)
            parsed_number = float(possible_value) if win_rate else int(possible_value)
        except (ValueError, OverflowError):
            # A single numeric field could be a legacy value. Otherwise
            # a line ending in an ID with a nonnumeric stat is malformed.
            if len(stripped.split()) > 2:
                return None
        else:
            if win_rate and not math.isfinite(parsed_number):
                return None
            if possible_name.strip():
                name, last_field, discord_id = (
                    possible_name, possible_value, last_field
                )

    if not name.strip():
        return None
    try:
        value = float(last_field) if win_rate else int(last_field)
    except (ValueError, OverflowError):
        return None
    if win_rate and not math.isfinite(value):
        return None
    return name, last_field, discord_id


def parse_leaderboard_line(
    line: str, *, win_rate: bool = False
) -> tuple[str, float | int, str | None] | None:
    """Parse a leaderboard line with an optional ID and multi-word name."""
    fields = _split_leaderboard_fields(line, win_rate=win_rate)
    if fields is None:
        return None
    name, value_str, discord_id = fields
    return name, float(value_str) if win_rate else int(value_str), discord_id


def format_leaderboard_export_line(name: str, value, discord_id) -> str:
    """Format a record for an archive or exported leaderboard file."""
    if discord_id is None:
        return f"{name} {value}"
    return f"{name} {value} {discord_id}"


LB_ARCHIVE_FILES = {
    "data.txt": False,
    "w.txt": False,
    "l.txt": False,
    "wr.txt": True,
    "games.txt": False,
}


def get_lb_root(guild_id: int | str) -> str:
    """Resolve lb/<guild_id>/ or an existing lb/<guild_id>_<note>/ folder."""
    guild_id_str = str(guild_id)
    direct = os.path.join("lb", guild_id_str)
    if os.path.isdir(direct):
        return direct

    lb_root = "lb"
    try:
        entries = os.listdir(lb_root) if os.path.isdir(lb_root) else []
    except OSError:
        return direct

    for entry in entries:
        full = os.path.join(lb_root, entry)
        if not os.path.isdir(full):
            continue
        match = re.match(r"^(\d+)", entry)
        if match and match.group(1) == guild_id_str:
            return full
    return direct


def sync_discord_ids_to_lb_archives(
    lb_root: str, players_by_name: dict[str, str]
) -> tuple[int, int, int]:
    """Backfill missing archive IDs using current registered player names.

    Existing Discord IDs are stable identities and must never be reassigned
    merely because a historical name was reused by another account.
    Return (files_updated, lines_updated, players_updated).
    """
    if not players_by_name or not os.path.isdir(lb_root):
        return 0, 0, 0

    files_updated = 0
    lines_updated = 0
    players_updated: set[str] = set()

    for dirpath, _, filenames in os.walk(lb_root):
        for filename in filenames:
            if filename not in LB_ARCHIVE_FILES:
                continue
            filepath = os.path.join(dirpath, filename)
            win_rate = LB_ARCHIVE_FILES[filename]

            with open(filepath, "r", encoding="utf-8", newline="") as f:
                lines = f.readlines()

            changed = False
            new_lines: list[str] = []
            for line in lines:
                fields = _split_leaderboard_fields(line, win_rate=win_rate)
                if fields is None:
                    new_lines.append(line)
                    continue

                name, value_token, existing_id = fields
                discord_id = players_by_name.get(name.lower())
                if not discord_id or (existing_id is not None and existing_id != discord_id):
                    new_lines.append(line)
                    continue

                updated = format_leaderboard_export_line(
                    name, value_token, discord_id
                )
                original = line.rstrip("\r\n")
                if updated != original:
                    changed = True
                    lines_updated += 1
                    players_updated.add(name.lower())
                    line_ending = line[len(original):]
                    new_lines.append(updated + (line_ending or "\n"))
                else:
                    new_lines.append(line)

            if changed:
                with open(filepath, "w", encoding="utf-8", newline="") as f:
                    f.writelines(new_lines)
                files_updated += 1

    return files_updated, lines_updated, len(players_updated)


def update_lb_name(
    guild_id: int | str,
    old_name: str,
    new_name: str,
    *,
    discord_id: int | str | None = None,
) -> None:
    """Rename a player's archived entries in all five leaderboard categories.

    Retain the original value token, Discord ID, header lines and line ending.
    When a known ID is supplied, never rename an entry belonging to a
    different ID. ID-less historical entries can only be matched by name.
    """
    base_dir = get_lb_root(guild_id)
    if not os.path.isdir(base_dir):
        return

    target_id = str(discord_id) if discord_id is not None else None

    for root, _, filenames in os.walk(base_dir):
        for filename in filenames:
            if filename not in LB_ARCHIVE_FILES:
                continue
            file_path = os.path.join(root, filename)
            try:
                with open(file_path, "r", encoding="utf-8", newline="") as f:
                    lines = f.readlines()

                changed = False
                new_lines: list[str] = []
                for line in lines:
                    fields = _split_leaderboard_fields(
                        line, win_rate=LB_ARCHIVE_FILES[filename]
                    )
                    if fields is None:
                        new_lines.append(line)
                        continue
                    name, value_token, existing_id = fields
                    if (name.casefold() != old_name.casefold() or
                            (target_id is not None and existing_id is not None
                             and existing_id != target_id)):
                        new_lines.append(line)
                        continue

                    updated = format_leaderboard_export_line(
                        new_name, value_token, existing_id
                    )
                    original = line.rstrip("\r\n")
                    if updated != original:
                        changed = True
                        ending = line[len(original):]
                        new_lines.append(updated + (ending or "\n"))
                    else:
                        new_lines.append(line)

                if changed:
                    with open(file_path, "w", encoding="utf-8", newline="") as f:
                        f.writelines(new_lines)
            except OSError:
                # Preserve the behavior of ignoring inaccessible old archives.
                continue
