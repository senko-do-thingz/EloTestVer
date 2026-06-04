import io
import os
import re
import zipfile
import discord
from discord.ext import commands

from db.elo_db import db_connection
from utils import (
    is_admin,
    calculate_win_rate,
    format_leaderboard_export_line,
    parse_leaderboard_line,
    sync_discord_ids_to_lb_archives,
    get_lb_root,
)
from config import YES_EMOJI, NO_EMOJI


class AdminImportCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_command_error(self, ctx: commands.Context, error: Exception):
        if isinstance(error, commands.CheckFailure):
            await ctx.send(
                embed=discord.Embed(
                    title="Permission Denied",
                    description="You do not have permission to use this admin command.",
                    color=discord.Color.red(),
                )
            )
            return
        raise error

    @commands.command(
        help="Export all leaderboard stats to a zip file containing separate text files for each stat."
    )
    @commands.check(is_admin)
    async def get_lb(self, ctx: commands.Context):
        with db_connection(ctx.guild.id) as (conn, cursor):
            cursor.execute(
                "SELECT id, name, elo, games_won, games_lost, games_played_this_month FROM players"
            )
            all_players = cursor.fetchall()

        if not all_players:
            await ctx.send("No players have registered yet.")
            return

        # Create a temporary directory for files
        temp_dir = f"temp_lb_{ctx.guild.id}"
        os.makedirs(temp_dir, exist_ok=True)

        try:
            # 1. Elo Leaderboard
            elo_players = sorted(all_players, key=lambda x: x[2], reverse=True)
            elo_lines = []
            for discord_id, name, elo, _, _, _ in elo_players:
                elo_lines.append(format_leaderboard_export_line(name, elo, discord_id))
            elo_text = "\n".join(elo_lines)
            with open(f"{temp_dir}/elo.txt", "w", encoding="utf-8") as f:
                f.write(elo_text)

            # 2. Wins Leaderboard
            wins_players = sorted(all_players, key=lambda x: x[3], reverse=True)
            wins_lines = []
            for discord_id, name, _, wins, _, _ in wins_players:
                wins_lines.append(format_leaderboard_export_line(name, wins, discord_id))
            wins_text = "\n".join(wins_lines)
            with open(f"{temp_dir}/wins.txt", "w", encoding="utf-8") as f:
                f.write(wins_text)

            # 3. Losses Leaderboard
            losses_players = sorted(all_players, key=lambda x: x[4], reverse=True)
            losses_lines = []
            for discord_id, name, _, _, losses, _ in losses_players:
                losses_lines.append(
                    format_leaderboard_export_line(name, losses, discord_id)
                )
            losses_text = "\n".join(losses_lines)
            with open(f"{temp_dir}/losses.txt", "w", encoding="utf-8") as f:
                f.write(losses_text)

            # 4. Win Rate Leaderboard
            win_rate_players = []
            for discord_id, name, _, wins, losses, _ in all_players:
                win_rate = calculate_win_rate(wins, losses)
                win_rate_players.append((name, win_rate, wins, losses, discord_id))
            win_rate_players.sort(key=lambda x: x[1], reverse=True)
            win_rate_lines = []
            for name, win_rate, wins, losses, discord_id in win_rate_players:
                win_rate_lines.append(
                    format_leaderboard_export_line(
                        name, f"{win_rate:.2f}", discord_id
                    )
                )
            win_rate_text = "\n".join(win_rate_lines)
            with open(f"{temp_dir}/win_rate.txt", "w", encoding="utf-8") as f:
                f.write(win_rate_text)

            # 5. Monthly Games Played Leaderboard
            monthly_players = sorted(all_players, key=lambda x: x[5], reverse=True)
            monthly_lines = []
            for discord_id, name, _, _, _, games in monthly_players:
                monthly_lines.append(
                    format_leaderboard_export_line(name, games, discord_id)
                )
            monthly_text = "\n".join(monthly_lines)
            with open(
                f"{temp_dir}/games_played_this_month.txt", "w", encoding="utf-8"
            ) as f:
                f.write(monthly_text)

            # Create zip file
            zip_path = f"leaderboard_{ctx.guild.id}.zip"
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
                zipf.write(f"{temp_dir}/elo.txt", "data.txt")
                zipf.write(f"{temp_dir}/wins.txt", "w.txt")
                zipf.write(f"{temp_dir}/losses.txt", "l.txt")
                zipf.write(f"{temp_dir}/win_rate.txt", "wr.txt")
                zipf.write(f"{temp_dir}/games_played_this_month.txt", "games.txt")

            await ctx.send(file=discord.File(zip_path))
            os.remove(zip_path)

        finally:
            # Clean up temporary directory
            for file in os.listdir(temp_dir):
                os.remove(os.path.join(temp_dir, file))
            os.rmdir(temp_dir)

    @commands.command(
        help="Sync all registered players' Discord IDs into archived lb/<server_id>/ files."
    )
    @commands.check(is_admin)
    async def set_id(self, ctx: commands.Context, server_id: str):
        try:
            guild_id = int(server_id)
        except ValueError:
            await ctx.send(
                embed=discord.Embed(
                    title="Invalid Server ID",
                    description="Please provide a valid numeric server (guild) ID.",
                    color=discord.Color.red(),
                )
            )
            return

        lb_root = get_lb_root(guild_id)
        if not os.path.isdir(lb_root):
            await ctx.send(
                embed=discord.Embed(
                    title="Error",
                    description=(
                        f"Leaderboard directory does not exist: `lb/{guild_id}/`"
                    ),
                    color=discord.Color.red(),
                )
            )
            return

        with db_connection(guild_id) as (conn, cursor):
            cursor.execute("SELECT id, name FROM players")
            rows = cursor.fetchall()

        if not rows:
            await ctx.send(
                embed=discord.Embed(
                    title="No Players",
                    description="No registered players in the database.",
                    color=discord.Color.red(),
                )
            )
            return

        players_by_name = {name.lower(): str(discord_id) for discord_id, name in rows}
        files_updated, lines_updated, players_updated = sync_discord_ids_to_lb_archives(
            lb_root, players_by_name
        )

        if lines_updated == 0:
            description = (
                f"No matching entries found in `lb/` for the **{len(rows)}** registered "
                "player(s), or all IDs are already up to date."
            )
            color = discord.Color.orange()
        else:
            description = (
                f"Updated **{lines_updated}** line(s) across **{files_updated}** file(s) "
                f"for **{players_updated}** player(s) "
                f"(**{len(rows)}** registered in database)."
            )
            color = discord.Color.green()

        await ctx.send(
            embed=discord.Embed(
                title="Discord IDs Updated",
                description=description,
                color=color,
            )
        )

    @commands.command(
        help="Calculate and export the total leaderboard for a specific season from a given server's lb/ folder."
    )
    @commands.check(is_admin)
    async def sum_lb(self, ctx: commands.Context, server_id: int, season: str):
        season_match = re.search(r"\d+", season)
        if not season_match:
            await ctx.send(
                embed=discord.Embed(
                    title="Invalid Season Format",
                    description=(
                        "Please provide a season number (e.g., 'S2', 's2', 'Season 2', '2')"
                    ),
                    color=discord.Color.red(),
                )
            )
            return
        season_num = int(season_match.group())
        base = get_lb_root(server_id)
        if not os.path.isdir(base):
            await ctx.send(
                embed=discord.Embed(
                    title="Error",
                    description=(
                        f"No leaderboard archive found for server **{server_id}** "
                        f"(`lb/{server_id}/`)."
                    ),
                    color=discord.Color.red(),
                )
            )
            return

        # Find season directory (case-insensitive)
        seasons = [d for d in os.listdir(base) if os.path.isdir(os.path.join(base, d))]
        season_name = None
        for s in seasons:
            if re.match(r"^season\s+" + str(season_num) + r"$", s, re.IGNORECASE):
                season_name = s
                break

        if not season_name:
            await ctx.send(
                embed=discord.Embed(
                    title="Season Not Found",
                    description=f"Season {season_num} does not exist in server **{server_id}**.",
                    color=discord.Color.red(),
                )
            )
            return

        season_path = os.path.join(base, season_name)
        months = [
            d
            for d in os.listdir(season_path)
            if os.path.isdir(os.path.join(season_path, d))
        ]
        if not months:
            await ctx.send(
                embed=discord.Embed(
                    title="No Data Found",
                    description=f"No month data found in {season_name}.",
                    color=discord.Color.red(),
                )
            )
            return

        # Initialize dictionaries to store totals for each metric
        player_elo = {}
        player_wins = {}
        player_losses = {}

        for month in months:
            month_path = os.path.join(season_path, month)

            # Read Elo (data.txt)
            data_file = os.path.join(month_path, "data.txt")
            if os.path.exists(data_file):
                try:
                    with open(data_file, "r", encoding="utf-8") as f:
                        lines = f.readlines()
                    for line in lines:
                        stripped = line.strip()
                        if not stripped or stripped.lower().endswith("leaderboard"):
                            continue
                        parsed = parse_leaderboard_line(stripped)
                        if parsed:
                            name, elo, _ = parsed
                            player_elo[name] = player_elo.get(name, 0) + elo
                except Exception:
                    continue

            # Read Wins (w.txt)
            wins_file = os.path.join(month_path, "w.txt")
            if os.path.exists(wins_file):
                try:
                    with open(wins_file, "r", encoding="utf-8") as f:
                        lines = f.readlines()
                    for line in lines:
                        stripped = line.strip()
                        if not stripped or stripped.lower().endswith("leaderboard"):
                            continue
                        parsed = parse_leaderboard_line(stripped)
                        if parsed:
                            name, wins, _ = parsed
                            player_wins[name] = player_wins.get(name, 0) + wins
                except Exception:
                    continue

            # Read Losses (l.txt)
            losses_file = os.path.join(month_path, "l.txt")
            if os.path.exists(losses_file):
                try:
                    with open(losses_file, "r", encoding="utf-8") as f:
                        lines = f.readlines()
                    for line in lines:
                        stripped = line.strip()
                        if not stripped or stripped.lower().endswith("leaderboard"):
                            continue
                        parsed = parse_leaderboard_line(stripped)
                        if parsed:
                            name, losses, _ = parsed
                            player_losses[name] = (
                                player_losses.get(name, 0) + losses
                            )
                except Exception:
                    continue

        if not player_elo and not player_wins and not player_losses:
            await ctx.send(
                embed=discord.Embed(
                    title="No Data Found",
                    description=f"No valid data found in {season_name}.",
                    color=discord.Color.red(),
                )
            )
            return

        # Build zip in memory
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            if player_elo:
                elo_lines = [
                    f"{name} {elo}"
                    for name, elo in sorted(player_elo.items(), key=lambda x: x[1], reverse=True)
                ]
                zf.writestr("data.txt", "Elo Leaderboard\n\n" + "\n".join(elo_lines))

            if player_wins:
                wins_lines = [
                    f"{name} {wins}"
                    for name, wins in sorted(player_wins.items(), key=lambda x: x[1], reverse=True)
                ]
                zf.writestr("w.txt", "Wins Leaderboard\n\n" + "\n".join(wins_lines))

            if player_losses:
                losses_lines = [
                    f"{name} {losses}"
                    for name, losses in sorted(player_losses.items(), key=lambda x: x[1], reverse=True)
                ]
                zf.writestr("l.txt", "Losses Leaderboard\n\n" + "\n".join(losses_lines))

            all_player_names = set(player_wins.keys()) | set(player_losses.keys())
            if all_player_names:
                wr_players = sorted(
                    [
                        (name, calculate_win_rate(player_wins.get(name, 0), player_losses.get(name, 0)))
                        for name in all_player_names
                    ],
                    key=lambda x: x[1],
                    reverse=True,
                )
                zf.writestr("wr.txt", "Win Rate Leaderboard\n\n" + "\n".join(f"{name} {wr:.2f}" for name, wr in wr_players))

        buf.seek(0)
        zip_name = f"sum_lb_{season_name.replace(' ', '_')}_{server_id}.zip"
        await ctx.send(file=discord.File(buf, filename=zip_name))


async def setup(bot: commands.Bot):
    await bot.add_cog(AdminImportCog(bot))