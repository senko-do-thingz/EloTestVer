import asyncio
import functools
import io
import logging
import os
from typing import TYPE_CHECKING

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

_LB_BG = (49, 51, 56)
_LB_ROW_HIGHLIGHT = (59, 66, 114)
_LB_TEXT = (219, 222, 225)
_LB_TEXT_MUTED = (148, 155, 164)
_LB_RANK_COLORS = {1: (255, 215, 0), 2: (192, 192, 192), 3: (205, 127, 50)}

_CJK_FONT_ASSET_NAME = "NotoSansCJKsc-Regular.otf"

# ---------------------------------------------------------------------------
# SCALE constant exposed for tests
# ---------------------------------------------------------------------------
SCALE = 2


def _assets_dir() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, "..", "assets"))


def _resolve_cjk_font_asset() -> str | None:
    """
    Return the path to a CJK font file already present in assets/, or None.
    Font files must be bundled manually - no automatic download is performed.
    Supported filenames (checked in order):
      - NotoSansCJKsc-Regular.otf  (preferred)
      - NotoSansCJK-Regular.ttc
      - NotoSansCJK-Bold.ttc
      - NotoSansJP-Regular.otf
    """
    assets = _assets_dir()
    for name in (
        _CJK_FONT_ASSET_NAME,
        "NotoSansCJK-Regular.ttc",
        "NotoSansCJK-Bold.ttc",
        "NotoSansJP-Regular.otf",
    ):
        path = os.path.join(assets, name)
        if os.path.isfile(path):
            return path
    return None


# Resolved once at import time; None if no CJK font is bundled in assets/.
_cjk_font_asset_path: str | None = _resolve_cjk_font_asset()


def _text_needs_cjk(text: str) -> bool:
    for ch in text:
        if ch in "._- " or ch.isdigit():
            continue
        o = ord(ch)
        if (
            0x3040 <= o <= 0x30FF      # Hiragana / Katakana
            or 0x4E00 <= o <= 0x9FFF   # CJK Unified Ideographs
            or 0x3400 <= o <= 0x4DBF   # CJK Extension A
            or 0x20000 <= o <= 0x2A6DF # CJK Extension B
            or 0x2A700 <= o <= 0x2CEAF # CJK Extension C/D/E
            or 0xF900 <= o <= 0xFAFF   # CJK Compatibility Ideographs
            or 0x2F800 <= o <= 0x2FA1F # CJK Compatibility Supplement
            or 0xAC00 <= o <= 0xD7AF   # Hangul Syllables (Korean)
            or 0x1100 <= o <= 0x11FF   # Hangul Jamo
            or 0xA960 <= o <= 0xA97F   # Hangul Jamo Extended-A
        ):
            return True
    return False


def _cjk_font_paths(*, bold: bool) -> list[str]:
    paths: list[str] = []
    assets = _assets_dir()

    # Prefer an already-downloaded file / one available in assets/
    if _cjk_font_asset_path:
        paths.append(_cjk_font_asset_path)

    for name in (
        "NotoSansCJK-Regular.ttc",
        "NotoSansCJK-Bold.ttc" if bold else "NotoSansCJK-Regular.ttc",
        "NotoSansCJKsc-Regular.otf",
        "NotoSansJP-Regular.otf",
    ):
        paths.append(os.path.join(assets, name))

    if os.name == "nt":
        if bold:
            paths.extend([
                "C:/Windows/Fonts/meiryob.ttc",
                "C:/Windows/Fonts/msyhbd.ttc",
                "C:/Windows/Fonts/malgunbd.ttf",
            ])
        paths.extend([
            "C:/Windows/Fonts/meiryo.ttc",
            "C:/Windows/Fonts/msyh.ttc",
            "C:/Windows/Fonts/malgun.ttf",
        ])
    else:
        paths.extend([
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
            if bold
            else "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJKjp-Regular.otf",
            "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
            "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
            "/System/Library/Fonts/PingFang.ttc",
            "/System/Library/Fonts/Hiragino Sans GB.ttc",
            "/Library/Fonts/Arial Unicode.ttf",
        ])
    return paths


