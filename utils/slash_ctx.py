"""Lightweight context adapter so slash and prefix commands share implementations."""

from __future__ import annotations

import discord
from discord.ext import commands


class SlashContext:
    """Minimal Context-like wrapper for app command handlers."""

    def __init__(
        self,
        interaction: discord.Interaction,
        *,
        command: commands.Command | None = None,
    ):
        self.interaction = interaction
        self.bot = interaction.client
        self.guild = interaction.guild
        self.author = interaction.user
        self.channel = interaction.channel
        self.message = None
        self.command = command
        self.invoked_with = command.name if command else None
        self.prefix = "/"

    async def send(self, *args, **kwargs):
        if not self.interaction.response.is_done():
            # NOTE: interaction.response.send_message() returns an
            # InteractionCallbackResponse (discord.py 2.4+), NOT a discord.Message.
            # Callers (e.g. reporting.py's _ad_rp_impl) call .add_reaction() on the
            # returned object, which only exists on discord.Message. Fetch the real
            # Message via original_response() so send() behaves like commands.Context.send().
            await self.interaction.response.send_message(*args, **kwargs)
            return await self.interaction.original_response()
        return await self.interaction.followup.send(*args, **kwargs)

    async def defer(self, **kwargs):
        if not self.interaction.response.is_done():
            await self.interaction.response.defer(**kwargs)


def as_context(ctx_or_interaction: commands.Context | discord.Interaction) -> commands.Context | SlashContext:
    if isinstance(ctx_or_interaction, discord.Interaction):
        return SlashContext(ctx_or_interaction)
    return ctx_or_interaction