import re
import typing
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from db.elo_db import db_connection
from utils import get_head_to_head_stats, render_compare_image_async
from utils.autocomplete import registered_name_autocomplete

ACCENT_COLOR = discord.Color.blue()


def _footer_text(last_updated: datetime) -> str:
    """Build the 'Last updated' line, mirroring the leaderboard/stats footer
    style. Uses <t:...:R> so Discord updates the relative time client-side,
    without the bot needing to re-edit the message."""
    return f"Last updated: <t:{int(last_updated.timestamp())}:R>"


class CompareRefreshButton(discord.ui.Button):
    def __init__(self, custom_view: "CompareView"):
        super().__init__(style=discord.ButtonStyle.secondary, emoji="🔄")
        self.custom_view = custom_view

    async def callback(self, interaction: discord.Interaction):
        self.custom_view.last_updated = datetime.now(timezone.utc)
        await self.custom_view.update_compare(interaction)


# CompareView renders the comparison as a Components V2 LayoutView, the same
# pattern used by leaderboard/stats: the comparison image sits inside a
# Container, with a Refresh button + "Last updated" line as a Section
# accessory right below it.
class CompareView(discord.ui.LayoutView):
    def __init__(
        self,
        cog: "Compare",
        ctx: commands.Context,
        user_id1: int,
        user_id2: int,
        mention_line: str,
    ):
        super().__init__(timeout=3600.0)
        self.cog = cog
        self.ctx = ctx
        self.user_id1 = user_id1
        self.user_id2 = user_id2
        self.mention_line = mention_line
        self.last_updated = datetime.now(timezone.utc)
        self.message = None
        self.refresh_button = CompareRefreshButton(self)

    async def on_timeout(self) -> None:
        self.refresh_button.disabled = True
        if self.message:
            try:
                await self.message.edit(
                    view=self, allowed_mentions=discord.AllowedMentions.none()
                )
            except Exception:
                pass

    def _rebuild_layout(self, filename: str) -> None:
        """Rebuild the whole layout: Container (mention line + comparison
        image + Refresh inside the frame)."""
        self.clear_items()

        container = discord.ui.Container(accent_color=ACCENT_COLOR)
        if self.mention_line:
            container.add_item(discord.ui.TextDisplay(self.mention_line))
        container.add_item(
            discord.ui.MediaGallery(discord.MediaGalleryItem(media=f"attachment://{filename}"))
        )
        container.add_item(discord.ui.Separator())
        container.add_item(
            discord.ui.Section(
                discord.ui.TextDisplay(_footer_text(self.last_updated)),
                accessory=self.refresh_button,
            )
        )
        self.add_item(container)

    async def _send_or_edit(self, interaction_or_ctx, file: discord.File) -> None:
        # Modified: mention_line is rendered as a raw TextDisplay (Components V2),
        # not inside an embed, so without allowed_mentions=none() Discord would
        # actually ping both compared users. Mirrors the same fix in StatsView.
        if isinstance(interaction_or_ctx, discord.Interaction):
            await interaction_or_ctx.response.edit_message(
                view=self, attachments=[file], allowed_mentions=discord.AllowedMentions.none()
            )
            if interaction_or_ctx.message:
                self.message = interaction_or_ctx.message
            else:
                try:
                    self.message = await interaction_or_ctx.original_response()
                except Exception:
                    pass
        else:
            self.message = await interaction_or_ctx.send(
                view=self, file=file, allowed_mentions=discord.AllowedMentions.none()
            )

    async def update_compare(self, interaction_or_ctx) -> None:
        with db_connection(self.ctx.guild.id) as (conn, cursor):
            data = self.cog._fetch_compare_data(cursor, self.user_id1, self.user_id2)

        if data is None:
            error_embed = discord.Embed(
                title="Comparison Error",
                description="One of the players is no longer registered.",
                color=discord.Color.red(),
            )
            if isinstance(interaction_or_ctx, discord.Interaction):
                await interaction_or_ctx.response.send_message(embed=error_embed, ephemeral=True)
            else:
                await interaction_or_ctx.send(embed=error_embed)
            return

        filename = "compare.png"
        buf = await render_compare_image_async(
            data["rows"],
            name1=data["name1"],
            name2=data["name2"],
            winner_text=data["winner_text"],
        )
        file = discord.File(buf, filename=filename)
        self._rebuild_layout(filename)
        await self._send_or_edit(interaction_or_ctx, file)


