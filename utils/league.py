import asyncio
import json
import logging

import discord

from db.elo_db import db_connection

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# League data helpers
# ---------------------------------------------------------------------------

def _league_sort_key(row: tuple[str, int | None, int | None, str, str, int | None, int]):
    name, min_elo, max_elo, min_kind, max_kind, _role_id, _discovered = row
    eff_min = -10**18 if min_kind == "l" else (min_elo if min_elo is not None else -10**18)
    eff_max = 10**18 if max_kind == "h" else (max_elo if max_elo is not None else 10**18)
    return (eff_min, eff_max, name.lower())


def get_leagues(
    guild_id: int,
) -> list[tuple[str, int | None, int | None, str, str, int | None, int]]:
    with db_connection(guild_id) as (conn, cursor):
        cursor.execute(
            "SELECT name, min_elo, max_elo, COALESCE(min_kind,'n'), COALESCE(max_kind,'n'), role_id, COALESCE(discovered, 0) FROM leagues"
        )
        rows = cursor.fetchall()
    rows.sort(key=_league_sort_key)
    return rows


def set_leagues(
    guild_id: int,
    leagues: list[tuple[str, int | None, int | None, str, str, int | None, int]],
) -> None:
    with db_connection(guild_id) as (conn, cursor):
        cursor.execute("DELETE FROM leagues")
        cursor.executemany(
            "INSERT INTO leagues (name, min_elo, max_elo, min_kind, max_kind, role_id, discovered) VALUES (?, ?, ?, ?, ?, ?, ?)",
            leagues,
        )
        conn.commit()


def pick_league_role_id_for_elo(
    leagues: list[tuple[str, int | None, int | None, str, str, int | None]], elo: int
) -> int | None:
    for _name, min_elo, max_elo, min_kind, max_kind, role_id in leagues:
        if role_id is None:
            continue
        if min_kind == "l":
            if max_elo is not None and elo <= max_elo:
                return role_id
            continue
        if max_kind == "h":
            if min_elo is not None and elo >= min_elo:
                return role_id
            continue
        if min_elo is None or max_elo is None:
            continue
        if min_elo <= elo <= max_elo:
            return role_id
    return None


def format_league_stat_line(
    leagues: list[tuple[str, int | None, int | None, str, str, int | None, int]],
    elo: int,
    games_won: int,
    games_lost: int,
    cursor,
) -> str:
    first_place = "\N{FIRST PLACE MEDAL}"
    if int(games_won) + int(games_lost) <= 0:
        return f"{first_place} League: -"

    effective = [
        (n, mi, ma, mik, mak, rid)
        for (n, mi, ma, mik, mak, rid, _d) in leagues
        if rid is not None
    ]
    role_id = pick_league_role_id_for_elo(effective, int(elo))
    if role_id is None:
        return f"{first_place} League: -"

    cursor.execute("SELECT id, elo, games_won, games_lost FROM players")
    rows = cursor.fetchall()
    elo_i = int(elo)
    count_above = sum(
        1 for _pid, e, w, l in rows
        if int(w) + int(l) > 0
        and pick_league_role_id_for_elo(effective, int(e)) == role_id
        and int(e) > elo_i
    )
    return f"{first_place} League: <@&{role_id}> (#{count_above + 1})"


# ---------------------------------------------------------------------------
# Announce channel helpers
# ---------------------------------------------------------------------------

def get_league_announce_channel_ids(guild_id: int) -> list[int]:
    with db_connection(guild_id) as (conn, cursor):
        cursor.execute(
            "SELECT league_announce_channel_ids, league_announce_channel_id FROM settings WHERE guild_id=?",
            (guild_id,),
        )
        row = cursor.fetchone()
    if not row:
        return []
    ids_json, legacy = row[0], row[1]
    if ids_json:
        try:
            return [int(x) for x in json.loads(ids_json)]
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    if legacy is not None:
        return [int(legacy)]
    return []


