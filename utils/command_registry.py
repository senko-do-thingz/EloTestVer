"""Central command metadata for /help and autocomplete."""

from __future__ import annotations

from dataclasses import dataclass

from utils.guards import NAME_RULE_TEXT


@dataclass(frozen=True)
class CommandHelp:
    name: str
    aliases: tuple[str, ...]
    description: str
    syntax: str
    slash_syntax: str
    permission: str
    examples: tuple[str, ...]
    admin: bool = False
    private: bool = False
    is_slash: bool = True

    @property
    def lookup_keys(self) -> set[str]:
        keys = {self.name.lower(), f"-{self.name.lower()}", f"/{self.name.lower()}"}
        for alias in self.aliases:
            keys.add(alias.lower())
            keys.add(f"-{alias.lower()}")
            keys.add(f"/{alias.lower()}")
        return keys


def _slash_name(name: str) -> str:
    """Slash-command display form of a command name: '_' becomes ' '."""
    return name.replace("_", " ")


def _cmd(
    name: str,
    *,
    aliases: tuple[str, ...] = (),
    description: str,
    syntax: str,
    slash_syntax: str | None = None,
    permission: str = "Everyone",
    examples: tuple[str, ...],
    admin: bool = False,
    private: bool = False,
    is_slash: bool = True,
) -> CommandHelp:
    return CommandHelp(
        name=name,
        aliases=aliases,
        description=description,
        syntax=syntax,
        slash_syntax=slash_syntax or f"/{_slash_name(name)}",
        permission=permission,
        examples=examples,
        admin=admin,
        private=private,
        is_slash=is_slash,
    )


COMMAND_HELP: dict[str, CommandHelp] = {}


def _register(entry: CommandHelp) -> None:
    COMMAND_HELP[entry.name] = entry


def _register_many(*entries: CommandHelp) -> None:
    for entry in entries:
        _register(entry)


