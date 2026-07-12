"""/msg: a DM-only slash command that lets any user send a message straight
to the bot's private/management guild. Each sender gets their own thread
(named after their username) under the channel configured via -support, so
staff can hold a normal back-and-forth conversation with them.

Instead of an ephemeral "message sent" confirmation, the bot replies with a
plain-text receipt that repeats what was relayed. The user can then reply
directly to that receipt (in DMs) to keep the conversation going - including
attaching files or images - without having to run /msg again. Staff can also
reply from inside the thread and it gets relayed back to the user's DMs the
same way.

Only the very first message of a session (the /msg call) posts the small
"who is this" embed into the thread; every later message in that session is
just the highlighted text, so the thread doesn't get spammed with repeated
author cards. While a session is active, /msg itself is locked to one call -
the user has to reply to keep going, or wait for the session to end before
opening a new one. Users banned via -ban_msg can't use /msg at all until an
admin runs -unban_msg on them.
"""

from __future__ import annotations

import asyncio

import discord
from discord import app_commands
from discord.ext import commands

from db.elo_db import (
    get_msg_ban_reason,
    get_support_channel_id,
    get_support_thread_id,
    get_support_user_id_by_thread,
    set_support_notify_message_id,
    set_support_thread_id,
)
from utils.logger import PRIVATE_GUILD_ID, bot_log

_THREAD_AUTO_ARCHIVE_MINUTES = 10080  # 7 days

# A conversation session (started by /msg) stays open for 30 minutes of
# inactivity. Every new message - the initial /msg, a reply from the user,
# or a reply from staff in the thread - refreshes the countdown back to 30
# minutes. Once it lapses, the receipt stops accepting replies and the user
# has to run /msg again to open a new session.
_SESSION_TIMEOUT_SECONDS = 30 * 60
_SESSION_EXPIRED_TEXT = (
    "**Conversation Session Ended**\n"
    "Your current conversation with Senko has ended. "
    "Please use `/msg` again to continue."
)
_SESSION_ACTIVE_TEXT = (
    "**Conversation Already Active**\n"
    "You already have an open conversation with Senko. Reply to the previous "
    "message to keep talking, or wait for the current session to end before "
    "running `/msg` again."
)

_CONTINUE_HINT = "*Reply to this message to keep the conversation going.*"
# Embed footers don't render markdown, so keep a plain copy for use there.
_CONTINUE_HINT_PLAIN = "Reply to this message to keep the conversation going."
_RECEIPT_HEADER = "Your message has been sent to Senko:"
_RECEIPT_HEADER_WITH_FILES = "Your message and files have been sent to Senko:"


class MsgCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # user_id -> the running 30-minute countdown task for their session.
        # In-memory only: a bot restart implicitly ends any open sessions,
        # which is fine since the countdown would have interrupted the
        # conversation anyway.
        self._session_tasks: dict[int, asyncio.Task] = {}
        # user_id set: whether the "who is replying" identity embed has
        # already been shown to this user in their current session. This
        # first-message-only behaviour is specific to the staff -> user
        # direction; every user -> staff message always embeds (see
        # `_relay_to_thread`).
        self._staff_greeted: set[int] = set()

    def cog_unload(self) -> None:
        for task in self._session_tasks.values():
            if not task.done():
                task.cancel()
        self._session_tasks.clear()
        self._staff_greeted.clear()

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    async def _get_support_channel(self) -> discord.TextChannel | None:
        support_channel_id = get_support_channel_id(PRIVATE_GUILD_ID)
        if support_channel_id is None:
            return None
        channel = self.bot.get_channel(support_channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(support_channel_id)
            except Exception:
                channel = None
        return channel if isinstance(channel, discord.TextChannel) else None

    async def _get_or_create_thread(
        self, channel: discord.TextChannel, user: discord.User | discord.Member
    ) -> discord.Thread | None:
        """Return the existing support thread for this user, creating a new
        one (named after their username) if none exists yet or the
        previously stored thread is no longer reachable."""
        thread_id = get_support_thread_id(PRIVATE_GUILD_ID, user.id)
        if thread_id is not None:
            thread = channel.guild.get_thread(thread_id)
            if thread is None:
                try:
                    fetched = await self.bot.fetch_channel(thread_id)
                    if isinstance(fetched, discord.Thread):
                        thread = fetched
                except Exception:
                    thread = None
            if thread is not None:
                if thread.archived:
                    try:
                        await thread.edit(archived=False)
                    except Exception:
                        pass
                return thread

        try:
            thread = await channel.create_thread(
                name=user.name[:100],
                type=discord.ChannelType.public_thread,
                auto_archive_duration=_THREAD_AUTO_ARCHIVE_MINUTES,
            )
        except Exception:
            return None

        set_support_thread_id(PRIVATE_GUILD_ID, user.id, thread.id)
        return thread

    @staticmethod
    def _highlight(content: str) -> str:
        """Wrap the sender's message so it stands out in the thread, per the
        single-backtick inline-code style. Falls back to a fenced code block
        for multi-line text or text that itself contains a backtick, so the
        formatting doesn't break."""
        if not content:
            return ""
        if "\n" in content or "`" in content:
            return f"```\n{content}\n```"
        return f"`{content}`"

    _IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".webp")

    @classmethod
    def _embed_first_image(cls, embed: discord.Embed, files: list[discord.File]) -> None:
        """If any of the files being sent alongside this embed looks like
        an image, render it inside the embed frame (via Discord's
        attachment:// reference) instead of as a separate attachment
        block underneath. Only one image can live inside a single embed,
        so the rest - if any - still get sent as regular attachments next
        to it. The file must be included in the same message's `files=`
        list for the attachment:// reference to resolve."""
        for file in files:
            filename = (file.filename or "").lower()
            if filename.endswith(cls._IMAGE_EXTENSIONS):
                embed.set_image(url=f"attachment://{file.filename}")
                return

    @staticmethod
    def _staff_reply_embed(message: discord.Message, content: str) -> discord.Embed:
        """Build the identity embed shown for the first staff reply of a
        session (staff -> user direction). Author name is the replier's
        Discord display name only - no username, no ID. Unlike
        `_highlight`, which is reserved for narrating the user's own /msg
        messages, this embed's description is left as plain text on
        purpose. The footer carries the "reply to continue" hint on its
        own line, with the replier's raw username underneath - Discord
        appends its automatic timestamp to that last line, giving:
        Reply to this message to keep the conversation going - ...
        from someuser - Today at 00:17"""
        embed = discord.Embed(
            description=content or None,
            color=discord.Color.blurple(),
            timestamp=discord.utils.utcnow(),
        )
        embed.set_author(
            name=message.author.display_name,
            icon_url=message.author.display_avatar.url,
        )
        embed.set_footer(text=f"{_CONTINUE_HINT_PLAIN}\nfrom {message.author.name}")
        return embed

    async def _relay_to_thread(
        self,
        thread: discord.Thread,
        user: discord.User | discord.Member,
        content: str,
        attachments: list[discord.Attachment] | None = None,
    ) -> bool:
        """Forward one message from the user into their support thread as
        an embed showing their avatar and ID, so staff can always see who
        it's from regardless of which message in the session it is. Any
        first image attachment renders inside the embed itself; the rest
        (if any) still come through as regular attachments alongside it."""
        files: list[discord.File] = []
        for attachment in attachments or []:
            try:
                files.append(await attachment.to_file())
            except Exception:
                continue

        try:
            embed = discord.Embed(
                description=self._highlight(content) or None,
                color=discord.Color.blurple(),
                timestamp=discord.utils.utcnow(),
            )
            embed.set_author(
                name=f"{user} ({user.id})",
                icon_url=user.display_avatar.url,
            )
            self._embed_first_image(embed, files)
            await thread.send(embed=embed, files=files)
        except Exception:
            return False
        return True

    def _refresh_session(self, user_id: int, channel: discord.abc.Messageable) -> None:
        """(Re)start the 30-minute countdown for this user's session,
        cancelling whatever countdown was already running for them."""
        existing = self._session_tasks.pop(user_id, None)
        if existing is not None and not existing.done():
            existing.cancel()
        self._session_tasks[user_id] = asyncio.create_task(
            self._expire_session_later(user_id, channel)
        )

    def _has_active_session(self, user_id: int) -> bool:
        task = self._session_tasks.get(user_id)
        return task is not None and not task.done()

    async def _expire_session_later(
        self, user_id: int, channel: discord.abc.Messageable
    ) -> None:
        try:
            await asyncio.sleep(_SESSION_TIMEOUT_SECONDS)
        except asyncio.CancelledError:
            return

        # Session timed out: forget the receipt so a stray reply to it no
        # longer relays, then let the user know they need to run /msg again.
        set_support_notify_message_id(PRIVATE_GUILD_ID, user_id, None)
        self._session_tasks.pop(user_id, None)
        self._staff_greeted.discard(user_id)
        try:
            await channel.send(_SESSION_EXPIRED_TEXT)
        except Exception:
            pass

    @staticmethod
    def _build_receipt_embed(content: str, *, has_attachments: bool = False) -> discord.Embed:
        """Build the confirmation embed shown to the /msg user, echoing
        back their own message. Embeds allow far more room than a plain
        message, but the content is still bounded by Discord's normal
        message length anyway, so a generous cap is enough. The echoed
        content is highlighted the same way it's narrated elsewhere, and
        the footer carries the shortened reply-to-continue hint."""
        shown_content = content
        if len(shown_content) > 4000:  # embed description hard cap is 4096
            shown_content = shown_content[:3999].rstrip() + "..."
        echoed = MsgCog._highlight(shown_content) if shown_content else "*(attachment only)*"
        title = _RECEIPT_HEADER_WITH_FILES if has_attachments else _RECEIPT_HEADER
        embed = discord.Embed(
            title=title,
            description=echoed,
            color=discord.Color.blurple(),
            timestamp=discord.utils.utcnow(),
        )
        embed.set_footer(text=_CONTINUE_HINT_PLAIN)
        return embed

    @staticmethod
    def _delivery_confirmation_embed(
        recipient: discord.User | discord.Member, content: str
    ) -> discord.Embed:
        """Build the confirmation embed posted back into the support
        thread once a staff reply has been relayed to the user's DMs.
        The author is the /msg user's own avatar and display name, so
        staff can see at a glance who the message went to; the echoed
        content is highlighted the same way content is narrated back to
        its own sender elsewhere."""
        echoed = MsgCog._highlight(content) if content else "*(attachment only)*"
        embed = discord.Embed(
            description=echoed,
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow(),
        )
        embed.set_author(
            name=recipient.display_name,
            icon_url=recipient.display_avatar.url,
        )
        return embed

    # ------------------------------------------------------------------
    # /msg slash command
    # ------------------------------------------------------------------

    @app_commands.command(
        name="msg",
        description="Send a private message to Senko.",
    )
    @app_commands.describe(content="The message you want to send")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=False, dms=True, private_channels=True)
    async def msg(
        self, interaction: discord.Interaction, content: app_commands.Range[str, 1, 1800]
    ) -> None:
        ban_reason = get_msg_ban_reason(PRIVATE_GUILD_ID, interaction.user.id)
        if ban_reason is not None:
            await interaction.response.send_message(
                f"**You Are Banned**\nYou are currently banned from using this command.\n"
                f"Reason: {ban_reason}",
                ephemeral=True,
            )
            return

        if self._has_active_session(interaction.user.id):
            await interaction.response.send_message(_SESSION_ACTIVE_TEXT, ephemeral=True)
            return

        channel = await self._get_support_channel()
        if channel is None:
            await interaction.response.send_message(
                "**Support Not Available**\nThe support system has not been set up yet.",
                ephemeral=True,
            )
            return

        thread = await self._get_or_create_thread(channel, interaction.user)
        if thread is None:
            await interaction.response.send_message(
                "**Message Not Sent**\nCould not create or reach your support thread. "
                "Please try again later.",
                ephemeral=True,
            )
            return

        relayed = await self._relay_to_thread(thread, interaction.user, content)
        if not relayed:
            await interaction.response.send_message(
                "**Message Not Sent**\nSomething went wrong while relaying your message. "
                "Please try again later.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(embed=self._build_receipt_embed(content))
        try:
            receipt = await interaction.original_response()
            set_support_notify_message_id(PRIVATE_GUILD_ID, interaction.user.id, receipt.id)
            self._staff_greeted.discard(interaction.user.id)
            self._refresh_session(interaction.user.id, interaction.channel)
        except Exception:
            pass

        await bot_log(
            self.bot,
            0,
            "Command used: /msg",
            level="INFO",
            fields={
                "User": f"{interaction.user} ({interaction.user.id})",
                "Thread": thread.mention,
            },
        )

    # ------------------------------------------------------------------
    # Reply-to-continue and staff relay, both driven off on_message
    # ------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return

        if isinstance(message.channel, discord.DMChannel):
            await self._handle_user_reply(message)
        elif isinstance(message.channel, discord.Thread):
            await self._handle_staff_reply(message)

    async def _handle_user_reply(self, message: discord.Message) -> None:
        """A user replied to any message in their own DMs while their
        /msg session is still active: relay it into their support thread
        and refresh their session. Every message sent during an active
        session is a valid reply target - it's the session's 30-minute
        cooldown that governs whether replying still works, not which
        specific message got replied to. Once the cooldown lapses, no
        reply (to any message) relays anymore until /msg is run again."""
        if message.reference is None or message.reference.message_id is None:
            return
        if not message.content and not message.attachments:
            return

        if not self._has_active_session(message.author.id):
            return

        ban_reason = get_msg_ban_reason(PRIVATE_GUILD_ID, message.author.id)
        if ban_reason is not None:
            await message.channel.send(
                f"**You Are Banned**\nYou are currently banned from using this command.\n"
                f"Reason: {ban_reason}"
            )
            return

        channel = await self._get_support_channel()
        if channel is None:
            await message.channel.send(
                "**Message Not Sent**\nThe support system has not been set up yet."
            )
            return

        thread = await self._get_or_create_thread(channel, message.author)
        if thread is None:
            await message.channel.send(
                "**Message Not Sent**\nCould not reach your support thread. "
                "Please try again later."
            )
            return

        relayed = await self._relay_to_thread(
            thread, message.author, message.content, message.attachments
        )
        if not relayed:
            await message.channel.send(
                "**Message Not Sent**\nSomething went wrong while relaying your message. "
                "Please try again later."
            )
            return

        receipt = await message.channel.send(
            embed=self._build_receipt_embed(
                message.content, has_attachments=bool(message.attachments)
            )
        )
        set_support_notify_message_id(PRIVATE_GUILD_ID, message.author.id, receipt.id)
        self._refresh_session(message.author.id, message.channel)

        await bot_log(
            self.bot,
            0,
            "Command used: /msg (reply)",
            level="INFO",
            fields={
                "User": f"{message.author} ({message.author.id})",
                "Thread": thread.mention,
            },
        )

    async def _handle_staff_reply(self, message: discord.Message) -> None:
        """Staff replied inside a user's support thread: relay the content
        straight to that user's DMs, and refresh their session so they can
        keep replying too."""
        if not message.content and not message.attachments:
            return

        support_channel_id = get_support_channel_id(PRIVATE_GUILD_ID)
        if support_channel_id is None or message.channel.parent_id != support_channel_id:
            return

        user_id = get_support_user_id_by_thread(PRIVATE_GUILD_ID, message.channel.id)
        if user_id is None:
            return

        try:
            user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
            dm_channel = user.dm_channel or await user.create_dm()
        except Exception:
            return

        files: list[discord.File] = []
        for attachment in message.attachments:
            try:
                files.append(await attachment.to_file())
            except Exception:
                continue

        body = message.content or "*(attachment only)*"
        is_first_reply = user_id not in self._staff_greeted

        try:
            if is_first_reply:
                embed = self._staff_reply_embed(message, body)
                self._embed_first_image(embed, files)
                receipt = await dm_channel.send(embed=embed, files=files)
            else:
                receipt = await dm_channel.send(
                    f"{self._highlight(body)}\n\n{_CONTINUE_HINT}", files=files
                )
        except Exception:
            return

        self._staff_greeted.add(user_id)
        set_support_notify_message_id(PRIVATE_GUILD_ID, user_id, receipt.id)
        self._refresh_session(user_id, dm_channel)

        try:
            await message.channel.send(
                embed=self._delivery_confirmation_embed(user, message.content)
            )
        except Exception:
            pass

        await bot_log(
            self.bot,
            0,
            "Staff replied to /msg user",
            level="INFO",
            fields={
                "Staff": f"{message.author} ({message.author.id})",
                "User": f"{user} ({user.id})",
                "Thread": message.channel.mention,
            },
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(MsgCog(bot))