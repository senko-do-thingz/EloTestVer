import os
import discord
from discord.ext import commands
from datetime import datetime, timedelta, timezone
from config import YES_EMOJI, NO_EMOJI
from db import (
    counting_db,
    increment_counting_stat,
    get_counting_leaderboard,
    reset_counting_stats,
)
from utils import is_admin


class Counting(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Per-channel counting state: {channel_id: {"current": 1, "last_user": None}}
        self.counting_channels = {}
        # Per-(guild, user) cooldown timestamps: {(guild_id, user_id): datetime}
        self.user_cooldowns = {}
        self.loaded_guilds = set()

    def ensure_guild_state_loaded(self, guild_id: int):
        """Load channel and cooldown state from DB for a guild (runs once per guild)."""
        if guild_id in self.loaded_guilds:
            return

        self.loaded_guilds.add(guild_id)
        db_path = os.path.join("databases", f"c_{guild_id}.db")
        if not os.path.exists(db_path):
            return

        with counting_db(guild_id) as (conn, cursor):
            cursor.execute("SELECT channel_id, current, last_user FROM channels")
            for channel_id, current, last_user in cursor.fetchall():
                self.counting_channels[channel_id] = {
                    "current": current,
                    "last_user": last_user,
                }

            cursor.execute("SELECT user_id, last_count FROM cooldowns")
            for user_id, last_count in cursor.fetchall():
                if last_count is None:
                    continue
                self.user_cooldowns[(guild_id, user_id)] = datetime.fromtimestamp(
                    float(last_count), tz=timezone.utc
                )

    def save_channel_state(self, guild_id: int, channel_id: int):
        """Persist the current counting state for a channel to DB."""
        state = self.counting_channels.get(channel_id)
        if not state:
            return

        with counting_db(guild_id) as (conn, cursor):
            cursor.execute(
                """
                INSERT INTO channels (channel_id, current, last_user)
                VALUES (?, ?, ?)
                ON CONFLICT(channel_id)
                DO UPDATE SET current=excluded.current, last_user=excluded.last_user
                """,
                (channel_id, state["current"], state["last_user"]),
            )
            conn.commit()

    def remove_channel_state(self, guild_id: int, channel_id: int):
        """Delete the counting state for a channel from DB."""
        with counting_db(guild_id) as (conn, cursor):
            cursor.execute("DELETE FROM channels WHERE channel_id=?", (channel_id,))
            conn.commit()

    def save_user_cooldown(self, guild_id: int, user_id: int, timestamp: datetime):
        with counting_db(guild_id) as (conn, cursor):
            cursor.execute(
                """
                INSERT INTO cooldowns (user_id, last_count)
                VALUES (?, ?)
                ON CONFLICT(user_id)
                DO UPDATE SET last_count=excluded.last_count
                """,
                (user_id, int(timestamp.timestamp())),
            )
            conn.commit()

    def clear_cooldowns_in_db(self, guild_id: int):
        with counting_db(guild_id) as (conn, cursor):
            cursor.execute("DELETE FROM cooldowns")
            conn.commit()

    @commands.Cog.listener()
    async def on_ready(self):
        for guild in self.bot.guilds:
            self.ensure_guild_state_loaded(guild.id)

    @commands.command(name="counting")
    async def counting_command(
        self, ctx, server_id: int, channel_name: str, action: str
    ):
        """
        Enable or disable the counting game in a channel.

        Usage: -counting <server_id> <channel_name> enable/disable
        Example: -counting 123456789 counting enable
        """
        action = action.lower()

        if not is_admin(ctx):
            embed = discord.Embed(
                title="Permission Denied",
                description=(
                    f"{ctx.author.mention} You do not have permission to use this admin command."
                ),
                color=discord.Color.red(),
            )
            msg = await ctx.send(embed=embed)
            await msg.delete(delay=30)
            return

        if action not in ["enable", "disable"]:
            await ctx.send("Action must be `enable` or `disable`.")
            return

        guild = self.bot.get_guild(server_id)
        if not guild:
            await ctx.send(f"Server with ID {server_id} not found.")
            return

        self.ensure_guild_state_loaded(guild.id)

        channel = discord.utils.find(
            lambda c: c.name == channel_name, guild.text_channels
        )
        if not channel:
            await ctx.send(f"Channel `{channel_name}` not found in that server.")
            return

        if action == "enable":
            self.counting_channels[channel.id] = {"current": 1, "last_user": None}
            self.save_channel_state(guild.id, channel.id)
            embed = discord.Embed(
                title="Counting Game Started",
                description=(
                    "Start counting from **1**!\n\n"
                    "**Rules:**\n"
                    "- Each person counts **one number**\n"
                    "- Wrong number → **restart from 1**\n"
                    "- **No consecutive counts by the same person**\n"
                    "- **One count per person per day**"
                ),
                color=discord.Color.green(),
            )
            await channel.send(embed=embed)
            await ctx.send(f"Counting game enabled in {channel.mention}.")

        else:  # disable
            if channel.id in self.counting_channels:
                del self.counting_channels[channel.id]
            self.remove_channel_state(guild.id, channel.id)
            reset_counting_stats(guild.id, channel.id)
            self.clear_all_cooldowns(guild.id)
            await ctx.send(f"Counting game disabled in {channel.mention}.")

    def get_remaining_cooldown(self, guild_id: int, user_id: int) -> timedelta:
        """Return the remaining cooldown duration for a user, or None if not on cooldown."""
        key = (guild_id, user_id)
        if key not in self.user_cooldowns:
            return None

        last_time = self.user_cooldowns[key]
        cooldown_end = last_time + timedelta(days=1)
        remaining = cooldown_end - datetime.now(timezone.utc)

        if remaining.total_seconds() <= 0:
            return None

        return remaining

    def is_user_on_cooldown(self, guild_id: int, user_id: int) -> bool:
        """Return True if the user has counted within the past 24 hours."""
        key = (guild_id, user_id)
        if key not in self.user_cooldowns:
            return False

        last_time = self.user_cooldowns[key]
        if datetime.now(timezone.utc) - last_time >= timedelta(days=1):
            return False

        return True

    def set_user_cooldown(self, guild_id: int, user_id: int):
        """Record the current time as the user's last count timestamp."""
        now = datetime.now(timezone.utc)
        self.user_cooldowns[(guild_id, user_id)] = now
        self.save_user_cooldown(guild_id, user_id, now)

    def clear_all_cooldowns(self, guild_id: int):
        """Clear all in-memory and persisted cooldowns for a guild."""
        keys_to_remove = [
            key for key in self.user_cooldowns.keys() if key[0] == guild_id
        ]
        for key in keys_to_remove:
            del self.user_cooldowns[key]

        self.clear_cooldowns_in_db(guild_id)

    def format_cooldown_time(self, remaining: timedelta) -> str:
        """Format a cooldown duration as a human-readable string."""
        total_seconds = int(remaining.total_seconds())
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60

        if hours > 0:
            return f"{hours}h {minutes}m {seconds}s"
        elif minutes > 0:
            return f"{minutes}m {seconds}s"
        else:
            return f"{seconds}s"

    def _build_reset_embed(
        self, reason_lines: list[str], guild_id: int, channel_id: int
    ) -> discord.Embed:
        """Build the reset embed, appending the leaderboard when available."""
        leaderboard = get_counting_leaderboard(guild_id, channel_id, limit=10)
        if leaderboard:
            lb_lines = [
                f"**{idx}.** <@{user_id}> - **{total_counts}** valid counts"
                for idx, (user_id, total_counts) in enumerate(leaderboard, start=1)
            ]
            reason_lines.append("\n**Counting Leaderboard:**")
            reason_lines.extend(lb_lines)

        return discord.Embed(
            title="Counting Game Reset",
            description="\n".join(reason_lines),
            color=discord.Color.blue(),
        )

    def _do_reset_channel(self, guild_id: int, channel_id: int) -> None:
        """Reset counting state, stats, and cooldowns for a channel."""
        self.clear_all_cooldowns(guild_id)
        self.remove_channel_state(guild_id, channel_id)
        reset_counting_stats(guild_id, channel_id)
        self.counting_channels[channel_id] = {"current": 1, "last_user": None}
        self.save_channel_state(guild_id, channel_id)

    @commands.Cog.listener()
    async def on_message(self, message):
        """Handle messages in counting channels."""
        if message.author.bot:
            return

        if message.guild is None:
            return

        guild_id = message.guild.id
        self.ensure_guild_state_loaded(guild_id)

        if message.channel.id not in self.counting_channels:
            return

        # Ignore bot commands
        if message.content.startswith("-"):
            return

        try:
            user_number = int(message.content.strip())
        except ValueError:
            return

        # Check cooldown
        remaining = self.get_remaining_cooldown(guild_id, message.author.id)
        if remaining is not None:
            cooldown_str = self.format_cooldown_time(remaining)
            embed = discord.Embed(
                title="On Cooldown",
                description=f"{message.author.mention} You can count again in **{cooldown_str}**.",
                color=discord.Color.orange(),
            )
            await message.reply(embed=embed, delete_after=30)
            return

        counting_state = self.counting_channels[message.channel.id]
        expected_number = counting_state["current"]
        last_user = counting_state["last_user"]

        # Consecutive count by the same user
        if last_user == message.author.id:
            await message.add_reaction(NO_EMOJI)
            reason_lines = [
                f"{message.author.mention} **cannot count twice in a row**!",
                "Restarting from **1**.",
            ]
            embed = self._build_reset_embed(reason_lines, guild_id, message.channel.id)
            await message.channel.send(embed=embed)
            self._do_reset_channel(guild_id, message.channel.id)
            return

        # Correct number
        if user_number == expected_number:
            await message.add_reaction(YES_EMOJI)
            counting_state["current"] += 1
            counting_state["last_user"] = message.author.id
            self.set_user_cooldown(guild_id, message.author.id)
            increment_counting_stat(guild_id, message.channel.id, message.author.id, 1)
            self.save_channel_state(guild_id, message.channel.id)

            if expected_number % 10 == 0:
                embed = discord.Embed(
                    title="Milestone!",
                    description=f"Reached **{expected_number}**!",
                    color=discord.Color.gold(),
                )
                await message.channel.send(embed=embed)

        # Wrong number
        else:
            await message.add_reaction(NO_EMOJI)
            reason_lines = [
                f"{message.author.mention} counted **{user_number}** but the correct number was **{expected_number}**.",
                "Restarting from **1**.",
            ]
            embed = self._build_reset_embed(reason_lines, guild_id, message.channel.id)
            await message.channel.send(embed=embed)
            self._do_reset_channel(guild_id, message.channel.id)


async def setup(bot):
    await bot.add_cog(Counting(bot))