def _persist_league_announce_channel_ids(guild_id: int, ids: list[int]) -> None:
    seen: set[int] = set()
    unique: list[int] = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            unique.append(i)
    payload = json.dumps(unique)
    with db_connection(guild_id) as (conn, cursor):
        cursor.execute(
            """
            INSERT INTO settings (
                guild_id, language, dev_mode,
                league_announce_channel_ids
            )
            VALUES (
                ?,
                COALESCE((SELECT language FROM settings WHERE guild_id=?), 'en'),
                COALESCE((SELECT dev_mode FROM settings WHERE guild_id=?), 0),
                ?
            )
            ON CONFLICT(guild_id) DO UPDATE SET
                league_announce_channel_ids = excluded.league_announce_channel_ids,
                league_announce_channel_id = NULL
            """,
            (guild_id, guild_id, guild_id, payload),
        )
        conn.commit()


def add_league_announce_channel_id(guild_id: int, channel_id: int) -> None:
    ids = get_league_announce_channel_ids(guild_id)
    if channel_id not in ids:
        ids.append(channel_id)
    _persist_league_announce_channel_ids(guild_id, ids)


def remove_league_announce_channel_id(guild_id: int, channel_id: int) -> bool:
    ids = get_league_announce_channel_ids(guild_id)
    if channel_id not in ids:
        return False
    _persist_league_announce_channel_ids(guild_id, [i for i in ids if i != channel_id])
    return True


def get_league_announce_channels(guild: discord.Guild) -> list[discord.TextChannel]:
    """Resolve saved announce-channel IDs into actual TextChannel objects for the guild."""
    return [
        ch for ch in (guild.get_channel(cid) for cid in get_league_announce_channel_ids(guild.id))
        if isinstance(ch, discord.TextChannel)
    ]


# ---------------------------------------------------------------------------
# Role sync helpers
# ---------------------------------------------------------------------------

def _build_effective_leagues(
    guild: discord.Guild,
    leagues: list[tuple[str, int | None, int | None, str, str, int | None]],
) -> tuple[
    list[tuple[str, int | None, int | None, str, str, int | None]],
    set[int],
    dict[int, str],
    dict[int, int],
    list[str],
]:
    """
    Filter leagues that have a valid role and build lookup structures.
    Returns (effective_leagues, league_role_ids, role_id_to_name, role_rank, missing_names).
    """
    role_by_id = {r.id: r for r in guild.roles}
    effective_leagues = []
    league_role_ids: set[int] = set()
    role_id_to_name: dict[int, str] = {}
    role_rank: dict[int, int] = {}
    missing_names: list[str] = []

    for name, min_elo, max_elo, min_kind, max_kind, role_id in leagues:
        if role_id is None or role_id not in role_by_id:
            missing_names.append(name)
            continue
        effective_leagues.append((name, min_elo, max_elo, min_kind, max_kind, role_id))
        league_role_ids.add(role_id)
        role_id_to_name[role_id] = name

    for idx, (*_, rid) in enumerate(effective_leagues):
        if rid is not None:
            role_rank[rid] = idx

    return effective_leagues, league_role_ids, role_id_to_name, role_rank, missing_names