def _latin_font_paths(*, bold: bool) -> list[str]:
    assets = _assets_dir()
    paths = [
        os.path.join(assets, "NotoSans.ttf"),
        os.path.join(assets, "Inter.ttf"),
    ]
    if os.name == "nt":
        paths.extend([
            "C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf",
            "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
        ])
    else:
        paths.extend([
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
            if bold
            else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"
            if bold
            else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        ])
    return paths


def _open_truetype(path: str, size: int, *, bold: bool) -> ImageFont.FreeTypeFont | None:
    if not os.path.isfile(path):
        return None
    try:
        if path.lower().endswith(".ttc"):
            regular = None
            bold_face = None
            for index in range(8):
                try:
                    font = ImageFont.truetype(path, size, index=index)
                except Exception:
                    continue
                _family, style = font.getname()
                style_l = (style or "").lower()
                if "italic" in style_l or "oblique" in style_l:
                    continue
                if "bold" in style_l:
                    if bold_face is None:
                        bold_face = font
                elif regular is None:
                    regular = font
            if bold:
                return bold_face or regular
            return regular or bold_face
        font = ImageFont.truetype(path, size)
        if bold:
            try:
                font.set_variation_by_name("Bold")
            except Exception:
                pass
        return font
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Font caching - lru_cache keeps font objects in memory, avoiding disk reads
# on every render.  maxsize=128 is enough for all realistic size/bold combos.
# ---------------------------------------------------------------------------

@functools.lru_cache(maxsize=128)
def _load_cjk_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | None:
    for path in _cjk_font_paths(bold=bold):
        font = _open_truetype(path, size, bold=bold)
        if font is not None:
            return font
    return None


@functools.lru_cache(maxsize=128)
def _load_latin_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in _latin_font_paths(bold=bold):
        font = _open_truetype(path, size, bold=bold)
        if font is not None:
            return font
    return ImageFont.load_default(size=size)