class Compare(commands.Cog):
    """Standalone cog for the -compare command."""

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

    def _fetch_compare_data(
        self, cursor, user_id1: int, user_id2: int
    ) -> typing.Optional[dict]:
        """Fetch fresh stats for both players and build the comparison rows
        used by the rendered image. Returns None if either player is no
        longer registered (e.g. deleted between the initial call and a
        refresh)."""

        def _load(uid: int):
            cursor.execute(
                "SELECT name, games_won, games_lost, elo FROM players WHERE id=?",
                (uid,),
            )
            return cursor.fetchone()

        row1 = _load(user_id1)
        row2 = _load(user_id2)
        if not row1 or not row2:
            return None

        name1, gw1, gl1, elo1 = row1
        name2, gw2, gl2, elo2 = row2

        total1 = gw1 + gl1
        total2 = gw2 + gl2
        win_rate1 = (gw1 / total1) * 100 if total1 > 0 else 0.0
        win_rate2 = (gw2 / total2) * 100 if total2 > 0 else 0.0

        h2h_wins1, h2h_losses1 = get_head_to_head_stats(cursor, user_id1, name2)
        h2h_wins2, _h2h_losses2 = get_head_to_head_stats(cursor, user_id2, name1)
        h2h_total = h2h_wins1 + h2h_losses1
        h2h_wr1 = (h2h_wins1 / h2h_total) * 100 if h2h_total > 0 else 0.0
        h2h_wr2 = (h2h_wins2 / h2h_total) * 100 if h2h_total > 0 else 0.0

        rows: list[tuple[str, str, str, int]] = []
        points1 = 0.0
        points2 = 0.0

        def add_row(label: str, a, b, fmt: str = "{}") -> None:
            nonlocal points1, points2
            if a > b:
                winner = 1
                points1 += 1
            elif b > a:
                winner = 2
                points2 += 1
            else:
                winner = 0
                points1 += 0.5
                points2 += 0.5
            rows.append((label, fmt.format(a), fmt.format(b), winner))

        # Overall comparisons: Elo, Total Games, Win Rate
        add_row("Elo", elo1, elo2)
        add_row("Total Games", total1, total2)
        add_row("Win Rate (%)", round(win_rate1, 2), round(win_rate2, 2), fmt="{:.2f}")

        # Head-to-Head section (doesn't count toward points)
        if h2h_total > 0:
            h2h_wins_winner = 1 if h2h_wins1 > h2h_wins2 else 2 if h2h_wins2 > h2h_wins1 else 0
            rows.append(("H2H Wins", str(h2h_wins1), str(h2h_wins2), h2h_wins_winner))

            h2h_wr_winner = 1 if h2h_wr1 > h2h_wr2 else 2 if h2h_wr2 > h2h_wr1 else 0
            rows.append(("H2H WR (%)", f"{h2h_wr1:.2f}", f"{h2h_wr2:.2f}", h2h_wr_winner))
        else:
            rows.append(("H2H Wins", "-", "-", 0))
            rows.append(("H2H WR (%)", "-", "-", 0))

        if points1 > points2:
            winner_text = f"{name1} wins with {points1:.1f} - {points2:.1f} points"
        elif points2 > points1:
            winner_text = f"{name2} wins with {points2:.1f} - {points1:.1f} points"
        else:
            winner_text = f"It's a tie! Both players have {points1:.1f} points"

        return {
            "name1": name1,
            "name2": name2,
            "rows": rows,
            "winner_text": winner_text,
        }

    @commands.hybrid_command(
        name="compare",
        aliases=["cp"],
        description="Compare two registered players.",
        with_app_command=True,
    )
    @app_commands.describe(
        player1="First player (registered name)",
        player2="Second player (registered name)",
    )
    @app_commands.autocomplete(player1=registered_name_autocomplete, player2=registered_name_autocomplete)
    @app_commands.guild_only()
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

        # require total games > 0 for both
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

        # build "Name (@mention)" for each player and join with " vs "
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
        part1 = f"{name1} ({mention1})" if mention1 else name1
        part2 = f"{name2} ({mention2})" if mention2 else name2
        mention_line = f"{part1} vs {part2}"

        # Modified: comparison result is now rendered as an image (like the
        # -history command) inside a Components V2 Container, with a Refresh
        # button + "Last updated" line below it (same pattern as -leaderboard).
        view = CompareView(self, ctx, user_id1, user_id2, mention_line)
        await view.update_compare(ctx)


async def setup(bot: commands.Bot):
    await bot.add_cog(Compare(bot))