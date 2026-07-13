from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from db.elo_db import get_season_month
from utils.autocomplete import command_name_autocomplete
from utils.command_registry import (
    CommandHelp,
    admin_command_names,
    lookup_command_help,
    public_command_names,
)
from utils.guards import is_admin
from utils.logger import PRIVATE_GUILD_ID


async def help_command_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    # Admin-only commands are intentionally excluded from /help suggestions.
    # They live under /adhelp instead, so a regular member browsing /help
    # never sees admin-only command names (revert, set_e, set_stats, etc.)
    # as autocomplete options.
    names = public_command_names()
    return await command_name_autocomplete(interaction, current, names=names)


async def adhelp_command_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    names = admin_command_names()
    return await command_name_autocomplete(interaction, current, names=names)


def _general_commands_list(guild_id: int) -> list[tuple[str, str]]:
    commands_list = [
        ("-info / /info", "Show information about the bot."),
        ("-register <name> / /register", "Register with a custom name and initial 500 Elo."),
        ("-rename <new_name> / /rename", "Change your name (7-day cooldown)."),
        (
            "-report / -rp / /report",
            "Report a match result.",
        ),
        (
            "-history / /history [name] [season] [month]",
            "Show recent match history as a paginated image (10/page, all matches kept). -history always shows your own current season/month; /history can view another player and filter by season/month.",
        ),
    ]
    commands_list.extend(
        [
            (
                "-leaderboard / lb / /leaderboard",
                "Show the leaderboard of players based on various stats.",
            ),
            ("-lbs / /lbs", "Show the leaderboards for each season."),
            (
                "-stats / /stats [player]",
                "Show your stats / Show another player's stats.",
            ),
            (
                "/stats expanded",
                "Show your averaged/summed stats across one or more season/month combinations.",
            ),
            (
                "/export history",
                "Export your full match history to an Excel (.xlsx) file.",
            ),
            (
                "-compare / -cp / /compare",
                "Compare two players.",
            ),
            (
                "-leagues / /leagues [name]",
                "Show discovered leagues, Elo ranges, and player counts.",
            ),
            (
                "-help [command] / /help [command]",
                "Show this list or detailed help for one command.",
            ),
            ("-adhelp / /adhelp", "Show the admin help message."),
            (
                "/msg <content>",
                "DM the bot to send a private message to the support team.",
            ),
        ]
    )
    if guild_id == PRIVATE_GUILD_ID:
        commands_list.extend(
            [
                ("-lb_id <server_id>", "View another server's leaderboard."),
                ("-gld <server_id>", "Export another server's full leaderboard."),
                ("-st <server_id> <player_id>", "View a player's stats in another server."),
                ("-log [<channel> | off]", "Set or clear the bot log channel."),
                ("-hpbd <@user> <server_id>", "Send a birthday message in another server."),
                ("-status", "Show internal bot/process status."),
                (
                    "-support [<channel> | off]",
                    "Set or clear the channel /msg relays DM messages into.",
                ),
            ]
        )
    return commands_list


def build_overview_embed(guild_id: int, *, title_suffix: str = "") -> discord.Embed:
    title = "📜 Help - General Commands"
    if guild_id == PRIVATE_GUILD_ID:
        title += " (+ Private Commands)"
    if title_suffix:
        title += title_suffix
    embed = discord.Embed(
        title=title,
        description=(
            "Available commands (prefix `-` or slash `/`):\n"
            "Use `-help <command>` or `/help command:<name>` for details."
        ),
        color=discord.Color.blue(),
    )
    for name, desc in _general_commands_list(guild_id):
        embed.add_field(name=name, value=desc, inline=False)
    return embed


_LEAGUE_RELATED_COMMANDS = (
    ("-ladmin", "Admin view: show all leagues (discovered and undiscovered)."),
    ("-ladd <number>", "Create league ranges and sync roles (admin only)."),
    ("-ledit", "Edit league boundaries (admin only)."),
    ("-lapply", "Re-apply league roles for all registered players (admin only)."),
    ("-lclear", "Remove all league roles and clear all leagues (admin only)."),
    ("-lcancel", "Cancel any running league task in this channel (admin only)."),
    ("-lannounce <channel>", "Add a channel for league promotion/demotion announcements (admin only)."),
    ("-lrannounce <channel>", "Remove a channel from league announcements (admin only)."),
    ("-lhelp", "Show detailed help for all league-related commands."),
)


def build_command_detail_embed(entry: CommandHelp) -> discord.Embed:
    alias_text = ", ".join(f"`{a}`" for a in entry.aliases) if entry.aliases else "None"
    examples = "\n".join(f"- `{ex}`" for ex in entry.examples)
    embed = discord.Embed(
        title=f"Help - {entry.name}",
        description=entry.description,
        color=discord.Color.green(),
    )
    embed.add_field(name="Prefix syntax", value=f"`{entry.syntax}`", inline=False)
    if entry.is_slash:
        embed.add_field(name="Slash syntax", value=f"`{entry.slash_syntax}`", inline=False)
    embed.add_field(name="Permission", value=entry.permission, inline=True)
    embed.add_field(name="Aliases", value=alias_text, inline=True)
    embed.add_field(name="Examples", value=examples or "-", inline=False)

    if entry.name == "leagues":
        related = "\n".join(f"- `{cmd}` - {desc}" for cmd, desc in _LEAGUE_RELATED_COMMANDS)
        embed.add_field(name="Related league commands", value=related, inline=False)

    return embed


class HelpCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_command_error(self, ctx: commands.Context, error: Exception) -> None:
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

    async def cog_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        """Slash-command counterpart of cog_command_error.

        Without this, an app_commands.CheckFailure (e.g. is_admin rejecting
        a non-admin user on /adhelp) is never caught, so the interaction is
        left unanswered instead of showing a proper permission-denied message.
        """
        if isinstance(error, app_commands.CheckFailure):
            embed = discord.Embed(
                title="Permission Denied",
                description="You do not have permission to use this admin command.",
                color=discord.Color.red(),
            )
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        raise error

    async def _help_impl(self, ctx: commands.Context, command: str | None = None) -> None:
        if command:
            entry = lookup_command_help(command)
            if entry is None:
                await ctx.send(
                    embed=discord.Embed(
                        title="Command Not Found",
                        description=(
                            f"No help entry for `{command}`.\n"
                            "Use `-help` without arguments to see all commands."
                        ),
                        color=discord.Color.red(),
                    )
                )
                return
            await ctx.send(embed=build_command_detail_embed(entry))
            return

        await ctx.send(embed=build_overview_embed(ctx.guild.id))

    @commands.hybrid_command(
        name="help",
        description="Show available commands or detailed help for one command.",
        with_app_command=True,
    )
    @app_commands.describe(command="Command name for detailed help (optional)")
    @app_commands.autocomplete(command=help_command_autocomplete)
    @app_commands.guild_only()
    async def help_command(
        self, ctx: commands.Context, command: str | None = None
    ):
        await self._help_impl(ctx, command)

    @commands.hybrid_command(
        name="info",
        description="Show information about the bot.",
        help="Show information about the bot.",
        with_app_command=True,
    )
    async def info(self, ctx: commands.Context):
        current_time = datetime.now(timezone.utc).strftime("%d %b %Y, %H:%M:%S UTC")

        prefix_command_names = {cmd.qualified_name for cmd in self.bot.commands}
        tree_command_names = {cmd.qualified_name for cmd in self.bot.tree.get_commands()}
        command_count = len(prefix_command_names | tree_command_names)

        embed = discord.Embed(
            title="EloBot Information",
            description="> A Discord bot built for managing Elo-based PvZH competitions.",
            color=discord.Color.blue(),
        )
        if self.bot.user:
            embed.set_thumbnail(url=self.bot.user.display_avatar.url)

        embed.add_field(name="Developer", value="Senko", inline=True)
        embed.add_field(name="Created", value="8 Dec 2024", inline=True)
        embed.add_field(name="Programming Language", value="Python 3.13", inline=True)
        if ctx.guild is not None:
            season, month = get_season_month(ctx.guild.id)
            season_text = f"Season: {season}, Month: {month}"
        else:
            season_text = "Not available outside a server"
        embed.add_field(name="Season", value=season_text, inline=True)
        embed.add_field(name="Bot Time (UTC)", value=current_time, inline=True)
        embed.add_field(name="Commands", value=str(command_count), inline=True)
        embed.add_field(
            name="\u200b",
            value=(
                "Thank you for using EloBot! ❤️\n"
                "If you encounter any issues or have suggestions, dm me and use "
                "/msg to contact <@674610469977194521>."
            ),
            inline=False,
        )

        await ctx.send(embed=embed)

    @commands.hybrid_command(
        name="adhelp",
        help="Show admin-only commands, or detailed help for one admin command (admin only).",
        description="Show admin-only commands, or detailed help for one admin command (admin only).",
        with_app_command=True,
    )
    @app_commands.describe(command="Admin command name for detailed help (optional)")
    @app_commands.autocomplete(command=adhelp_command_autocomplete)
    @app_commands.guild_only()
    @commands.check(is_admin)
    async def adhelp(self, ctx: commands.Context, command: str | None = None):
        if command:
            entry = lookup_command_help(command)
            if entry is None:
                await ctx.send(
                    embed=discord.Embed(
                        title="Command Not Found",
                        description=(
                            f"No help entry for `{command}`.\n"
                            "Use `-adhelp` without arguments to see all admin commands."
                        ),
                        color=discord.Color.red(),
                    )
                )
                return
            await ctx.send(embed=build_command_detail_embed(entry))
            return

        stats_commands = [
            (
                "-decay",
                "Manually run Elo decay for all players (also runs automatically "
                "every calendar month - see -decay_setup).",
            ),
            (
                "-decay_setup <channel>",
                "Set or clear the channel the automatic monthly -decay change log is posted to.",
            ),
            (
                "-nickname <server_id> <name>",
                "Assign this server's nickname, used for -get_lb/-sum_lb/-decay file names.",
            ),
            ("-reset_e", "Set **Elo** for all players to 500."),
            ("-set_e <@player> <elo> / /set_e", "Set **Elo** for a specific player."),
            (
                "-set_eid <discord_id> <elo>",
                "Set **Elo** for a specific player using their Discord ID.",
            ),
            (
                "-set_w <@player> <wins> / /set_w",
                "Set the **win count** of a specific player.",
            ),
            (
                "-set_wid <discord_id> <wins> / /set_wid",
                "Set the **win count** of a specific player using their Discord ID.",
            ),
            (
                "-set_l <@player> <losses> / /set_l",
                "Set the **loss count** of a specific player.",
            ),
            (
                "-set_lid <discord_id> <losses> / /set_lid",
                "Set the **loss count** of a specific player using their Discord ID.",
            ),
            (
                "-set_gid <discord_id> <games_played>",
                "Set the **monthly games played** for a player using their Discord ID.",
            ),
            (
                "-set_stats <discord_id> <elo> <wins> <losses> <games_played> / /set_stats",
                "Set all **stats** for a player using their Discord ID.",
            ),
            (
                "-ad_rp / /ad_rp",
                "Report a game result between two players using their Discord IDs.",
            ),
            (
                "-revert / /revert",
                "Undo a previously confirmed match report. Use the same player1_id, "
                "p1_score, p2_score, player2_id (same order) as the original /report "
                "to reverse it. Use `-help revert` for details.",
            ),
            (
                "/months",
                "Set the total number of months a season can have on this server.",
            ),
            (
                "/season",
                "Update the current season and month for this server. Requires "
                "/months to be set first.",
            ),
        ]
        admin_commands = {
            "register": [
                (
                    "-register_o",
                    "Register another player with a custom name.",
                ),
                (
                    "-register_oid",
                    "Register another player using their Discord ID with a custom name.",
                ),
                (
                    "-rename_o",
                    "Rename another player (no cooldown).",
                ),
                (
                    "-rename_oid",
                    "Rename another player by Discord ID (no cooldown).",
                ),
                ("-d_reg <@player>", "Delete a player's registration."),
                (
                    "-did_reg <discord_id>",
                    "Delete a player's registration using their Discord ID.",
                ),
                ("-c_reg", "Clear all registered player data."),
                (
                    "-db_d <server_id> <id>",
                    "Delete a player's data using their ID stored in the Database.",
                ),
                ("-get_lb", "Get the leaderboard from the Database."),
                (
                    "-set_id <server_id>",
                    "Sync all registered players' Discord IDs into archived lb/<server_id>/ files.",
                ),
                (
                    "-sum_lb <season>",
                    "Calculate total leaderboard for a specific season (e.g., S2, Season 2).",
                ),
            ],
            "stats": stats_commands,
        }

        class AdminHelpView(discord.ui.View):
            def __init__(self):
                super().__init__()
                self.page = 0
                self.total_pages = 2
                self.update_buttons()

            def update_buttons(self):
                for child in self.children:
                    if isinstance(child, PreviousPageButton):
                        child.disabled = self.page == 0
                    elif isinstance(child, NextPageButton):
                        child.disabled = self.page >= self.total_pages - 1

            async def update_help(self, interaction):
                embed = self.create_embed()
                self.update_buttons()
                await interaction.response.edit_message(embed=embed, view=self)

            def create_embed(self):
                page_type = "register" if self.page == 0 else "stats"
                title = (
                    "📜 Admin Help - Registration Commands"
                    if self.page == 0
                    else "📜 Admin Help - Stats Commands"
                )
                embed = discord.Embed(
                    title=title,
                    description=(
                        "Available commands (prefix `-` or slash `/`):\n"
                        "Use `-adhelp <command>` or `/adhelp command:<name>` for details."
                    ),
                    color=discord.Color.dark_red(),
                )
                for name, desc in admin_commands[page_type]:
                    embed.add_field(name=name, value=desc, inline=False)
                return embed

        class PreviousPageButton(discord.ui.Button):
            def __init__(self, view: AdminHelpView):
                super().__init__(
                    style=discord.ButtonStyle.secondary, label="Prev", disabled=True
                )
                self.custom_view = view

            async def callback(self, interaction: discord.Interaction):
                self.view.page -= 1
                await self.view.update_help(interaction)

        class NextPageButton(discord.ui.Button):
            def __init__(self, view: AdminHelpView):
                super().__init__(style=discord.ButtonStyle.secondary, label="Next")
                self.custom_view = view

            async def callback(self, interaction: discord.Interaction):
                self.view.page += 1
                await self.view.update_help(interaction)

        view = AdminHelpView()
        view.add_item(PreviousPageButton(view))
        view.add_item(NextPageButton(view))
        await ctx.send(embed=view.create_embed(), view=view)


async def setup(bot: commands.Bot):
    await bot.add_cog(HelpCog(bot))