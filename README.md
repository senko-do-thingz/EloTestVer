# EloBot - Discord Elo rating bot

**Author:** Senko

**Documentation language:** [English](README.md) | [Tiếng Việt](docs/README.vi.md) | [日本語](docs/README.ja.md)

## Overview

EloBot is a Python Discord bot for managing registered players, reporting match results, calculating Elo ratings, and viewing leaderboards and history. It stores data in per-server SQLite databases and provides both prefix commands and selected slash commands. The default command prefix is `-`.

The source currently uses English for most bot responses. This documentation is available in English, Vietnamese, and Japanese. Selecting a documentation language does not change the bot's response language. A database language setting exists, but a complete three-language translation of the Discord UI is not implemented.

## Main features

- Player registration, renaming, and administrator-managed registration
- Match reports with player confirmation, Elo updates, and match history
- Leaderboards for Elo, wins, losses, win rate, streaks, and monthly activity
- Player statistics, history images, comparison, and history export to Excel
- Season and month configuration with historical leaderboards and archives
- Automatic monthly Elo decay, backups, season rollover, and end-of-season registration cleanup
- League roles and administrator tools
- Counting, private support messages, and internal management commands
- Per-guild databases and optional log channels

## Requirements

- Python 3.11 or 3.12 (the versions currently checked in CI)
- Dependencies listed in `requirements.txt`, including discord.py 2.7.1
- A Discord bot token and access to a server where you can configure the bot
- SQLite, provided by the Python standard library

## Setup

1. Clone the repository and enter its directory:

   ~~~bash
   git clone https://github.com/senko-do-thingz/EloTestVer.git
   cd EloTestVer
   ~~~

2. Create and activate a Python virtual environment:

   ~~~bash
   python -m venv .venv
   ~~~

   On Windows: `.venv\Scripts\activate`

   On macOS or Linux: `source .venv/bin/activate`

3. Install dependencies:

   ~~~bash
   python -m pip install -r requirements.txt
   ~~~

4. Create a local `.env` file at the repository root:

   ~~~dotenv
   DISCORD_TOKEN=replace_with_your_bot_token
   ~~~

   Never commit the token or the `.env` file. The token is loaded through `config.py`. Set `IS_PRODUCTION=true` only if you want the production reaction configuration. Optional `YES_EMOJI` and `NO_EMOJI` variables can override those reaction identifiers.

5. In the Discord Developer Portal, enable the Message Content intent for prefix commands. Invite the bot with the permissions needed for commands, messages, embeds, reactions, and any league-role management you intend to use.

6. Start the bot:

   ~~~bash
   python elobot.py
   ~~~

The Discord bot token is required only to start the live bot. The automated test suite does not log into Discord.

## Commands

The following are common commands. Some are prefix-only, others are also available as slash commands. Use `-help`, `-adhelp`, `/help`, and the command-specific help for the complete list and correct syntax.

| Purpose | Command | Notes |
| --- | --- | --- |
| Register | `-register <name>` | Initial Elo is 500 |
| Rename | `-rename <new_name>` | Standard 7-day cooldown |
| Report a match | `-report @player1 3 1 @player2` | `-rp` is an alias |
| Report with combined score | `-report @winner 31 @loser` | Also supports formats such as `3-1` and `3:1` |
| Current leaderboard | `-leaderboard` or `-lb` | Interactive categories and pagination |
| Archived leaderboard | `-lbs` | Choose season and month |
| Player statistics | `-stats` | See help for other-player options |
| Match history | `-history` | Slash history has additional selection options |
| Player comparison | `-compare` or `-cp` | Compare registered players |
| Leagues | `-leagues` | View league information |
| Help | `-help` or `/help` | General command reference |

Administrator commands require the configured `Elo Bot Manager` role unless a specific command documents additional restrictions.