async def _sync_member_role(
    guild: discord.Guild,
    member: discord.Member,
    elo: int,
    games_won: int,
    games_lost: int,
    effective_leagues: list[tuple[str, int | None, int | None, str, str, int | None]],
    league_role_ids: set[int],
    role_id_to_name: dict[int, str],
    role_rank: dict[int, int],
    announce_channels: list[discord.TextChannel],
    discovered_role_ids: set[int],
    *,
    silent: bool = False,
    league_changes_file: str | None = None,
) -> tuple[int, list[tuple[int, int]], list[tuple[int, int]]]:
    """Sync role for a single member. Returns (changed, added_log, removed_log).

    Args:
        silent: If True, skip sending individual league-change notifications to
                announce channels. Used when called from -decay to avoid spam.
        league_changes_file: If provided (a file path), every time a league
                change (promote/demote) is detected, a line is appended to this
                file with the member's bold display name and the league
                transition. After all members are processed the caller reads
                this file and sends a single consolidated message.
    """
    role_by_id = {r.id: r for r in guild.roles}

    total_games = int(games_won) + int(games_lost)
    desired_role_id = (
        None if total_games <= 0
        else pick_league_role_id_for_elo(effective_leagues, int(elo))
    )
    desired_role = role_by_id.get(desired_role_id) if desired_role_id else None
    if desired_role_id:
        discovered_role_ids.add(desired_role_id)

    current_league_roles = [r for r in member.roles if r.id in league_role_ids]
    to_remove = [r for r in current_league_roles if not desired_role or r.id != desired_role.id]
    to_add = [desired_role] if desired_role and desired_role not in member.roles else []

    if not to_remove and not to_add:
        return 0, [], []

    me = guild.me
    if not me or not me.guild_permissions.manage_roles:
        return 0, [], []
    if desired_role and desired_role >= me.top_role:
        return 0, [], []
    if any(r >= me.top_role for r in to_remove):
        return 0, [], []

    removed_log = [(member.id, r.id) for r in to_remove]
    added_log = [(member.id, to_add[0].id)] if to_add else []

    async def _do_with_retry(coro_fn, max_retries: int = 3):
        """Call coro_fn(), retrying on 429 with exponential backoff."""
        delay = 1.0
        for attempt in range(max_retries):
            try:
                await coro_fn()
                return
            except discord.HTTPException as e:
                if e.status == 429 and attempt < max_retries - 1:
                    retry_after = float(getattr(e, "retry_after", delay))
                    await asyncio.sleep(max(retry_after, delay))
                    delay *= 2
                else:
                    raise

    try:
        if to_remove:
            await _do_with_retry(
                lambda: member.remove_roles(*to_remove, reason="League sync by Elo")
            )
        if to_add:
            await _do_with_retry(
                lambda: member.add_roles(*to_add, reason="League sync by Elo")
            )

        if to_remove and to_add:
            old_role = to_remove[0]
            new_role = to_add[0]
            old_name = role_id_to_name.get(old_role.id, old_role.name)
            new_name = role_id_to_name.get(new_role.id, new_role.name)

            if league_changes_file is not None:
                # Write the change to the temp file so the caller can send a
                # single consolidated message after all members are processed.
                with open(league_changes_file, "a", encoding="utf-8") as f:
                    f.write(f"**{member.display_name}**: {old_name} -> {new_name}\n")
            elif not silent and announce_channels:
                change_word = "promoted" if role_rank.get(new_role.id, 0) > role_rank.get(old_role.id, 0) else "demoted"
                msg = f"{member.mention} has been {change_word} from **{old_name}** to **{new_name}**."
                for ch in announce_channels:
                    try:
                        await ch.send(msg)
                    except Exception:
                        pass

        return 1, added_log, removed_log
    except Exception:
        logger.exception("Failed to sync roles for member %s", member.id)
        return 0, [], []


