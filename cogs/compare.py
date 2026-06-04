import re
import typing

import discord
from discord.ext import commands

from db.elo_db import db_connection
from utils import get_head_to_head_stats


class Compare(commands.Cog):
    """Cog tách riêng cho lệnh -compare"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _safe_fetch_mention(
        self, user_obj_or_id: typing.Union[discord.User, discord.Member, int]
    ) -> str:
        try:
            if isinstance(user_obj_or_id, (discord.User, discord.Member)):
                return user_obj_or_id.mention
            user_id = int(user_obj_or_id)
            user = await self.bot.fetch_user(user_id)
            return user.mention
        except Exception:
            return f"<@{user_obj_or_id}>"

    @commands.command(
        aliases=["cp"], help="Compare two players. Usage: -compare <player1> <player2>"
    )
    async def compare(self, ctx: commands.Context, player1: str, player2: str):
        with db_connection(ctx.guild.id) as (conn, cursor):
            await self._compare_impl(ctx, cursor, player1, player2)

    async def _compare_impl(
        self, ctx: commands.Context, cursor, player1: str, player2: str
    ) -> None:
        # if raw inputs identical -> immediate error
        if player1.strip().lower() == player2.strip().lower():
            mention = ""
            try:
                if re.fullmatch(r"\d+", player1.strip()):
                    mention = await self._safe_fetch_mention(int(player1.strip()))
                else:
                    try:
                        member = await commands.MemberConverter().convert(ctx, player1)
                        mention = await self._safe_fetch_mention(member)
                    except commands.BadArgument:
                        cursor.execute(
                            "SELECT id FROM players WHERE name=?", (player1.strip(),)
                        )
                        row = cursor.fetchone()
                        if row:
                            mention = await self._safe_fetch_mention(row[0])
            except Exception:
                mention = ""
            desc = "You cannot compare the same player. Please choose two different players."
            if mention:
                desc = f"You cannot compare the same player {mention}. Please choose two different players."
            await ctx.send(
                embed=discord.Embed(
                    title="Comparison Error",
                    description=desc,
                    color=discord.Color.red(),
                )
            )
            return

        async def get_player_data(target: str):
            # numeric id first
            try:
                uid = int(target)
                cursor.execute(
                    "SELECT name, games_won, games_lost, elo, games_played_this_month FROM players WHERE id=?",
                    (uid,),
                )
                row = cursor.fetchone()
                if row:
                    name, gw, gl, elo, monthly = row
                    user_obj = ctx.guild.get_member(uid) or (
                        await self.bot.fetch_user(uid) if True else None
                    )
                    return name, (gw, gl, elo, monthly), user_obj, uid
                return None, None, None, uid
            except ValueError:
                pass

            # member converter / mention / name search
            user_obj = None
            search_name = None
            try:
                user_obj = await commands.MemberConverter().convert(ctx, target)
            except commands.BadArgument:
                mention_match = re.match(r"<@!?(\d+)>", target)
                if mention_match:
                    uid = int(mention_match.group(1))
                    user_obj = ctx.guild.get_member(uid) or (
                        await self.bot.fetch_user(uid) if True else None
                    )
                    if user_obj is None:
                        return None, None, None, uid
                else:
                    search_name = target

            if search_name:
                cursor.execute(
                    "SELECT id, name, games_won, games_lost, elo, games_played_this_month FROM players WHERE name LIKE ?",
                    (f"%{search_name}%",),
                )
                matches = cursor.fetchall()
                if not matches:
                    await ctx.send(
                        embed=discord.Embed(
                            title="Player Not Found",
                            description=f"No player found with the name `{search_name}`.",
                            color=discord.Color.red(),
                        )
                    )
                    return None, "not_found", None, None
                if len(matches) == 1:
                    uid, name, gw, gl, elo, monthly = matches[0]
                    user_obj = ctx.guild.get_member(uid) or (
                        await self.bot.fetch_user(uid) if True else None
                    )
                    return name, (gw, gl, elo, monthly), user_obj, uid
                # multiple matches
                player_list = []
                for i, m in enumerate(matches, start=1):
                    uid, pname = m[0], m[1]
                    member = ctx.guild.get_member(uid)
                    if member:
                        mention_text = member.mention
                    else:
                        try:
                            mention_text = (await self.bot.fetch_user(uid)).mention
                        except Exception:
                            mention_text = f"<@{uid}>"
                    player_list.append(f"{i}. {pname} ({mention_text})")
                embed = discord.Embed(
                    title="Comparison Error",
                    description=f"Found {len(matches)} players with name containing '{search_name}':\n\n"
                    + "\n".join(player_list),
                    color=discord.Color.yellow(),
                )
                embed.add_field(
                    name="",
                    value="Please be more specific, use IDs or mention the player with @",
                    inline=False,
                )
                await ctx.send(embed=embed)
                return None, "multiple", None, None

            if user_obj is None:
                return None, None, None, None

            uid = getattr(user_obj, "id", None)
            if uid is None:
                return None, None, None, None

            cursor.execute(
                "SELECT name, games_won, games_lost, elo, games_played_this_month FROM players WHERE id=?",
                (uid,),
            )
            row = cursor.fetchone()
            if not row:
                return None, None, None, uid
            name, gw, gl, elo, monthly = row
            return name, (gw, gl, elo, monthly), user_obj, uid

        # fetch first player and stop early if name-search produced multiple/not-found
        name1, stats1, user1, user_id1 = await get_player_data(player1)
        if stats1 in ("multiple", "not_found"):
            return

        # fetch second player
        name2, stats2, user2, user_id2 = await get_player_data(player2)
        if stats2 in ("multiple", "not_found"):
            return

        if stats1 is None or stats2 is None:
            description = ""
            if stats1 is None and stats2 is None:
                if user_id1 and user_id2:
                    description = f"{await self._safe_fetch_mention(user_id1)}, {await self._safe_fetch_mention(user_id2)} are not registered. Use `-register <name>` to register."
                else:
                    description = "Both players are not registered. Use `-register <name>` to register."
            elif stats1 is None:
                if user_id1:
                    description = f"{await self._safe_fetch_mention(user_id1)} is not registered. Use `-register <name>` to register."
                else:
                    description = "The first player is not registered. Use `-register <name>` to register."
            else:
                if user_id2:
                    description = f"{await self._safe_fetch_mention(user_id2)} is not registered. Use `-register <name>` to register."
                else:
                    description = "The second player is not registered. Use `-register <name>` to register."
            await ctx.send(
                embed=discord.Embed(
                    title="Comparison Error",
                    description=description,
                    color=discord.Color.red(),
                )
            )
            return

        gw1, gl1, elo1, monthly1 = stats1
        gw2, gl2, elo2, monthly2 = stats2

        # check identical by resolved ids
        if (user_id1 is not None and user_id2 is not None and user_id1 == user_id2) or (
            user1 is not None
            and user2 is not None
            and getattr(user1, "id", None) == getattr(user2, "id", None)
        ):
            mention = await self._safe_fetch_mention(
                user_id1 or (user1.id if user1 else None)
            )
            desc = "You cannot compare the same player. Please choose two different players."
            if mention:
                desc = f"You cannot compare the same player {mention}. Please choose two different players."
            await ctx.send(
                embed=discord.Embed(
                    title="Comparison Error",
                    description=desc,
                    color=discord.Color.red(),
                )
            )
            return

        h2h_wins1, h2h_losses1 = get_head_to_head_stats(cursor, user_id1, name2)
        h2h_wins2, h2h_losses2 = get_head_to_head_stats(cursor, user_id2, name1)
        h2h_total_games = h2h_wins1 + h2h_losses1

        # compute totals and require total games > 0 for both
        total1 = gw1 + gl1
        total2 = gw2 + gl2
        if total1 == 0 or total2 == 0:
            lines = []
            if total1 == 0:
                mention1 = ""
                if user1 is not None or user_id1:
                    mention1 = await self._safe_fetch_mention(
                        user1.id if user1 is not None else user_id1
                    )
                lines.append(
                    f"{name1} ({mention1}) hasn't played any sets. Cannot compare."
                )
            if total2 == 0:
                mention2 = ""
                if user2 is not None or user_id2:
                    mention2 = await self._safe_fetch_mention(
                        user2.id if user2 is not None else user_id2
                    )
                lines.append(
                    f"{name2} ({mention2}) hasn't played any sets. Cannot compare."
                )
            await ctx.send(
                embed=discord.Embed(
                    title="Comparison Error",
                    description="\n".join(lines),
                    color=discord.Color.red(),
                )
            )
            return

        win_rate1 = (gw1 / total1) * 100 if total1 > 0 else 0
        win_rate2 = (gw2 / total2) * 100 if total2 > 0 else 0

        player1_points = 0.0
        player2_points = 0.0
        lines: typing.List[str] = []

        # build mentions early and place at top, use " vs " between mentions
        mention1 = (
            await self._safe_fetch_mention(user1.id if user1 is not None else user_id1)
            if (user1 or user_id1)
            else ""
        )
        mention2 = (
            await self._safe_fetch_mention(user2.id if user2 is not None else user_id2)
            if (user2 or user_id2)
            else ""
        )
        mention_line = ""
        if mention1 or mention2:
            mention_line = " vs ".join([m for m in (mention1, mention2) if m])

        def format_stat(label: str, a, b, higher_better: bool = True):
            nonlocal player1_points, player2_points
            if higher_better:
                if a > b:
                    lines.append(f"{label}: **{a}** - {b}")
                    player1_points += 1
                elif b > a:
                    lines.append(f"{label}: {a} - **{b}**")
                    player2_points += 1
                else:
                    lines.append(f"{label}: **{a}** - **{b}**")
                    player1_points += 0.5
                    player2_points += 0.5
            else:
                if a < b:
                    lines.append(f"{label}: **{a}** - {b}")
                    player1_points += 1
                elif b < a:
                    lines.append(f"{label}: {a} - **{b}**")
                    player2_points += 1
                else:
                    lines.append(f"{label}: **{a}** - **{b}**")
                    player1_points += 0.5
                    player2_points += 0.5

        # Overall comparisons: Elo, Total Games (wins+losses), Win Rate
        format_stat("Elo", elo1, elo2, higher_better=True)
        format_stat("Total Games", total1, total2, higher_better=True)

        if win_rate1 > win_rate2:
            lines.append(f"Win Rate (%): **{win_rate1:.2f}** - {win_rate2:.2f}")
            player1_points += 1
        elif win_rate2 > win_rate1:
            lines.append(f"Win Rate (%): {win_rate1:.2f} - **{win_rate2:.2f}**")
            player2_points += 1
        else:
            lines.append(f"Win Rate (%): **{win_rate1:.2f}** - **{win_rate2:.2f}**")
            player1_points += 0.5
            player2_points += 0.5

        description = ""
        if mention_line:
            description += mention_line + "\n"
        description += "**Overall**\n"
        description += "\n".join(lines)

        # Head-to-Head section (doesn't count points)
        description += "\n\n**Head-to-Head**\n"

        if h2h_total_games > 0:
            # Head-to-Head Wins
            if h2h_wins1 > h2h_wins2:
                description += f"Head-to-Head Wins: **{h2h_wins1}** - {h2h_wins2}\n"
            elif h2h_wins2 > h2h_wins1:
                description += f"Head-to-Head Wins: {h2h_wins1} - **{h2h_wins2}**\n"
            else:
                description += f"Head-to-Head Wins: **{h2h_wins1}** - **{h2h_wins2}**\n"

            # Head-to-Head WR
            h2h_wr1 = (h2h_wins1 / h2h_total_games) * 100
            h2h_wr2 = (h2h_wins2 / h2h_total_games) * 100

            if h2h_wr1 > h2h_wr2:
                description += f"Head-to-Head WR (%): **{h2h_wr1:.2f}** - {h2h_wr2:.2f}"
            elif h2h_wr2 > h2h_wr1:
                description += f"Head-to-Head WR (%): {h2h_wr1:.2f} - **{h2h_wr2:.2f}**"
            else:
                description += (
                    f"Head-to-Head WR (%): **{h2h_wr1:.2f}** - **{h2h_wr2:.2f}**"
                )
        else:
            description += "Head-to-Head Wins: -\nHead-to-Head WR (%): -"

        if player1_points > player2_points:
            winner_text = f"**{name1}** ({mention1}) wins with {player1_points:.1f} points - {player2_points:.1f} points"
        elif player2_points > player1_points:
            winner_text = f"**{name2}** ({mention2}) wins with {player2_points:.1f} points - {player1_points:.1f} points"
        else:
            winner_text = f"It's a tie! Both players have {player1_points:.1f} points"
        description += f"\n\n{winner_text}"

        await ctx.send(
            embed=discord.Embed(
                title="Player Comparison",
                description=description,
                color=discord.Color.blue(),
            )
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Compare(bot))