def _load_lb_font(
    size: int, bold: bool = False, *, for_text: str | None = None
) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Pick a font that can render the given text (CJK / Latin)."""
    if for_text and _text_needs_cjk(for_text):
        cjk = _load_cjk_font(size, bold=bold)
        if cjk is not None:
            return cjk
        logger.warning(
            "CJK text detected (%r) but no CJK font found in assets/. "
            "Place NotoSansCJKsc-Regular.otf (or any supported variant) in the assets/ "
            "directory to enable CJK rendering.",
            for_text[:20],
        )
    return _load_latin_font(size, bold=bold)


# ---------------------------------------------------------------------------
# Template / background cache
# ---------------------------------------------------------------------------

@functools.lru_cache(maxsize=16)
def _get_lb_template(width_px: int, height_px: int) -> Image.Image:
    """
    Cache blank leaderboard backgrounds keyed by (width, height).

    Storing a pre-allocated Image object avoids repeated malloc/memset for
    the background fill on every render call.  We copy() before drawing
    so the cached template stays pristine.
    """
    img = Image.new("RGB", (width_px, height_px), _LB_BG)
    return img


def _lb_text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0]


def _lb_truncate_text(
    draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int
) -> str:
    if _lb_text_width(draw, text, font) <= max_width:
        return text
    ellipsis = "..."
    while text and _lb_text_width(draw, text + ellipsis, font) > max_width:
        text = text[:-1]
    return text + ellipsis if text else ellipsis


# ---------------------------------------------------------------------------
# Core render (sync) – optimized
# ---------------------------------------------------------------------------

def render_leaderboard_image(
    entries: list[tuple[int, str, str] | tuple[int, str, str, bool]],
    *,
    title: str,
    page: int | None = None,
    total_pages: int | None = None,
    width: int = 580,
) -> io.BytesIO:
    """Render leaderboard rows to PNG. Each entry is (rank, name, stat[, highlight])."""
    row_height = 50 * SCALE
    pad_x = 20 * SCALE
    header_h = 58 * SCALE
    pad_bottom = 12 * SCALE
    rank_col_w = 44 * SCALE
    stat_col_w = 110 * SCALE
    width_px = width * SCALE
    name_max_w = width_px - pad_x * 2 - rank_col_w - stat_col_w - 16 * SCALE

    height_px = header_h + len(entries) * row_height + pad_bottom

    # Reuse cached background template (copy before mutating)
    img = _get_lb_template(width_px, height_px).copy()
    draw = ImageDraw.Draw(img)

    # Pre-load all fonts once per call (already cached by lru_cache)
    title_font = _load_lb_font(23 * SCALE, bold=True, for_text=title)
    page_font  = _load_lb_font(18 * SCALE, for_text=title)
    font       = _load_latin_font(21 * SCALE)
    font_bold  = _load_latin_font(21 * SCALE, bold=True)

    # Header
    page_text = (
        f"Page {page}/{total_pages}"
        if page is not None and total_pages is not None
        else ""
    )
    page_w = _lb_text_width(draw, page_text, page_font) if page_text else 0
    title_max_w = width_px - pad_x * 2 - (page_w + 12 * SCALE if page_text else 0)
    display_title = _lb_truncate_text(draw, title, title_font, title_max_w)
    draw.text((pad_x, 14 * SCALE), display_title, fill=_LB_TEXT, font=title_font)
    if page_text:
        draw.text(
            (width_px - pad_x - page_w, 18 * SCALE),
            page_text,
            fill=_LB_TEXT_MUTED,
            font=page_font,
        )

    # Rows
    for idx, entry in enumerate(entries):
        rank, name, stat = entry[0], entry[1], entry[2]
        highlight = entry[3] if len(entry) > 3 else False
        y0 = header_h + idx * row_height
        y1 = y0 + row_height - 4 * SCALE

        if highlight:
            draw.rounded_rectangle(
                (pad_x - 8 * SCALE, y0, width_px - pad_x + 8 * SCALE, y1),
                radius=6 * SCALE,
                fill=_LB_ROW_HIGHLIGHT,
            )

        rank_color = _LB_RANK_COLORS.get(rank, _LB_TEXT_MUTED)
        rank_font = font_bold if rank <= 3 else font
        draw.text((pad_x, y0 + 10 * SCALE), f"{rank}.", fill=rank_color, font=rank_font)

        name_font = _load_lb_font(21 * SCALE, bold=highlight, for_text=name)
        display_name = _lb_truncate_text(draw, name, name_font, name_max_w)
        draw.text(
            (pad_x + rank_col_w, y0 + 10 * SCALE),
            display_name,
            fill=_LB_TEXT,
            font=name_font,
        )

        stat_w = _lb_text_width(draw, stat, font_bold)
        stat_x = width_px - pad_x - stat_w
        draw.text((stat_x, y0 + 10 * SCALE), stat, fill=_LB_TEXT, font=font_bold)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


# ---------------------------------------------------------------------------
# Async wrapper – offload CPU-bound Pillow work to a thread so the Discord
# bot event loop stays responsive while images are being rendered.
# ---------------------------------------------------------------------------

async def render_leaderboard_image_async(
    entries: list[tuple[int, str, str] | tuple[int, str, str, bool]],
    *,
    title: str,
    page: int | None = None,
    total_pages: int | None = None,
    width: int = 580,
) -> io.BytesIO:
    """
    Async version of render_leaderboard_image.

    Runs the CPU-bound Pillow work in a thread pool via asyncio.to_thread()
    so the bot event loop is never blocked during rendering.

    Usage (inside a discord.py command):
        buf = await render_leaderboard_image_async(entries, title="Global LB")
        await ctx.send(file=discord.File(buf, filename="leaderboard.png"))
    """
    return await asyncio.to_thread(
        render_leaderboard_image,
        entries,
        title=title,
        page=page,
        total_pages=total_pages,
        width=width,
    )


# ---------------------------------------------------------------------------
# Match history image (ClashPerk "war log" style table)
# ---------------------------------------------------------------------------

_HIST_WIN = (87, 242, 135)     # green accent / positive delta
_HIST_LOSS = (237, 66, 69)     # red accent / negative delta
_HIST_SEPARATOR = (65, 68, 74)

# (date, opponent, wins, losses, delta, elo_init, elo_final)
HistoryRowTuple = tuple[str, str, int, int, int, int, int]


def _circular_avatar(avatar_bytes: bytes, size: int) -> Image.Image | None:
    """Decode raw avatar bytes into a circular RGBA thumbnail of ``size`` px,
    ready to be pasted directly onto a rendered image."""
    try:
        im = Image.open(io.BytesIO(avatar_bytes)).convert("RGBA")
    except Exception:
        return None
    im = im.resize((size, size), Image.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)
    out = Image.new("RGBA", (size, size))
    out.paste(im, (0, 0), mask)
    return out


def render_history_image(
    rows: list[HistoryRowTuple],
    *,
    title: str,
    summary: str | None = None,
    width: int = 620,
    highest_streak: int | None = None,
    avatar_bytes: bytes | None = None,
) -> io.BytesIO:
    """
    Render match history rows as a PNG "log" table, ClashPerk-style:
    a colored win/loss accent bar per row + a DATE/OPPONENT/W/L/+-/INIT/FINAL
    grid, instead of a plain text table or a wall of embed fields.

    If ``highest_streak`` is given, a small "Best Streak" badge is drawn on
    the right side of the summary row. This is purely cosmetic: win streak
    never factors into the Elo math.

    If ``avatar_bytes`` is given, the player's avatar is drawn as a small
    circular thumbnail directly in front of the title (to its left), inside
    the image itself.
    """
    row_height = 42 * SCALE
    pad_x = 20 * SCALE
    title_h = 34 * SCALE
    summary_h = 26 * SCALE if (summary or highest_streak is not None) else 0
    col_header_h = 30 * SCALE
    pad_bottom = 14 * SCALE
    accent_w = 4 * SCALE
    accent_gap = 10 * SCALE
    width_px = width * SCALE

    # Column widths (must sum to width_px - 2*pad_x - accent_w - accent_gap)
    date_w = 70 * SCALE
    wins_w = 55 * SCALE
    losses_w = 55 * SCALE
    delta_w = 75 * SCALE
    init_w = 80 * SCALE
    final_w = 80 * SCALE
    fixed_w = date_w + wins_w + losses_w + delta_w + init_w + final_w
    opponent_w = width_px - pad_x * 2 - accent_w - accent_gap - fixed_w

    top_h = title_h + summary_h + col_header_h
    height_px = top_h + len(rows) * row_height + pad_bottom

    img = _get_lb_template(width_px, height_px).copy()
    draw = ImageDraw.Draw(img)

    title_font = _load_lb_font(22 * SCALE, bold=True, for_text=title)
    summary_font = _load_latin_font(15 * SCALE)
    header_font = _load_latin_font(15 * SCALE, bold=True)
    cell_font = _load_latin_font(17 * SCALE)
    cell_font_bold = _load_latin_font(17 * SCALE, bold=True)

    content_x0 = pad_x + accent_w + accent_gap

    # ── title / summary ────────────────────────────────────────────────
    title_x = pad_x
    title_y = 8 * SCALE
    avatar_size = 26 * SCALE
    if avatar_bytes:
        avatar_img = _circular_avatar(avatar_bytes, avatar_size)
        if avatar_img is not None:
            # Center the avatar on the title text's actual visual bounding
            # box (not just its font-metric line height), so it lines up
            # with the letters regardless of font/script.
            probe_bbox = draw.textbbox((0, title_y), title, font=title_font)
            text_center_y = (probe_bbox[1] + probe_bbox[3]) / 2
            avatar_y = int(text_center_y - avatar_size / 2)
            img.paste(avatar_img, (pad_x, avatar_y), avatar_img)
            title_x = pad_x + avatar_size + 8 * SCALE

    draw.text((title_x, title_y), title, fill=_LB_TEXT, font=title_font)
    if summary:
        draw.text(
            (pad_x, title_h + 4 * SCALE),
            summary,
            fill=_LB_TEXT_MUTED,
            font=summary_font,
        )
    if highest_streak is not None:
        streak_text = f"Best Streak: {highest_streak}"
        streak_font = _load_latin_font(14 * SCALE, bold=True)
        streak_w = draw.textlength(streak_text, font=streak_font)
        draw.text(
            (width_px - pad_x - streak_w, title_h + 4 * SCALE),
            streak_text,
            fill=_LB_TEXT,
            font=streak_font,
        )

    # ── column headers ─────────────────────────────────────────────────
    col_x = {
        "date": content_x0,
        "opponent": content_x0 + date_w,
    }
    col_x["wins"] = col_x["opponent"] + opponent_w
    col_x["losses"] = col_x["wins"] + wins_w
    col_x["delta"] = col_x["losses"] + losses_w
    col_x["init"] = col_x["delta"] + delta_w
    col_x["final"] = col_x["init"] + init_w

    header_y = title_h + summary_h + 4 * SCALE
    draw.text((col_x["date"], header_y), "DATE", fill=_LB_TEXT_MUTED, font=header_font)
    draw.text((col_x["opponent"], header_y), "OPPONENT", fill=_LB_TEXT_MUTED, font=header_font)
    draw.text((col_x["wins"], header_y), "W", fill=_LB_TEXT_MUTED, font=header_font)
    draw.text((col_x["losses"], header_y), "L", fill=_LB_TEXT_MUTED, font=header_font)
    draw.text((col_x["delta"], header_y), "+/-", fill=_LB_TEXT_MUTED, font=header_font)
    draw.text((col_x["init"], header_y), "INIT", fill=_LB_TEXT_MUTED, font=header_font)
    draw.text((col_x["final"], header_y), "FINAL", fill=_LB_TEXT_MUTED, font=header_font)

    sep_y = top_h - 4 * SCALE
    draw.line(
        (pad_x, sep_y, width_px - pad_x, sep_y),
        fill=_HIST_SEPARATOR,
        width=max(1, SCALE // 2),
    )

    # ── rows ────────────────────────────────────────────────────────────
    for idx, (date_str, opponent, wins, losses, delta, elo_init, elo_final) in enumerate(rows):
        won = wins > losses
        accent_color = _HIST_WIN if won else _HIST_LOSS
        y0 = top_h + idx * row_height
        y1 = y0 + row_height - 6 * SCALE
        text_y = y0 + 9 * SCALE

        draw.rounded_rectangle(
            (pad_x, y0, pad_x + accent_w, y1),
            radius=2 * SCALE,
            fill=accent_color,
        )

        draw.text((col_x["date"], text_y), date_str, fill=_LB_TEXT, font=cell_font)

        display_opponent = _lb_truncate_text(
            draw, opponent, cell_font, opponent_w - 8 * SCALE
        )
        draw.text((col_x["opponent"], text_y), display_opponent, fill=_LB_TEXT, font=cell_font)

        draw.text((col_x["wins"], text_y), str(wins), fill=_HIST_WIN, font=cell_font_bold)
        draw.text((col_x["losses"], text_y), str(losses), fill=_HIST_LOSS, font=cell_font_bold)

        delta_text = f"+{delta}" if delta >= 0 else str(delta)
        delta_color = _HIST_WIN if delta >= 0 else _HIST_LOSS
        draw.text((col_x["delta"], text_y), delta_text, fill=delta_color, font=cell_font_bold)

        draw.text((col_x["init"], text_y), str(elo_init), fill=_LB_TEXT_MUTED, font=cell_font)
        draw.text((col_x["final"], text_y), str(elo_final), fill=_LB_TEXT, font=cell_font_bold)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


async def render_history_image_async(
    rows: list[HistoryRowTuple],
    *,
    title: str,
    summary: str | None = None,
    width: int = 620,
    highest_streak: int | None = None,
    avatar_bytes: bytes | None = None,
) -> io.BytesIO:
    """Async wrapper around render_history_image (see render_leaderboard_image_async)."""
    return await asyncio.to_thread(
        render_history_image,
        rows,
        title=title,
        summary=summary,
        width=width,
        highest_streak=highest_streak,
        avatar_bytes=avatar_bytes,
    )

# ---------------------------------------------------------------------------
# Player comparison image (two-column "scoreboard" table)
# ---------------------------------------------------------------------------

_CMP_WIN = (87, 242, 135)      # winning value accent (matches history win color)
_CMP_LOSE = (237, 66, 69)      # losing value accent (matches history loss color)
_CMP_GOLD = (255, 215, 0)      # winner-declaration line

# (label, value1_str, value2_str, winner) where winner is 0=tie, 1=player1, 2=player2
CompareRowTuple = tuple[str, str, str, int]


def render_compare_image(
    rows: list[CompareRowTuple],
    *,
    name1: str,
    name2: str,
    winner_text: str,
    width: int = 560,
    avatar1_bytes: bytes | None = None,
    avatar2_bytes: bytes | None = None,
) -> io.BytesIO:
    """
    Render a two-player comparison as a PNG "scoreboard" table: player
    names as column headers, one row per stat with the higher value
    highlighted, and a winner-declaration line at the bottom.

    The top title row shows each player's avatar (instead of their name)
    separated by " vs ". If an avatar is missing (fetch failed or player
    has no cached avatar bytes), that side falls back to the player's name
    so the title never ends up blank.
    """
    row_height = 42 * SCALE
    pad_x = 20 * SCALE
    title_h = 46 * SCALE
    header_h = 30 * SCALE
    winner_h = 40 * SCALE
    pad_bottom = 14 * SCALE
    width_px = width * SCALE

    top_h = title_h + header_h
    height_px = top_h + len(rows) * row_height + winner_h + pad_bottom

    img = _get_lb_template(width_px, height_px).copy()
    draw = ImageDraw.Draw(img)

    title_font = _load_lb_font(22 * SCALE, bold=True, for_text=f"{name1} vs {name2}")
    header_font = _load_lb_font(18 * SCALE, bold=True, for_text=f"{name1}{name2}")
    label_font = _load_latin_font(17 * SCALE)
    value_font = _load_latin_font(19 * SCALE)
    value_font_bold = _load_latin_font(19 * SCALE, bold=True)
    winner_font = _load_lb_font(18 * SCALE, bold=True, for_text=winner_text)

    # Modified: size the label column to the longest label actually used
    # ("Win Rate (%)", "H2H WR (%)", etc.) instead of a fixed 160px, so the
    # dead space between a short label like "Elo" and its value column
    # shrinks too - values sit closer to their labels instead of pulling
    # the eye all the way to the right.
    label_gap = 24 * SCALE
    max_label_w = max((_lb_text_width(draw, label, label_font) for label, _, _, _ in rows), default=0)
    label_w = min(max_label_w + label_gap, (width_px - pad_x * 2) // 2)
    value_col_w = (width_px - pad_x * 2 - label_w) // 2

    value1_x = pad_x + label_w
    value2_x = value1_x + value_col_w

    # ── title (avatar1 vs avatar2, name text as a per-side fallback) ────
    # Centered on each stat column (same x-centers as the name headers and
    # values below) rather than on the full image width, so avatar1/name1
    # lines up with player 1's column and avatar2/name2 with player 2's
    # column, with "vs" sitting at the midpoint between them.
    col1_center_x = value1_x + value_col_w // 2
    col2_center_x = value2_x + value_col_w // 2
    title_max_w = width_px - pad_x * 2

    avatar_size = 34 * SCALE
    vs_text = "vs"
    vs_w = _lb_text_width(draw, vs_text, title_font)

    avatar1_img = _circular_avatar(avatar1_bytes, avatar_size) if avatar1_bytes else None
    avatar2_img = _circular_avatar(avatar2_bytes, avatar_size) if avatar2_bytes else None

    # Fallback text for whichever side is missing an avatar, truncated to
    # its own column width so it can't collide with the other column.
    name1_fallback = None if avatar1_img is not None else _lb_truncate_text(
        draw, name1, title_font, value_col_w - 8 * SCALE
    )
    name2_fallback = None if avatar2_img is not None else _lb_truncate_text(
        draw, name2, title_font, value_col_w - 8 * SCALE
    )

    title_y = 8 * SCALE
    title_row_h = max(avatar_size, title_font.size if hasattr(title_font, "size") else 22 * SCALE)
    title_center_y = title_y + title_row_h / 2

    if avatar1_img is not None:
        img.paste(
            avatar1_img,
            (col1_center_x - avatar_size // 2, int(title_center_y - avatar_size / 2)),
            avatar1_img,
        )
    else:
        name1_w = _lb_text_width(draw, name1_fallback, title_font)
        draw.text((col1_center_x - name1_w // 2, title_y), name1_fallback, fill=_LB_TEXT, font=title_font)

    if avatar2_img is not None:
        img.paste(
            avatar2_img,
            (col2_center_x - avatar_size // 2, int(title_center_y - avatar_size / 2)),
            avatar2_img,
        )
    else:
        name2_w = _lb_text_width(draw, name2_fallback, title_font)
        draw.text((col2_center_x - name2_w // 2, title_y), name2_fallback, fill=_LB_TEXT, font=title_font)

    vs_center_x = (col1_center_x + col2_center_x) // 2
    vs_bbox = draw.textbbox((0, 0), vs_text, font=title_font)
    vs_y = int(title_center_y - (vs_bbox[3] - vs_bbox[1]) / 2 - vs_bbox[1])
    draw.text((vs_center_x - vs_w // 2, vs_y), vs_text, fill=_LB_TEXT_MUTED, font=title_font)

    # ── column headers ─────────────────────────────────────────────────
    header_y = title_h + 4 * SCALE
    name1_disp = _lb_truncate_text(draw, name1, header_font, value_col_w - 8 * SCALE)
    name2_disp = _lb_truncate_text(draw, name2, header_font, value_col_w - 8 * SCALE)
    name1_w = _lb_text_width(draw, name1_disp, header_font)
    name2_w = _lb_text_width(draw, name2_disp, header_font)
    draw.text(
        (value1_x + (value_col_w - name1_w) // 2, header_y),
        name1_disp,
        fill=_LB_TEXT_MUTED,
        font=header_font,
    )
    draw.text(
        (value2_x + (value_col_w - name2_w) // 2, header_y),
        name2_disp,
        fill=_LB_TEXT_MUTED,
        font=header_font,
    )

    sep_y = top_h - 4 * SCALE
    draw.line(
        (pad_x, sep_y, width_px - pad_x, sep_y),
        fill=_HIST_SEPARATOR,
        width=max(1, SCALE // 2),
    )

    # ── stat rows ───────────────────────────────────────────────────────
    for idx, (label, val1, val2, winner) in enumerate(rows):
        y0 = top_h + idx * row_height
        text_y = y0 + 10 * SCALE

        draw.text((pad_x, text_y), label, fill=_LB_TEXT_MUTED, font=label_font)

        color1 = _CMP_WIN if winner == 1 else (_LB_TEXT if winner == 0 else _LB_TEXT_MUTED)
        color2 = _CMP_WIN if winner == 2 else (_LB_TEXT if winner == 0 else _LB_TEXT_MUTED)
        font1 = value_font_bold if winner in (0, 1) else value_font
        font2 = value_font_bold if winner in (0, 2) else value_font

        val1_w = _lb_text_width(draw, val1, font1)
        val2_w = _lb_text_width(draw, val2, font2)
        draw.text(
            (value1_x + (value_col_w - val1_w) // 2, text_y),
            val1,
            fill=color1,
            font=font1,
        )
        draw.text(
            (value2_x + (value_col_w - val2_w) // 2, text_y),
            val2,
            fill=color2,
            font=font2,
        )

    # ── winner-declaration footer line ─────────────────────────────────
    winner_y = top_h + len(rows) * row_height + 10 * SCALE
    draw.line(
        (pad_x, winner_y - 6 * SCALE, width_px - pad_x, winner_y - 6 * SCALE),
        fill=_HIST_SEPARATOR,
        width=max(1, SCALE // 2),
    )
    display_winner = _lb_truncate_text(draw, winner_text, winner_font, title_max_w)
    winner_w = _lb_text_width(draw, display_winner, winner_font)
    draw.text(
        ((width_px - winner_w) // 2, winner_y),
        display_winner,
        fill=_CMP_GOLD,
        font=winner_font,
    )

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


async def render_compare_image_async(
    rows: list[CompareRowTuple],
    *,
    name1: str,
    name2: str,
    winner_text: str,
    width: int = 560,
    avatar1_bytes: bytes | None = None,
    avatar2_bytes: bytes | None = None,
) -> io.BytesIO:
    """Async wrapper around render_compare_image (see render_leaderboard_image_async)."""
    return await asyncio.to_thread(
        render_compare_image,
        rows,
        name1=name1,
        name2=name2,
        winner_text=winner_text,
        width=width,
        avatar1_bytes=avatar1_bytes,
        avatar2_bytes=avatar2_bytes,
    )