| Purpose | Command | Notes |
| --- | --- | --- |
| Register another player | `-register_o` / `-register_oid` | By mention or Discord ID |
| Rename another player | `-rename_o` / `-rename_oid` | By mention or Discord ID |
| Administrator match report | `-ad_rp` | By Discord IDs |
| Update ratings and counts | `-set_e`, `-set_w`, `-set_l`, `-set_stats` | See `-adhelp` for arguments |
| Reset Elo | `-reset_e` | Requires confirmation |
| Clear registrations | `-c_reg` | Destructive and requires confirmation |
| Export standings | `-get_lb` | Produces leaderboard archive files |
| Aggregate a season | `-sum_lb` | Summarizes archived monthly data |
| Configure season length | `/months` | Sets months per season |
| Configure season position | `/season` | Sets current season and month |
| Inspect or run decay | `-decay` | Manual mutation requires dev mode and confirmation |
| Configure decay logs | `-decay_setup` | Choose or clear a channel |

Additional commands exist for leagues, support, moderation, and the restricted management guild. Internal/private commands should not be exposed as general-user functionality.

## Player naming rules

Names must be 2-20 characters. They may contain ASCII letters, digits, spaces, underscores, periods, and supported accented Latin characters, including Vietnamese. Names consisting only of spaces, names containing `elobot` (case-insensitive), and unsupported characters are rejected. Do not assume that Japanese characters can be used as player display names.

## Ratings, matches, and seasons

- New registrations start at 500 Elo. Elo changes are calculated by the implementation in `utils/elo.py`, which includes score-sensitive adjustments and bonuses.
- A match report may require reaction-based confirmation before data is written. Check score and player identities before confirming.
- `/months` configures the number of months in a season. `/season` sets the current position. A manual `-decay` run modifies data only when dev mode permits it.
- The automatic job checks on the first day of each calendar month at 00:10 UTC. In an ordinary month it applies decay and resets monthly counters. At the final configured month, it archives standings and clears registrations using the same cleanup routine as `-c_reg` before moving to the next season.
- `-c_reg` removes registered players and associated match data and attempts to remove league roles. Daily activity streak data is deliberately retained. Treat this command as irreversible and keep backups.
- Historical leaderboard files are stored under `lb/<guild_id>/Season <n>/Month <m>/` and `Overall/` as appropriate. The bot also supports existing guild-folder suffixes.

**Known limitation:** Archived `-lbs` files with multi-word player names may be parsed incorrectly by the current archive reader. A regression test documents this pending fix. Current leaderboard display and archived leaderboard browsing are separate paths.

## Repository layout

~~~text
EloTestVer/
  elobot.py                 Bot entry point and cog loader
  config.py                 Token and reaction configuration
  requirements.txt          Runtime dependencies
  cogs/                     User, admin, league, and management commands
    admin_elo/              Decay, registration cleanup, admin setters
    roles/                  League role management
    seasonal_lb/            Archived leaderboard picker and UI
    private/                Restricted management commands
  db/                       SQLite schema and database utilities
  utils/                    Elo, rendering, archives, cache, and helpers
  tests/                    Automated tests
  .github/workflows/        GitHub Actions CI
  assets/                   Fonts for image rendering
  databases/                Runtime SQLite database files (not committed)
  lb/                       Runtime leaderboard archives (not committed)
~~~

## Testing and CI

Run all automated tests:

~~~bash
python -m pip install -r requirements.txt "pytest>=8,<9"
python -m compileall -q cogs db utils tests elobot.py
python -m pytest tests -q
~~~

GitHub Actions runs the syntax check and full test suite on pushes and pull requests to `main` using Python 3.11 and 3.12. The tests use temporary databases and mocked Discord interactions. They cannot replace a live Discord end-to-end check of bot permissions, reactions, role hierarchy, and message delivery.

Some known `-lbs` regression cases are marked `xfail` while that feature remains unfixed. They are not passing behavior tests.

## Troubleshooting

- `DISCORD_TOKEN` missing: create `.env` or set the environment variable before starting the bot.
- Prefix commands do not respond: confirm the Message Content intent and permissions, then inspect startup logs for failed extensions.
- `Permission Denied`: check the `Elo Bot Manager` role and the command's guild restrictions.
- Archived rankings missing: confirm that the season/month was exported and that the `lb` directory is accessible.
- Reactions or roles fail: check bot permissions, role ordering, and Discord rate limits.
- Tests fail: use the GitHub Actions job logs to identify the exact assertion; CI should not be marked successful by skipping failures.

## Support and maintenance

Start with `-help` and `-adhelp` for the current command inventory. Review issues and commits in the repository for implementation changes. Back up guild databases before destructive operations.

**Developed by Senko.**
