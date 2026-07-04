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
            "page, up to 30 stored), or view another player's by mentioning them."
        ),
        syntax="-history [@player]",
        slash_syntax="/history [name:<@user>]",
        examples=("-history", "-history @Senko", "/history name:@Senko"),
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
            "Undo a previously confirmed match report (admin only). Revert reverses "
            "the exact Elo, win/loss, and monthly-games changes that report applied, "
            "then deletes the match from history. Use the same player IDs and scores "
            "that were used in the original `/report` (or `/ad_rp`) - in the same "
            "order - so the bot knows exactly which match to undo. It does not matter "
            "who technically \"won\"; just re-enter player1_id/p1_score/p2_score/"
            "player2_id exactly as reported."
        ),
        syntax="-revert <player1_id> <p1_score> <p2_score> <player2_id>  |  -revert <player1_id> <combined_score> <player2_id>",
        slash_syntax="/revert player1_id:<id> p1_score:<0-4> p2_score:<0-3> player2_id:<id>",
        permission="Elo Bot Manager",
        examples=(
            "If a match was reported with `-report @Senko 3 1 @Autony`, undo it with "
            "`-revert <Senko_id> 3 1 <Autony_id>` - same scores, same order.",
            "-revert 111 3 1 222",
            "-revert 111 31 222",
            "/revert player1_id:111 p1_score:3 p2_score:1 player2_id:222",
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


def all_command_names() -> list[str]:
    names: list[str] = []
    for entry in COMMAND_HELP.values():
        names.append(entry.name)
        names.extend(entry.aliases)
    return sorted(set(names))