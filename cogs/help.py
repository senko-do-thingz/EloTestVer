import discord
from discord.ext import commands

from db import get_seasonal_report_enabled


class HelpCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(
        name="qna",
        help="Hiển thị danh sách câu hỏi thường gặp / Show frequently asked questions.",
    )
    async def qna(self, ctx: commands.Context):
        faqs = {
            "vn": [
                (
                    "**Q: Tôi đã nhập một câu lệnh hợp lệ, nhưng không có gì xảy ra.**",
                    "A: Có thể bạn đã nhập lệnh ở sai kênh, bot chỉ được gửi tin nhắn ở các kênh mà bot được cho phép.",
                ),
                (
                    "**Q: Bot tính điểm Elo dựa trên công thức nào?**",
                    "A: Công thức của Autony.",
                ),
                (
                    "**Q: Tại sao đôi lúc bot không hoạt động?**",
                    "A: Bot sẽ tự đóng sau một khoảng thời gian để tránh quá tải, thường là từ 10 đến 15 phút.",
                ),
                (
                    "**Q: Tại sao một số lệnh không còn hoạt động và hiện <This interaction failed> sau một thời gian?**",
                    "A: Vì nếu các lệnh đó chạy liên tục sẽ gây ra tình trạng quá tải cho bot, vì thế bot tự động đóng nó và chờ lần sử dụng tiếp theo",
                ),
                (
                    "**Q: Tôi có thể xem mã nguồn của bot không?**",
                    "A: Không, dev không muốn công khai nó.",
                ),
                (
                    "**Q: Bot có được cập nhật để hỗ trợ bo3 không?**",
                    "A: Không, bo3 không được công bằng (nếu bạn cho dev 1000$ thì hắn sẽ nghĩ lại).",
                ),
                ("**Q: Bot được viết trong ngôn ngữ nào?**", "A: Python."),
                ("**Q: Dev có ngu không?**", "A: Có, dev ngu như bò."),
            ],
            "en": [
                (
                    "**Q: I typed a valid command, but nothing happened.**",
                    "A: Maybe you typed the command in the wrong channel, the bot can only send messages in allowed channels.",
                ),
                (
                    "**Q: What formula does the bot use to calculate Elo scores?**",
                    "A: Autony's formula.",
                ),
                (
                    "**Q: Why does the bot sometimes not work?**",
                    "A: The bot will automatically close after a period of time to avoid overloading, usually from 10 to 15 minutes.",
                ),
                (
                    "**Q: Why do some commands stop working and show <This interaction failed> after a while?**",
                    "A: Because if those commands run continuously, it will cause the bot to overload, so the bot automatically closes it and waits for the next use.",
                ),
                (
                    "**Q: Can I see the bot's source code?**",
                    "A: No, the dev doesn't want to make it public.",
                ),
                (
                    "**Q: Will the bot be updated to support bo3?**",
                    "A: No, bo3 is not fair (if you gave the dev $1000, he would think again).",
                ),
                (
                    "**Q: What programming language is this bot coded in?**",
                    "A: Python.",
                ),
                ("**Q: Is the dev stupid?**", "A: Yes, the dev is stupid as hell."),
            ],
        }

        def create_embed(language):
            embed = discord.Embed(
                title=(
                    "📖 Câu Hỏi Thường Gặp"
                    if language == "vn"
                    else "📖 Frequently Asked Questions"
                ),
                color=discord.Color.purple(),
            )
            for question, answer in faqs[language]:
                embed.add_field(name=question, value=answer, inline=False)
            return embed

        class LanguageSelect(discord.ui.View):
            def __init__(self):
                super().__init__()
                self.language = "en"

            @discord.ui.select(
                placeholder="Chọn ngôn ngữ / Choose language",
                options=[
                    discord.SelectOption(label="English", value="en", emoji="🇺🇸"),
                    discord.SelectOption(label="Tiếng Việt", value="vn", emoji="🇻🇳"),
                ],
            )
            async def select_callback(
                self, interaction: discord.Interaction, select: discord.ui.Select
            ):
                self.language = select.values[0]
                await interaction.response.edit_message(
                    embed=create_embed(self.language)
                )

        view = LanguageSelect()
        await ctx.send(embed=create_embed("en"), view=view)

    @commands.command(name="help", help="Show available commands.")
    async def help_command(self, ctx: commands.Context):
        commands_list = [
            ("-qna", "Show frequently asked questions."),
            ("-register <name>", "Register with a custom name and initial 500 Elo."),
            ("-rename <new_name>", "Change your name (7-day cooldown)."),
            ("-report / -rp <@player1> <score> <@player2>", "Report a match result."),
        ]
        if get_seasonal_report_enabled(ctx.guild.id):
            commands_list.append(
                (
                    "-seasonal_report / -srp <@player1> <score> <@player2>",
                    "Report a seasonal match (1.5x Elo). Same usage as -report.",
                ),
            )
        commands_list.extend(
            [
                (
                    "-leaderboard / lb",
                    "Show the leaderboard of players based on various stats.",
                ),
                ("-lbs", "Show the leaderboards for each season."),
                (
                    "-stats / -stats <player>",
                    "Show your stats / Show the mentioned player's stats. <player> can be @mention, Discord ID, or registered name.",
                ),
                (
                    "-compare / -cp <player1> <player2>",
                    "Compare two players. <player> can be @mention, Discord ID, or registered name.",
                ),
                (
                    "-leagues / -leagues <name>",
                    "Show discovered leagues, Elo ranges, and player counts. "
                    "Add a name (substring match) to list everyone in that league.",
                ),
                ("-help", "Show this help message."),
                ("-adhelp", "Show the admin help message."),
            ]
        )

        def create_embed():
            embed = discord.Embed(
                title="📜 Help - General Commands",
                description="Available commands:",
                color=discord.Color.blue(),
            )
            for name, desc in commands_list:
                embed.add_field(name=name, value=desc, inline=False)
            return embed

        await ctx.send(embed=create_embed())

    @commands.command(name="adhelp", help="Show admin-only commands.")
    async def admin_help_command(self, ctx: commands.Context):
        stats_commands = [
            (
                "-decay",
                "Automatically **increase** or **decrease** Elo for all players.",
            ),
            ("-reset_e", "Set **Elo** for all players to 500."),
            ("-set_e <@player> <elo>", "Set **Elo** for a specific player."),
            (
                "-set_eid <discord_id> <elo>",
                "Set **Elo** for a specific player using their Discord ID.",
            ),
            (
                "-set_w <@player> <wins>",
                "Set the **win count** of a specific player.",
            ),
            (
                "-set_wid <discord_id> <wins>",
                "Set the **win count** of a specific player using their Discord ID.",
            ),
            (
                "-set_l <@player> <losses>",
                "Set the **loss count** of a specific player.",
            ),
            (
                "-set_lid <discord_id> <losses>",
                "Set the **loss count** of a specific player using their Discord ID.",
            ),
            (
                "-set_gid <discord_id> <games_played>",
                "Set the **monthly games played** for a player using their Discord ID.",
            ),
            (
                "-set_stats <discord_id> <elo> <wins> <losses> <games_played>",
                "Set all **stats** for a player using their Discord ID.",
            ),
            (
                "-ad_rp <id1> <score1> <score2> <id2>  |  -ad_rp <id1> <score> <id2>  (score: 4-3, 3:1, 43)",
                "Report a game result between two players using their Discord IDs.",
            ),
        ]
        if get_seasonal_report_enabled(ctx.guild.id):
            stats_commands.append(
                (
                    "-ad_srp <winner_discord_id> <winner_score> <loser_score> <loser_discord_id>",
                    "Report a seasonal match (1.5x Elo) using Discord IDs. Same args as -ad_rp.",
                ),
            )
        stats_commands.append(
            (
                "-gimmick on | off",
                "Enable or disable seasonal report (1.5x Elo) for this server. When on, members can use -seasonal_report / -srp.",
            ),
        )
        admin_commands = {
            "register": [
                (
                    "-register_o <@player> <name>",
                    "Register another player with a custom name.",
                ),
                (
                    "-register_oid <discord_id> <name>",
                    "Register another player using their Discord ID with a custom name.",
                ),
                (
                    "-rename_o <@player> <new_name>",
                    "Rename another player (no cooldown).",
                ),
                (
                    "-rename_oid <discord_id> <new_name>",
                    "Rename another player by Discord ID (no cooldown).",
                ),
                ("-d_reg <@player>", "Delete a player's registration."),
                (
                    "-did_reg <discord_id>",
                    "Delete a player's registration using their Discord ID.",
                ),
                ("-c_reg", "Clear all registered player data."),
                (
                    "-db_d <server_id> <id>",
                    "Delete a player's data using their ID stored in the Database.",
                ),
                ("-get_lb", "Get the leaderboard from the Database."),
                (
                    "-set_id <server_id>",
                    "Sync all registered players' Discord IDs into archived lb/<server_id>/ files.",
                ),
                (
                    "-sum_lb <season>",
                    "Calculate total leaderboard for a specific season (e.g., S2, Season 2).",
                ),
            ],
            "stats": stats_commands,
        }

        class AdminHelpView(discord.ui.View):
            def __init__(self):
                super().__init__()
                self.page = 0
                self.total_pages = 2
                self.update_buttons()

            def update_buttons(self):
                for child in self.children:
                    if isinstance(child, PreviousPageButton):
                        child.disabled = self.page == 0
                    elif isinstance(child, NextPageButton):
                        child.disabled = self.page >= self.total_pages - 1

            async def update_help(self, interaction):
                embed = self.create_embed()
                self.update_buttons()
                await interaction.response.edit_message(embed=embed, view=self)

            def create_embed(self):
                page_type = "register" if self.page == 0 else "stats"
                title = (
                    "📜 Admin Help - Registration Commands"
                    if self.page == 0
                    else "📜 Admin Help - Stats Commands"
                )
                embed = discord.Embed(title=title, color=discord.Color.dark_red())
                for name, desc in admin_commands[page_type]:
                    embed.add_field(name=name, value=desc, inline=False)
                return embed

        class PreviousPageButton(discord.ui.Button):
            def __init__(self, view: AdminHelpView):
                super().__init__(
                    style=discord.ButtonStyle.secondary, label="Prev", disabled=True
                )
                self.custom_view = view

            async def callback(self, interaction: discord.Interaction):
                self.view.page -= 1
                await self.view.update_help(interaction)

        class NextPageButton(discord.ui.Button):
            def __init__(self, view: AdminHelpView):
                super().__init__(style=discord.ButtonStyle.secondary, label="Next")
                self.custom_view = view

            async def callback(self, interaction: discord.Interaction):
                self.view.page += 1
                await self.view.update_help(interaction)

        view = AdminHelpView()
        view.add_item(PreviousPageButton(view))
        view.add_item(NextPageButton(view))
        await ctx.send(embed=view.create_embed(), view=view)


async def setup(bot: commands.Bot):
    await bot.add_cog(HelpCog(bot))