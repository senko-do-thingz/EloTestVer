"""League boundary editor: ledit."""

from __future__ import annotations

import discord
from discord.ext import commands

from db.elo_db import db_connection
from utils import get_leagues, is_admin, set_leagues

from .base import RolesBaseCog


class RolesEditCog(RolesBaseCog):
    """Commands: ledit."""

    @commands.command(
        name="ledit",
        help="Edit league boundaries while keeping leagues non-overlapping (admin only).",
    )
    @commands.check(is_admin)
    async def ledit(self, ctx: commands.Context) -> None:
        leagues = get_leagues(ctx.guild.id)
        if len(leagues) < 2:
            await ctx.send(
                embed=discord.Embed(
                    title="Not Enough Leagues",
                    description="You need at least 2 leagues to edit boundaries. Use `-ladd` first.",
                    color=discord.Color.red(),
                )
            )
            return

        cancel_event = self._try_begin_league_task(ctx)
        if cancel_event is None:
            await ctx.send(
                embed=discord.Embed(
                    title="League Task Already Running",
                    description=(
                        "You already have a league task running in this channel. "
                        "Use `-lcancel` to cancel it."
                    ),
                    color=discord.Color.red(),
                )
            )
            return

        # ---- Step 0: choose edit mode (1 = one league, 2 = all leagues) ----
        mode = await self._ask_reply_number(
            ctx,
            discord.Embed(
                title="League Editor",
                description=(
                    "Which edit mode would you like?\n"
                    "- Reply `1`: Edit **one league**\n"
                    "- Reply `2`: Edit **all leagues**\n\n"
                    "Reply to this message with `1` or `2`."
                ),
                color=discord.Color.orange(),
            ),
            min_value=1,
            max_value=2,
            cancel_event=cancel_event,
        )
        if mode is None:
            self._end_league_task(ctx)
            return

        # ----------------------------------------------------------------
        # Mode 2: replace all leagues at once
        # ----------------------------------------------------------------
        if mode == 2:
            await self._ledit_all(ctx, leagues, cancel_event)
            return

        # ----------------------------------------------------------------
        # Mode 1: interactive boundary edits for one selected league
        # ----------------------------------------------------------------
        await self._ledit_one(ctx, leagues, cancel_event)

    # ------------------------------------------------------------------
    # Mode 2 helper: replace-all
    # ------------------------------------------------------------------

    async def _ledit_all(self, ctx, leagues, cancel_event) -> None:
        import asyncio

        leagues_sorted = sorted(
            leagues,
            key=lambda r: (
                self._effective_range(r[1], r[2], r[3], r[4])[0],
                self._effective_range(r[1], r[2], r[3], r[4])[1],
                r[0].lower(),
            ),
        )
        role_by_id = {r.id: r for r in ctx.guild.roles}
        current_lines: list[str] = []
        for i, (name, min_elo, max_elo, min_kind, max_kind, role_id, discovered) in enumerate(
            leagues_sorted, start=1
        ):
            role_display = self._format_league_display(role_by_id, name, role_id)
            rng = self._format_range(min_elo, max_elo, min_kind, max_kind)
            status = "discovered" if discovered else "undiscovered"
            current_lines.append(f"**{i}.** {role_display} — **{rng}** ({status})")

        expected_lines = len(leagues_sorted)
        example = "Silver l 599 1\nGold 600 699 1\nPlatinum 700 h 0"
        prompt = await ctx.send(
            embed=discord.Embed(
                title="Edit All Leagues",
                description=(
                    "Current leagues:\n" + "\n".join(current_lines) + "\n\n"
                    f"Please **reply** to this message with exactly **{expected_lines}** line(s).\n"
                    "Each line must follow this format:\n"
                    "`league_name minimum_elo maximum_elo [discovered]`\n\n"
                    "Special values:\n"
                    "- Use `l` as `minimum_elo` to mean **Lower** (from `maximum_elo` and below)\n"
                    "- Use `h` as `maximum_elo` to mean **Higher** (from `minimum_elo` and above)\n"
                    "- `discovered` (optional): `1` = discovered, `0` = undiscovered (default: `0`)\n\n"
                    "League name:\n"
                    "- If it has **2+ characters**, `_` represents a space. Example: `Diamond_League` → `Diamond League`\n\n"
                    f"Example:\n```\n{example}\n```"
                ),
                color=discord.Color.orange(),
            )
        )

        def check(m: discord.Message):
            return (
                m.author.id == ctx.author.id
                and m.channel.id == ctx.channel.id
                and m.reference is not None
                and m.reference.message_id == prompt.id
            )

        message_task = asyncio.create_task(
            self.bot.wait_for("message", timeout=300.0, check=check)
        )
        cancel_task = asyncio.create_task(cancel_event.wait())
        try:
            done, pending = await asyncio.wait(
                {message_task, cancel_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for t in pending:
                t.cancel()
            if cancel_task in done and cancel_event.is_set():
                await ctx.send(
                    embed=discord.Embed(
                        title="Canceled",
                        description="League task has been canceled.",
                        color=discord.Color.red(),
                    )
                )
                return
            reply: discord.Message = message_task.result()
        except Exception:
            await ctx.send(
                embed=discord.Embed(
                    title="Timeout",
                    description="Timed out. Please run `-ledit` again.",
                    color=discord.Color.red(),
                )
            )
            return
        finally:
            self._end_league_task(ctx)

        raw_lines = [
            ln.strip() for ln in (reply.content or "").splitlines() if ln.strip()
        ]
        if len(raw_lines) != int(expected_lines):
            await ctx.send(
                embed=discord.Embed(
                    title="Invalid Lines",
                    description=(
                        f"Expected exactly **{expected_lines}** line(s), "
                        f"but received **{len(raw_lines)}**."
                    ),
                    color=discord.Color.red(),
                )
            )
            return

        leagues_to_store, bad_lines, missing_roles = self._parse_league_lines(
            ctx, raw_lines
        )

        if bad_lines:
            await ctx.send(
                embed=discord.Embed(
                    title="Invalid League Lines",
                    description="These lines have an invalid format:\n"
                    + "\n".join(f"- `{x}`" for x in bad_lines),
                    color=discord.Color.red(),
                )
            )
            return

        if missing_roles:
            await ctx.send(
                embed=discord.Embed(
                    title="Missing League Roles",
                    description=(
                        "Could not find a server role for these leagues (skipped):\n"
                        + "\n".join(f"- `{x}`" for x in missing_roles)
                    ),
                    color=discord.Color.red(),
                )
            )

        if not leagues_to_store:
            await ctx.send(
                embed=discord.Embed(
                    title="No Valid Leagues",
                    description="No valid leagues to save.",
                    color=discord.Color.red(),
                )
            )
            return

        def _eff(row):
            _, min_elo, max_elo, min_kind, max_kind, _r, _d = row
            low = -(10**18) if min_kind == "l" else int(min_elo)
            high = 10**18 if max_kind == "h" else int(max_elo)
            return low, high

        sorted_rows = sorted(
            leagues_to_store,
            key=lambda r: (_eff(r)[0], _eff(r)[1], r[0].lower()),
        )
        overlaps: list[str] = []
        gaps: list[str] = []
        prev = None
        for row in sorted_rows:
            low, high = _eff(row)
            if prev is not None:
                _p_low, p_high, p_name = prev
                if low <= p_high:
                    overlaps.append(f"`{p_name}` overlaps `{row[0]}`")
                elif low > p_high + 1:
                    gaps.append(f"`{p_name}` → `{row[0]}` (Elo {p_high + 1}–{low - 1} is unassigned)")
            prev = (low, high, row[0])

        if overlaps:
            await ctx.send(
                embed=discord.Embed(
                    title="Overlapping Leagues",
                    description=(
                        "Each league must be strictly separate (no shared Elo).\n"
                        "Fix these overlaps and try again:\n"
                        + "\n".join(f"- {x}" for x in overlaps)
                    ),
                    color=discord.Color.red(),
                )
            )
            return

        if gaps:
            await ctx.send(
                embed=discord.Embed(
                    title="Gap Between Leagues",
                    description=(
                        "Players with Elo in the gap will not be assigned any league.\n"
                        "Fix these gaps and try again:\n"
                        + "\n".join(f"- {x}" for x in gaps)
                    ),
                    color=discord.Color.red(),
                )
            )
            return

        set_leagues(ctx.guild.id, leagues_to_store)
        _u, _assigned_count, missing_from_db, apply_details = await self._apply_leagues(
            ctx.guild
        )

        missing_all = sorted(set(missing_roles + missing_from_db))
        desc = f"Saved **{len(leagues_to_store)}** league(s).\n\n{apply_details}"
        if missing_all:
            desc += "\n\nLeague roles not found (skipped):\n" + "\n".join(
                f"- `{x}`" for x in missing_all
            )

        await ctx.send(
            embed=discord.Embed(
                title="Leagues Updated",
                description=desc,
                color=discord.Color.green(),
            )
        )

    # ------------------------------------------------------------------
    # Mode 1 helper: single-league interactive edit
    # ------------------------------------------------------------------

    async def _ledit_one(self, ctx, leagues, cancel_event) -> None:
        leagues_sorted = sorted(
            leagues,
            key=lambda r: (
                self._effective_range(r[1], r[2], r[3], r[4])[0],
                self._effective_range(r[1], r[2], r[3], r[4])[1],
                r[0].lower(),
            ),
        )
        role_by_id = {r.id: r for r in ctx.guild.roles}
        lines: list[str] = []
        for i, (name, min_elo, max_elo, min_kind, max_kind, role_id, discovered) in enumerate(
            leagues_sorted, start=1
        ):
            role_display = self._format_league_display(role_by_id, name, role_id)
            rng = self._format_range(min_elo, max_elo, min_kind, max_kind)
            status = "discovered" if discovered else "undiscovered"
            lines.append(f"**{i}.** {role_display} — **{rng}** ({status})")

        idx = await self._ask_reply_number(
            ctx,
            discord.Embed(
                title="League Editor",
                description=(
                    "Reply with the **number** of the league you want to adjust.\n\n"
                    + "\n".join(lines)
                ),
                color=discord.Color.orange(),
            ),
            min_value=1,
            max_value=len(leagues_sorted),
            cancel_event=cancel_event,
        )
        if idx is None:
            self._end_league_task(ctx)
            return

        sel_i = idx - 1
        sel = leagues_sorted[sel_i]
        (
            sel_name,
            sel_min,
            sel_max,
            sel_min_kind,
            sel_max_kind,
            sel_role_id,
            sel_disc,
        ) = sel
        sel_display = self._format_league_display(role_by_id, sel_name, sel_role_id)
        sel_range = self._format_range(sel_min, sel_max, sel_min_kind, sel_max_kind)
        await ctx.send(
            embed=discord.Embed(
                title="Selected League",
                description=f"Editing {sel_display} — **{sel_range}**",
                color=discord.Color.blurple(),
            )
        )

        def _update_pair(
            name_a, min_a, max_a, min_kind_a, max_kind_a,
            name_b, min_b, max_b, min_kind_b, max_kind_b,
        ) -> None:
            with db_connection(ctx.guild.id) as (conn, cursor):
                cursor.execute(
                    "UPDATE leagues SET min_elo=?, max_elo=?, min_kind=?, max_kind=? WHERE name=?",
                    (min_a, max_a, min_kind_a, max_kind_a, name_a),
                )
                cursor.execute(
                    "UPDATE leagues SET min_elo=?, max_elo=?, min_kind=?, max_kind=? WHERE name=?",
                    (min_b, max_b, min_kind_b, max_kind_b, name_b),
                )
                conn.commit()

        # Case 1: lowest league — edit its max_elo
        if sel_i == 0:
            next_row = leagues_sorted[1]
            n_name, n_min, n_max, n_min_kind, n_max_kind, n_role_id, n_disc = next_row
            n_display = self._format_league_display(role_by_id, n_name, n_role_id)

            new_max = await self._ask_reply_number(
                ctx,
                discord.Embed(
                    title="Edit Lowest League",
                    description=(
                        f"Reply with the new **maximum_elo** for {sel_display}.\n"
                        f"It will also set {n_display} minimum_elo to **(maximum_elo + 1)**."
                    ),
                    color=discord.Color.orange(),
                ),
                min_value=-1_000_000,
                max_value=1_000_000,
                cancel_event=cancel_event,
            )
            if new_max is None:
                self._end_league_task(ctx)
                return

            n_new_min = int(new_max) + 1
            n_new_max = n_max
            if n_min_kind == "l":
                n_min_kind = "n"
            if n_max_kind != "h" and (n_new_max is None or n_new_min > int(n_new_max)):
                await ctx.send(
                    embed=discord.Embed(
                        title="Invalid Boundary",
                        description=(
                            "This change would make the next league invalid. "
                            "Please choose a different value."
                        ),
                        color=discord.Color.red(),
                    )
                )
                self._end_league_task(ctx)
                return

            _update_pair(
                sel_name, sel_min, int(new_max), sel_min_kind, "n",
                n_name, n_new_min, n_new_max, "n", n_max_kind,
            )
            await ctx.send(
                embed=discord.Embed(
                    title="Boundary Applied",
                    description=(
                        f"{sel_display} (max) → **{int(new_max)}**\n"
                        f"{n_display} (min) → **{n_new_min}**"
                    ),
                    color=discord.Color.blurple(),
                )
            )

        # Case 2: highest league — edit its min_elo
        elif sel_i == len(leagues_sorted) - 1:
            prev_row = leagues_sorted[-2]
            p_name, p_min, p_max, p_min_kind, p_max_kind, p_role_id, p_disc = prev_row
            p_display = self._format_league_display(role_by_id, p_name, p_role_id)

            new_min = await self._ask_reply_number(
                ctx,
                discord.Embed(
                    title="Edit Highest League",
                    description=(
                        f"Reply with the new **minimum_elo** for {sel_display}.\n"
                        f"It will also set {p_display} maximum_elo to **(minimum_elo - 1)**."
                    ),
                    color=discord.Color.orange(),
                ),
                min_value=-1_000_000,
                max_value=1_000_000,
                cancel_event=cancel_event,
            )
            if new_min is None:
                self._end_league_task(ctx)
                return

            sel_new_min = int(new_min)
            p_new_max = sel_new_min - 1
            if p_max_kind == "h":
                p_max_kind = "n"
            if p_min_kind != "l" and (p_min is None or int(p_min) > p_new_max):
                await ctx.send(
                    embed=discord.Embed(
                        title="Invalid Boundary",
                        description=(
                            "This change would make the previous league invalid. "
                            "Please choose a different value."
                        ),
                        color=discord.Color.red(),
                    )
                )
                self._end_league_task(ctx)
                return

            _update_pair(
                p_name, p_min, p_new_max, p_min_kind, "n",
                sel_name, sel_new_min, sel_max, "n", sel_max_kind,
            )
            await ctx.send(
                embed=discord.Embed(
                    title="Boundary Applied",
                    description=(
                        f"{p_display} (max) → **{p_new_max}**\n"
                        f"{sel_display} (min) → **{sel_new_min}**"
                    ),
                    color=discord.Color.blurple(),
                )
            )

        # Case 3: middle league — ask what to edit
        else:
            await self._ledit_middle(
                ctx, leagues_sorted, sel_i, role_by_id, cancel_event, _update_pair
            )

        # Final safety overlap check
        updated_leagues = get_leagues(ctx.guild.id)
        updated_sorted = sorted(
            updated_leagues,
            key=lambda r: (
                self._effective_range(r[1], r[2], r[3], r[4])[0],
                self._effective_range(r[1], r[2], r[3], r[4])[1],
                r[0].lower(),
            ),
        )
        prev_high = None
        for r in updated_sorted:
            low, high = self._effective_range(r[1], r[2], r[3], r[4])
            if prev_high is not None and low <= prev_high:
                await ctx.send(
                    embed=discord.Embed(
                        title="Edit Rejected",
                        description=(
                            "This edit would create overlapping leagues. No changes were applied."
                        ),
                        color=discord.Color.red(),
                    )
                )
                self._end_league_task(ctx)
                return
            prev_high = high

        await self._apply_leagues(ctx.guild)
        await ctx.send(
            embed=discord.Embed(
                title="League Updated",
                description="League boundaries updated and roles synced.",
                color=discord.Color.green(),
            )
        )
        self._end_league_task(ctx)

    async def _ledit_middle(
        self, ctx, leagues_sorted, sel_i, role_by_id, cancel_event, _update_pair
    ) -> None:
        sel = leagues_sorted[sel_i]
        sel_name, sel_min, sel_max, sel_min_kind, sel_max_kind, sel_role_id, _ = sel
        sel_display = self._format_league_display(role_by_id, sel_name, sel_role_id)

        prev_row = leagues_sorted[sel_i - 1]
        next_row = leagues_sorted[sel_i + 1]
        p_name, p_min, p_max, p_min_kind, p_max_kind, p_role_id, _ = prev_row
        n_name, n_min, n_max, n_min_kind, n_max_kind, n_role_id, _ = next_row
        p_display = self._format_league_display(role_by_id, p_name, p_role_id)
        n_display = self._format_league_display(role_by_id, n_name, n_role_id)

        edit_mode = await self._ask_reply_number(
            ctx,
            discord.Embed(
                title="Edit Options",
                description=(
                    f"You selected {sel_display}.\n\n"
                    "What would you like to edit?\n"
                    "- Reply `1` to edit **minimum_elo**\n"
                    "- Reply `2` to edit **maximum_elo**\n"
                    "- Reply `3` to edit **both**\n\n"
                    "The bot will automatically adjust the adjacent league(s) by ±1 to keep leagues separate."
                ),
                color=discord.Color.orange(),
            ),
            min_value=1,
            max_value=3,
            cancel_event=cancel_event,
        )
        if edit_mode is None:
            self._end_league_task(ctx)
            return

        if edit_mode == 1:
            new_min = await self._ask_reply_number(
                ctx,
                discord.Embed(
                    title="Edit minimum_elo",
                    description=(
                        f"Reply with the new **minimum_elo** for {sel_display}.\n"
                        f"It will set {p_display} maximum_elo to **(minimum_elo - 1)**."
                    ),
                    color=discord.Color.orange(),
                ),
                min_value=-1_000_000,
                max_value=1_000_000,
                cancel_event=cancel_event,
            )
            if new_min is None:
                self._end_league_task(ctx)
                return

            sel_new_min = int(new_min)
            p_new_max = sel_new_min - 1

            if sel_max_kind != "h" and (sel_max is None or sel_new_min > int(sel_max)):
                await ctx.send(
                    embed=discord.Embed(
                        title="Invalid Boundary",
                        description="This change would make the selected league invalid.",
                        color=discord.Color.red(),
                    )
                )
                return
            if p_max_kind == "h":
                p_max_kind = "n"
            if p_min_kind != "l" and (p_min is None or int(p_min) > p_new_max):
                await ctx.send(
                    embed=discord.Embed(
                        title="Invalid Boundary",
                        description="This change would make the previous league invalid.",
                        color=discord.Color.red(),
                    )
                )
                return

            _update_pair(
                p_name, p_min, p_new_max, p_min_kind, "n",
                sel_name, sel_new_min, sel_max, "n", sel_max_kind,
            )
            await ctx.send(
                embed=discord.Embed(
                    title="Boundary Applied",
                    description=(
                        f"{p_display} (max) → **{p_new_max}**\n"
                        f"{sel_display} (min) → **{sel_new_min}**"
                    ),
                    color=discord.Color.blurple(),
                )
            )

        elif edit_mode == 2:
            new_max = await self._ask_reply_number(
                ctx,
                discord.Embed(
                    title="Edit maximum_elo",
                    description=(
                        f"Reply with the new **maximum_elo** for {sel_display}.\n"
                        f"It will set {n_display} minimum_elo to **(maximum_elo + 1)**."
                    ),
                    color=discord.Color.orange(),
                ),
                min_value=-1_000_000,
                max_value=1_000_000,
                cancel_event=cancel_event,
            )
            if new_max is None:
                self._end_league_task(ctx)
                return

            sel_new_max = int(new_max)
            n_new_min = sel_new_max + 1

            if sel_min_kind == "l":
                sel_min_kind = "n"
            if sel_min is None or int(sel_min) > sel_new_max:
                await ctx.send(
                    embed=discord.Embed(
                        title="Invalid Boundary",
                        description="This change would make the selected league invalid.",
                        color=discord.Color.red(),
                    )
                )
                return
            if n_min_kind == "l":
                n_min_kind = "n"
            if n_max_kind != "h" and (n_max is None or n_new_min > int(n_max)):
                await ctx.send(
                    embed=discord.Embed(
                        title="Invalid Boundary",
                        description="This change would make the next league invalid.",
                        color=discord.Color.red(),
                    )
                )
                return

            _update_pair(
                sel_name, sel_min, sel_new_max, "n", "n",
                n_name, n_new_min, n_max, "n", n_max_kind,
            )
            await ctx.send(
                embed=discord.Embed(
                    title="Boundary Applied",
                    description=(
                        f"{sel_display} (max) → **{sel_new_max}**\n"
                        f"{n_display} (min) → **{n_new_min}**"
                    ),
                    color=discord.Color.blurple(),
                )
            )

        else:  # edit_mode == 3: both
            new_min = await self._ask_reply_number(
                ctx,
                discord.Embed(
                    title="Edit both (step 1/2)",
                    description=f"Reply with the new **minimum_elo** for {sel_display}.",
                    color=discord.Color.orange(),
                ),
                min_value=-1_000_000,
                max_value=1_000_000,
                cancel_event=cancel_event,
            )
            if new_min is None:
                self._end_league_task(ctx)
                return

            new_max = await self._ask_reply_number(
                ctx,
                discord.Embed(
                    title="Edit both (step 2/2)",
                    description=f"Reply with the new **maximum_elo** for {sel_display}.",
                    color=discord.Color.orange(),
                ),
                min_value=-1_000_000,
                max_value=1_000_000,
                cancel_event=cancel_event,
            )
            if new_max is None:
                self._end_league_task(ctx)
                return

            sel_new_min = int(new_min)
            sel_new_max = int(new_max)
            if sel_new_min > sel_new_max:
                await ctx.send(
                    embed=discord.Embed(
                        title="Invalid Boundary",
                        description="minimum_elo must be less than or equal to maximum_elo.",
                        color=discord.Color.red(),
                    )
                )
                return

            p_new_max = sel_new_min - 1
            n_new_min = sel_new_max + 1

            if p_max_kind == "h":
                p_max_kind = "n"
            if p_min_kind != "l" and (p_min is None or int(p_min) > p_new_max):
                await ctx.send(
                    embed=discord.Embed(
                        title="Invalid Boundary",
                        description="This change would make the previous league invalid.",
                        color=discord.Color.red(),
                    )
                )
                return
            if n_min_kind == "l":
                n_min_kind = "n"
            if n_max_kind != "h" and (n_max is None or n_new_min > int(n_max)):
                await ctx.send(
                    embed=discord.Embed(
                        title="Invalid Boundary",
                        description="This change would make the next league invalid.",
                        color=discord.Color.red(),
                    )
                )
                return

            _update_pair(
                p_name, p_min, p_new_max, p_min_kind, "n",
                sel_name, sel_new_min, sel_new_max, "n", "n",
            )
            _update_pair(
                sel_name, sel_new_min, sel_new_max, "n", "n",
                n_name, n_new_min, n_max, "n", n_max_kind,
            )
            await ctx.send(
                embed=discord.Embed(
                    title="Boundary Applied",
                    description=(
                        f"{p_display} (max) → **{p_new_max}**\n"
                        f"{sel_display} (min,max) → **{sel_new_min} - {sel_new_max}**\n"
                        f"{n_display} (min) → **{n_new_min}**"
                    ),
                    color=discord.Color.blurple(),
                )
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RolesEditCog(bot))