import asyncio
import logging
import time

import discord
from discord.ext import commands

from db.elo_db import db_connection
from utils import is_valid_name, is_admin, update_lb_name, sync_against_names
from config import YES_EMOJI, NO_EMOJI

logger = logging.getLogger(__name__)

_NAME_RULE = "Name must be 2-25 characters and only contain English letters, accented letters (e.g. à, ü), Chinese/Japanese/Korean characters, numbers, '.' and '_'."
_RENAME_COOLDOWN_SECONDS = 7 * 24 * 3600


def _name_error_embed(reason: str, title: str = "Registration Failed") -> discord.Embed:
    desc = "You cannot use that name" if reason == "banned" else _NAME_RULE
    return discord.Embed(title=title, description=desc, color=discord.Color.red())


class RegistrationCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ------------------------------------------------------------------
    # Register
    # ------------------------------------------------------------------

    @commands.command(help="Register with a custom name and initial Elo of 500.")
    async def register(self, ctx: commands.Context, name: str = None):
        if name is None:
            prompt = await ctx.send(embed=discord.Embed(
                title="Registration",
                description="Please reply to this message with your name.",
                color=discord.Color.orange(),
            ))

            def check(m: discord.Message):
                return (
                    m.author.id == ctx.author.id
                    and m.channel.id == ctx.channel.id
                    and m.reference is not None
                    and m.reference.message_id == prompt.id
                )

            try:
                reply: discord.Message = await self.bot.wait_for(
                    "message", timeout=300.0, check=check
                )
            except asyncio.TimeoutError:
                await ctx.send(embed=discord.Embed(
                    title="Timeout",
                    description="Timed out. Please try again.",
                    color=discord.Color.red(),
                ))
                return

            name = (reply.content or "").strip()
            if not name:
                await ctx.send(embed=discord.Embed(
                    title="Registration Failed",
                    description="No name provided. Please try again.",
                    color=discord.Color.red(),
                ))
                return

        is_valid, reason = is_valid_name(name)
        if not is_valid:
            await ctx.send(embed=_name_error_embed(reason))
            return

        user_id = str(ctx.author.id)
        with db_connection(ctx.guild.id) as (conn, cursor):
            cursor.execute("SELECT name FROM players WHERE name = ?", (name,))
            if cursor.fetchone():
                await ctx.send(embed=discord.Embed(
                    title="Registration Failed",
                    description=f"The name `{name}` is already taken. Please choose a different name.",
                    color=discord.Color.red(),
                ))
                return

            cursor.execute("SELECT id FROM players WHERE id = ?", (user_id,))
            if cursor.fetchone():
                await ctx.send(embed=discord.Embed(
                    title="Registration Failed",
                    description="You are already registered!",
                    color=discord.Color.red(),
                ))
                return

            cursor.execute("INSERT INTO players (id, name, elo) VALUES (?, ?, 500)", (user_id, name))
            conn.commit()

        await ctx.send(embed=discord.Embed(
            title="Player Registered",
            description=f"**{name}** has been registered with an Elo of 500!",
            color=discord.Color.green(),
        ))

    @commands.command(help="Register another player with a custom name and initial Elo of 500 (admin only).")
    @commands.check(is_admin)
    async def register_o(self, ctx: commands.Context, member: discord.Member, name: str = None):
        if name is None:
            await ctx.send(embed=discord.Embed(
                title="Registration Failed",
                description="Please provide a name. Usage: `-register_o <@player> <name>`",
                color=discord.Color.red(),
            ))
            return

        is_valid, reason = is_valid_name(name)
        if not is_valid:
            await ctx.send(embed=_name_error_embed(reason))
            return

        user_id = str(member.id)
        with db_connection(ctx.guild.id) as (conn, cursor):
            cursor.execute("SELECT name FROM players WHERE name = ?", (name,))
            if cursor.fetchone():
                await ctx.send(embed=discord.Embed(
                    title="Registration Failed",
                    description=f"The name `{name}` is already taken. Please choose a different name.",
                    color=discord.Color.red(),
                ))
                return

            cursor.execute("SELECT id FROM players WHERE id = ?", (user_id,))
            if cursor.fetchone():
                await ctx.send(embed=discord.Embed(
                    title="Already Registered",
                    description=f"`{member.name}` is already registered!",
                    color=discord.Color.orange(),
                ))
                return

            cursor.execute("INSERT INTO players (id, name, elo) VALUES (?, ?, 500)", (user_id, name))
            conn.commit()

        await ctx.send(embed=discord.Embed(
            title="Player Registered",
            description=f"Player `{name}` has been registered with an Elo of 500!",
            color=discord.Color.green(),
        ))

    @register_o.error
    async def register_o_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.CheckFailure):
            await ctx.send(embed=discord.Embed(
                title="Permission Denied",
                description="You do not have permission to use this command.",
                color=discord.Color.red(),
            ))

    @commands.command(help="Register another player using their Discord ID with a custom name (admin only).")
    @commands.check(is_admin)
    async def register_oid(self, ctx: commands.Context, discord_id: str, name: str = None):
        if name is None:
            await ctx.send(embed=discord.Embed(
                title="Registration Failed",
                description="Please provide a name. Usage: `-register_oid <discord id> <name>`",
                color=discord.Color.red(),
            ))
            return

        is_valid, reason = is_valid_name(name)
        if not is_valid:
            await ctx.send(embed=_name_error_embed(reason))
            return

        try:
            user_id = int(discord_id)
        except ValueError:
            await ctx.send(embed=discord.Embed(
                title="Invalid ID",
                description="Please provide a valid Discord user ID.",
                color=discord.Color.red(),
            ))
            return

        try:
            await self.bot.fetch_user(user_id)
        except discord.NotFound:
            await ctx.send(embed=discord.Embed(
                title="User Not Found",
                description="No user found with the provided Discord ID.",
                color=discord.Color.red(),
            ))
            return

        with db_connection(ctx.guild.id) as (conn, cursor):
            cursor.execute("SELECT name FROM players WHERE name = ?", (name,))
            if cursor.fetchone():
                await ctx.send(embed=discord.Embed(
                    title="Registration Failed",
                    description=f"The name `{name}` is already taken. Please choose a different name.",
                    color=discord.Color.red(),
                ))
                return

            cursor.execute("SELECT name FROM players WHERE id = ?", (user_id,))
            existing = cursor.fetchone()
            if existing:
                await ctx.send(embed=discord.Embed(
                    title="Already Registered",
                    description=f"Player `{existing[0]}` is already registered!",
                    color=discord.Color.orange(),
                ))
                return

            cursor.execute("INSERT INTO players (id, name, elo) VALUES (?, ?, 500)", (user_id, name))
            conn.commit()

        await ctx.send(embed=discord.Embed(
            title="Player Registered",
            description=f"Player `{name}` has been registered with an Elo of 500!",
            color=discord.Color.green(),
        ))

    @register_oid.error
    async def register_oid_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.CheckFailure):
            await ctx.send(embed=discord.Embed(
                title="Permission Denied",
                description="You do not have permission to use this command.",
                color=discord.Color.red(),
            ))

    # ------------------------------------------------------------------
    # Rename helpers
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

    async def _do_rename(
        self,
        ctx: commands.Context,
        user_id: str,
        new_name: str,
        current_name: str,
        member_mention: str,
        *,
        skip_cooldown: bool = False,
        last_rename: int = 0,
    ) -> None:
        """
        Gửi confirmation embed, chờ reaction, rồi thực hiện rename.
        Dùng chung cho -rename, -rename_o, -rename_oid.
        """
        is_admin_user = skip_cooldown
        now = int(time.time())

        if not is_admin_user and now - last_rename < _RENAME_COOLDOWN_SECONDS:
            remaining = _RENAME_COOLDOWN_SECONDS - (now - last_rename)
            hours, minutes = remaining // 3600, (remaining % 3600) // 60
            await ctx.send(embed=discord.Embed(
                title="Rename Cooldown",
                description=f"You can rename again in {hours}h {minutes}m.",
                color=discord.Color.orange(),
            ))
            return

        cooldown_note = "" if is_admin_user else "You can only change your name once every 7 days. "
        embed = discord.Embed(
            title="Confirm Rename",
            description=(
                f"{cooldown_note}Are you sure you want to change {member_mention}'s name to `{new_name}`? "
                f"Please react with {YES_EMOJI} to confirm or {NO_EMOJI} to cancel within 5 minutes."
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
            reaction, _ = await ctx.bot.wait_for("reaction_add", timeout=300.0, check=check)
        except asyncio.TimeoutError:
            await self._remove_bot_reactions(message)
            await message.edit(embed=discord.Embed(
                title="Timeout",
                description="Rename confirmation timed out. Please try again.",
                color=discord.Color.red(),
            ))
            return

        if str(reaction.emoji) != YES_EMOJI:
            await self._remove_bot_reactions(message)
            await message.edit(embed=discord.Embed(
                title="Rename Canceled",
                description="Rename operation has been canceled.",
                color=discord.Color.red(),
            ))
            return

        with db_connection(ctx.guild.id) as (conn, cursor):
            cursor.execute(
                "UPDATE players SET name=?, last_rename=? WHERE id=?",
                (new_name, now, user_id),
            )
            sync_against_names(cursor, current_name, new_name)
            conn.commit()

        try:
            update_lb_name(ctx.guild.id, current_name, new_name)
        except Exception:
            logger.exception("Failed to update lb name for guild %s", ctx.guild.id)

        await self._remove_bot_reactions(message)
        await message.edit(embed=discord.Embed(
            title="Rename Successful",
            description=f"{member_mention}'s name has been changed to `{new_name}`.",
            color=discord.Color.green(),
        ))

    # ------------------------------------------------------------------
    # Rename commands
    # ------------------------------------------------------------------

    @commands.command(help="Change your name (7-day cooldown).")
    async def rename(self, ctx: commands.Context, new_name: str = None):
        if new_name is None:
            await ctx.send(embed=discord.Embed(
                title="Rename Failed",
                description="Please enter a name. Usage: `-rename <new_name>`",
                color=discord.Color.red(),
            ))
            return

        is_valid, reason = is_valid_name(new_name)
        if not is_valid:
            await ctx.send(embed=_name_error_embed(reason, title="Rename Failed"))
            return

        user_id = str(ctx.author.id)
        with db_connection(ctx.guild.id) as (conn, cursor):
            cursor.execute("SELECT name, last_rename FROM players WHERE id = ?", (user_id,))
            row = cursor.fetchone()

        if not row:
            await ctx.send(embed=discord.Embed(
                title="Rename Failed",
                description="You are not registered. Use `-register <name>` to register.",
                color=discord.Color.red(),
            ))
            return

        current_name, last_rename = row[0], row[1] or 0
        if new_name == current_name:
            await ctx.send(embed=discord.Embed(
                title="Rename Failed",
                description=f"You are already using the name `{new_name}`.",
                color=discord.Color.red(),
            ))
            return

        with db_connection(ctx.guild.id) as (conn, cursor):
            cursor.execute("SELECT id FROM players WHERE name = ?", (new_name,))
            if cursor.fetchone():
                await ctx.send(embed=discord.Embed(
                    title="Rename Failed",
                    description=f"The name `{new_name}` is already taken.",
                    color=discord.Color.red(),
                ))
                return

        is_admin_user = any(r.name == "Elo Bot Manager" for r in getattr(ctx.author, "roles", []))
        await self._do_rename(
            ctx, user_id, new_name, current_name, "your",
            skip_cooldown=is_admin_user,
            last_rename=last_rename,
        )

    @commands.command(help="Admin: Change another player's name (no cooldown).")
    @commands.check(is_admin)
    async def rename_o(self, ctx: commands.Context, member: discord.Member, new_name: str = None):
        if new_name is None:
            await ctx.send(embed=discord.Embed(
                title="Rename Failed",
                description="Please enter a name. Usage: `-rename_o <@player> <new_name>`",
                color=discord.Color.red(),
            ))
            return

        is_valid, reason = is_valid_name(new_name)
        if not is_valid:
            await ctx.send(embed=_name_error_embed(reason, title="Rename Failed"))
            return

        user_id = str(member.id)
        with db_connection(ctx.guild.id) as (conn, cursor):
            cursor.execute("SELECT name FROM players WHERE id = ?", (user_id,))
            row = cursor.fetchone()

        if not row:
            await ctx.send(embed=discord.Embed(
                title="Rename Failed",
                description=f"{member.mention} is not registered. Use `-register <name>` to register.",
                color=discord.Color.red(),
            ))
            return

        current_name = row[0]
        if new_name == current_name:
            await ctx.send(embed=discord.Embed(
                title="Rename Failed",
                description=f"{member.mention} is already using the name `{new_name}`.",
                color=discord.Color.red(),
            ))
            return

        with db_connection(ctx.guild.id) as (conn, cursor):
            cursor.execute("SELECT id FROM players WHERE name = ?", (new_name,))
            if cursor.fetchone():
                await ctx.send(embed=discord.Embed(
                    title="Rename Failed",
                    description=f"The name `{new_name}` is already taken.",
                    color=discord.Color.red(),
                ))
                return

        await self._do_rename(ctx, user_id, new_name, current_name, member.mention, skip_cooldown=True)

    @rename_o.error
    async def rename_o_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.CheckFailure):
            await ctx.send(embed=discord.Embed(
                title="Permission Denied",
                description="You do not have permission to use this command.",
                color=discord.Color.red(),
            ))

    @commands.command(help="Admin: Change another player's name by Discord ID (no cooldown).")
    @commands.check(is_admin)
    async def rename_oid(self, ctx: commands.Context, discord_id: str, new_name: str = None):
        if new_name is None:
            await ctx.send(embed=discord.Embed(
                title="Rename Failed",
                description="Please enter a name. Usage: `-rename_oid <discord_id> <new_name>`",
                color=discord.Color.red(),
            ))
            return

        is_valid, reason = is_valid_name(new_name)
        if not is_valid:
            await ctx.send(embed=_name_error_embed(reason, title="Rename Failed"))
            return

        try:
            user_id = int(discord_id)
        except ValueError:
            await ctx.send(embed=discord.Embed(
                title="Invalid ID",
                description="Please provide a valid Discord user ID.",
                color=discord.Color.red(),
            ))
            return

        with db_connection(ctx.guild.id) as (conn, cursor):
            cursor.execute("SELECT name FROM players WHERE id = ?", (user_id,))
            row = cursor.fetchone()

        if not row:
            await ctx.send(embed=discord.Embed(
                title="Rename Failed",
                description=f"No registered player found with ID {user_id}.",
                color=discord.Color.red(),
            ))
            return

        current_name = row[0]
        try:
            fetched = await self.bot.fetch_user(user_id)
            member_mention = fetched.mention
        except Exception:
            member_mention = f"`{user_id}`"

        if new_name == current_name:
            await ctx.send(embed=discord.Embed(
                title="Rename Failed",
                description=f"{member_mention} is already using the name `{new_name}`.",
                color=discord.Color.red(),
            ))
            return

        with db_connection(ctx.guild.id) as (conn, cursor):
            cursor.execute("SELECT id FROM players WHERE name = ?", (new_name,))
            if cursor.fetchone():
                await ctx.send(embed=discord.Embed(
                    title="Rename Failed",
                    description=f"The name `{new_name}` is already taken.",
                    color=discord.Color.red(),
                ))
                return

        await self._do_rename(ctx, str(user_id), new_name, current_name, member_mention, skip_cooldown=True)

    @rename_oid.error
    async def rename_oid_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.CheckFailure):
            await ctx.send(embed=discord.Embed(
                title="Permission Denied",
                description="You do not have permission to use this command.",
                color=discord.Color.red(),
            ))


async def setup(bot: commands.Bot):
    await bot.add_cog(RegistrationCog(bot))