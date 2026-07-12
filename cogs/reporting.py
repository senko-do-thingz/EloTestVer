import asyncio
import logging
import re
import typing

import discord
from discord import app_commands
from discord.ext import commands

from db.elo_db import db_connection, get_dev_mode, apply_win_streak, get_season_month_cursor
from db.h2h_db import add_h2h_match
from db.match_history_db import (
    add_match_history_row,
    increment_player_matches,
    next_match_id,
    trim_match_history,
)
from utils import calculate_elo, is_admin, is_admin_interaction, sync_league_roles
from utils.lb_cache import invalidate as lb_cache_invalidate, schedule_rebuild as lb_cache_rebuild
from config import YES_EMOJI, NO_EMOJI
from utils.slash_ctx import SlashContext

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
        )

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
        Parse the arguments of the report command.
        Returns (winner, loser, winner_score, loser_score) or an error string.
        """
        if player2 is None:
            # Old format: @winner score @loser
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
            # New format: @player1 score1 score2 @player2
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
        show_formula_comparison: bool = False,
    ):
        """Shared implementation for -report, -rp."""
        lock_player_ids: set[int] = set()
        lock_acquired = False

        # --- Parse score args ---
        parse_result = await self._parse_score_args(player1, arg1, arg2, player2)
        if isinstance(parse_result, str):
            await ctx.send(embed=discord.Embed(
                title="Match Report Error",
                description=parse_result,
                color=discord.Color.red(),
            ))
            return

        winner, loser, winner_score, loser_score = parse_result

        if winner == loser:
            await ctx.send(embed=discord.Embed(
                title="Match Report Error",
                description="A player cannot report a match against themselves.",
                color=discord.Color.red(),
            ))
            return

        if winner_score not in [3, 4]:
            await ctx.send(embed=discord.Embed(
                title="Match Report Error",
                description="Invalid match score. Winner's score must be 3 or 4.",
                color=discord.Color.red(),
            ))
            return

        lock_player_ids = {winner.id, loser.id}
        guild_id = ctx.guild.id
        if not self._acquire_pending_report_lock(guild_id, lock_player_ids):
            await ctx.send(embed=discord.Embed(
                title="Match Report Error",
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
                    title="Match Report Error",
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
                old_actual_gain = round(base_winner_elo - preview_winner_elo)
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

            # --- Send pending embed and wait for reaction ---
            embed = discord.Embed(
                title="Match Pending Confirmation",
                description=(
                    f"**{winner_name}** defeated **{loser_name}** with a score of {winner_score}-{loser_score}.\n"
                    f"**New Elo Ratings Preview:**\n"
                    f"🏆 {winner_name}: **{preview_new_winner_elo}** ({preview_winner_elo} + {preview_winner_delta})\n"
                    f"💀 {loser_name}: **{preview_new_loser_elo}** ({preview_loser_elo} - {abs(preview_loser_delta)})\n"
                    f"{comparison_text}\n"
                    f"Both players, please react with {YES_EMOJI} to confirm or {NO_EMOJI} to cancel within 1 hour." # Modified: increased timeout to 1 hour
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
                    # Modified: increased wait_for timeout from 300.0 (5 min) to 3600.0 (1 hour)
                    reaction, user = await self.bot.wait_for("reaction_add", timeout=3600.0, check=check)
                    reactions[str(reaction.emoji)].add(user)

                    if winner in reactions[YES_EMOJI] and loser in reactions[YES_EMOJI]:
                        confirmed = True
                        break
                    if winner in reactions[NO_EMOJI] or loser in reactions[NO_EMOJI]:
                        canceling_name = winner_name if winner in reactions[NO_EMOJI] else loser_name
                        await self._remove_bot_reactions(message)
                        await message.edit(embed=discord.Embed(
                            title="Match Report Canceled",
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
                    title="Match Confirmation Timeout",
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
                        title="Match Report Error",
                        description="A player was unregistered while the report was pending. No changes applied.",
                        color=discord.Color.red(),
                    ))
                    return

                winner_elo, winner_wins, winner_losses = winner_fresh
                loser_elo, loser_wins, loser_losses = loser_fresh

                calc = self._calculate_new_formula(
                    winner_elo, loser_elo, winner_score, loser_score,
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
                match_id = next_match_id(cursor)
                current_season, current_month = get_season_month_cursor(cursor, guild_id)
                add_match_history_row(
                    cursor,
                    match_id=match_id,
                    player_id=winner.id,
                    opponent_name=loser_name,
                    wins=winner_score,
                    losses=loser_score,
                    elo_init=winner_elo,
                    elo_final=new_winner_elo,
                    season=current_season,
                    month=current_month,
                )
                add_match_history_row(
                    cursor,
                    match_id=match_id,
                    player_id=loser.id,
                    opponent_name=winner_name,
                    wins=loser_score,
                    losses=winner_score,
                    elo_init=loser_elo,
                    elo_final=new_loser_elo,
                    season=current_season,
                    month=current_month,
                )
                increment_player_matches(cursor, winner.id)
                increment_player_matches(cursor, loser.id)
                apply_win_streak(cursor, winner.id, won=True)
                apply_win_streak(cursor, loser.id, won=False)
                trim_match_history(cursor, winner.id)
                trim_match_history(cursor, loser.id)
                conn.commit()
            # Connection closed - sync_league_roles can open the DB freely

            # Invalidate cache and rebuild right after the Elo change
            lb_cache_invalidate(ctx.guild.id)
            lb_cache_rebuild(self.bot, ctx.guild.id)

            # Only sync the 2 relevant players, don't scan the whole guild
            await sync_league_roles(ctx.guild, player_ids=[winner.id, loser.id])

            await self._remove_bot_reactions(message)
            await message.edit(embed=discord.Embed(
                title="Match Confirmed",
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
                title="Match Report Error",
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
    ):
        """Shared implementation for -ad_rp."""
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
                title="Match Report Error",
                description=f"An error occurred: {e}\n{self._AD_RP_USAGE}",
                color=discord.Color.red(),
            ))
            return

        try:
            if winner == loser:
                await ctx.send(embed=discord.Embed(
                    title="Match Report Error",
                    description="A player cannot report a match against themselves.",
                    color=discord.Color.red(),
                ))
                return

            if winner_score < loser_score:
                winner_score, loser_score = loser_score, winner_score
                winner, loser = loser, winner

            if winner_score == loser_score:
                await ctx.send(embed=discord.Embed(
                    title="Match Report Error",
                    description="Scores cannot be equal. There must be a winner.",
                    color=discord.Color.red(),
                ))
                return

            if winner_score not in [3, 4]:
                await ctx.send(embed=discord.Embed(
                    title="Match Report Error",
                    description="Invalid match score. Winner's score must be 3 or 4.",
                    color=discord.Color.red(),
                ))
                return

            lock_player_ids = {winner.id, loser.id}
            guild_id = ctx.guild.id
            if not self._acquire_pending_report_lock(guild_id, lock_player_ids):
                await ctx.send(embed=discord.Embed(
                    title="Match Report Error",
                    description=(
                        f"<@{winner.id}> or <@{loser.id}> already has a pending report.\n"
                        "Please wait until the current report is confirmed or canceled."
                    ),
                    color=discord.Color.red(),
                ))
                return
            lock_acquired = True

            # --- Read player data, close connection right away ---
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
                    title="Match Report Error",
                    description=desc + " Use `-register <name>` to register.",
                    color=discord.Color.red(),
                ))
                return

            winner_elo, winner_wins, winner_losses, winner_name = winner_data
            loser_elo, loser_wins, loser_losses, loser_name = loser_data

            calc = self._calculate_new_formula(
                winner_elo, loser_elo, winner_score, loser_score,
            )
            new_winner_elo = calc["new_winner_elo"]
            new_loser_elo = calc["new_loser_elo"]
            winner_delta = new_winner_elo - winner_elo
            loser_delta = new_loser_elo - loser_elo
            total_games = winner_score + loser_score

            embed = discord.Embed(
                title="Match Pending Confirmation",
                description=(
                    f"**{winner_name}** defeated **{loser_name}** with a score of {winner_score}-{loser_score}.\n"
                    f"**New Elo Ratings Preview:**\n"
                    f"🏆 {winner_name}: **{new_winner_elo}** ({winner_elo} + {winner_delta})\n"
                    f"💀 {loser_name}: **{new_loser_elo}** ({loser_elo} - {abs(loser_delta)})\n\n"
                    f"Admin ({ctx.author.mention}), please react with {YES_EMOJI} to confirm or {NO_EMOJI} to cancel within 1 hour." # Modified: increased timeout to 1 hour
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
                # Modified: increased wait_for timeout from 300.0 (5 min) to 3600.0 (1 hour)
                reaction, _ = await self.bot.wait_for("reaction_add", timeout=3600.0, check=check)
                if str(reaction.emoji) != YES_EMOJI:
                    await self._remove_bot_reactions(message)
                    await message.edit(embed=discord.Embed(
                        title="Match Report Canceled",
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
                    title="Match Confirmation Timeout",
                    description=(
                        "Match report canceled due to timeout. Please try again.\n"
                        "No Elo changes were applied.\n"
                        f"**{winner_name}**: {winner_elo}\n"
                        f"**{loser_name}**: {loser_elo}"
                    ),
                    color=discord.Color.red(),
                ))
                return

            # --- Commit, close connection, then sync roles ---
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
                match_id = next_match_id(cursor)
                current_season, current_month = get_season_month_cursor(cursor, ctx.guild.id)
                add_match_history_row(
                    cursor,
                    match_id=match_id,
                    player_id=winner.id,
                    opponent_name=loser_name,
                    wins=winner_score,
                    losses=loser_score,
                    elo_init=winner_elo,
                    elo_final=new_winner_elo,
                    season=current_season,
                    month=current_month,
                )
                add_match_history_row(
                    cursor,
                    match_id=match_id,
                    player_id=loser.id,
                    opponent_name=winner_name,
                    wins=loser_score,
                    losses=winner_score,
                    elo_init=loser_elo,
                    elo_final=new_loser_elo,
                    season=current_season,
                    month=current_month,
                )
                increment_player_matches(cursor, winner.id)
                increment_player_matches(cursor, loser.id)
                apply_win_streak(cursor, winner.id, won=True)
                apply_win_streak(cursor, loser.id, won=False)
                trim_match_history(cursor, winner.id)
                trim_match_history(cursor, loser.id)
                conn.commit()

            lb_cache_invalidate(ctx.guild.id)
            lb_cache_rebuild(self.bot, ctx.guild.id)

            await sync_league_roles(ctx.guild, player_ids=[winner.id, loser.id])

            await self._remove_bot_reactions(message)
            await message.edit(embed=discord.Embed(
                title="Match Confirmed",
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
                title="Match Report Error",
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

    # ------------------------------------------------------------------
    # Admin report commands (ad_rp)
    # ------------------------------------------------------------------

    async def _run_ad_rp_command(
        self,
        ctx: commands.Context,
        player1_id: int,
        args: tuple[str, ...],
    ):
        parsed = self._parse_ad_rp_args(player1_id, args)
        if not parsed:
            await ctx.send(embed=discord.Embed(
                title="Match Report Error",
                description=f"Invalid format or score.\n{self._AD_RP_USAGE}\nScore examples: `4-3`, `3:1`, `43`, `4 2`.",
                color=discord.Color.red(),
            ))
            return
        p1_id, s1, s2, p2_id = parsed
        await self._ad_rp_impl(ctx, p1_id, s1, s2, p2_id)

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

    async def cog_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        """Slash-command counterpart of ad_rp_error.

        Without this, an app_commands.CheckFailure from is_admin_interaction
        (used on /ad_rp) is never caught, so the interaction is left
        unanswered and Discord shows "The application did not respond"
        instead of a proper permission-denied message.
        """
        if isinstance(error, app_commands.CheckFailure):
            embed = discord.Embed(
                title="Permission Denied",
                description="You do not have permission to use this command.",
                color=discord.Color.red(),
            )
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        raise error

    # ------------------------------------------------------------------
    # Slash commands (structured inputs; prefix keeps legacy parsing)
    # ------------------------------------------------------------------

    @app_commands.command(
        name="report",
        description="Report a match between two registered players. The player with the higher score wins.",
    )
    @app_commands.describe(
        player1="First player",
        p1_score="Player 1's set score (0-4)",
        p2_score="Player 2's set score (0-4)",
        player2="Second player",
    )
    @app_commands.guild_only()
    async def slash_report(
        self,
        interaction: discord.Interaction,
        player1: discord.Member,
        p1_score: app_commands.Range[int, 0, 4],
        p2_score: app_commands.Range[int, 0, 4],
        player2: discord.Member,
    ):
        ctx = SlashContext(interaction)
        await self._report_impl(
            ctx,
            player1,
            str(p1_score),
            str(p2_score),
            player2,
            show_formula_comparison=get_dev_mode(interaction.guild_id),
        )

    @app_commands.command(name="ad_rp", description="Report a match using Discord IDs (admin only).")
    @app_commands.describe(
        player1_id="First player's Discord ID",
        p1_score="First player's set score (0-4)",
        p2_score="Second player's set score (0-3)",
        player2_id="Second player's Discord ID",
    )
    @app_commands.guild_only()
    @app_commands.check(is_admin_interaction)
    async def slash_ad_rp(
        self,
        interaction: discord.Interaction,
        player1_id: str,
        p1_score: app_commands.Range[int, 0, 4],
        p2_score: app_commands.Range[int, 0, 3],
        player2_id: str,
    ):
        try:
            p1_id, p2_id = int(player1_id), int(player2_id)
        except ValueError:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="Match Report Error",
                    description="Invalid Discord ID format.",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
            return
        ctx = SlashContext(interaction)
        await self._ad_rp_impl(ctx, p1_id, p1_score, p2_score, p2_id)


async def setup(bot: commands.Bot):
    await bot.add_cog(ReportingCog(bot))