_register_many(
    _cmd(
        "help",
        description="Show available commands or detailed help for one command.",
        syntax="-help [command]",
        slash_syntax="/help [command]",
        examples=("-help", "-help report", "/help history"),
    ),
    _cmd(
        "info",
        description="Show information about the bot.",
        syntax="-info",
        slash_syntax="/info",
        examples=("-info", "/info"),
    ),
    _cmd(
        "register",
        description=f"Register with a custom name and initial 500 Elo. {NAME_RULE_TEXT}",
        syntax="-register <name>",
        slash_syntax="/register name:<name>",
        examples=("-register Senko", "/register name:Senko"),
    ),
    _cmd(
        "rename",
        description=f"Change your registered name (7-day cooldown). {NAME_RULE_TEXT}",
        syntax="-rename <new_name>",
        slash_syntax="/rename new_name:<name>",
        examples=("-rename Senko2", "/rename new_name:Senko2"),
    ),
    _cmd(
        "report",
        aliases=("rp",),
        description="Report a match result between two registered players. The player with the higher score wins.",
        syntax="-report @player1 <score1> <score2> @player2  |  -report @winner <score> @loser",
        slash_syntax="/report player1:<@user> p1_score:<0-4> p2_score:<0-4> player2:<@user>",
        examples=(
            "-report @Senko 3 1 @Autony",
            "-report @Senko 31 @Autony",
            "/report player1:@Senko p1_score:3 p2_score:1 player2:@Autony",
        ),
    ),
    _cmd(
        "history",
        description=(
            "Show your recent match history in a paginated image (10 matches per "
            "page, all matches kept). The prefix form (`-history`) always shows "
            "your own history for this server's current season/month. The slash "
            "form additionally lets you view another player's history and filter "
            "by season and/or month."
        ),
        syntax="-history",
        slash_syntax="/history [name:<@user>] [season:<n>] [month:<n>]",
        examples=(
            "-history",
            "/history",
            "/history name:@Senko",
            "/history season:2",
            "/history season:2 month:1",
        ),
    ),
    _cmd(
        "export_history",
        aliases=("export history",),
        description=(
            "Export your full match history to an Excel (.xlsx) file, sent as "
            "an attachment. The workbook has one sheet per season, and within "
            "each season's sheet, one table per month with date, opponent, "
            "score, Elo change, and Elo before/after for every match."
        ),
        syntax="Not available as a prefix command.",
        slash_syntax="/export history",
        examples=("/export history",),
    ),
    _cmd(
        "stats",
        description="View Elo, wins, losses, and league info for yourself or another player.",
        syntax="-stats [@player | name | id]",
        slash_syntax="/stats [name:<name> | mention:<@user> | discord_id:<id>]",
        examples=(
            "-stats",
            "-stats @Senko",
            "/stats name:Senko",
            "/stats mention:@Senko",
            "/stats discord_id:123456789012345678",
        ),
    ),
    _cmd(
        "stats_expanded",
        aliases=("stats expanded",),
        description=(
            "View your Elo, wins, losses, and win rate averaged/summed across "
            "one or more season/month combinations. Each entry in the "
            "`seasons` field is written as `s<season>` (that season's Overall "
            "stats) or `s<season>m<month>m<month>...` (that season, only the "
            "listed months), and multiple entries are separated by a space to "
            "combine and average them together. If you list every month of a "
            "season, it automatically collapses to that season's Overall "
            "stats. If you are not currently registered, the bot falls back "
            "to the name recorded (by your Discord ID) in the archive for the "
            "first season/month you list."
        ),
        syntax="Not available as a prefix command.",
        slash_syntax="/stats expanded seasons:<s1m1m2 s2m2 ...>",
        examples=(
            "/stats expanded seasons:s2",
            "/stats expanded seasons:s1m2",
            "/stats expanded seasons:s1m1m2 s2m2 s3m1m2m3",
        ),
    ),
    _cmd(
        "compare",
        aliases=("cp",),
        description="Compare two registered players side by side.",
        syntax="-compare <player1> <player2>",
        slash_syntax="/compare player1:<@user|name> player2:<@user|name>",
        examples=("-compare Senko Autony", "/compare player1:Senko player2:Autony"),
    ),
    _cmd(
        "leaderboard",
        aliases=("lb",),
        description="Show the server leaderboard.",
        syntax="-leaderboard",
        slash_syntax="/leaderboard",
        examples=("-lb", "/leaderboard"),
    ),
    _cmd(
        "lbs",
        description="Show archived leaderboards for each season.",
        syntax="-lbs",
        slash_syntax="/lbs",
        examples=("-lbs", "/lbs"),
    ),
    _cmd(
        "leagues",
        description="Show discovered leagues and optional roster filter by name.",
        syntax="-leagues [name]",
        slash_syntax="/leagues [league_query:<substring>]",
        examples=("-leagues", "-leagues Gold", "/leagues league_query:Gold"),
    ),
    _cmd(
        "adhelp",
        description="Show admin-only commands, or detailed help for one admin command.",
        syntax="-adhelp [command]",
        slash_syntax="/adhelp [command:<name>]",
        permission="Elo Bot Manager",
        examples=("-adhelp", "-adhelp revert", "/adhelp command:revert"),
        admin=True,
    ),
    _cmd(
        "register_o",
        description="Register another player with a custom name (admin only).",
        syntax="-register_o <@player> <name>",
        slash_syntax="Not available as a slash command.",
        permission="Elo Bot Manager",
        examples=("-register_o @Autony Autony",),
        admin=True,
        is_slash=False,
    ),
    _cmd(
        "register_oid",
        description="Register a player by Discord ID (admin only).",
        syntax="-register_oid <discord_id> <name>",
        slash_syntax="Not available as a slash command.",
        permission="Elo Bot Manager",
        examples=("-register_oid 123456789012345678 Autony",),
        admin=True,
        is_slash=False,
    ),
    _cmd(
        "rename_o",
        description="Rename another player without cooldown (admin only).",
        syntax="-rename_o <@player> <new_name>",
        slash_syntax="Not available as a slash command.",
        permission="Elo Bot Manager",
        examples=("-rename_o @Autony Autony2",),
        admin=True,
        is_slash=False,
    ),
    _cmd(
        "rename_oid",
        description="Rename a player by Discord ID (admin only).",
        syntax="-rename_oid <discord_id> <new_name>",
        slash_syntax="Not available as a slash command.",
        permission="Elo Bot Manager",
        examples=("-rename_oid 123456789012345678 Autony2",),
        admin=True,
        is_slash=False,
    ),
    _cmd(
        "ad_rp",
        description="Report a match using Discord IDs (admin only).",
        syntax="-ad_rp <id1> <score1> <score2> <id2>  |  -ad_rp <id1> <score> <id2>",
        slash_syntax="/ad rp player1_id:<id> p1_score:<0-4> p2_score:<0-3> player2_id:<id>",
        permission="Elo Bot Manager",
        examples=("-ad_rp 111 3 1 222", "/ad_rp player1_id:111 p1_score:3 p2_score:1 player2_id:222"),
        admin=True,
    ),
    _cmd(
        "revert",
        description=(
            "Undo a previously recorded match by its Match ID (admin only). "
            "Use `-match_ids` to export every recorded match's ID in this "
            "server to an Excel file, then pass that ID here. Revert reads "
            "the exact Elo values stored for that match at report time, so "
            "it restores both players' Elo precisely (no re-typing scores "
            "or guessing which order they were reported in), reverses the "
            "win/loss and monthly-games counts, and deletes the match from "
            "history. Asks for reaction confirmation before applying."
        ),
        syntax="-revert <match_id>",
        slash_syntax="/revert match_id:<id>",
        permission="Elo Bot Manager",
        examples=(
            "-match_ids",
            "-revert 482913",
            "/revert match_id:482913",
        ),
        admin=True,
    ),
    _cmd(
        "set_e",
        description="Set a player's Elo (admin only).",
        syntax="-set_e <@player> <elo>",
        slash_syntax="/set e member:<@user> elo:<number>",
        permission="Elo Bot Manager",
        examples=("-set_e @Senko 600", "/set_e member:@Senko elo:600"),
        admin=True,
    ),
    _cmd(
        "set_eid",
        description="Set Elo by Discord ID (admin only).",
        syntax="-set_eid <discord_id> <elo>",
        slash_syntax="/set eid discord_id:<id> elo:<number>",
        permission="Elo Bot Manager",
        examples=("-set_eid 111 600",),
        admin=True,
        is_slash=False,
    ),
    _cmd(
        "set_w",
        description="Set win count for a player (admin only).",
        syntax="-set_w <@player> <wins>",
        slash_syntax="/set w member:<@user> wins:<number>",
        permission="Elo Bot Manager",
        examples=("-set_w @Senko 10",),
        admin=True,
    ),
    _cmd(
        "set_wid",
        description="Set win count by Discord ID (admin only).",
        syntax="-set_wid <discord_id> <wins>",
        slash_syntax="/set wid discord_id:<id> wins:<number>",
        permission="Elo Bot Manager",
        examples=("-set_wid 111 10",),
        admin=True,
        is_slash=False,
    ),
    _cmd(
        "set_l",
        description="Set loss count for a player (admin only).",
        syntax="-set_l <@player> <losses>",
        slash_syntax="/set l member:<@user> losses:<number>",
        permission="Elo Bot Manager",
        examples=("-set_l @Senko 5",),
        admin=True,
    ),
    _cmd(
        "set_lid",
        description="Set loss count by Discord ID (admin only).",
        syntax="-set_lid <discord_id> <losses>",
        slash_syntax="/set lid discord_id:<id> losses:<number>",
        permission="Elo Bot Manager",
        examples=("-set_lid 111 5",),
        admin=True,
        is_slash=False,
    ),
    _cmd(
        "set_gid",
        description="Set monthly games played by Discord ID (admin only).",
        syntax="-set_gid <discord_id> <games_played>",
        slash_syntax="Not available as a slash command.",
        permission="Elo Bot Manager",
        examples=("-set_gid 111 12",),
        admin=True,
        is_slash=False,
    ),
    _cmd(
        "set_stats",
        description="Set all stats for a player by Discord ID (admin only).",
        syntax="-set_stats <discord_id> <elo> <wins> <losses> <games_played>",
        slash_syntax="/set_stats discord_id:<id> elo:<n> wins:<n> losses:<n> games_played:<n>",
        permission="Elo Bot Manager",
        examples=("-set_stats 111 600 10 5 15", "/set_stats discord_id:111 elo:600 wins:10 losses:5 games_played:15"),
        admin=True,
    ),
    _cmd(
        "months",
        description=(
            "Set the total number of months a season can have on this server "
            "(admin only). Applies to /season, -decay, and /history going forward."
        ),
        syntax="Not available as a prefix command.",
        slash_syntax="/months total_months:<n>",
        permission="Elo Bot Manager",
        examples=("/months total_months:3",),
        admin=True,
    ),
    _cmd(
        "season",
        description=(
            "Update the current season and month for this server (admin only). "
            "Requires /months to be set first."
        ),
        syntax="Not available as a prefix command.",
        slash_syntax="/season season:<n> month:<n>",
        permission="Elo Bot Manager",
        examples=("/season season:1 month:2",),
        admin=True,
    ),
    _cmd(
        "c_reg",
        description=(
            "Clear all registered player data for this server (admin only). "
            "Deletes every player's registration along with their match "
            "history and head-to-head stats. Asks for reaction confirmation "
            "first; this cannot be undone."
        ),
        syntax="-c_reg",
        slash_syntax="Not available as a slash command.",
        permission="Elo Bot Manager",
        examples=("-c_reg",),
        admin=True,
        is_slash=False,
    ),
    _cmd(
        "msg",
        description=(
            "DM the bot to send a private message to the support team. Only "
            "usable in the bot's DMs, not in a server. Each message is "
            "relayed into a thread (named after you) in the support channel "
            "the admins have configured."
        ),
        syntax="Not available as a prefix command.",
        slash_syntax="/msg content:<text>",
        examples=("/msg content:Hi, I found a bug with -report.",),
    ),
    _cmd(
        "decay",
        description=(
            "With dev mode off (see -devmode), shows the time remaining "
            "until the next automatic Elo decay, using Discord's own "
            "timestamp formatting (admin only). With dev mode on, manually "
            "runs Elo decay for all players instead: shows a preview of "
            "every player's Elo change and asks for reaction confirmation "
            "before applying it. Elo decay also runs automatically every "
            "calendar month regardless of dev mode. If the server's "
            "current month is the season's last configured month (see "
            "/months), decay is skipped, a season-end leaderboard export "
            "runs instead, and -c_reg runs automatically right after with "
            "no separate confirmation."
        ),
        syntax="-decay",
        slash_syntax="Not available as a slash command.",
        permission="Elo Bot Manager",
        examples=("-decay",),
        admin=True,
        is_slash=False,
    ),
    _cmd(
        "decay_setup",
        description=(
            "Set or clear the channel the automatic monthly -decay change "
            "log is posted to (admin only)."
        ),
        syntax="-decay_setup <channel>  |  -decay_setup off",
        slash_syntax="Not available as a slash command.",
        permission="Elo Bot Manager",
        examples=("-decay_setup #decay-log", "-decay_setup off"),
        admin=True,
        is_slash=False,
    ),
    _cmd(
        "nickname",
        description=(
            "Assign a short nickname to a server ID, used purely for file "
            "naming in -get_lb, -sum_lb, and the automatic monthly -decay "
            "task (admin only). The prefix form works for any server's "
            "admin, naming their own server. The slash form is only "
            "available in the private/management guild, and can name any "
            "server by ID."
        ),
        syntax="-nickname <server_id> <name>",
        slash_syntax="/nickname server_id:<id> name:<text> (private guild only)",
        permission="Elo Bot Manager",
        examples=("-nickname 123456789012345678 QS", "/nickname server_id:123456789012345678 name:QS"),
        admin=True,
    ),
    _cmd(
        "get_lb",
        description=(
            "Export all leaderboard stats for this server to a zip file "
            "containing separate text files for each stat (Elo, wins, "
            "losses, win rate, monthly games played) (admin only)."
        ),
        syntax="-get_lb",
        slash_syntax="Not available as a slash command.",
        permission="Elo Bot Manager",
        examples=("-get_lb",),
        admin=True,
        is_slash=False,
    ),
    _cmd(
        "set_id",
        description=(
            "Sync all of this server's registered players' Discord IDs into "
            "the archived lb/<server_id>/ leaderboard files (admin only)."
        ),
        syntax="-set_id <server_id>",
        slash_syntax="Not available as a slash command.",
        permission="Elo Bot Manager",
        examples=("-set_id 123456789012345678",),
        admin=True,
        is_slash=False,
    ),
    _cmd(
        "sum_lb",
        description=(
            "Calculate and export the total leaderboard for a specific "
            "season from a given server's lb/ archive folder (admin only)."
        ),
        syntax="-sum_lb <server_id> <season>",
        slash_syntax="Not available as a slash command.",
        permission="Elo Bot Manager",
        examples=("-sum_lb 123456789012345678 S2", "-sum_lb 123456789012345678 2"),
        admin=True,
        is_slash=False,
    ),
    _cmd(
        "match_ids",
        description=(
            "Export every recorded match in this server to an Excel file, "
            "showing each match's ID (needed for -revert <match_id>) plus "
            "the exact date/time it was recorded (admin only)."
        ),
        syntax="-match_ids",
        slash_syntax="Not available as a slash command.",
        permission="Elo Bot Manager",
        examples=("-match_ids",),
        admin=True,
        is_slash=False,
    ),
    _cmd(
        "devmode",
        description=(
            "Toggle dev mode for this server (admin only). While enabled, "
            "-report/-rp additionally shows a comparison against the "
            "previous Elo formula, for debugging."
        ),
        syntax="-devmode",
        slash_syntax="Not available as a slash command.",
        permission="Elo Bot Manager",
        examples=("-devmode",),
        admin=True,
        is_slash=False,
    ),
    _cmd(
        "say",
        description=(
            "Send a message to a specific channel in another server the bot "
            "is in (private/management guild only)."
        ),
        syntax="-say <server_id> <channel_name> <message>",
        slash_syntax="Not available as a slash command.",
        permission="Elo Bot Manager (private guild only)",
        examples=("-say 123456789012345678 general Hello everyone!",),
        admin=True,
        private=True,
        is_slash=False,
    ),
    _cmd(
        "counting",
        description=(
            "Enable or disable the counting game in a channel of a given "
            "server (admin only)."
        ),
        syntax="-counting <server_id> <channel_name> <enable|disable>",
        slash_syntax="Not available as a slash command.",
        permission="Elo Bot Manager",
        examples=("-counting 123456789012345678 counting enable",),
        admin=True,
        is_slash=False,
    ),
    _cmd(
        "ladd",
        description="Create league ranges and sync roles across all registered players (admin only).",
        syntax="-ladd <number>",
        slash_syntax="Not available as a slash command.",
        permission="Elo Bot Manager",
        examples=("-ladd 5",),
        admin=True,
        is_slash=False,
    ),
    _cmd(
        "ledit",
        description="Edit league boundaries while keeping leagues non-overlapping (admin only).",
        syntax="-ledit",
        slash_syntax="Not available as a slash command.",
        permission="Elo Bot Manager",
        examples=("-ledit",),
        admin=True,
        is_slash=False,
    ),
    _cmd(
        "lapply",
        description="Re-apply league roles for all registered players based on current Elo (admin only).",
        syntax="-lapply",
        slash_syntax="Not available as a slash command.",
        permission="Elo Bot Manager",
        examples=("-lapply",),
        admin=True,
        is_slash=False,
    ),
    _cmd(
        "lclear",
        description="Remove all league roles from players and clear all leagues from the bot data (admin only).",
        syntax="-lclear",
        slash_syntax="Not available as a slash command.",
        permission="Elo Bot Manager",
        examples=("-lclear",),
        admin=True,
        is_slash=False,
    ),
    _cmd(
        "lcancel",
        description="Cancel any running league task in this channel (admin only).",
        syntax="-lcancel",
        slash_syntax="Not available as a slash command.",
        permission="Elo Bot Manager",
        examples=("-lcancel",),
        admin=True,
        is_slash=False,
    ),
    _cmd(
        "lannounce",
        description="Add a channel for league promotion/demotion announcements (admin only).",
        syntax="-lannounce <channel>",
        slash_syntax="Not available as a slash command.",
        permission="Elo Bot Manager",
        examples=("-lannounce #league-news",),
        admin=True,
        is_slash=False,
    ),
    _cmd(
        "lrannounce",
        description="Remove a channel from league promotion/demotion announcements (admin only).",
        syntax="-lrannounce <channel>",
        slash_syntax="Not available as a slash command.",
        permission="Elo Bot Manager",
        examples=("-lrannounce #league-news",),
        admin=True,
        is_slash=False,
    ),
    _cmd(
        "ladmin",
        description="Admin view of all leagues, both discovered and undiscovered (admin only).",
        syntax="-ladmin [server_id]",
        slash_syntax="Not available as a slash command.",
        permission="Elo Bot Manager",
        examples=("-ladmin", "-ladmin 123456789012345678"),
        admin=True,
        is_slash=False,
    ),
    _cmd(
        "lhelp",
        description="Show detailed help for all league-related commands.",
        syntax="-lhelp",
        slash_syntax="Not available as a slash command.",
        permission="Elo Bot Manager",
        examples=("-lhelp",),
        admin=True,
        is_slash=False,
    ),
    _cmd(
        "lb_id",
        description="View another server's leaderboard by server ID (private/management guild only).",
        syntax="-lb_id <server_id>",
        slash_syntax="Not available as a slash command.",
        permission="Elo Bot Manager (private guild only)",
        examples=("-lb_id 123456789012345678",),
        admin=True,
        private=True,
        is_slash=False,
    ),
    _cmd(
        "gld",
        description=(
            "Export another server's full leaderboard (Elo, wins, losses, "
            "win rate, monthly games) as a zip file, by server ID "
            "(private/management guild only)."
        ),
        syntax="-gld <server_id>",
        slash_syntax="Not available as a slash command.",
        permission="Elo Bot Manager (private guild only)",
        examples=("-gld 123456789012345678",),
        admin=True,
        private=True,
        is_slash=False,
    ),
    _cmd(
        "st",
        description=(
            "View a player's stats in another server, by server ID and the "
            "player's Discord ID (private/management guild only)."
        ),
        syntax="-st <server_id> <player_id>",
        slash_syntax="Not available as a slash command.",
        permission="Elo Bot Manager (private guild only)",
        examples=("-st 123456789012345678 987654321098765432",),
        admin=True,
        private=True,
        is_slash=False,
    ),
    _cmd(
        "hpbd",
        description=(
            "Send a happy-birthday message to a user in another server's "
            "#general channel (private/management guild only)."
        ),
        syntax="-hpbd <@user> <server_id>",
        slash_syntax="Not available as a slash command.",
        permission="Elo Bot Manager (private guild only)",
        examples=("-hpbd @Senko 123456789012345678",),
        admin=True,
        private=True,
        is_slash=False,
    ),
    _cmd(
        "log",
        description="Set or clear the channel the bot posts activity logs to (private/management guild only).",
        syntax="-log <channel>  |  -log off",
        slash_syntax="Not available as a slash command.",
        permission="Elo Bot Manager (private guild only)",
        examples=("-log #bot-log", "-log off"),
        admin=True,
        private=True,
        is_slash=False,
    ),
    _cmd(
        "support",
        description=(
            "Set or clear the channel /msg relays DM messages into "
            "(private/management guild only)."
        ),
        syntax="-support <channel>  |  -support off",
        slash_syntax="Not available as a slash command.",
        permission="Elo Bot Manager (private guild only)",
        examples=("-support #support-relay", "-support off"),
        admin=True,
        private=True,
        is_slash=False,
    ),
    _cmd(
        "status",
        description="Show internal bot/process status: latency, loaded cogs/extensions, command count (private/management guild only).",
        syntax="-status",
        slash_syntax="Not available as a slash command.",
        permission="Everyone (private guild only)",
        examples=("-status",),
        admin=True,
        private=True,
        is_slash=False,
    ),
    _cmd(
        "ban_msg",
        description="Ban a user from using /msg, with an optional reason (private/management guild only).",
        syntax="-ban_msg <discord_id> [reason]",
        slash_syntax="Not available as a slash command.",
        permission="Elo Bot Manager (private guild only)",
        examples=("-ban_msg 123456789012345678 spam",),
        admin=True,
        private=True,
        is_slash=False,
    ),
    _cmd(
        "unban_msg",
        description="Unban a user from using /msg, with an optional reason (private/management guild only). Also DMs the user that they've been unbanned.",
        syntax="-unban_msg <discord_id> [reason]",
        slash_syntax="Not available as a slash command.",
        permission="Elo Bot Manager (private guild only)",
        examples=("-unban_msg 123456789012345678",),
        admin=True,
        private=True,
        is_slash=False,
    ),
    _cmd(
        "lbs_id",
        description="View archived seasonal leaderboards for another server, by server ID (private/management guild only).",
        syntax="-lbs_id <server_id>",
        slash_syntax="Not available as a slash command.",
        permission="Elo Bot Manager (private guild only)",
        examples=("-lbs_id 123456789012345678",),
        admin=True,
        private=True,
        is_slash=False,
    ),
)


