import os
import re
import typing
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from db.elo_db import (
    db_connection,
    db_exists,
    get_db_path,
    get_effective_daily_streak,
    get_max_month,
)
from utils import (
    format_league_stat_line,
    get_leagues,
    get_lb_root,
    get_most_played_with,
    get_opponent_games,
    parse_leaderboard_line,
)
from utils.autocomplete import stats_top_elo_name_autocomplete
from utils.slash_ctx import SlashContext

# ---------------------------------------------------------------------------
# "-stats expanded" helpers
#
# Files read from the season/month leaderboard archive (lb/<guild_id>/...)
# for a single player. Only these four categories are averaged/summed by
# "-stats expanded"; "games_played_this_month" is intentionally excluded.
# ---------------------------------------------------------------------------

EXPANDED_FILE_MAP: dict[str, str] = {
    "elo": "data.txt",
    "wins": "w.txt",
    "losses": "l.txt",
    "win_rate": "wr.txt",
}


_EXPANDED_TOKEN_RE = re.compile(r"^[sS](\d+)((?:[mM]\d+)*)$")
_EXPANDED_MONTH_RE = re.compile(r"[mM](\d+)")


def _parse_expanded_args(args: str) -> list[tuple[int, list[int]]] | None:
    """
    Parse the raw argument string of the "/stats expanded" slash command
    into a list of (season, months) groups.

    Grammar: whitespace-separated tokens, each shaped "s<season>" followed
    by zero or more "m<month>" chunks, e.g.:
        s1m1m2 s2m2 s3m1m2m3
    means season 1 (months 1, 2), season 2 (month 2), season 3 (months 1,
    2, 3). A token with no "m" chunks (e.g. "s2") requests that season's
    Overall stats.

    Duplicate months within a token (e.g. "s1m1m1") are de-duplicated,
    keeping the first occurrence's position - otherwise the same month's
    directory would be read (and its wins/losses/elo counted) once per
    repetition further downstream.

    Returns None if any token doesn't match the "s<num>[m<num>...]" shape,
    so the caller can show a format-specific error.
    """
    tokens = (args or "").split()
    groups: list[tuple[int, list[int]]] = []

    for token in tokens:
        match = _EXPANDED_TOKEN_RE.match(token)
        if not match:
            return None
        season = int(match.group(1))
        months: list[int] = []
        seen_months: set[int] = set()
        for m in _EXPANDED_MONTH_RE.findall(match.group(2) or ""):
            month = int(m)
            if month not in seen_months:
                seen_months.add(month)
                months.append(month)
        groups.append((season, months))

    return groups


def _find_dir_by_regex(parent: str, pattern: str) -> str | None:
    """Return the path of the first subdirectory of `parent` matching
    `pattern` (case-insensitive, full match), or None if not found."""
    if not os.path.isdir(parent):
        return None
    regex = re.compile(pattern, re.IGNORECASE)
    for entry in os.listdir(parent):
        full = os.path.join(parent, entry)
        if os.path.isdir(full) and regex.match(entry):
            return full
    return None


def _resolve_season_dir(base: str, season: int) -> str | None:
    return _find_dir_by_regex(base, rf"^season\s+{season}$")


def _resolve_month_dir(season_dir: str, month: int) -> str | None:
    return _find_dir_by_regex(season_dir, rf"^month\s+{month}$")


def _resolve_overall_dir(season_dir: str) -> str | None:
    return _find_dir_by_regex(season_dir, r"^overall$")


