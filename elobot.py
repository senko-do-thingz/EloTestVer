import asyncio
import importlib
import pkgutil

import discord
from discord.ext import commands

import cogs as _cogs_pkg
import cogs.private as _private_cogs_pkg
from config import get_discord_token
from utils.logger import bot_log

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="-", intents=intents)
# Remove the default help command; we provide our own in cogs/help.py
bot.remove_command("help")

# ---------------------------------------------------------------------------
# Commands that get logged when they complete successfully
# ---------------------------------------------------------------------------
_USER_COMMANDS = {
    "register", "register_o", "register_oid",
    "rename", "rename_o", "rename_oid",
    "report", "rp",
    "history",
}
_ADMIN_COMMANDS = {
    "ad_rp",
    "revert",
    "decay", "reset_e",
    "set_e", "set_eid",
    "set_w", "set_wid",
    "set_l", "set_lid",
    "set_gid", "set_stats",
    "d_reg", "did_reg", "c_reg", "db_d",
    "get_lb", "set_id", "sum_lb",
    "devmode",
    "ladmin", "ledit", "ladd", "lapply", "lclear",
    "lannounce", "lrannounce",
    "set_lan",
    "log",
}
# Always log: viewing another player's stats via ID-based commands
_STATS_COMMANDS = {"st", "stats_id"}
_WATCHED_COMMANDS = _USER_COMMANDS | _ADMIN_COMMANDS | _STATS_COMMANDS

# Extension load failures collected before on_ready fires
_failed_extensions: list[tuple[str, Exception]] = []


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    await bot.change_presence(activity=discord.Game(name="-help | /help"))

    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash command(s)")
    except Exception as exc:
        print(f"Failed to sync slash commands: {exc}")
        await bot_log(
            bot,
            0,
            "Failed to sync slash commands",
            level="ERROR",
            exc=exc,
        )

    await bot_log(
        bot,
        0,
        f"Bot online: {bot.user}",
        level="SUCCESS",
        fields={
            "Bot ID": str(bot.user.id),
            "Servers": str(len(bot.guilds)),
        },
    )

    # Report any extensions that failed to load during startup
    for ext, exc in _failed_extensions:
        await bot_log(
            bot,
            0,
            f"Failed to load extension: {ext}",
            level="ERROR",
            fields={"Extension": ext},
            exc=exc,
        )
    _failed_extensions.clear()


@bot.event
async def on_command_completion(ctx: commands.Context):
    """Log important commands after they finish successfully."""
    cmd = ctx.command
    if cmd is None or cmd.name not in _WATCHED_COMMANDS:
        return

    # -st without a target means viewing own stats – skip
    # Bug #7 fix: command is named "st", not "stats"
    if cmd.name == "st":
        args = ctx.args[2:]  # drop 'self' and 'ctx'
        kwargs = ctx.kwargs
        target = (args[0] if args else None) or kwargs.get("target")
        if not target:
            return

    level = "INFO" if cmd.name in _USER_COMMANDS | _STATS_COMMANDS else "WARNING"
    guild_id = ctx.guild.id if ctx.guild else 0

    content = ctx.message.content or ""
    if len(content) > 200:
        content = content[:200] + "…"

    fields = {
        "Command": f"`-{cmd.name}`",
        "User": f"{ctx.author.mention} ({ctx.author.id})",
        "Channel": f"#{ctx.channel.name}" if hasattr(ctx.channel, "name") else str(ctx.channel.id),
        "Input": f"`{content}`",
    }

    await bot_log(
        bot,
        guild_id,
        f"Command used: -{cmd.name}",
        level=level,
        fields=fields,
    )


