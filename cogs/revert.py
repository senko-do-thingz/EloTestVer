"""`-revert <match_id>` / `/revert match_id:` (admin only).

Reverts a previously recorded match by its Match ID instead of re-typing
the original report's players/score/order. Match IDs are random and
strictly unique per match (see db.match_history_db.next_match_id) and can
be looked up with `-match_ids`, which exports every recorded match in this
server to an Excel file.

Unlike the old score-based revert (which had to *guess* the pre-match Elo
by iterating the report formula backwards), this reads the exact
elo_init/elo_final that were stored for this match at report time, so the
revert is always exact - and it deletes that match's two match_history
rows so it no longer shows up in -history/-match_ids.
"""

import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands

from db.elo_db import db_connection
from db.match_history_db import (
    MatchHistoryRow,
    decrement_player_matches,
    delete_match_history_by_id,
    get_match_history_rows_by_match_id,
    get_player_matches_count,
)
from utils import is_admin, is_admin_interaction, sync_league_roles
from utils.lb_cache import invalidate as lb_cache_invalidate, schedule_rebuild as lb_cache_rebuild
from config import YES_EMOJI, NO_EMOJI
from utils.slash_ctx import SlashContext

logger = logging.getLogger(__name__)


class RevertCog(commands.Cog):
    _REVERT_USAGE = (
        "- `-revert <match_id>`\n"
        "Use `-match_ids` to look up the Match ID of the match you want to undo."
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

    @staticmethod
    def _split_winner_loser(
        rows: list[MatchHistoryRow],
    ) -> tuple[MatchHistoryRow, MatchHistoryRow] | None:
        """Given the 2 match_history rows for one match_id, return
        (winner_row, loser_row), or None if the data is malformed (e.g. a
        corrupted/partial match_id with anything other than exactly 2
        rows, or neither row shows a win)."""
        if len(rows) != 2:
            return None
        row_a, row_b = rows
        if row_a.wins > row_a.losses and row_b.losses > row_b.wins:
            return row_a, row_b
        if row_b.wins > row_b.losses and row_a.losses > row_a.wins:
            return row_b, row_a
        return None

    # ------------------------------------------------------------------
    # Core revert implementation
    # ------------------------------------------------------------------

    async def _revert_impl(self, ctx: commands.Context, match_id: int):
        with db_connection(ctx.guild.id) as (_conn, cursor):
            rows = get_match_history_rows_by_match_id(cursor, match_id)

        if not rows:
            await ctx.send(embed=discord.Embed(
                title="Revert Error",
                description=(
                    f"No match found with Match ID `{match_id}`.\n"
                    f"Use `-match_ids` to look up valid Match IDs.\n{self._REVERT_USAGE}"
                ),
                color=discord.Color.red(),
            ))
            return

        split = self._split_winner_loser(rows)
        if split is None:
            await ctx.send(embed=discord.Embed(
                title="Revert Error",
                description=(
                    f"Match ID `{match_id}` has malformed or incomplete data and "
                    "cannot be safely reverted automatically."
                ),
                color=discord.Color.red(),
            ))
            return
        winner_row, loser_row = split

        with db_connection(ctx.guild.id) as (_conn, cursor):
            cursor.execute(
                "SELECT elo, name FROM players WHERE id=?", (winner_row.player_id,)
            )
            winner_player = cursor.fetchone()
            cursor.execute(
                "SELECT elo, name FROM players WHERE id=?", (loser_row.player_id,)
            )
            loser_player = cursor.fetchone()

        if not winner_player or not loser_player:
            missing = []
            if not winner_player:
                missing.append(f"<@{winner_row.player_id}>")
            if not loser_player:
                missing.append(f"<@{loser_row.player_id}>")
            await ctx.send(embed=discord.Embed(
                title="Revert Error",
                description=(
                    "Cannot revert: " + ", ".join(missing) +
                    " is no longer registered."
                ),
                color=discord.Color.red(),
            ))
            return

        cur_winner_elo, winner_name = winner_player
        cur_loser_elo, loser_name = loser_player

        with db_connection(ctx.guild.id) as (_conn, cursor):
            winner_matches = get_player_matches_count(cursor, winner_row.player_id)
            loser_matches = get_player_matches_count(cursor, loser_row.player_id)

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

        target_winner_elo = winner_row.elo_init
        target_loser_elo = loser_row.elo_init
        winner_delta = target_winner_elo - cur_winner_elo
        loser_delta = target_loser_elo - cur_loser_elo

        embed = discord.Embed(
            title="Match Revert Pending Confirmation",
            description=(
                f"Reverting Match ID `{match_id}`: **{winner_name}** "
                f"{winner_row.wins}-{winner_row.losses} **{loser_name}**\n"
                f"**Elo Ratings Preview:**\n"
                f"{winner_name}: **{target_winner_elo}** ({cur_winner_elo} "
                f"{'+' if winner_delta >= 0 else '-'} {abs(winner_delta)})\n"
                f"{loser_name}: **{target_loser_elo}** ({cur_loser_elo} "
                f"{'+' if loser_delta >= 0 else '-'} {abs(loser_delta)})\n\n"
                f"This match will also be deleted from match history.\n"
                f"Admin ({ctx.author.mention}), please react with {YES_EMOJI} to confirm or "
                f"{NO_EMOJI} to cancel within 5 minutes."
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
            winner_matches = get_player_matches_count(cursor, winner_row.player_id)
            loser_matches = get_player_matches_count(cursor, loser_row.player_id)
            if winner_matches < 1 or loser_matches < 1:
                await ctx.send(embed=discord.Embed(
                    title="Revert Error",
                    description=(
                        "Revert canceled: a player's matches count would drop below 0."
                    ),
                    color=discord.Color.red(),
                ))
                return

            cursor.execute(
                """
                UPDATE players
                SET elo=?,
                    games_won=MAX(0, games_won - ?),
                    games_lost=MAX(0, games_lost - ?),
                    games_played_this_month=MAX(0, games_played_this_month - ?)
                WHERE id=?
                """,
                (
                    target_winner_elo, winner_row.wins, winner_row.losses,
                    winner_row.wins + winner_row.losses, winner_row.player_id,
                ),
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
                (
                    target_loser_elo, loser_row.wins, loser_row.losses,
                    loser_row.wins + loser_row.losses, loser_row.player_id,
                ),
            )
            # Reverse h2h_stats additions from the original report - mirrors
            # add_h2h_match(cursor, row.player_id, row.opponent_name, row.wins, row.losses)
            # done at report time, for each row.
            _subtract_h2h_match(cursor, winner_row.player_id, winner_row.opponent_name, winner_row.wins, winner_row.losses)
            _subtract_h2h_match(cursor, loser_row.player_id, loser_row.opponent_name, loser_row.wins, loser_row.losses)

            delete_match_history_by_id(cursor, match_id)
            decrement_player_matches(cursor, winner_row.player_id)
            decrement_player_matches(cursor, loser_row.player_id)
            conn.commit()
        # Connection closed - sync_league_roles can open DB freely

        lb_cache_invalidate(ctx.guild.id)
        lb_cache_rebuild(self.bot, ctx.guild.id)

        await sync_league_roles(ctx.guild, player_ids=[int(winner_row.player_id), int(loser_row.player_id)])

        await self._remove_bot_reactions(message)
        await message.edit(embed=discord.Embed(
            title="Match Reverted",
            description=(
                f"Match ID `{match_id}` reverted: **{winner_name}** "
                f"{winner_row.wins}-{winner_row.losses} **{loser_name}**\n"
                f"Elo Ratings:\n"
                f"{winner_name}: **{target_winner_elo}** ({cur_winner_elo} "
                f"{'+' if winner_delta >= 0 else '-'} {abs(winner_delta)})\n"
                f"{loser_name}: **{target_loser_elo}** ({cur_loser_elo} "
                f"{'+' if loser_delta >= 0 else '-'} {abs(loser_delta)})\n\n"
                f"This match has been removed from match history."
            ),
            color=discord.Color.green(),
        ))

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    @commands.command(
        name="revert",
        help="Revert a previously recorded match by its Match ID (admin only). Usage: -revert <match_id>. Look up IDs with -match_ids.",
    )
    @commands.check(is_admin)
    async def revert(self, ctx: commands.Context, match_id: int):
        await self._revert_impl(ctx, match_id)

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
        elif isinstance(error, commands.BadArgument):
            await ctx.send(embed=discord.Embed(
                title="Revert Error",
                description=f"Invalid Match ID.\n{self._REVERT_USAGE}",
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

    @app_commands.command(name="revert", description="Revert a previously recorded match by its Match ID (admin only).")
    @app_commands.describe(
        match_id="The Match ID to revert (look it up with -match_ids)",
    )
    @app_commands.guild_only()
    @app_commands.check(is_admin_interaction)
    async def slash_revert(self, interaction: discord.Interaction, match_id: int):
        ctx = SlashContext(interaction)
        await self._revert_impl(ctx, match_id)


# ------------------------------------------------------------------
# DB helper - subtract from h2h_stats without going below 0
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