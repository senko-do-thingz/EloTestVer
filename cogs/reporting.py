import asyncio
import logging
import re
import typing

import discord
from discord.ext import commands

from db.elo_db import db_connection, get_dev_mode, get_seasonal_report_enabled, set_seasonal_report_enabled
from db.h2h_db import add_h2h_match
from utils import calculate_elo, is_admin, sync_league_roles
from utils.lb_cache import invalidate as lb_cache_invalidate, schedule_rebuild as lb_cache_rebuild
from config import YES_EMOJI, NO_EMOJI

logger = logging.getLogger(__name__)


class ReportingCog(commands.Cog):
    EXPECTED_OUTCOME_DIVISOR = 1147.44429731658
    RAW_GAIN_MULTIPLIER = 20
    SET_WIN_BONUS = 5
    SWEEP_REDUCTION_FACTOR = 0.076923
    BO7_FIXED_BONUS = 2

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Bug fix #2: keyed by guild_id so locks are per-guild, not global
        self.pending_report_players: dict[int, set[int]] = {}

    # ------------------------------------------------------------------
    # Pending-report lock
    # ------------------------------------------------------------------

    def _acquire_pending_report_lock(self, guild_id: int, player_ids: set[int]) -> bool:
        guild_set = self.pending_report_players.setdefault(guild_id, set())
        if any(pid in guild_set for pid in player_ids):
            return False
        guild_set.update(player_ids)
        return True

    def _release_pending_report_lock(self, guild_id: int, player_ids: set[int]) -> None:
        guild_set = self.pending_report_players.get(guild_id)
        if guild_set:
            guild_set.difference_update(player_ids)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    _AD_RP_USAGE = (
        "- `-ad_rp <id1> <score1> <score2> <id2>`\n"
        "- `-ad_rp <id1> <score> <id2>` (score: `4-3`, `3:1`, `43`, `4 2`)"
    )

    def _parse_combined_score(self, score: str) -> tuple[int, int] | None:
        stripped = score.strip()
        match = re.match(r"(\d)\s*(\d)$", stripped) or re.match(r"(\d)\s*[-:]\s*(\d)$", stripped)
        if not match:
            return None
        return int(match.group(1)), int(match.group(2))

    def _parse_ad_rp_args(
        self, player1_id: int, args: tuple[str, ...]
    ) -> tuple[int, int, int, int] | None:
        if len(args) == 2:
            score_str, player2_raw = args
            try:
                player2_id = int(player2_raw)
            except ValueError:
                return None
            parsed = self._parse_combined_score(score_str)
            if not parsed:
                return None
            s1, s2 = parsed
            return player1_id, s1, s2, player2_id
        if len(args) == 3:
            try:
                s1, s2, player2_id = int(args[0]), int(args[1]), int(args[2])
            except ValueError:
                return None
            return player1_id, s1, s2, player2_id
        return None

    async def _remove_bot_reactions(self, message: discord.Message) -> None:
        bot_user = getattr(self.bot, "user", None)
        if not bot_user:
            return
        for emoji in (YES_EMOJI, NO_EMOJI):
            try:
                await message.remove_reaction(emoji, bot_user)
            except Exception:
                pass

    def _calculate_new_formula(
        self,
        winner_elo: int,
        loser_elo: int,
        winner_score: int,
        loser_score: int,
        *,
        elo_multiplier: float = 1.0,
    ) -> dict:
        total_games = winner_score + loser_score
        # Bug #9 fix: guard against zero-division if scores are somehow both 0
        if total_games == 0:
            raise ValueError("total_games cannot be zero (winner_score + loser_score == 0)")
        elo_diff = abs(winner_elo - loser_elo)

        expected_lower = 1 / (1 + (10 ** (elo_diff / self.EXPECTED_OUTCOME_DIVISOR)))
        expected_winner = 1 - expected_lower if winner_elo >= loser_elo else expected_lower
        quotient = winner_score / total_games

        raw_gain = (
            self.RAW_GAIN_MULTIPLIER * total_games * (quotient - expected_winner)
            + self.SET_WIN_BONUS
        ) * elo_multiplier

        score_gap = winner_score - loser_score
        sweep_factor = 1 - (self.SWEEP_REDUCTION_FACTOR * (score_gap - 1))
        actual_gain = raw_gain * sweep_factor

        bonus_raw = total_games / 4
        bo7_winner_bonus = self.BO7_FIXED_BONUS if winner_score == 4 else 0
        bo7_loser_bonus = -self.BO7_FIXED_BONUS if winner_score == 4 else 0

        winner_pre_round = winner_elo + actual_gain + bonus_raw + bo7_winner_bonus
        loser_pre_round = loser_elo - actual_gain + bonus_raw + bo7_loser_bonus

        return {
            "total_games": total_games,
            "elo_diff": elo_diff,
            "expected_lower": expected_lower,
            "expected_winner": expected_winner,
            "quotient": quotient,
            "raw_gain": raw_gain,
            "score_gap": score_gap,
            "sweep_factor": sweep_factor,
            "actual_gain": actual_gain,
            "bonus_raw": bonus_raw,
            "bo7_winner_bonus": bo7_winner_bonus,
            "bo7_loser_bonus": bo7_loser_bonus,
            "winner_pre_round": winner_pre_round,
            "loser_pre_round": loser_pre_round,
            "new_winner_elo": round(winner_pre_round),
            "new_loser_elo": round(loser_pre_round),
        }

    # ------------------------------------------------------------------
    # Score / player parsing
    # ------------------------------------------------------------------

    async def _parse_score_args(
        self,
        player1: discord.User,
        arg1: str,
        arg2: str | None,
        player2: discord.User | None,
    ) -> tuple[discord.User, discord.User, int, int] | str:
        """
        Parse các arguments của lệnh report.
        Trả về (winner, loser, winner_score, loser_score) hoặc chuỗi lỗi.
        """
        if player2 is None:
            # Format cũ: @winner score @loser
            if arg2 is None:
                return "Invalid format. Please use either:\n- `-report @winner score @loser`\n- `-report @player1 score1 score2 @player2`"

            match = re.match(r"(\d)\s*(\d)$", arg1) or re.match(r"(\d)\s*[-:]\s*(\d)$", arg1)
            if not match:
                return "Invalid score format. Please use formats like '31', '4-2', '3:1', or '4 2'."

            winner_score = int(match.group(1))
            loser_score = int(match.group(2))
            winner, loser = player1, None

            try:
                user_id = int(arg2.strip("<@!>&"))
                loser = await self.bot.fetch_user(user_id)
            except (discord.NotFound, discord.HTTPException):
                loser = None
            except (ValueError, TypeError):
                loser = None
            if loser is None:
                return "Could not find the mentioned user. Please make sure to properly mention the user."

            if winner_score < loser_score:
                winner_score, loser_score = loser_score, winner_score
                winner, loser = loser, player1

        else:
            # Format mới: @player1 score1 score2 @player2
            try:
                p1_score, p2_score = int(arg1), int(arg2)
            except (ValueError, TypeError):
                return "Invalid score values."

            if p1_score == p2_score:
                return "Scores cannot be equal. There must be a winner."

            if p1_score > p2_score:
                winner, loser, winner_score, loser_score = player1, player2, p1_score, p2_score
            else:
                winner, loser, winner_score, loser_score = player2, player1, p2_score, p1_score

        return winner, loser, winner_score, loser_score

    # ------------------------------------------------------------------
    # Core report implementation
    # ------------------------------------------------------------------

    async def _report_impl(
        self,
        ctx: commands.Context,
        player1: discord.User,
        arg1: str,
        arg2: typing.Optional[str],
        player2: typing.Optional[discord.User],
        *,
        elo_multiplier: float = 1.0,
        title_prefix: str = "",
        show_formula_comparison: bool = False,
    ):
        """Shared implementation cho -report, -rp, -seasonal_report."""
        lock_player_ids: set[int] = set()
        lock_acquired = False

        # --- Parse score args ---
        parse_result = await self._parse_score_args(player1, arg1, arg2, player2)
        if isinstance(parse_result, str):
            await ctx.send(embed=discord.Embed(
                title=title_prefix + "Match Report Error",
                description=parse_result,
                color=discord.Color.red(),
            ))
            return

        winner, loser, winner_score, loser_score = parse_result

        if winner == loser:
            await ctx.send(embed=discord.Embed(
                title=title_prefix + "Match Report Error",
                description="A player cannot report a match against themselves.",
                color=discord.Color.red(),
            ))
            return

        if winner_score not in [3, 4]:
            await ctx.send(embed=discord.Embed(
                title=title_prefix + "Match Report Error",
                description="Invalid match score. Winner's score must be 3 or 4.",
                color=discord.Color.red(),
            ))
            return

        lock_player_ids = {winner.id, loser.id}
        guild_id = ctx.guild.id
        if not self._acquire_pending_report_lock(guild_id, lock_player_ids):
            await ctx.send(embed=discord.Embed(
                title=title_prefix + "Match Report Error",
                description=(
                    f"<@{winner.id}> or <@{loser.id}> already has a pending report.\n"
                    "Please wait until the current report is confirmed or canceled."
                ),
                color=discord.Color.red(),
            ))
            return
        lock_acquired = True

        try:
            # --- Pre-flight: verify both players are registered and get display names ---
            # We only read names/registration status here; Elo is read fresh at commit time
            # to avoid the stale-Elo race condition (Bug #1).
            with db_connection(guild_id) as (conn, cursor):
                cursor.execute("SELECT name FROM players WHERE id=?", (winner.id,))
                winner_name_row = cursor.fetchone()
                cursor.execute("SELECT name FROM players WHERE id=?", (loser.id,))
                loser_name_row = cursor.fetchone()

            if not winner_name_row or not loser_name_row:
                if not winner_name_row and not loser_name_row:
                    desc = f"<@{winner.id}>, <@{loser.id}> are not registered. Use `-register <name>` to register."
                elif not winner_name_row:
                    desc = f"<@{winner.id}> is not registered. Use `-register <name>` to register."
                else:
                    desc = f"<@{loser.id}> is not registered. Use `-register <name>` to register."
                await ctx.send(embed=discord.Embed(
                    title=title_prefix + "Match Report Error",
                    description=desc,
                    color=discord.Color.red(),
                ))
                return

            winner_name = winner_name_row[0]
            loser_name = loser_name_row[0]
            total_games = winner_score + loser_score

            # --- Read fresh Elo for the preview embed (used for display only) ---
            with db_connection(guild_id) as (conn, cursor):
                cursor.execute("SELECT elo FROM players WHERE id=?", (winner.id,))
                preview_winner_elo = (cursor.fetchone() or (500,))[0]
                cursor.execute("SELECT elo FROM players WHERE id=?", (loser.id,))
                preview_loser_elo = (cursor.fetchone() or (500,))[0]

            preview_calc = self._calculate_new_formula(
                preview_winner_elo, preview_loser_elo, winner_score, loser_score,
                elo_multiplier=elo_multiplier,
            )
            preview_new_winner_elo = preview_calc["new_winner_elo"]
            preview_new_loser_elo = preview_calc["new_loser_elo"]
            preview_winner_delta = preview_new_winner_elo - preview_winner_elo
            preview_loser_delta = preview_new_loser_elo - preview_loser_elo

            comparison_text = ""
            if show_formula_comparison:
                base_winner_elo, base_loser_elo = calculate_elo(
                    preview_winner_elo, preview_loser_elo, total_games, winner_score, loser_score
                )
                old_actual_gain = round((base_winner_elo - preview_winner_elo) * elo_multiplier)
                bonus_text = f"{preview_calc['bonus_raw']:.2f}".rstrip("0").rstrip(".")
                comparison_text = (
                    "\n**Dev Mode - Old vs New Formula:**\n"
                    f"Input: winner_elo={preview_winner_elo}, loser_elo={preview_loser_elo}, score={winner_score}-{loser_score}, total_games={preview_calc['total_games']}\n"
                    f"Elo diff = {preview_calc['elo_diff']}\n"
                    f"Expected winner = {preview_calc['expected_winner']:.6f}\n"
                    f"Raw gain = {preview_calc['raw_gain']:.6f}\n"
                    f"Sweep factor = {preview_calc['sweep_factor']:.6f}\n"
                    f"Actual gain = {preview_calc['actual_gain']:.6f}\n"
                    f"Extra by set length = {bonus_text}\n"
                    f"BO7 bonus winner/loser = {preview_calc['bo7_winner_bonus']}/{preview_calc['bo7_loser_bonus']}\n"
                    f"Old formula: {preview_winner_elo + old_actual_gain} / {preview_loser_elo - old_actual_gain}\n"
                    f"New formula: {preview_new_winner_elo} / {preview_new_loser_elo}\n"
                )

            # --- Gửi embed pending và chờ reaction ---
            embed = discord.Embed(
                title=title_prefix + "Match Pending Confirmation",
                description=(
                    f"**{winner_name}** defeated **{loser_name}** with a score of {winner_score}-{loser_score}.\n"
                    f"**New Elo Ratings Preview:**\n"
                    f"🏆 {winner_name}: **{preview_new_winner_elo}** ({preview_winner_elo} + {preview_winner_delta})\n"
                    f"💀 {loser_name}: **{preview_new_loser_elo}** ({preview_loser_elo} - {abs(preview_loser_delta)})\n"
                    f"{comparison_text}\n"
                    f"Both players, please react with {YES_EMOJI} to confirm or {NO_EMOJI} to cancel within 5 minutes."
                ),
                color=discord.Color.orange(),
            )
            message = await ctx.send(embed=embed)
            await message.add_reaction(YES_EMOJI)
            await message.add_reaction(NO_EMOJI)

            def check(reaction, user):
                return (
                    user in [winner, loser]
                    and str(reaction.emoji) in [YES_EMOJI, NO_EMOJI]
                    and reaction.message.id == message.id
                )

            reactions: dict[str, set] = {YES_EMOJI: set(), NO_EMOJI: set()}
            confirmed = False
            try:
                while True:
                    reaction, user = await self.bot.wait_for("reaction_add", timeout=300.0, check=check)
                    reactions[str(reaction.emoji)].add(user)

                    if winner in reactions[YES_EMOJI] and loser in reactions[YES_EMOJI]:
                        confirmed = True
                        break
                    if winner in reactions[NO_EMOJI] or loser in reactions[NO_EMOJI]:
                        canceling_name = winner_name if winner in reactions[NO_EMOJI] else loser_name
                        await self._remove_bot_reactions(message)
                        await message.edit(embed=discord.Embed(
                            title=title_prefix + "Match Report Canceled",
                            description=(
                                f"**{canceling_name}** canceled the match report.\n"
                                "No Elo changes were applied."
                            ),
                            color=discord.Color.red(),
                        ))
                        return
            except asyncio.TimeoutError:
                await self._remove_bot_reactions(message)
                await message.edit(embed=discord.Embed(
                    title=title_prefix + "Match Confirmation Timeout",
                    description="Match report canceled due to timeout. Please try again.\nNo Elo changes were applied.",
                    color=discord.Color.red(),
                ))
                return

            if not confirmed:
                return

            # --- Bug #1 fix: re-read Elo fresh at commit time ---
            # Any admin edits or other confirmed matches that happened during
            # the 5-minute wait window are now reflected correctly.
            with db_connection(guild_id) as (conn, cursor):
                cursor.execute(
                    "SELECT elo, games_won, games_lost FROM players WHERE id=?",
                    (winner.id,),
                )
                winner_fresh = cursor.fetchone()
                cursor.execute(
                    "SELECT elo, games_won, games_lost FROM players WHERE id=?",
                    (loser.id,),
                )
                loser_fresh = cursor.fetchone()

                if not winner_fresh or not loser_fresh:
                    await ctx.send(embed=discord.Embed(
                        title=title_prefix + "Match Report Error",
                        description="A player was unregistered while the report was pending. No changes applied.",
                        color=discord.Color.red(),
                    ))
                    return

                winner_elo, winner_wins, winner_losses = winner_fresh
                loser_elo, loser_wins, loser_losses = loser_fresh

                calc = self._calculate_new_formula(
                    winner_elo, loser_elo, winner_score, loser_score,
                    elo_multiplier=elo_multiplier,
                )
                new_winner_elo = calc["new_winner_elo"]
                new_loser_elo = calc["new_loser_elo"]
                winner_delta = new_winner_elo - winner_elo
                loser_delta = new_loser_elo - loser_elo

                cursor.execute(
                    """
                    UPDATE players
                    SET elo=?, games_won=?, games_lost=?, games_played_this_month=games_played_this_month + ?
                    WHERE id=?
                    """,
                    (
                        new_winner_elo,
                        winner_wins + winner_score,
                        winner_losses + loser_score,
                        total_games,
                        winner.id,
                    ),
                )
                cursor.execute(
                    """
                    UPDATE players
                    SET elo=?, games_won=?, games_lost=?, games_played_this_month=games_played_this_month + ?
                    WHERE id=?
                    """,
                    (
                        new_loser_elo,
                        loser_wins + loser_score,
                        loser_losses + winner_score,
                        total_games,
                        loser.id,
                    ),
                )
                add_h2h_match(
                    cursor, winner.id, loser_name, winner_score, loser_score
                )
                add_h2h_match(
                    cursor, loser.id, winner_name, loser_score, winner_score
                )
                conn.commit()
            # Connection đã đóng – sync_league_roles có thể mở DB tự do

            # Invalidate cache và rebuild ngay sau khi Elo thay đổi
            lb_cache_invalidate(ctx.guild.id)
            lb_cache_rebuild(self.bot, ctx.guild.id)

            # Chỉ sync 2 player liên quan, không scan toàn bộ guild
            await sync_league_roles(ctx.guild, player_ids=[winner.id, loser.id])

            await self._remove_bot_reactions(message)
            await message.edit(embed=discord.Embed(
                title=title_prefix + "Match Confirmed",
                description=(
                    f"**{winner_name}** defeated **{loser_name}** with a score of {winner_score}-{loser_score}.\n"
                    f"New Elo Ratings:\n"
                    f"🏆 {winner_name}: **{new_winner_elo}** ({winner_elo} + {winner_delta})\n"
                    f"💀 {loser_name}: **{new_loser_elo}** ({loser_elo} - {abs(loser_delta)})"
                ),
                color=discord.Color.green(),
            ))

        except Exception as e:
            logger.exception("Unexpected error in _report_impl for guild %s", ctx.guild.id)
            await ctx.send(embed=discord.Embed(
                title=title_prefix + "Match Report Error",
                description=(
                    f"An unexpected error occurred: {e}\n"
                    "Please use the format: `-report @player1 score1 score2 @player2` or `-report @winner score @loser`"
                ),
                color=discord.Color.red(),
            ))
        finally:
            if lock_acquired:
                self._release_pending_report_lock(guild_id, lock_player_ids)

    # ------------------------------------------------------------------
    # Admin report (ad_rp) implementation
    # ------------------------------------------------------------------

    async def _ad_rp_impl(
        self,
        ctx: commands.Context,
        winner_id: int,
        winner_score: int,
        loser_score: int,
        loser_id: int,
        *,
        elo_multiplier: float = 1.0,
        title_prefix: str = "",
    ):
        """Shared implementation cho -ad_rp và -ad_srp."""
        lock_player_ids: set[int] = set()
        lock_acquired = False

        try:
            winner = await self.bot.fetch_user(winner_id)
            loser = await self.bot.fetch_user(loser_id)
        except discord.NotFound:
            await ctx.send(embed=discord.Embed(
                title="User Not Found",
                description="One or both users were not found with the provided Discord IDs.",
                color=discord.Color.red(),
            ))
            return
        except Exception as e:
            logger.exception("fetch_user failed in _ad_rp_impl")
            await ctx.send(embed=discord.Embed(
                title=title_prefix + "Match Report Error",
                description=f"An error occurred: {e}\n{self._AD_RP_USAGE}",
                color=discord.Color.red(),
            ))
            return

        try:
            if winner == loser:
                await ctx.send(embed=discord.Embed(
                    title=title_prefix + "Match Report Error",
                    description="A player cannot report a match against themselves.",
                    color=discord.Color.red(),
                ))
                return

            if winner_score < loser_score:
                winner_score, loser_score = loser_score, winner_score
                winner, loser = loser, winner

            if winner_score == loser_score:
                await ctx.send(embed=discord.Embed(
                    title=title_prefix + "Match Report Error",
                    description="Scores cannot be equal. There must be a winner.",
                    color=discord.Color.red(),
                ))
                return

            if winner_score not in [3, 4]:
                await ctx.send(embed=discord.Embed(
                    title=title_prefix + "Match Report Error",
                    description="Invalid match score. Winner's score must be 3 or 4.",
                    color=discord.Color.red(),
                ))
                return

            lock_player_ids = {winner.id, loser.id}
            guild_id = ctx.guild.id
            if not self._acquire_pending_report_lock(guild_id, lock_player_ids):
                await ctx.send(embed=discord.Embed(
                    title=title_prefix + "Match Report Error",
                    description=(
                        f"<@{winner.id}> or <@{loser.id}> already has a pending report.\n"
                        "Please wait until the current report is confirmed or canceled."
                    ),
                    color=discord.Color.red(),
                ))
                return
            lock_acquired = True

            # --- Đọc player data, đóng connection ngay ---
            with db_connection(ctx.guild.id) as (conn, cursor):
                cursor.execute(
                    "SELECT elo, games_won, games_lost, name FROM players WHERE id=?",
                    (winner.id,),
                )
                winner_data = cursor.fetchone()
                cursor.execute(
                    "SELECT elo, games_won, games_lost, name FROM players WHERE id=?",
                    (loser.id,),
                )
                loser_data = cursor.fetchone()

            if not winner_data or not loser_data:
                if not winner_data and not loser_data:
                    desc = f"<@{winner.id}>, <@{loser.id}> are not registered."
                elif not winner_data:
                    desc = f"<@{winner.id}> is not registered."
                else:
                    desc = f"<@{loser.id}> is not registered."
                await ctx.send(embed=discord.Embed(
                    title=title_prefix + "Match Report Error",
                    description=desc + " Use `-register <name>` to register.",
                    color=discord.Color.red(),
                ))
                return

            winner_elo, winner_wins, winner_losses, winner_name = winner_data
            loser_elo, loser_wins, loser_losses, loser_name = loser_data

            calc = self._calculate_new_formula(
                winner_elo, loser_elo, winner_score, loser_score,
                elo_multiplier=elo_multiplier,
            )
            new_winner_elo = calc["new_winner_elo"]
            new_loser_elo = calc["new_loser_elo"]
            winner_delta = new_winner_elo - winner_elo
            loser_delta = new_loser_elo - loser_elo
            total_games = winner_score + loser_score

            embed = discord.Embed(
                title=title_prefix + "Match Pending Confirmation",
                description=(
                    f"**{winner_name}** defeated **{loser_name}** with a score of {winner_score}-{loser_score}.\n"
                    f"**New Elo Ratings Preview:**\n"
                    f"🏆 {winner_name}: **{new_winner_elo}** ({winner_elo} + {winner_delta})\n"
                    f"💀 {loser_name}: **{new_loser_elo}** ({loser_elo} - {abs(loser_delta)})\n\n"
                    f"Admin ({ctx.author.mention}), please react with {YES_EMOJI} to confirm or {NO_EMOJI} to cancel within 5 minutes."
                ),
                color=discord.Color.orange(),
            )
            message = await ctx.send(embed=embed)
            await message.add_reaction(YES_EMOJI)
            await message.add_reaction(NO_EMOJI)

            def check(reaction, user):
                return (
                    user == ctx.author
                    and str(reaction.emoji) in [YES_EMOJI, NO_EMOJI]
                    and reaction.message.id == message.id
                )

            try:
                reaction, _ = await self.bot.wait_for("reaction_add", timeout=300.0, check=check)
                if str(reaction.emoji) != YES_EMOJI:
                    await self._remove_bot_reactions(message)
                    await message.edit(embed=discord.Embed(
                        title=title_prefix + "Match Report Canceled",
                        description=(
                            "The match report has been canceled.\n"
                            "No Elo changes were applied.\n"
                            f"**{winner_name}**: {winner_elo}\n"
                            f"**{loser_name}**: {loser_elo}"
                        ),
                        color=discord.Color.red(),
                    ))
                    return
            except asyncio.TimeoutError:
                await self._remove_bot_reactions(message)
                await message.edit(embed=discord.Embed(
                    title=title_prefix + "Match Confirmation Timeout",
                    description=(
                        "Match report canceled due to timeout. Please try again.\n"
                        "No Elo changes were applied.\n"
                        f"**{winner_name}**: {winner_elo}\n"
                        f"**{loser_name}**: {loser_elo}"
                    ),
                    color=discord.Color.red(),
                ))
                return

            # --- Commit, đóng connection, rồi sync roles ---
            with db_connection(ctx.guild.id) as (conn, cursor):
                cursor.execute(
                    """
                    UPDATE players
                    SET elo=?, games_won=?, games_lost=?, games_played_this_month=games_played_this_month + ?
                    WHERE id=?
                    """,
                    (
                        new_winner_elo,
                        winner_wins + winner_score,
                        winner_losses + loser_score,
                        total_games,
                        winner.id,
                    ),
                )
                cursor.execute(
                    """
                    UPDATE players
                    SET elo=?, games_won=?, games_lost=?, games_played_this_month=games_played_this_month + ?
                    WHERE id=?
                    """,
                    (
                        new_loser_elo,
                        loser_wins + loser_score,
                        loser_losses + winner_score,
                        total_games,
                        loser.id,
                    ),
                )
                add_h2h_match(
                    cursor, winner.id, loser_name, winner_score, loser_score
                )
                add_h2h_match(
                    cursor, loser.id, winner_name, loser_score, winner_score
                )
                conn.commit()

            lb_cache_invalidate(ctx.guild.id)
            lb_cache_rebuild(self.bot, ctx.guild.id)

            await sync_league_roles(ctx.guild, player_ids=[winner.id, loser.id])

            await self._remove_bot_reactions(message)
            await message.edit(embed=discord.Embed(
                title=title_prefix + "Match Confirmed",
                description=(
                    f"**{winner_name}** defeated **{loser_name}** with a score of {winner_score}-{loser_score}.\n"
                    f"New Elo Ratings:\n"
                    f"🏆 {winner_name}: **{new_winner_elo}** ({winner_elo} + {winner_delta})\n"
                    f"💀 {loser_name}: **{new_loser_elo}** ({loser_elo} - {abs(loser_delta)})"
                ),
                color=discord.Color.green(),
            ))

        except Exception as e:
            logger.exception("Unexpected error in _ad_rp_impl for guild %s", ctx.guild.id)
            await ctx.send(embed=discord.Embed(
                title=title_prefix + "Match Report Error",
                description=f"An unexpected error occurred: {e}\n{self._AD_RP_USAGE}",
                color=discord.Color.red(),
            ))
        finally:
            if lock_acquired:
                self._release_pending_report_lock(guild_id, lock_player_ids)

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    @commands.command(
        name="report",
        help="Report a match. Usage: -report @player1 score1 score2 @player2 or -report @winner score @loser",
    )
    async def report(
        self,
        ctx: commands.Context,
        player1: discord.User,
        arg1: str,
        arg2: typing.Optional[str] = None,
        player2: typing.Optional[discord.User] = None,
    ):
        await self._report_impl(ctx, player1, arg1, arg2, player2)

    @commands.command(
        name="rp",
        help="Alias for -report. Usage: -rp @player1 score1 score2 @player2 or -rp @winner score @loser",
    )
    async def rp(
        self,
        ctx: commands.Context,
        player1: discord.User,
        arg1: str,
        arg2: typing.Optional[str] = None,
        player2: typing.Optional[discord.User] = None,
    ):
        await self._report_impl(
            ctx, player1, arg1, arg2, player2,
            show_formula_comparison=get_dev_mode(ctx.guild.id),
        )

    @commands.command(
        name="gimmick",
        help="Enable or disable seasonal report (1.5x Elo). Admin only. Usage: -gimmick on | off",
    )
    @commands.check(is_admin)
    async def gimmick(self, ctx: commands.Context, on_off: str):
        if on_off.lower() not in ("on", "off"):
            await ctx.send(embed=discord.Embed(
                title="Invalid Argument",
                description="Use `-gimmick on` or `-gimmick off`.",
                color=discord.Color.red(),
            ))
            return
        enabled = on_off.lower() == "on"
        set_seasonal_report_enabled(ctx.guild.id, enabled)
        await ctx.send(embed=discord.Embed(
            title="Seasonal Report",
            description=(
                f"Seasonal report is now **{'enabled' if enabled else 'disabled'}**."
                + (" Members can use `-seasonal_report` or `-srp` to report matches with 1.5x Elo." if enabled else "")
            ),
            color=discord.Color.green(),
        ))

    @gimmick.error
    async def gimmick_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.CheckFailure):
            await ctx.send(embed=discord.Embed(
                title="Permission Denied",
                description="You do not have permission to use this command.",
                color=discord.Color.red(),
            ))

    @commands.command(
        name="seasonal_report",
        aliases=["srp"],
        help="Report a seasonal match (1.5x Elo).",
    )
    async def seasonal_report(
        self,
        ctx: commands.Context,
        player1: discord.User,
        arg1: str,
        arg2: typing.Optional[str] = None,
        player2: typing.Optional[discord.User] = None,
    ):
        if not get_seasonal_report_enabled(ctx.guild.id):
            await ctx.send(embed=discord.Embed(
                title="Seasonal Report Disabled",
                description="Seasonal report is currently disabled.",
                color=discord.Color.red(),
            ))
            return
        await self._report_impl(ctx, player1, arg1, arg2, player2, elo_multiplier=1.5, title_prefix="Seasonal ")

    # ------------------------------------------------------------------
    # Admin report commands (ad_rp)
    # ------------------------------------------------------------------

    async def _run_ad_rp_command(
        self,
        ctx: commands.Context,
        player1_id: int,
        args: tuple[str, ...],
        *,
        elo_multiplier: float = 1.0,
        title_prefix: str = "",
    ):
        parsed = self._parse_ad_rp_args(player1_id, args)
        if not parsed:
            await ctx.send(embed=discord.Embed(
                title=title_prefix + "Match Report Error",
                description=f"Invalid format or score.\n{self._AD_RP_USAGE}\nScore examples: `4-3`, `3:1`, `43`, `4 2`.",
                color=discord.Color.red(),
            ))
            return
        p1_id, s1, s2, p2_id = parsed
        await self._ad_rp_impl(ctx, p1_id, s1, s2, p2_id, elo_multiplier=elo_multiplier, title_prefix=title_prefix)

    @commands.command(
        name="ad_rp",
        help="Report a match using Discord IDs (admin only). Usage: -ad_rp <id1> <score1> <score2> <id2>",
    )
    @commands.check(is_admin)
    async def ad_rp(self, ctx: commands.Context, player1_id: int, *args: str):
        await self._run_ad_rp_command(ctx, player1_id, args)

    @ad_rp.error
    async def ad_rp_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.CheckFailure):
            await ctx.send(embed=discord.Embed(
                title="Permission Denied",
                description="You do not have permission to use this command.",
                color=discord.Color.red(),
            ))
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(embed=discord.Embed(
                title="Match Report Error",
                description=f"Missing arguments.\n{self._AD_RP_USAGE}",
                color=discord.Color.red(),
            ))

    @commands.command(
        name="ad_srp",
        help="Report a seasonal match (1.5x Elo) using Discord IDs (admin only). Same args as -ad_rp.",
    )
    @commands.check(is_admin)
    async def ad_srp(self, ctx: commands.Context, player1_id: int, *args: str):
        await self._run_ad_rp_command(ctx, player1_id, args, elo_multiplier=1.5, title_prefix="Seasonal ")

    @ad_srp.error
    async def ad_srp_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.CheckFailure):
            await ctx.send(embed=discord.Embed(
                title="Permission Denied",
                description="You do not have permission to use this command.",
                color=discord.Color.red(),
            ))


async def setup(bot: commands.Bot):
    await bot.add_cog(ReportingCog(bot))