@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError):
    """Log unexpected command errors. Expected/user-facing errors are skipped."""
    _ignored = (
        commands.CommandNotFound,
        commands.CheckFailure,
        commands.MissingRequiredArgument,
        commands.BadArgument,
        commands.UserNotFound,
        commands.MemberNotFound,
        commands.ChannelNotFound,
    )
    if isinstance(error, _ignored):
        return

    # Unwrap CommandInvokeError to expose the real exception + traceback
    exc = error.original if isinstance(error, commands.CommandInvokeError) else error

    guild_id = ctx.guild.id if ctx.guild else 0
    cmd_name = ctx.command.name if ctx.command else "unknown"

    content = ctx.message.content or ""
    await bot_log(
        bot,
        guild_id,
        f"Error in command: -{cmd_name}",
        level="ERROR",
        fields={
            "User": f"{ctx.author.mention} ({ctx.author.id})",
            "Input": content[:200],
        },
        exc=exc,
    )


async def load_extensions():
    """Auto-discover and load all modules inside the `cogs` package."""
    for module_info in pkgutil.iter_modules(_cogs_pkg.__path__):
        if module_info.name.startswith("_"):
            continue  # skip __init__ and private helpers

        if module_info.ispkg:
            # Skip cogs/private/ — handled separately below.
            if module_info.name == "private":
                continue
            # Auto-detect: load the sub-package only if its __init__.py
            # exposes a top-level setup() callable.
            try:
                pkg = importlib.import_module(f"cogs.{module_info.name}")
                if not callable(getattr(pkg, "setup", None)):
                    continue
            except Exception:
                continue
            ext = f"cogs.{module_info.name}"
            try:
                await bot.load_extension(ext)
                print(f"Loaded sub-package extension: {ext}")
            except Exception as e:
                print(f"Failed to load sub-package extension {ext}: {e}")
                _failed_extensions.append((ext, e))
            continue

        ext = f"cogs.{module_info.name}"
        if ext in bot.extensions:
            print(f"Skipping already-loaded extension: {ext}")
            continue
        try:
            await bot.load_extension(ext)
            print(f"Loaded extension: {ext}")
        except (commands.ExtensionAlreadyLoaded, commands.ExtensionFailed) as e:
            inner = getattr(e, "original", e)
            if isinstance(e, commands.ExtensionAlreadyLoaded) or (
                isinstance(inner, discord.ClientException)
                and "already loaded" in str(inner)
            ):
                print(f"Already loaded (skipping): {ext}")
            else:
                print(f"Failed to load extension {ext}: {e}")
                _failed_extensions.append((ext, e))
        except Exception as e:
            print(f"Failed to load extension {ext}: {e}")
            # bot_log needs the bot to be ready – defer until on_ready
            _failed_extensions.append((ext, e))

    # Load private cogs from cogs/private/ — must come AFTER main cogs so that
    # cogs like PrivateHelpCog can safely call bot.remove_command("help").
    for module_info in pkgutil.iter_modules(_private_cogs_pkg.__path__):
        if module_info.name.startswith("_"):
            continue
        ext = f"cogs.private.{module_info.name}"
        if ext in bot.extensions:
            print(f"Skipping already-loaded private extension: {ext}")
            continue
        try:
            await bot.load_extension(ext)
            print(f"Loaded private extension: {ext}")
        except (commands.ExtensionAlreadyLoaded, commands.ExtensionFailed) as e:
            # ExtensionAlreadyLoaded: extension registered twice (e.g. Windows __pycache__ artefact).
            # ExtensionFailed wrapping ClientException("Cog already loaded"): cog was added via
            # a direct add_cog path before load_extension ran — treat as already loaded.
            inner = getattr(e, "original", e)
            if isinstance(e, commands.ExtensionAlreadyLoaded) or (
                isinstance(inner, discord.ClientException)
                and "already loaded" in str(inner)
            ):
                print(f"Already loaded (skipping): {ext}")
            else:
                print(f"Failed to load private extension {ext}: {e}")
                _failed_extensions.append((ext, e))
        except Exception as e:
            print(f"Failed to load private extension {ext}: {e}")
            _failed_extensions.append((ext, e))


async def main():
    await load_extensions()
    await bot.start(get_discord_token())


if __name__ == "__main__":
    asyncio.run(main())