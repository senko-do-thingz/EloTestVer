import time

import discord
from discord.ext import commands

from db.elo_db import get_dev_mode
from utils.logger import bot_log, PRIVATE_GUILD_ID


class PrivateStatusCog(commands.Cog, name="PrivateStatus"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="status", hidden=True, help="Show bot status: ping, servers, devmode (private only).")
    async def status(self, ctx: commands.Context):
        """Print full bot status — ping, all servers the bot is in, and devmode state per server."""
        if ctx.guild is None or ctx.guild.id != PRIVATE_GUILD_ID:
            await ctx.send(
                embed=discord.Embed(
                    title="Not Available",
                    description="This command is only available in the private server.",
                    color=discord.Color.red(),
                )
            )
            return

        # Measure round-trip latency
        start = time.perf_counter()
        msg = await ctx.send("…")
        ping_ms = (time.perf_counter() - start) * 1000
        ws_ms = self.bot.latency * 1000

        guilds = sorted(self.bot.guilds, key=lambda g: g.name.lower())

        # Build server list with devmode indicator
        lines: list[str] = []
        for g in guilds:
            try:
                dev = get_dev_mode(g.id)
            except Exception:
                dev = False
            dev_tag = " 🔧" if dev else ""
            lines.append(f"`{g.id}` **{g.name}**{dev_tag} — {g.member_count} members")

        server_block = "\n".join(lines) if lines else "*(no servers)*"

        embed = discord.Embed(
            title="Bot Status",
            color=discord.Color.green(),
        )
        embed.add_field(name="WebSocket latency", value=f"{ws_ms:.1f} ms", inline=True)
        embed.add_field(name="Round-trip ping", value=f"{ping_ms:.1f} ms", inline=True)
        embed.add_field(name="Servers", value=str(len(guilds)), inline=True)
        embed.add_field(
            name="Server list  (🔧 = devmode on)",
            value=server_block[:1024],  # embed field limit
            inline=False,
        )

        await msg.edit(content=None, embed=embed)

        await bot_log(
            ctx.bot,
            ctx.guild.id if ctx.guild else 0,
            "Command used: -status",
            level="INFO",
            fields={
                "User": f"{ctx.author.mention} ({ctx.author.id})",
                "WS latency": f"{ws_ms:.1f} ms",
                "Servers": str(len(guilds)),
            },
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(PrivateStatusCog(bot))