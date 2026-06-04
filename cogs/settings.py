import discord
from discord.ext import commands

from db.elo_db import db_connection
from utils import is_admin


class SettingsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_command_error(self, ctx: commands.Context, error: Exception):
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

    @commands.command(help="Change bot language to English or Vietnamese.")
    @commands.check(is_admin)
    async def set_lan(self, ctx: commands.Context, lang: str):
        if lang.lower() not in ["en", "vn"]:
            embed = discord.Embed(
                title="Invalid Language",
                description="Use `en` for English or `vn` for Vietnamese.",
                color=discord.Color.red(),
            )
            await ctx.send(embed=embed)
            return

        with db_connection(ctx.guild.id) as (conn, cursor):
            cursor.execute(
                "INSERT INTO settings (guild_id, language) VALUES (?, ?) "
                "ON CONFLICT(guild_id) DO UPDATE SET language = ?",
                (ctx.guild.id, lang.lower(), lang.lower()),
            )
            conn.commit()

        embed = discord.Embed(
            title="Language Changed",
            description=(
                f"The bot language has been set to {'English' if lang.lower() == 'en' else 'Tiếng Việt'}."
            ),
            color=discord.Color.green(),
        )
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(SettingsCog(bot))