def _read_player_stat(
    dir_path: str, filename: str, player_name_lower: str, *, win_rate: bool = False
) -> float | int | None:
    """Look up a single player's value in a leaderboard archive file
    (data.txt / w.txt / l.txt / wr.txt / games.txt), matched by exact name
    (case insensitive). Returns None if the file or the player's line is
    missing."""
    file_path = os.path.join(dir_path, filename)
    if not os.path.exists(file_path):
        return None
    with open(file_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.lower().endswith("leaderboard"):
            continue
        parsed = parse_leaderboard_line(stripped, win_rate=win_rate)
        if not parsed:
            continue
        name, value, _ = parsed
        if name.lower() == player_name_lower:
            return value
    return None


def _get_rank_for_stat(
    dir_path: str, filename: str, value, *, win_rate: bool = False
) -> int | None:
    """Return the 1-based rank of `value` within a leaderboard archive
    file, or None if the file is missing/empty."""
    file_path = os.path.join(dir_path, filename)
    if not os.path.exists(file_path):
        return None
    with open(file_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    all_values = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.lower().endswith("leaderboard"):
            continue
        parsed = parse_leaderboard_line(stripped, win_rate=win_rate)
        if parsed:
            all_values.append(parsed[1])
    if not all_values:
        return None
    return sum(1 for v in all_values if v > value) + 1


def _find_player_name_in_archive(dir_path: str | None, user_id: int) -> str | None:
    """
    Look up a player's name by Discord ID within a single season/month (or
    Overall) leaderboard archive directory. Used as a fallback for players
    who are no longer (or not yet) present in the `players` table, so their
    historical archive entry still resolves to a name.
    """
    if not dir_path or not os.path.isdir(dir_path):
        return None
    user_id_str = str(user_id)
    for filename, win_rate in (
        ("data.txt", False),
        ("w.txt", False),
        ("l.txt", False),
        ("wr.txt", True),
        ("games.txt", False),
    ):
        file_path = os.path.join(dir_path, filename)
        if not os.path.exists(file_path):
            continue
        with open(file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.lower().endswith("leaderboard"):
                continue
            parsed = parse_leaderboard_line(stripped, win_rate=win_rate)
            if not parsed:
                continue
            name, _value, discord_id = parsed
            if discord_id and discord_id == user_id_str:
                return name
    return None

STATS_ELO_LEADERBOARD_NOTE = (
    "*Play at least one set to be ranked on the Elo leaderboard.*"
)

ACCENT_COLOR = discord.Color.blue()


class StatsRefreshButton(discord.ui.Button):
    def __init__(self, custom_view):
        super().__init__(style=discord.ButtonStyle.secondary, emoji="🔄")
        self.custom_view = custom_view

    async def callback(self, interaction: discord.Interaction):
        self.custom_view.last_updated = datetime.now(timezone.utc)
        await self.custom_view.update_stats(interaction)


# Modified: StatsView is now a LayoutView (Components V2) so the Refresh
# button sits INSIDE the colored border (as an accessory of Section), like an
# OwO card, instead of floating in a separate action row below the embed.
class StatsView(discord.ui.LayoutView):
    def __init__(self, cog, ctx, user_id: int, had_target: bool):
        super().__init__(timeout=3600.0)
        self.cog = cog
        self.ctx = ctx
        self.user_id = user_id
        self.had_target = had_target
        self.last_updated = datetime.now(timezone.utc)
        self.message = None
        self.refresh_button = StatsRefreshButton(self)

    async def update_stats(self, interaction_or_ctx):
        with db_connection(self.ctx.guild.id) as (conn, cursor):
            container = await self.cog.build_stats_container(
                self.ctx,
                cursor,
                self.user_id,
                self.had_target,
                self.last_updated,
                self.refresh_button,
            )

        self.clear_items()
        self.add_item(container)

        if isinstance(interaction_or_ctx, discord.Interaction):
            await interaction_or_ctx.response.edit_message(
                view=self, allowed_mentions=discord.AllowedMentions.none()
            )
            if interaction_or_ctx.message:
                self.message = interaction_or_ctx.message
            else:
                try:
                    self.message = await interaction_or_ctx.original_response()
                except Exception:
                    pass
        else:
            self.message = await interaction_or_ctx.send(
                view=self, allowed_mentions=discord.AllowedMentions.none()
            )

    async def on_timeout(self) -> None:
        self.refresh_button.disabled = True
        if self.message:
            try:
                await self.message.edit(
                    view=self, allowed_mentions=discord.AllowedMentions.none()
                )
            except Exception:
                pass


class StatsCog(commands.GroupCog, name="stats", description="View player statistics."):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def build_stats_container(
        self,
        ctx: commands.Context,
        cursor,
        user_id: int,
        had_target: bool,
        last_updated: datetime,
        refresh_button: discord.ui.Button,
    ) -> discord.ui.Container:
        # Fetch fresh player data
        row = self._get_player_data(cursor, user_id=user_id)
        if not row:
            # Should not happen but fallback gracefully
            return discord.ui.Container(
                discord.ui.TextDisplay("**Error**\nPlayer not found in database."),
                accent_color=discord.Color.red(),
            )

        _uid, name, games_won, games_lost, elo, games_played_this_month, win_streak = row[0]
        player_id = str(user_id)
        total_games = games_won + games_lost
        win_rate = (games_won / total_games) * 100 if total_games > 0 else 0

        cursor.execute("SELECT COUNT(*) FROM players WHERE elo > ?", (elo,))
        rank = cursor.fetchone()[0] + 1

        leagues = get_leagues(ctx.guild.id)
        league_line = format_league_stat_line(
            leagues, elo, games_won, games_lost, cursor
        )
        if total_games <= 0:
            elo_line = "🔥 Elo: -"
        else:
            elo_line = f"🔥 Elo: {elo} (#{rank})"

        # inline mention next to the player header (only when caller provided target)
        mention_inline = ""
        if had_target:
            mention_inline = await self._safe_fetch_mention(user_id)

        header = f"**{name}**"
        if mention_inline:
            header = f"{header} ({mention_inline})"

        if total_games > 0:
            most_played_with = get_most_played_with(cursor, player_id)
            if most_played_with:
                most_played_text = await self._format_most_played_with(
                    ctx, most_played_with, cursor, player_id
                )
            else:
                most_played_text = "Play at least 1 set to see your dream person"
            tail = f"👥 Most Played: {most_played_text}"
        else:
            tail = STATS_ELO_LEADERBOARD_NOTE

        daily_streak = get_effective_daily_streak(cursor, user_id)

        body = (
            f"## Player Statistics\n"
            f"{header}\n"
            f"{elo_line}\n"
            f"{league_line}\n"
            f"🏆 Games Won: {games_won}\n"
            f"💀 Games Lost: {games_lost}\n"
            f"🎮 Monthly Games: {games_played_this_month} (Daily Streak: {daily_streak})\n"
            f"📊 Win Rate: {win_rate:.2f}% (Win Streak: {win_streak})\n"
            f"{tail}"
        )

        avatar_url = await self._get_display_avatar_url(
            ctx,
            None,
            user_id,
        )

        container = discord.ui.Container(accent_color=ACCENT_COLOR)
        if avatar_url:
            container.add_item(
                discord.ui.Section(
                    discord.ui.TextDisplay(body),
                    accessory=discord.ui.Thumbnail(avatar_url),
                )
            )
        else:
            container.add_item(discord.ui.TextDisplay(body))

        container.add_item(discord.ui.Separator())
        # Modified: "Last updated" uses <t:...:R> (TextDisplay supports markdown)
        # so Discord updates the relative time itself, no need for the bot to re-edit.
        container.add_item(
            discord.ui.Section(
                discord.ui.TextDisplay(
                    f"Last updated: <t:{int(last_updated.timestamp())}:R>"
                ),
                accessory=refresh_button,
            )
        )

        return container

    async def _get_display_avatar_url(
        self,
        ctx: commands.Context,
        user: typing.Union[discord.User, discord.Member, None],
        user_id: int | None,
    ) -> str | None:
        if isinstance(user, (discord.User, discord.Member)):
            return user.display_avatar.url
        if user_id is None:
            return None
        if ctx.guild:
            member = ctx.guild.get_member(user_id)
            if member is not None:
                return member.display_avatar.url
        try:
            fetched = await self.bot.fetch_user(user_id)
            return fetched.display_avatar.url
        except Exception:
            return None

    async def _safe_fetch_mention(
        self, user_obj_or_id: typing.Union[discord.User, discord.Member, int]
    ) -> str:
        try:
            if isinstance(user_obj_or_id, (discord.User, discord.Member)):
                return user_obj_or_id.mention
            user_id = int(user_obj_or_id)
            user = await self.bot.fetch_user(user_id)
            return user.mention
        except Exception:
            return f"<@{user_obj_or_id}>"

    async def _format_most_played_with(
        self,
        ctx: commands.Context,
        most_played_with: list[str],
        cursor,
        player_id: str,
    ) -> str:
        """Format most played with list with bold names, mentions, and games count."""
        if not most_played_with:
            return "Play at least 1 set to see your dream person"

        formatted_list = []
        for name in most_played_with:
            # Get user ID from database. Match case-insensitively and ignore
            # leading/trailing whitespace so a stale exact-match (e.g. from a
            # name with different casing or stray whitespace/unicode) doesn't
            # silently drop the opponent's mention.
            cursor.execute(
                "SELECT id FROM players WHERE TRIM(name) = TRIM(?) COLLATE NOCASE",
                (name,),
            )
            row = cursor.fetchone()
            games = get_opponent_games(cursor, player_id, name)
            if row:
                user_id = int(row[0])
                mention = await self._safe_fetch_mention(user_id)
                formatted_list.append(f"**{name}** ({mention}) - {games} games")
            else:
                # If user not found in database, just show name in bold
                formatted_list.append(f"**{name}** - {games} games")

        return "; ".join(formatted_list)

    def _get_player_data(
        self,
        cursor,
        *,
        user_id: int | None = None,
        search_name: str | None = None,
    ) -> list[tuple]:
        """
        Fetch player row(s) from the database.

        Provide exactly one of *user_id* or *search_name*.

        Always returns a ``list[tuple]`` where each tuple is
        ``(id, name, games_won, games_lost, elo, games_played_this_month, win_streak)``:
        - Empty list  → no match found
        - 1-item list → exact match (or single partial match)
        - N-item list → multiple partial matches (callers show disambiguation)

        Callers can uniformly check ``len(result)`` without needing
        ``isinstance`` guards.
        """
        if user_id is not None:
            cursor.execute(
                "SELECT id, name, games_won, games_lost, elo, games_played_this_month, win_streak"
                " FROM players WHERE id = ?",
                (user_id,),
            )
            row = cursor.fetchone()
            return [row] if row else []

        if search_name is not None:
            cursor.execute(
                "SELECT id, name, games_won, games_lost, elo, games_played_this_month, win_streak"
                " FROM players WHERE name LIKE ?",
                (f"%{search_name}%",),
            )
            return cursor.fetchall()

        raise ValueError("Provide either user_id or search_name")

    async def _resolve_user_by_id_or_mention(
        self, ctx: commands.Context, raw: str
    ) -> typing.Union[discord.Member, discord.User, None]:
        """Resolve a raw mention token (``<@id>``) or a bare Discord ID string
        into a Member/User. Returns None if it can't be resolved."""
        raw = raw.strip()
        mention_match = re.fullmatch(r"<@!?(\d+)>", raw)
        uid_str = mention_match.group(1) if mention_match else raw
        try:
            uid = int(uid_str)
        except ValueError:
            return None
        member = ctx.guild.get_member(uid) if ctx.guild else None
        if member:
            return member
        try:
            return await self.bot.fetch_user(uid)
        except discord.NotFound:
            return None

    @commands.command(name="stats", help="View player stats (@mention, name, or Discord ID).")
    async def stats(
        self, ctx: commands.Context, *, target: typing.Optional[str] = None
    ):
        user = None
        search_name = None
        had_target = target is not None  # whether caller provided an explicit target

        # resolve target: mention, member, or name search
        if target is None:
            user = ctx.author
        else:
            target = target.strip()
            # Check for a raw mention token first (e.g. "@Senko" resolved by
            # Discord's client-side autocomplete to "<@123>"/"<@!123>"). This
            # must be checked before MemberConverter, since MemberConverter
            # can raise on interaction-derived Contexts (hybrid/slash
            # invocations) before it ever gets a chance to match the mention.
            mention_match = re.fullmatch(r"<@!?(\d+)>", target)
            if mention_match:
                uid = int(mention_match.group(1))
                user = ctx.guild.get_member(uid)
                if not user:
                    try:
                        user = await self.bot.fetch_user(uid)
                    except discord.NotFound:
                        await ctx.send(
                            embed=discord.Embed(
                                title="User Not Found",
                                description="Could not find the mentioned user.",
                                color=discord.Color.red(),
                            )
                        )
                        return
            else:
                try:
                    user = await commands.MemberConverter().convert(ctx, target)
                except Exception:
                    search_name = target
                    user = None

        with db_connection(ctx.guild.id) as (conn, cursor):
            await self._stats_impl(ctx, cursor, user, search_name, target, had_target)

    async def _stats_expanded_impl(
        self, ctx: commands.Context, args: typing.Optional[str]
    ) -> None:
        if not args or not args.strip():
            await ctx.send(
                embed=discord.Embed(
                    title="Missing Input",
                    description=(
                        "Please provide at least one season, e.g. "
                        "`/stats expanded seasons: s1m1m2 s2m2 s3m1m2m3`."
                    ),
                    color=discord.Color.red(),
                )
            )
            return

        groups = _parse_expanded_args(args)
        if groups is None:
            await ctx.send(
                embed=discord.Embed(
                    title="Invalid Input",
                    description=(
                        "Could not parse your input. Each entry must look like "
                        "`s<season>` or `s<season>m<month>m<month>...`, e.g. "
                        "`s1m1m2 s2m2 s3m1m2m3`."
                    ),
                    color=discord.Color.red(),
                )
            )
            return
        if not groups:
            await ctx.send(
                embed=discord.Embed(
                    title="Missing Input",
                    description=(
                        "Please provide at least one season, e.g. "
                        "`/stats expanded seasons: s1m1m2 s2m2 s3m1m2m3`."
                    ),
                    color=discord.Color.red(),
                )
            )
            return

        guild_id = ctx.guild.id

        base = get_lb_root(guild_id)
        if not os.path.isdir(base):
            await ctx.send(
                embed=discord.Embed(
                    title="Error",
                    description=(
                        f"No leaderboard archive found for this server "
                        f"(`{base.replace(os.sep, '/')}/`)."
                    ),
                    color=discord.Color.red(),
                )
            )
            return

        with db_connection(guild_id) as (conn, cursor):
            cursor.execute(
                "SELECT name FROM players WHERE id = ?", (str(ctx.author.id),)
            )
            row = cursor.fetchone()

        if row:
            player_name = row[0]
        else:
            # Not currently registered: fall back to the name recorded in
            # the archive (matched by Discord ID) for the first season/month
            # mentioned in the command, so past players still resolve.
            first_season, first_months = groups[0]
            first_season_dir = _resolve_season_dir(base, first_season)
            lookup_dir = None
            if first_season_dir is not None:
                lookup_dir = (
                    _resolve_month_dir(first_season_dir, first_months[0])
                    if first_months
                    else _resolve_overall_dir(first_season_dir)
                )
            player_name = _find_player_name_in_archive(lookup_dir, ctx.author.id)

            if not player_name:
                await ctx.send(
                    embed=discord.Embed(
                        title="Player Not Registered",
                        description="You are not registered. Use `-register <name>` to sign up!",
                        color=discord.Color.red(),
                    )
                )
                return

        player_name_lower = player_name.lower()

        max_month = get_max_month(guild_id)

        # Resolve every group into either a single "Overall" directory, or
        # one directory per requested month. `season` and `months` (the
        # latter None for Overall entries) are carried alongside the
        # display label so the multi-entry suffix below can be rebuilt
        # de-duplicated per season instead of re-joining the raw per-token
        # labels verbatim.
        resolved_entries: list[
            tuple[str, list[str], bool, int, list[int] | None]
        ] = []
        for season, months in groups:
            season_dir = _resolve_season_dir(base, season)
            if season_dir is None:
                await ctx.send(
                    embed=discord.Embed(
                        title="Data Not Found",
                        description=f"No matching data found for Season {season}.",
                        color=discord.Color.red(),
                    )
                )
                return

            # No months given for this season -> use the Overall folder.
            use_overall = not months
            # Months given cover the server's entire configured month range
            # -> also use Overall instead of averaging each month.
            if (
                not use_overall
                and max_month is not None
                and sorted(set(months)) == list(range(1, max_month + 1))
            ):
                use_overall = True

            if use_overall:
                overall_dir = _resolve_overall_dir(season_dir)
                if overall_dir is None:
                    await ctx.send(
                        embed=discord.Embed(
                            title="Data Not Found",
                            description=(
                                f"No matching data found for Season {season}, Overall."
                            ),
                            color=discord.Color.red(),
                        )
                    )
                    return
                resolved_entries.append(
                    (f"S{season}Overall", [overall_dir], True, season, None)
                )
                continue

            month_dirs = []
            for month in months:
                month_dir = _resolve_month_dir(season_dir, month)
                if month_dir is None:
                    await ctx.send(
                        embed=discord.Embed(
                            title="Data Not Found",
                            description=(
                                f"No matching data found for Season {season}, "
                                f"Month {month}."
                            ),
                            color=discord.Color.red(),
                        )
                    )
                    return
                month_dirs.append(month_dir)

            label = f"S{season}" + "".join(f"M{m}" for m in months)
            resolved_entries.append((label, month_dirs, False, season, months))

        # A single season resolving to exactly one directory - either one
        # exact month, or the season collapsing to Overall - shows the full
        # stats for that month/Overall instead of an averaged summary.
        if len(resolved_entries) == 1 and len(resolved_entries[0][1]) == 1:
            label, dirs, is_overall, _, _ = resolved_entries[0]
            dir_path = dirs[0]

            elo = _read_player_stat(dir_path, "data.txt", player_name_lower)
            wins = _read_player_stat(dir_path, "w.txt", player_name_lower)
            losses = _read_player_stat(dir_path, "l.txt", player_name_lower)
            win_rate = _read_player_stat(
                dir_path, "wr.txt", player_name_lower, win_rate=True
            )
            games = (
                None
                if is_overall
                else _read_player_stat(dir_path, "games.txt", player_name_lower)
            )

            if (
                elo is None
                and wins is None
                and losses is None
                and win_rate is None
                and games is None
            ):
                await ctx.send(
                    embed=discord.Embed(
                        title="Data Not Found",
                        description=(
                            f"No matching data found for **{player_name}** in the "
                            "requested Season/Month."
                        ),
                        color=discord.Color.red(),
                    )
                )
                return

            def _rank(filename: str, value, *, win_rate_stat: bool = False) -> str:
                if value is None:
                    return ""
                r = _get_rank_for_stat(dir_path, filename, value, win_rate=win_rate_stat)
                return f" (#{r})" if r is not None else ""

            elo_line = (
                f"🔥 Elo: {elo}{_rank('data.txt', elo)}" if elo is not None else "🔥 Elo: -"
            )
            wins_line = (
                f"🏆 Wins: {wins}{_rank('w.txt', wins)}" if wins is not None else "🏆 Wins: -"
            )
            losses_line = (
                f"💀 Losses: {losses}{_rank('l.txt', losses)}"
                if losses is not None
                else "💀 Losses: -"
            )
            win_rate_line = (
                f"📊 Win Rate: {win_rate:.2f}%{_rank('wr.txt', win_rate, win_rate_stat=True)}"
                if win_rate is not None
                else "📊 Win Rate: -"
            )

            body_lines = [
                "## Player Statistics",
                f"**{player_name} - {label}**",
                elo_line,
                wins_line,
                losses_line,
            ]
            if not is_overall:
                games_line = (
                    f"🎮 Monthly Games: {games}{_rank('games.txt', games)}"
                    if games is not None
                    else "🎮 Monthly Games: -"
                )
                body_lines.append(games_line)
            body_lines.append(win_rate_line)

            embed = discord.Embed(description="\n".join(body_lines), color=ACCENT_COLOR)
            avatar_url = await self._get_display_avatar_url(ctx, ctx.author, ctx.author.id)
            if avatar_url:
                embed.set_thumbnail(url=avatar_url)
            await ctx.send(embed=embed)
            return

        # Otherwise: several season/month entries were requested -> average
        # Elo, sum Wins and Losses, and recompute Win Rate from those summed
        # totals (see the comment further below for why it isn't averaged).
        #
        # Deduplicated (keeping first occurrence) because _parse_expanded_args
        # only de-dupes months *within* a single token (e.g. "s1m1m1"), not
        # across separate tokens - input like "s1m1m2 s1m2m3" would otherwise
        # resolve to the same "Season 1/Month 2" directory twice, silently
        # double-counting that month's wins/losses and skewing the Elo
        # average toward it.
        all_dirs = list(
            dict.fromkeys(d for _, dirs, _, _, _ in resolved_entries for d in dirs)
        )

        elo_values: list[float] = []
        wins_total = 0
        losses_total = 0
        any_data_found = False

        for dir_path in all_dirs:
            elo = _read_player_stat(
                dir_path, EXPANDED_FILE_MAP["elo"], player_name_lower
            )
            wins = _read_player_stat(
                dir_path, EXPANDED_FILE_MAP["wins"], player_name_lower
            )
            losses = _read_player_stat(
                dir_path, EXPANDED_FILE_MAP["losses"], player_name_lower
            )
            win_rate = _read_player_stat(
                dir_path, EXPANDED_FILE_MAP["win_rate"], player_name_lower, win_rate=True
            )

            if elo is not None:
                elo_values.append(elo)
                any_data_found = True
            if wins is not None:
                wins_total += wins
                any_data_found = True
            if losses is not None:
                losses_total += losses
                any_data_found = True
            if win_rate is not None:
                any_data_found = True

        if not any_data_found:
            await ctx.send(
                embed=discord.Embed(
                    title="Data Not Found",
                    description=(
                        f"No matching data found for **{player_name}** in the "
                        "requested Season(s)/Month(s)."
                    ),
                    color=discord.Color.red(),
                )
            )
            return

        elo_avg = sum(elo_values) / len(elo_values) if elo_values else None
        # Win Rate is recalculated from the summed wins/losses (not averaged
        # from each period's already-computed percentage): averaging
        # percentages directly would weight a low-sample-size month the
        # same as a high-sample-size one, e.g. 90% (from 90W/10L) and 50%
        # (from 1W/1L) averaging to 70% even though the combined record is
        # 91W/11L (~89.2%). Since wins_total/losses_total are already exact
        # sums across all requested periods, deriving Win Rate from them
        # keeps it consistent with the Wins/Losses shown right next to it.
        total_games = wins_total + losses_total
        win_rate_total = (wins_total / total_games * 100) if total_games > 0 else None

        # Build the display suffix with months de-duplicated per season, so
        # it matches the de-duplicated directories actually counted above.
        # Overlapping tokens like "s1m1m2 s1m2m3" would otherwise show
        # "S1M1M2 + S1M2M3" (Month 2 listed twice), which reads as if it
        # were double-counted even though the totals above only count it
        # once. Overall entries are kept as separate segments (never merged
        # with a month entry for the same season) since they're read from a
        # distinct directory.
        combined_labels: dict[tuple, list[int] | None] = {}
        for label, _dirs, is_overall, season, months in resolved_entries:
            if is_overall:
                combined_labels[("overall", season, label)] = None
            else:
                key = ("months", season)
                combined_labels.setdefault(key, []).extend(months)

        suffix_parts: list[str] = []
        for key, months in combined_labels.items():
            if months is None:
                suffix_parts.append(key[2])
            else:
                season = key[1]
                deduped_months = list(dict.fromkeys(months))
                suffix_parts.append(
                    f"S{season}" + "".join(f"M{m}" for m in deduped_months)
                )
        suffix = " + ".join(suffix_parts)

        elo_line = f"🔥 Elo: {elo_avg:.2f}" if elo_avg is not None else "🔥 Elo: -"
        win_rate_line = (
            f"📊 Win Rate: {win_rate_total:.2f}%"
            if win_rate_total is not None
            else "📊 Win Rate: -"
        )

        body = (
            f"## Player Statistics\n"
            f"**{player_name} - {suffix}**\n"
            f"{elo_line}\n"
            f"🏆 Wins: {wins_total}\n"
            f"💀 Losses: {losses_total}\n"
            f"{win_rate_line}"
        )

        embed = discord.Embed(description=body, color=ACCENT_COLOR)
        avatar_url = await self._get_display_avatar_url(ctx, ctx.author, ctx.author.id)
        if avatar_url:
            embed.set_thumbnail(url=avatar_url)
        await ctx.send(embed=embed)

    @app_commands.command(name="view", description="View player statistics.")
    @app_commands.describe(
        name="Player's registered name.",
        mention="Player to mention.",
        discord_id="Player's Discord ID.",
    )
    @app_commands.autocomplete(
        name=stats_top_elo_name_autocomplete,
    )
    @app_commands.guild_only()
    async def slash_stats(
        self,
        interaction: discord.Interaction,
        name: typing.Optional[str] = None,
        mention: typing.Optional[discord.Member] = None,
        discord_id: typing.Optional[str] = None,
    ):
        provided = [v for v in (name, mention, discord_id) if v]
        if len(provided) > 1:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="Too Many Inputs",
                    description="Please provide only one of `name`, `mention`, or `discord_id`.",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
            return

        ctx = SlashContext(interaction)
        user = None
        search_name = None
        target_text = None
        had_target = bool(provided)

        if discord_id:
            target_text = discord_id
            user = await self._resolve_user_by_id_or_mention(ctx, discord_id)
            if user is None:
                await interaction.response.send_message(
                    embed=discord.Embed(
                        title="User Not Found",
                        description="Could not find a user with that Discord ID.",
                        color=discord.Color.red(),
                    ),
                    ephemeral=True,
                )
                return
        elif mention:
            target_text = mention.mention
            user = mention
        elif name:
            target_text = name
            search_name = name
        else:
            user = interaction.user

        with db_connection(interaction.guild.id) as (conn, cursor):
            await self._stats_impl(ctx, cursor, user, search_name, target_text, had_target)

    @app_commands.command(
        name="expanded",
        description="View your averaged stats across one or more season/month combinations.",
    )
    @app_commands.describe(
        seasons=(
            "Season and month entries, e.g. `s1m1m2 s2m2 s3m1m2m3` for "
            "Season 1 months 1-2, Season 2 month 2, Season 3 months 1-3. "
            "A season with no `m` part (e.g. `s2`) gives that season's "
            "Overall stats."
        ),
    )
    @app_commands.guild_only()
    async def slash_stats_expanded(
        self,
        interaction: discord.Interaction,
        seasons: str,
    ):
        ctx = SlashContext(interaction)
        await self._stats_expanded_impl(ctx, seasons)

    async def _stats_impl(
        self,
        ctx: commands.Context,
        cursor,
        user,
        search_name,
        target,
        had_target: bool,
    ) -> None:
        user_id = None
        data_from_search = False

        if search_name:
            matches = self._get_player_data(cursor, search_name=search_name)
            if not matches:
                await ctx.send(
                    embed=discord.Embed(
                        title="Player Not Found",
                        description=f"No player found with the name `{search_name}`.",
                        color=discord.Color.red(),
                    )
                )
                return
            if len(matches) == 1:
                user_id = matches[0][0]
                data_from_search = True
            else:
                player_list = []
                for i, m in enumerate(matches, start=1):
                    uid, pname = m[0], m[1]
                    member = ctx.guild.get_member(uid)
                    if member:
                        mention_text = member.mention
                    else:
                        try:
                            mention_text = (await self.bot.fetch_user(uid)).mention
                        except Exception:
                            mention_text = f"<@{uid}>"
                    player_list.append(f"{i}. {pname} ({mention_text})")
                embed = discord.Embed(
                    title="Multiple Players Found",
                    description=f"Found {len(matches)} players with name containing '{search_name}':\n\n"
                    + "\n".join(player_list),
                    color=discord.Color.yellow(),
                )
                embed.add_field(
                    name="",
                    value="Please be more specific, use IDs or mention the player with @",
                    inline=False,
                )
                await ctx.send(embed=embed)
                return

        if not data_from_search:
            if user is None:
                await ctx.send(
                    embed=discord.Embed(
                        title="Error",
                        description="Could not find the user.",
                        color=discord.Color.red(),
                    )
                )
                return
            row = self._get_player_data(cursor, user_id=user.id)
            if not row:
                mention_text = (
                    user.mention if hasattr(user, "mention") else f"<@{user.id}>"
                )
                await ctx.send(
                    embed=discord.Embed(
                        title="Player Not Registered",
                        description=f"{mention_text} is not registered. Use `-register <name>` to sign up!",
                        color=discord.Color.red(),
                    )
                )
                return
            user_id = user.id

        view = StatsView(self, ctx, user_id, had_target)
        await view.update_stats(ctx)

    @commands.command(help="View player stats by Discord ID.")
    async def stats_id(self, ctx: commands.Context, discord_id: str):
        try:
            user_id = int(discord_id)
        except ValueError:
            await ctx.send(
                embed=discord.Embed(
                    title="Invalid ID",
                    description="Please provide a valid Discord user ID.",
                    color=discord.Color.red(),
                )
            )
            return

        mention_inline = await self._safe_fetch_mention(user_id)

        with db_connection(ctx.guild.id) as (conn, cursor):
            row = self._get_player_data(cursor, user_id=user_id)

            if not row:
                await ctx.send(
                    embed=discord.Embed(
                        title="Player Not Registered",
                        description=f"{mention_inline} is not registered. Use `-register <n>` to sign up!",
                        color=discord.Color.red(),
                    )
                )
                return

        view = StatsView(self, ctx, user_id, had_target=True)
        await view.update_stats(ctx)


async def setup(bot: commands.Bot):
    await bot.add_cog(StatsCog(bot))