async def sync_league_roles(
    guild: discord.Guild,
    player_ids: list[int] | None = None,
    *,
    silent: bool = False,
    league_changes_file: str | None = None,
) -> tuple[int, list[str], list[tuple[int, int]], list[tuple[int, int]]]:
    """
    Assign/remove league roles based on Elo.

    Args:
        guild: Discord guild to sync.
        player_ids: If provided, only sync those players (e.g. after a report
                    match, only the 2 involved players need syncing instead of
                    the whole guild). If None, sync everyone.
        silent: If True, skip sending individual league-change notifications
                to announce channels. Used when called from -decay to avoid
                spamming the bot.
        league_changes_file: If provided (a file path), every league change
                detected during this sync is appended to the file as a line
                with the member's bold display name and league transition.
                After sync completes, the caller reads this file and sends
                a single consolidated message.

    Returns:
        (updated_count, missing_role_names, added_log, removed_log)
    """
    leagues_full = get_leagues(guild.id)
    leagues = [(n, mi, ma, mik, mak, rid) for (n, mi, ma, mik, mak, rid, _d) in leagues_full]
    if not leagues:
        return 0, [], [], []

    effective_leagues, league_role_ids, role_id_to_name, role_rank, missing_names = (
        _build_effective_leagues(guild, leagues)
    )
    if not effective_leagues:
        return 0, missing_names, [], []

    # Fetch player data from DB -- only the specified players if given
    with db_connection(guild.id) as (conn, cursor):
        if player_ids:
            placeholders = ",".join("?" * len(player_ids))
            cursor.execute(
                f"SELECT id, elo, games_won, games_lost FROM players WHERE id IN ({placeholders})",
                [str(pid) for pid in player_ids],
            )
        else:
            cursor.execute("SELECT id, elo, games_won, games_lost FROM players")
        players = cursor.fetchall()

    announce_channels = get_league_announce_channels(guild)

    discovered_role_ids: set[int] = set()

    # Full sync (decay, lapply, ...): sequential + small delay between each
    # member to avoid 429s from KataProxy when there are many players.
    # Partial sync (after reporting a match, 1-2 people): parallel is fine.
    is_full_sync = player_ids is None
    # Delay (seconds) between each role operation during a full sync.
    _FULL_SYNC_DELAY = 0.5
    # After a 429 (once already retried inside _sync_member_role), wait longer before retrying the member
    _RETRY_BACKOFF = 5.0
    _MAX_MEMBER_RETRIES = 3

    async def _process_one(pid_raw, elo, gw, gl) -> tuple[int, list, list]:
        try:
            uid = int(pid_raw)
        except (ValueError, TypeError):
            return 0, [], []
        try:
            member = guild.get_member(uid) or await guild.fetch_member(uid)
        except Exception:
            return 0, [], []
        return await _sync_member_role(
            guild, member, elo, gw, gl,
            effective_leagues, league_role_ids, role_id_to_name, role_rank,
            announce_channels, discovered_role_ids,
            silent=silent,
            league_changes_file=league_changes_file,
        )

    if is_full_sync:
        # Fully sequential with a retry queue -- ensures every member gets processed
        all_results: list[tuple[int, list, list]] = []
        pending = list(players)  # [(pid, elo, gw, gl), ...]

        for attempt in range(_MAX_MEMBER_RETRIES):
            failed = []
            for pid_raw, elo, gw, gl in pending:
                try:
                    result = await _process_one(pid_raw, elo, gw, gl)
                    all_results.append(result)
                    if result[0]:
                        # Role change succeeded -> small delay before the next member
                        await asyncio.sleep(_FULL_SYNC_DELAY)
                except discord.HTTPException as e:
                    if e.status == 429:
                        failed.append((pid_raw, elo, gw, gl))
                        retry_after = float(getattr(e, "retry_after", _RETRY_BACKOFF))
                        await asyncio.sleep(max(retry_after, _RETRY_BACKOFF))
                    else:
                        logger.exception("HTTPException syncing member %s", pid_raw)
                        all_results.append((0, [], []))
                except Exception:
                    logger.exception("Unexpected error syncing member %s", pid_raw)
                    all_results.append((0, [], []))

            pending = failed
            if not pending:
                break
            # Members still failed -> wait longer before the next retry round
            if attempt < _MAX_MEMBER_RETRIES - 1:
                await asyncio.sleep(_RETRY_BACKOFF * (attempt + 2))

        results = all_results
    else:
        # Partial sync: parallel with semaphore(5), faster for 1-2 people
        semaphore = asyncio.Semaphore(5)

        async def _process_one_guarded(pid_raw, elo, gw, gl):
            async with semaphore:
                return await _process_one(pid_raw, elo, gw, gl)

        results = await asyncio.gather(
            *(_process_one_guarded(pid, elo, gw, gl) for pid, elo, gw, gl in players),
            return_exceptions=False,
        )

    # Persist discovered leagues
    if discovered_role_ids:
        with db_connection(guild.id) as (conn, cursor):
            cursor.executemany(
                "UPDATE leagues SET discovered=1 WHERE role_id=?",
                [(rid,) for rid in discovered_role_ids],
            )
            conn.commit()

    changed, added_all, removed_all = 0, [], []
    for c, adds, rems in results:
        changed += c
        added_all.extend(adds)
        removed_all.extend(rems)

    return changed, missing_names, added_all, removed_all