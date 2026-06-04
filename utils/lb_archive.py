import os
import re


def _is_discord_id(value: str) -> bool:
    return value.isdigit() and len(value) >= 17


def parse_leaderboard_line(
    line: str, *, win_rate: bool = False
) -> tuple[str, float | int, str | None] | None:
    """Parse `name value` or `name value discord_id` export lines."""
    stripped = line.strip()
    if not stripped or stripped.lower().endswith("leaderboard"):
        return None
    parts = stripped.split()
    if len(parts) < 2:
        return None
    discord_id = None
    if len(parts) >= 3 and _is_discord_id(parts[-1]):
        discord_id = parts[-1]
        parts = parts[:-1]
    if len(parts) < 2:
        return None
    name = parts[0]
    value_str = parts[1]
    try:
        if win_rate:
            return name, float(value_str), discord_id
        return name, int(value_str), discord_id
    except ValueError:
        return None


def format_leaderboard_export_line(name: str, value, discord_id) -> str:
    return f"{name} {value} {discord_id}"


LB_ARCHIVE_FILES = {
    "data.txt": False,
    "w.txt": False,
    "l.txt": False,
    "wr.txt": True,
    "games.txt": False,
}


def get_lb_root(guild_id: int | str) -> str:
    """
    Resolve leaderboard archive root folder for a guild.

    Supports folder names like:
    - lb/<guild_id>/
    - lb/<guild_id>_<note>/  (trim and match by leading digits)
    """
    guild_id_str = str(guild_id)
    direct = os.path.join("lb", guild_id_str)
    if os.path.isdir(direct):
        return direct

    lb_root = "lb"
    try:
        entries = os.listdir(lb_root) if os.path.isdir(lb_root) else []
    except Exception:
        return direct

    for entry in entries:
        full = os.path.join(lb_root, entry)
        if not os.path.isdir(full):
            continue
        # Take only leading digits from folder name.
        m = re.match(r"^(\d+)", entry)
        if not m:
            continue
        if m.group(1) == guild_id_str:
            return full

    return direct


def sync_discord_ids_to_lb_archives(
    lb_root: str, players_by_name: dict[str, str]
) -> tuple[int, int, int]:
    """Set discord_id on archive lines for every registered name in players_by_name.

    players_by_name maps lowercase player name -> discord_id string.
    Returns (files_updated, lines_updated, players_updated).
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

            with open(filepath, "r", encoding="utf-8") as f:
                lines = f.readlines()

            changed = False
            new_lines: list[str] = []
            for line in lines:
                stripped = line.rstrip("\r\n")
                if not stripped:
                    new_lines.append(line)
                    continue

                parsed = parse_leaderboard_line(stripped, win_rate=win_rate)
                if not parsed:
                    new_lines.append(line)
                    continue

                name, value, _ = parsed
                discord_id = players_by_name.get(name.lower())
                if not discord_id:
                    new_lines.append(line)
                    continue

                export_value = f"{value:.2f}" if win_rate else value
                updated = format_leaderboard_export_line(name, export_value, discord_id)
                if updated != stripped:
                    changed = True
                    lines_updated += 1
                    players_updated.add(name.lower())
                new_lines.append(updated + "\n")

            if changed:
                with open(filepath, "w", encoding="utf-8", newline="\n") as f:
                    f.writelines(new_lines)
                files_updated += 1

    return files_updated, lines_updated, len(players_updated)


def update_lb_name(guild_id: int | str, old_name: str, new_name: str) -> None:
    """Update old_name to new_name in data.txt files under lb/<guild_id>/.

    This assumes each relevant line in data.txt has the format: "<name> <elo>".
    Lines that do not match this format are left untouched.
    """
    base_dir = get_lb_root(guild_id)
    if not os.path.exists(base_dir):
        return

    for root, _, files in os.walk(base_dir):
        for file in files:
            if file != "data.txt":
                continue
            file_path = os.path.join(root, file)
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    lines = f.readlines()

                changed = False
                new_lines: list[str] = []
                for line in lines:
                    stripped = line.strip()
                    if not stripped:
                        new_lines.append(line)
                        continue
                    parts = stripped.split()
                    if len(parts) != 2:
                        new_lines.append(line)
                        continue
                    name_token, elo_token = parts[0], parts[1]
                    if name_token == old_name:
                        changed = True
                        new_lines.append(f"{new_name} {elo_token}\n")
                    else:
                        new_lines.append(line)

                if changed:
                    with open(file_path, "w", encoding="utf-8") as f:
                        f.writelines(new_lines)
            except Exception:
                continue

