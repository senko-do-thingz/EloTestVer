import asyncio
import logging
import re

import discord
from discord import app_commands
from discord.ext import commands

from db.elo_db import db_connection
from db.match_history_db import (
    decrement_player_matches,
    delete_match_history_by_id,
    find_latest_match_id_for_revert,
    get_player_matches_count,
)
from utils import is_admin, is_admin_interaction, sync_league_roles
from utils.lb_cache import invalidate as lb_cache_invalidate, schedule_rebuild as lb_cache_rebuild
from config import YES_EMOJI, NO_EMOJI
from utils.slash_ctx import SlashContext

logger = logging.getLogger(__name__)


class RevertCog(commands.Cog):
    # Must match ReportingCog exactly
    EXPECTED_OUTCOME_DIVISOR = 1147.44429731658
    RAW_GAIN_MULTIPLIER = 20
    SET_WIN_BONUS = 5
    SWEEP_REDUCTION_FACTOR = 0.076923
    BO7_FIXED_BONUS = 2

    _REVERT_USAGE = (
        "- `-revert <winner_id> <score1> <score2> <loser_id>`\n"
        "- `-revert <winner_id> <score> <loser_id>` (score: `4-3`, `3:1`, `43`, `4 2`)"
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _remove_bot_reactions(self, message: discord.Message) -> None:
        bot_user = getattr(self.bot, "user", None)
        if not bot_user:
            return
        for emoji in (YES_EMOJI, NO_EMOJI):
            try:
                await message.remove_reaction(emoji, bot_user)
            except Exception:
                pass

    def _parse_combined_score(self, score: str) -> tuple[int, int] | None:
        stripped = score.strip()
        match = re.match(r"(\d)\s*(\d)$", stripped) or re.match(
            r"(\d)\s*[-:]\s*(\d)$", stripped
        )
        if not match:
            return None
        return int(match.group(1)), int(match.group(2))

    def _parse_revert_args(
        self, winner_raw_id: int, args: tuple[str, ...]
    ) -> tuple[int, int, int, int] | None:
        """Return (winner_id, winner_score, loser_score, loser_id) or None."""
        if len(args) == 2:
            score_str, loser_raw = args
            try:
                loser_id = int(loser_raw)
            except ValueError:
                return None
            parsed = self._parse_combined_score(score_str)
            if not parsed:
                return None
            s1, s2 = parsed
            return winner_raw_id, s1, s2, loser_id
        if len(args) == 3:
            try:
                s1, s2, loser_id = int(args[0]), int(args[1]), int(args[2])
            except ValueError:
                return None
            return winner_raw_id, s1, s2, loser_id
        return None

    def _calculate_new_formula(
        self,
        winner_elo: int,
        loser_elo: int,
        winner_score: int,
        loser_score: int,
    ) -> dict:
        """Exact copy of ReportingCog._calculate_new_formula."""
        total_games = winner_score + loser_score
        # Bug #9 fix: guard against zero-division
        if total_games == 0:
            raise ValueError("total_games cannot be zero (winner_score + loser_score == 0)")
        elo_diff = abs(winner_elo - loser_elo)

        expected_lower = 1 / (1 + (10 ** (elo_diff / self.EXPECTED_OUTCOME_DIVISOR)))
        expected_winner = (
            1 - expected_lower if winner_elo >= loser_elo else expected_lower
        )
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
            "new_winner_elo": round(winner_pre_round),
            "new_loser_elo": round(loser_pre_round),
        }

    def _recover_pre_match_elos(
        self,
        cur_winner_elo: int,
        cur_loser_elo: int,
        winner_score: int,
        loser_score: int,
        *,
        max_iter: int = 50,
    ) -> tuple[int, int]:
        """
        Find (pre_winner_elo, pre_loser_elo) such that applying the formula
        produces exactly (cur_winner_elo, cur_loser_elo).
        Uses fixed-point iteration; converges in < 20 steps in all real cases.
        """
        est_w, est_l = cur_winner_elo, cur_loser_elo
        for _ in range(max_iter):
            calc = self._calculate_new_formula(
                est_w, est_l, winner_score, loser_score,
            )
            new_est_w = cur_winner_elo - (calc["new_winner_elo"] - est_w)
            new_est_l = cur_loser_elo - (calc["new_loser_elo"] - est_l)
            if new_est_w == est_w and new_est_l == est_l:
                break
            est_w, est_l = new_est_w, new_est_l
        return est_w, est_l

    # ------------------------------------------------------------------
    # Core revert implementation
    # ------------------------------------------------------------------

    async def _revert_impl(
        self,
        ctx: commands.Context,
        winner_id: int,
        winner_score: int,
        loser_score: int,
        loser_id: int,
    ):
        """Shared implementation for -revert."""
        # --- Fetch users ---
        try:
            winner = await self.bot.fetch_user(winner_id)
            loser = await self.bot.fetch_user(loser_id)
        except discord.NotFound:
            await ctx.send(embed=discord.Embed(
                title="Revert Error",
                description="One or both users were not found with the provided Discord IDs.",
                color=discord.Color.red(),
            ))
            return
        except Exception as e:
            logger.exception("fetch_user failed in _revert_impl")
            await ctx.send(embed=discord.Embed(
                title="Revert Error",
                description=f"An error occurred: {e}\n{self._REVERT_USAGE}",
                color=discord.Color.red(),
            ))
            return

        # --- Validate ---
        if winner_id == loser_id:
            await ctx.send(embed=discord.Embed(
                title="Revert Error",
                description="A player cannot have a match against themselves.",
                color=discord.Color.red(),
            ))
            return

        if winner_score < loser_score:
            winner_score, loser_score = loser_score, winner_score
            winner, loser = loser, winner
            winner_id, loser_id = loser_id, winner_id

        if winner_score == loser_score:
            await ctx.send(embed=discord.Embed(
                title="Revert Error",
                description="Scores cannot be equal. There must be a winner.",
                color=discord.Color.red(),
            ))
            return

        if winner_score not in [3, 4]:
            await ctx.send(embed=discord.Embed(
                title="Revert Error",
                description="Invalid match score. Winner's score must be 3 or 4.",
                color=discord.Color.red(),
            ))
            return

        # --- Read DB ---
        with db_connection(ctx.guild.id) as (conn, cursor):
            cursor.execute(
                "SELECT elo, games_won, games_lost, games_played_this_month, name FROM players WHERE id=?",
                (winner.id,),
            )
            winner_row = cursor.fetchone()
            cursor.execute(
                "SELECT elo, games_won, games_lost, games_played_this_month, name FROM players WHERE id=?",
                (loser.id,),
            )
            loser_row = cursor.fetchone()

        if not winner_row or not loser_row:
            if not winner_row and not loser_row:
                desc = f"<@{winner.id}>, <@{loser.id}> are not registered."
            elif not winner_row:
                desc = f"<@{winner.id}> is not registered."
            else:
                desc = f"<@{loser.id}> is not registered."
            await ctx.send(embed=discord.Embed(
                title="Revert Error",
                description=desc + " Use `-register <name>` to register.",
                color=discord.Color.red(),
            ))
            return

        cur_winner_elo, cur_winner_wins, cur_winner_losses, cur_winner_monthly, winner_name = winner_row
        cur_loser_elo, cur_loser_wins, cur_loser_losses, cur_loser_monthly, loser_name = loser_row
        total_games = winner_score + loser_score

        with db_connection(ctx.guild.id) as (conn, cursor):
            winner_matches = get_player_matches_count(cursor, winner.id)
            loser_matches = get_player_matches_count(cursor, loser.id)

        if winner_matches < 1 or loser_matches < 1:
            low_players = []
            if winner_matches < 1:
                low_players.append(f"**{winner_name}** (matches: {winner_matches})")
            if loser_matches < 1:
                low_players.append(f"**{loser_name}** (matches: {loser_matches})")
            await ctx.send(embed=discord.Embed(
                title="Revert Error",
                description=(
                    "Cannot revert: at least one player has no recorded matches to undo.\n"
                    + "\n".join(low_players)
                ),
                color=discord.Color.red(),
            ))
            return

        # --- Recover pre-match Elos ---
        pre_winner_elo, pre_loser_elo = self._recover_pre_match_elos(
            cur_winner_elo, cur_loser_elo,
            winner_score, loser_score,
        )

        winner_delta = cur_winner_elo - pre_winner_elo
        loser_delta = cur_loser_elo - pre_loser_elo

        # Warn if recovered Elos don't reproduce the current ones exactly
        verify = self._calculate_new_formula(
            pre_winner_elo, pre_loser_elo,
            winner_score, loser_score,
        )
        has_mismatch = verify["new_winner_elo"] != cur_winner_elo or verify["new_loser_elo"] != cur_loser_elo
        mismatch_warning = ""
        if has_mismatch:
            mismatch_warning = (
                f"\n\n⚠️ **Warning:** Current Elo ratings do not exactly match the expected "
                f"outcome of this score (expected {verify['new_winner_elo']}/{verify['new_loser_elo']}, "
                f"actual {cur_winner_elo}/{cur_loser_elo}). "
                "The Elo may have been manually adjusted, or this may not be the most recent match. "
                "Proceeding will revert based on the recovered values above."
            )

        # --- Send pending embed ---
        # Bug #3 fix: if there is a mismatch (iteration didn't converge), use a
        # red/danger color so the admin clearly sees this is an inexact revert.
        embed_color = discord.Color.red() if has_mismatch else discord.Color.orange()
        embed = discord.Embed(
            title="Match Revert Pending Confirmation",
            description=(
                f"Reverting match: **{winner_name}** {winner_score}-{loser_score} **{loser_name}**\n"
                f"**Elo Ratings Preview:**\n"
                f"{winner_name}: **{pre_winner_elo}** ({cur_winner_elo} {'+'  if -winner_delta >= 0 else '-'} {abs(-winner_delta)})\n"
                f"{loser_name}: **{pre_loser_elo}** ({cur_loser_elo} {'+' if -loser_delta >= 0 else '-'} {abs(-loser_delta)})\n"
                f"{mismatch_warning}\n"
                f"Admin ({ctx.author.mention}), please react with {YES_EMOJI} to confirm or {NO_EMOJI} to cancel within 5 minutes."
            ),
            color=embed_color,
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
                    title="Match Revert Canceled",
                    description=(
                        "The match revert has been canceled.\n"
                        "No Elo changes were applied.\n"
                        f"**{winner_name}**: {cur_winner_elo}\n"
                        f"**{loser_name}**: {cur_loser_elo}"
                    ),
                    color=discord.Color.red(),
                ))
                return
        except asyncio.TimeoutError:
            await self._remove_bot_reactions(message)
            await message.edit(embed=discord.Embed(
                title="Match Revert Timeout",
                description=(
                    "Match revert canceled due to timeout. Please try again.\n"
                    "No Elo changes were applied.\n"
                    f"**{winner_name}**: {cur_winner_elo}\n"
                    f"**{loser_name}**: {cur_loser_elo}"
                ),
                color=discord.Color.red(),
            ))
            return

        # --- Commit revert; close connection before role sync ---
        with db_connection(ctx.guild.id) as (conn, cursor):
            winner_matches = get_player_matches_count(cursor, winner.id)
            loser_matches = get_player_matches_count(cursor, loser.id)
            if winner_matches < 1 or loser_matches < 1:
                await ctx.send(embed=discord.Embed(
                    title="Revert Error",
                    description=(
                        "Revert canceled: a player's matches count would drop below 0."
                    ),
                    color=discord.Color.red(),
                ))
                return

            match_id = find_latest_match_id_for_revert(
                cursor,
                winner.id,
                loser.id,
                winner_name,
                loser_name,
                winner_score,
                loser_score,
            )

            cursor.execute(
                """
                UPDATE players
                SET elo=?,
                    games_won=MAX(0, games_won - ?),
                    games_lost=MAX(0, games_lost - ?),
                    games_played_this_month=MAX(0, games_played_this_month - ?)
                WHERE id=?
                """,
                (pre_winner_elo, winner_score, loser_score, total_games, winner.id),
            )
            cursor.execute(
                """
                UPDATE players
                SET elo=?,
                    games_won=MAX(0, games_won - ?),
                    games_lost=MAX(0, games_lost - ?),
                    games_played_this_month=MAX(0, games_played_this_month - ?)
                WHERE id=?
                """,
                (pre_loser_elo, loser_score, winner_score, total_games, loser.id),
            )
            # Reverse h2h_stats additions from the original report
            _subtract_h2h_match(cursor, winner.id, loser_name, winner_score, loser_score)
            _subtract_h2h_match(cursor, loser.id, winner_name, loser_score, winner_score)
            if match_id is not None:
                delete_match_history_by_id(cursor, match_id)
            decrement_player_matches(cursor, winner.id)
            decrement_player_matches(cursor, loser.id)
            conn.commit()
        # Connection closed – sync_league_roles can open DB freely

        lb_cache_invalidate(ctx.guild.id)
        lb_cache_rebuild(self.bot, ctx.guild.id)

        await sync_league_roles(ctx.guild, player_ids=[winner.id, loser.id])

        await self._remove_bot_reactions(message)
        await message.edit(embed=discord.Embed(
            title="Match Reverted",
            description=(
                f"Match reverted: **{winner_name}** {winner_score}-{loser_score} **{loser_name}**\n"
                f"Elo Ratings:\n"
                f"{winner_name}: **{pre_winner_elo}** ({cur_winner_elo} {'+' if -winner_delta >= 0 else '-'} {abs(-winner_delta)})\n"
                f"{loser_name}: **{pre_loser_elo}** ({cur_loser_elo} {'+' if -loser_delta >= 0 else '-'} {abs(-loser_delta)})"
            ),
            color=discord.Color.green(),
        ))

    # ------------------------------------------------------------------
    # Arg parsing helper
    # ------------------------------------------------------------------

    async def _run_revert_command(
        self,
        ctx: commands.Context,
        player1_id: int,
        args: tuple[str, ...],
    ):
        parsed = self._parse_revert_args(player1_id, args)
        if not parsed:
            await ctx.send(embed=discord.Embed(
                title="Revert Error",
                description=f"Invalid format or score.\n{self._REVERT_USAGE}\nScore examples: `4-3`, `3:1`, `43`, `4 2`.",
                color=discord.Color.red(),
            ))
            return
        winner_id, winner_score, loser_score, loser_id = parsed
        await self._revert_impl(
            ctx, winner_id, winner_score, loser_score, loser_id,
        )

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    @commands.command(
        name="revert",
        help="Revert a previously confirmed match report (admin only). Usage: -revert <winner_id> <score1> <score2> <loser_id>",
    )
    @commands.check(is_admin)
    async def revert(self, ctx: commands.Context, player1_id: int, *args: str):
        await self._run_revert_command(ctx, player1_id, args)

    @revert.error
    async def revert_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.CheckFailure):
            await ctx.send(embed=discord.Embed(
                title="Permission Denied",
                description="You do not have permission to use this command.",
                color=discord.Color.red(),
            ))
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(embed=discord.Embed(
                title="Revert Error",
                description=f"Missing arguments.\n{self._REVERT_USAGE}",
                color=discord.Color.red(),
            ))

    async def cog_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        """Slash-command counterpart of revert_error.

        Without this, an app_commands.CheckFailure from is_admin_interaction
        (used on /revert) is never caught, so the interaction is left
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

    @app_commands.command(name="revert", description="Revert a previously confirmed match (admin only).")
    @app_commands.describe(
        winner_id="Winner's Discord ID",
        winner_score="Winner's set score (3 or 4)",
        loser_score="Loser's set score",
        loser_id="Loser's Discord ID",
    )
    @app_commands.guild_only()
    @app_commands.check(is_admin_interaction)
    async def slash_revert(
        self,
        interaction: discord.Interaction,
        winner_id: str,
        winner_score: app_commands.Range[int, 0, 4],
        loser_score: app_commands.Range[int, 0, 3],
        loser_id: str,
    ):
        try:
            w_id, l_id = int(winner_id), int(loser_id)
        except ValueError:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="Revert Error",
                    description="Invalid Discord ID format.",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
            return
        ctx = SlashContext(interaction)
        await self._revert_impl(ctx, w_id, winner_score, loser_score, l_id)


# ------------------------------------------------------------------
# DB helper – subtract from h2h_stats without going below 0
# ------------------------------------------------------------------

def _subtract_h2h_match(cursor, player_id, opponent_name: str, wins: int, losses: int) -> None:
    cursor.execute(
        """
        UPDATE h2h_stats
        SET wins   = MAX(0, wins   - ?),
            losses = MAX(0, losses - ?)
        WHERE player_id=? AND opponent_name=?
        """,
        (wins, losses, str(player_id), opponent_name),
    )


async def setup(bot: commands.Bot):
    await bot.add_cog(RevertCog(bot))