def lookup_command_help(raw: str | None) -> CommandHelp | None:
    if not raw:
        return None
    key = raw.strip().lower().lstrip("-").lstrip("/")
    if not key:
        return None
    if key in COMMAND_HELP:
        return COMMAND_HELP[key]
    for entry in COMMAND_HELP.values():
        if key in entry.lookup_keys or key in {a.lower() for a in entry.aliases}:
            return entry
    return None


def public_command_names() -> list[str]:
    names: list[str] = []
    for entry in COMMAND_HELP.values():
        if entry.admin or entry.private:
            continue
        space_form = entry.name.replace("_", " ")
        if space_form in entry.aliases:
            # The underscore name (e.g. "export_history") is not itself a
            # usable command, only the space form (e.g. "export history")
            # is - so show only the space form, not both.
            names.append(space_form)
            names.extend(a for a in entry.aliases if a != space_form)
        else:
            names.append(entry.name)
            names.extend(entry.aliases)
    return sorted(set(names))


def admin_command_names() -> list[str]:
    """Command names (+ aliases) for admin-only entries, used by /adhelp's
    autocomplete so a regular member's /help never shows these, while
    /adhelp (itself admin-gated) still offers them as suggestions."""
    names: list[str] = []
    for entry in COMMAND_HELP.values():
        if not entry.admin:
            continue
        names.append(entry.name)
        names.extend(entry.aliases)
    return sorted(set(names))


def private_command_names() -> list[str]:
    """Command names (+ aliases) for entries only usable in the private/
    management guild. Used to strip these out of /adhelp's autocomplete
    (and to reject direct detail lookups) everywhere else."""
    names: list[str] = []
    for entry in COMMAND_HELP.values():
        if not entry.private:
            continue
        names.append(entry.name)
        names.extend(entry.aliases)
    return sorted(set(names))


def all_command_names() -> list[str]:
    names: list[str] = []
    for entry in COMMAND_HELP.values():
        names.append(entry.name)
        names.extend(entry.aliases)
    return sorted(set(names))