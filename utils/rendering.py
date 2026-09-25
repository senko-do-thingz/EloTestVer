import asyncio
import functools
import io
import logging
import math
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


# Titles built from a CJK/Hangul display name render noticeably wider per
# character than Latin text (see `_load_lb_font`'s CJK fallback above), so a
# name that would look fine in Latin script can still be long enough to run
# into the "Year: ..." / "Season: ..." text drawn on the same row (see the
# title rendering in `render_history_image`). Rather than measuring pixel
# width there, names that contain CJK/Hangul characters are capped to a
# fixed, conservative character count instead - simple, cheap, and immune to
# per-glyph width differences across scripts.
CJK_NAME_MAX_LENGTH = 10


def truncate_cjk_name(name: str, max_length: int = CJK_NAME_MAX_LENGTH) -> str:
    """Cap a display name to `max_length` characters, but only when it
    contains Japanese/Korean/Chinese characters - Latin names are returned
    unchanged. A plain "..." is appended when truncated, since it's drawn
    with the same CJK-aware font as the rest of the title and won't render
    as a missing-glyph box the way a single Unicode ellipsis character
    sometimes does on fonts without that glyph."""
    if not name or len(name) <= max_length or not _text_needs_cjk(name):
        return name
    return name[:max_length] + "..."


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
    season_month_text: str | None = None,
    year_text: str | None = None,
    streak_labels: list[int | None] | None = None,
) -> io.BytesIO:
    """
    Render match history rows as a PNG "log" table, ClashPerk-style:
    a colored win/loss accent bar per row + a DATE/OPPONENT/W/L/+-/INIT/FINAL
    grid, instead of a plain text table or a wall of embed fields.

    If ``highest_streak`` is given, a small "Best Streak" badge is drawn on
    the right side of the summary row. This is purely cosmetic: win streak
    never factors into the Elo math.

    If ``year_text`` is given (e.g. "Year: 2026"), it is drawn right-aligned
    directly above ``season_month_text``.

    If ``season_month_text`` is given (e.g. "Season: 1, Month: 2"), it is
    drawn right-aligned directly above the "Best Streak" badge (or directly
    below ``year_text`` when that is also given).

    If ``avatar_bytes`` is given, the player's avatar is drawn as a small
    circular thumbnail directly in front of the title (to its left), inside
    the image itself.

    ``rows`` are expected to be ordered most-recent-first (as returned by
    the match history queries). Consecutive rows with the same result (win
    or loss) form a "streak"; the streak length is drawn as a small colored
    number in a dedicated column to the left of each row's win/loss accent
    bar, but only on the first row of each streak (i.e. the most recent
    match in that streak, or the row where the previous streak was cut
    off) - the remaining rows of the same streak are left blank.

    ``streak_labels``, when given, must be pre-computed by the caller over
    the player's FULL month of matches (e.g. via
    db.match_history_db.compute_streak_labels) and already sliced down to
    align 1:1 with ``rows``. Passing it in this way is required whenever
    ``rows`` is only one page of a larger month - computing streaks from
    just the visible page (the old behavior when this is omitted) makes a
    streak that spans a page boundary look shorter than it really is on
    each of the pages it touches, and disagrees with both the "Best Streak"
    badge and the /export history sheet, which both already use the full
    month's data.
    """
    row_height = 42 * SCALE
    pad_x = 20 * SCALE
    streak_w = 22 * SCALE
    title_h = 34 * SCALE
    season_h = 20 * SCALE if season_month_text else 0
    summary_h = 26 * SCALE if (summary or highest_streak is not None) else 0
    col_header_h = 30 * SCALE
    pad_bottom = 14 * SCALE
    accent_w = 4 * SCALE
    accent_gap = 10 * SCALE
    width_px = width * SCALE

    # Column widths (must sum to width_px - 2*pad_x - streak_w - accent_w - accent_gap)
    date_w = 70 * SCALE
    wins_w = 55 * SCALE
    losses_w = 55 * SCALE
    delta_w = 75 * SCALE
    init_w = 80 * SCALE
    final_w = 80 * SCALE
    fixed_w = date_w + wins_w + losses_w + delta_w + init_w + final_w
    opponent_w = width_px - pad_x * 2 - streak_w - accent_w - accent_gap - fixed_w

    top_h = title_h + season_h + summary_h + col_header_h
    height_px = top_h + len(rows) * row_height + pad_bottom

    img = _get_lb_template(width_px, height_px).copy()
    draw = ImageDraw.Draw(img)

    title_font = _load_lb_font(22 * SCALE, bold=True, for_text=title)
    summary_font = _load_latin_font(15 * SCALE)
    header_font = _load_latin_font(15 * SCALE, bold=True)
    cell_font = _load_latin_font(17 * SCALE)
    cell_font_bold = _load_latin_font(17 * SCALE, bold=True)

    accent_x0 = pad_x + streak_w
    content_x0 = accent_x0 + accent_w + accent_gap

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
    if year_text:
        # Same row and same size as the title, right-aligned.
        year_font = _load_latin_font(22 * SCALE, bold=True)
        year_w = draw.textlength(year_text, font=year_font)
        draw.text(
            (width_px - pad_x - year_w, title_y),
            year_text,
            fill=_LB_TEXT,
            font=year_font,
        )
    if season_month_text:
        season_font = _load_latin_font(14 * SCALE, bold=True)
        season_w = draw.textlength(season_month_text, font=season_font)
        draw.text(
            (width_px - pad_x - season_w, title_h + 4 * SCALE),
            season_month_text,
            fill=_LB_TEXT_MUTED,
            font=season_font,
        )
    if summary:
        draw.text(
            (pad_x, title_h + season_h + 4 * SCALE),
            summary,
            fill=_LB_TEXT_MUTED,
            font=summary_font,
        )
    if highest_streak is not None:
        streak_badge_text = f"Best Streak: {highest_streak}"
        streak_badge_font = _load_latin_font(14 * SCALE, bold=True)
        streak_badge_w = draw.textlength(streak_badge_text, font=streak_badge_font)
        draw.text(
            (width_px - pad_x - streak_badge_w, title_h + season_h + 4 * SCALE),
            streak_badge_text,
            fill=_LB_TEXT,
            font=streak_badge_font,
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

    header_y = title_h + season_h + summary_h + 4 * SCALE
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

    # ── streak grouping ────────────────────────────────────────────────
    # rows are most-recent-first, so a streak's "first" row (top of the
    # group) is the most recent match in it, or the row where the previous
    # (opposite-result) streak was cut off. Only that row gets a number.
    if streak_labels is not None:
        # Caller already computed this over the full month and sliced it
        # to match `rows` - see the docstring above for why that matters.
        if len(streak_labels) != len(rows):
            raise ValueError(
                "streak_labels must have the same length as rows "
                f"({len(streak_labels)} != {len(rows)})"
            )
    else:
        streak_labels = [None] * len(rows)
        i = 0
        while i < len(rows):
            result = rows[i][2] > rows[i][3]  # won
            j = i
            while j < len(rows) and (rows[j][2] > rows[j][3]) == result:
                j += 1
            streak_labels[i] = j - i
            i = j

    # ── rows ────────────────────────────────────────────────────────────
    for idx, (date_str, opponent, wins, losses, delta, elo_init, elo_final) in enumerate(rows):
        won = wins > losses
        accent_color = _HIST_WIN if won else _HIST_LOSS
        y0 = top_h + idx * row_height
        y1 = y0 + row_height - 6 * SCALE
        text_y = y0 + 9 * SCALE

        streak_label = streak_labels[idx]
        if streak_label is not None:
            streak_num_text = str(streak_label)
            streak_num_w = draw.textlength(streak_num_text, font=cell_font_bold)
            draw.text(
                (accent_x0 - 6 * SCALE - streak_num_w, text_y),
                streak_num_text,
                fill=accent_color,
                font=cell_font_bold,
            )

        draw.rounded_rectangle(
            (accent_x0, y0, accent_x0 + accent_w, y1),
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
    season_month_text: str | None = None,
    year_text: str | None = None,
    streak_labels: list[int | None] | None = None,
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
        season_month_text=season_month_text,
        year_text=year_text,
        streak_labels=streak_labels,
    )

# ---------------------------------------------------------------------------
# Elo graph image (ClashPerk-style trophy trend card)
# ---------------------------------------------------------------------------

_GRAPH_COMPARE = (114, 190, 246)  # light blue used for "vs previous month" deltas
_GRAPH_AXIS_TEXT = _LB_TEXT_MUTED
_GRAPH_GRID = _HIST_SEPARATOR
_GRAPH_BOX_BG = _LB_ROW_HIGHLIGHT
_GRAPH_REST_GAP_FILL = (79, 142, 247, 40)  # translucent blue band fill (rest period)
_GRAPH_REST_GAP_BORDER = (79, 142, 247, 160)  # dashed blue band border (rest period)

# (x_fraction_start, x_fraction_end) - one entry per multi-day inactivity
# span on the same 0..1 calendar axis as GraphCalendarPoint/GraphAxisTick.
# See utils.elo_graph.find_rest_gaps() / rest_gap_fractions().
GraphRestGap = tuple[float, float]

# (date_label, elo) - one point per CALENDAR DAY, oldest first, including
# days the player didn't play. See utils.elo_graph.build_daily_series()
# for how this is built.
GraphSeriesPoint = tuple[str, int]

# (x_fraction, elo) - one point per MATCH actually played, positioned on
# a real calendar timeline (0..1 across the full axis span) rather than
# evenly spaced by index. See utils.elo_graph.build_calendar_series().
GraphCalendarPoint = tuple[float, int]

# (x_fraction, "MM-DD") - one entry per CALENDAR DAY across the full
# axis span, pre-generated up front (including days with no data yet)
# purely to drive the x-axis tick labels. See
# utils.elo_graph.build_calendar_series().
GraphAxisTick = tuple[float, str]


def _graph_ticks(axis_min: int, axis_max: int, axis_rows: int) -> list[float]:
    """Return axis_rows + 1 evenly spaced gridline values from axis_min to
    axis_max (inclusive), e.g. axis_rows=6 -> 7 gridlines, mirroring
    ClashPerk's fixed trophy-range graph rather than auto-scaling to data."""
    if axis_rows <= 0:
        return [float(axis_min), float(axis_max)]
    step = (axis_max - axis_min) / axis_rows
    return [axis_min + step * i for i in range(axis_rows + 1)]


# Rotation angle (degrees, counter-clockwise) for x-axis date labels,
# matching ClashPerk's slanted "Jun 16 / Jun 18 / ..." tick labels.
_GRAPH_AXIS_LABEL_ANGLE = 45.0


def _graph_pick_label_indices(xs: list[int], min_gap_px: int) -> set[int]:
    """Thin out x-axis date labels so they don't overlap: greedily keep a
    point's label only if it is at least ``min_gap_px`` past the last kept
    label's x-position, always keeping the first and last point labeled.

    ``min_gap_px`` should already reflect the label's actual on-screen
    horizontal footprint - for the slanted labels drawn by
    ``_draw_slanted_axis_label`` that footprint is the label's projected
    width (``text_width * cos(angle)``), not its full upright width, or
    labels would be thinned out far more aggressively than necessary."""
    if not xs:
        return set()
    if len(xs) == 1:
        return {0}

    selected = [0]
    last_x = xs[0]
    for i in range(1, len(xs) - 1):
        if xs[i] - last_x >= min_gap_px:
            selected.append(i)
            last_x = xs[i]

    last_idx = len(xs) - 1
    if xs[last_idx] - last_x >= min_gap_px * 0.6:
        selected.append(last_idx)
    else:
        selected[-1] = last_idx

    return set(selected)


def _draw_slanted_axis_label(
    img: Image.Image,
    draw: ImageDraw.ImageDraw,
    tick_x: float,
    top_y: float,
    text: str,
    font: ImageFont.ImageFont,
    fill: tuple[int, int, int],
    angle: float = _GRAPH_AXIS_LABEL_ANGLE,
) -> None:
    """Draw one x-axis date label rotated ClashPerk-style: slanted counter-
    clockwise by ``angle`` degrees, anchored by its top-right corner at
    (``tick_x``, ``top_y``) so the text hangs down-and-to-the-left of its
    tick, exactly like ClashPerk's "Jun 16 / Jun 18 / ..." labels.

    Pillow has no native angled-text draw call, so the label is rendered
    upright onto its own small transparent layer first, rotated, then
    alpha-composited onto ``img`` at the position that keeps the
    top-right anchor fixed under the tick regardless of rotation.

    This top-right anchoring (rather than the old center-then-clamp
    approach) is what fixes labels overlapping at the chart edges: a
    slanted label naturally grows down and to the left of its tick, so
    neighbouring labels never need to be clamped back into each other's
    space the way horizontal centered labels did.
    """
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    pad = 4
    layer_w = text_w + pad * 2
    layer_h = text_h + pad * 2

    layer = Image.new("RGBA", (layer_w, layer_h), (0, 0, 0, 0))
    ImageDraw.Draw(layer).text(
        (pad - bbox[0], pad - bbox[1]), text, fill=(*fill, 255), font=font
    )
    rotated = layer.rotate(angle, expand=True, resample=Image.BICUBIC)

    # Where the un-rotated layer's top-right corner (our anchor) ends up
    # once the layer is rotated about its own center.
    dx = (layer_w - pad) - layer_w / 2
    dy = pad - layer_h / 2
    rad = math.radians(angle)
    rx = dx * math.cos(rad) + dy * math.sin(rad)
    ry = -dx * math.sin(rad) + dy * math.cos(rad)
    anchor_x = rotated.width / 2 + rx
    anchor_y = rotated.height / 2 + ry

    paste_x = int(round(tick_x - anchor_x))
    paste_y = int(round(top_y - anchor_y))
    img.paste(rotated, (paste_x, paste_y), rotated)


def _monotone_cubic_tangents(xs: list[float], ys: list[float]) -> list[float]:
    """Compute per-point tangent slopes for a monotone cubic Hermite
    spline through (xs[i], ys[i]) using the Fritsch-Carlson method - the
    same monotonicity-preserving algorithm scipy.interpolate.
    PchipInterpolator uses internally.

    Unlike the Catmull-Rom spline this replaces, a tangent here is never
    allowed to point the curve past a data point: whenever the secant
    slope changes sign (or flattens to zero) at a point - i.e. that
    point is a local peak or valley - the tangent is pinned to exactly
    0, so the curve arrives flat and turns around right there instead of
    bulging past it. This is what "no overshoot" means in practice and
    is the whole reason PCHIP/monotone splines are preferred over a
    plain cubic/Catmull-Rom spline for this kind of chart.
    """
    n = len(xs)
    if n < 2:
        return [0.0] * n

    # Secant (straight-line) slope across each original interval.
    d = [
        (ys[i + 1] - ys[i]) / (xs[i + 1] - xs[i]) if xs[i + 1] != xs[i] else 0.0
        for i in range(n - 1)
    ]

    m = [0.0] * n
    m[0] = d[0]
    m[-1] = d[-1]
    for i in range(1, n - 1):
        if d[i - 1] == 0.0 or d[i] == 0.0 or (d[i - 1] > 0) != (d[i] > 0):
            # Local peak or valley - flat tangent, no overshoot.
            m[i] = 0.0
        else:
            # Fritsch-Carlson weighted harmonic mean of the two secants,
            # biased toward the shorter of the two neighboring
            # intervals so an interval's own local slope stays close to
            # its own two endpoints rather than being pulled toward a
            # much wider neighbor.
            h0 = xs[i] - xs[i - 1]
            h1 = xs[i + 1] - xs[i]
            w0 = 2 * h1 + h0
            w1 = h1 + 2 * h0
            m[i] = (w0 + w1) / (w0 / d[i - 1] + w1 / d[i])
    return m


def _sample_monotone_curve(
    points: list[tuple[float, float]], samples_per_segment: int
) -> list[tuple[float, float]]:
    """Interpolate ``points`` with a monotone cubic Hermite spline and
    flatten the WHOLE series into a single continuous, densely sampled
    polyline (as opposed to the old approach of spline-sampling one
    original segment at a time). Returns the first point followed by
    ``samples_per_segment`` extra points per original interval - a
    single unbroken path with no per-segment seams, ready to be
    re-colored internally (e.g. by LineCollection-style per-sub-segment
    coloring) without ever being drawn as separate disconnected paths.
    """
    n = len(points)
    if n < 3:
        return list(points)

    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    m = _monotone_cubic_tangents(xs, ys)

    curve = [points[0]]
    for i in range(n - 1):
        x0, y0 = points[i]
        x1, y1 = points[i + 1]
        h = x1 - x0
        m0, m1 = m[i], m[i + 1]
        for s in range(1, samples_per_segment + 1):
            t = s / samples_per_segment
            t2 = t * t
            t3 = t2 * t
            # Cubic Hermite basis functions.
            h00 = 2 * t3 - 3 * t2 + 1
            h10 = t3 - 2 * t2 + t
            h01 = -2 * t3 + 3 * t2
            h11 = t3 - t2
            x = x0 + h * t
            y = h00 * y0 + h10 * h * m0 + h01 * y1 + h11 * h * m1
            curve.append((x, y))
    return curve


def _draw_round_segment(
    draw: ImageDraw.ImageDraw,
    p0: tuple[float, float],
    p1: tuple[float, float],
    color: tuple[int, int, int],
    width: int,
) -> None:
    """Draw one stroke from p0 to p1 with rounded joins/caps.

    ImageDraw.line() only draws flat (butt) ends - back-to-back calls
    left a visible wedge-shaped notch at every join wherever the line
    changed angle or color, which is what produced the "disconnected
    segments" look. Stamping a filled circle the width of the stroke at
    both endpoints rounds those joins off, the same way a vector
    renderer's round line-join/line-cap would, so many tiny consecutive
    sub-segments still read as one smooth continuous tube instead of a
    chain of mitred rectangles.
    """
    draw.line((*p0, *p1), fill=color, width=width)
    r = width / 2
    for cx, cy in (p0, p1):
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=color)


def _draw_dashed_vline(
    draw: ImageDraw.ImageDraw,
    x: float,
    y0: float,
    y1: float,
    color,
    width: int,
    *,
    dash_on: int,
    dash_off: int,
) -> None:
    """Draw a dashed vertical line from y0 to y1 at column x - used for
    the rest-gap band borders so the boundary reads as visually distinct
    from the solid horizontal/vertical grid lines drawn elsewhere."""
    period = dash_on + dash_off
    y = y0
    while y < y1:
        seg_end = min(y + dash_on, y1)
        draw.line((x, y, x, seg_end), fill=color, width=width)
        y += period


def _draw_smooth_line(
    draw: ImageDraw.ImageDraw,
    points: list[tuple[float, float]],
    *,
    color_rising: tuple[int, int, int],
    color_falling: tuple[int, int, int],
    width: int,
    samples_per_segment: int = 10,
) -> None:
    """Draw ``points`` as ONE continuous ClashPerk-style curve using a
    monotone cubic Hermite spline (see ``_monotone_cubic_tangents``),
    colored per tiny sub-segment based on the interpolated curve's own
    local direction rather than per original data segment.

    This is the key fix for the color-transition gap: the previous
    implementation picked a single color for an entire original segment
    [i, i+1] based on the raw ``value[i+1] - value[i]`` delta, then
    spline-sampled that segment on its own. Because the spline's actual
    shape near a peak/valley is influenced by neighboring points, its
    real turning point rarely lands exactly on data point i or i+1 -
    so the hard color switch at the data vertex didn't line up with
    where the drawn curve actually turned around, reading as a
    visible kink/gap.

    Here the whole series is interpolated into a single dense polyline
    first (``_sample_monotone_curve``), and color is decided per tiny
    rendered sub-segment by comparing consecutive interpolated points.
    The color can therefore only flip where the rendered curve's own
    slope crosses zero - i.e. exactly at its visual peak/valley - so
    the transition always lines up with the curve's real turning point,
    with no gap. Screen y grows downward, so a falling pixel-y means
    the underlying value rose (mirrors the old ``delta >= 0`` rule).
    """
    n = len(points)
    if n < 2:
        return
    if n == 2:
        color = color_rising if points[1][1] <= points[0][1] else color_falling
        _draw_round_segment(draw, points[0], points[1], color, width)
        return

    curve = _sample_monotone_curve(points, samples_per_segment)
    for j in range(len(curve) - 1):
        p0, p1 = curve[j], curve[j + 1]
        color = color_rising if p1[1] <= p0[1] else color_falling
        _draw_round_segment(draw, p0, p1, color, width)


def _draw_smooth_dashed_line(
    draw: ImageDraw.ImageDraw,
    points: list[tuple[float, float]],
    *,
    color,
    width: int,
    samples_per_segment: int = 10,
    dash_on: int = 3,
    dash_off: int = 2,
) -> None:
    """Draw ``points`` as a single-color smooth dashed curve (monotone
    cubic Hermite spline), ClashPerk-style, used for the "vs previous
    month" comparison line. Dashing is applied by skipping every other
    small chunk of spline samples rather than true arc-length dashing -
    simple and looks right at the sample density used here."""
    n = len(points)
    if n < 2:
        return

    curve = _sample_monotone_curve(points, samples_per_segment)

    period = dash_on + dash_off
    for j in range(len(curve) - 1):
        if (j % period) < dash_on:
            _draw_round_segment(draw, curve[j], curve[j + 1], color, width)


def render_elo_graph_image(
    series: list[GraphCalendarPoint],
    *,
    title: str,
    width: int = 660,
    avatar_bytes: bytes | None = None,
    season_month_text: str | None = None,
    year_text: str | None = None,
    avg_win: int = 0,
    avg_loss: int = 0,
    avg_gain: int = 0,
    final_elo: int | None = None,
    prev_avg_win: int | None = None,
    prev_avg_loss: int | None = None,
    prev_avg_gain: int | None = None,
    prev_final_elo: int | None = None,
    prev_month_label: str | None = None,
    no_prev_data: bool = False,
    compare_series: list[GraphSeriesPoint] | None = None,
    axis_ticks: list[GraphAxisTick] | None = None,
    rest_gaps: list[GraphRestGap] | None = None,
    axis_min: int = 300,
    axis_max: int = 1000,
    axis_rows: int = 7,
) -> io.BytesIO:
    """
    Render a ClashPerk-style Elo trend card for one season/month: avatar +
    player name as the title, four stat boxes (AVG. WIN / AVG. LOSS /
    AVG. GAIN / FINAL ELO), and a smooth line chart of daily elo
    underneath.

    ``series`` is a list of (x_fraction, elo) points, oldest first - see
    utils.elo_graph.build_calendar_series(). ``x_fraction`` positions the
    point on a real calendar timeline (0..1 across the full axis span)
    rather than evenly spaced by index, so same-day matches ("sets") sit
    close together instead of on top of each other. It may include the
    previous month's carry-over anchor point as its first entry (at
    x_fraction=0.0); that point is drawn like any other, but the caller
    must NOT have folded it into avg_win/avg_loss/avg_gain (see
    utils.elo_graph.compute_month_averages). The line is rendered as a
    smooth monotone cubic Hermite curve rather than sharp straight
    segments, matching ClashPerk's trend graph.

    ``axis_ticks`` is a list of (x_fraction, "MM-DD") entries on that
    same 0..1 timeline, one per calendar day across the whole axis span
    - see utils.elo_graph.build_calendar_series(). It is pre-generated
    for every day regardless of whether ``series`` has a match that day,
    so the x-axis lays out the full month ahead of time (including
    future/unplayed days) instead of only labeling days with data. If
    omitted, falls back to labeling ``series`` points directly.

    If the ``prev_*`` values are given, a smaller comparison figure is
    drawn next to each stat box showing the previous month's equivalent
    value in a distinct color, ClashPerk-style; a stat with no previous
    data simply omits the comparison.

    ``compare_series``, if given, is a second (date_label, elo) series -
    typically the previous month's own build_daily_series() output -
    drawn as a smooth light-blue DASHED line with no dots, overlaid on the
    same chart for a shape-to-shape "vs last month" comparison. It is
    plotted across the full chart width independently of ``series`` (by
    its own point count, not by matching calendar dates), so the two
    lines may have different lengths.

    The y-axis is fixed to ``axis_min``..``axis_max``, split evenly into
    ``axis_rows`` rows (``axis_rows`` + 1 gridlines) rather than
    auto-scaling to the data, matching ClashPerk's trophy graph. With the
    defaults (300..1000, 7 rows) this gives clean 100-point gridlines:
    300, 400, 500, 600, 700, 800, 900, 1000.

    ``rest_gaps``, if given, is a list of (x_fraction_start,
    x_fraction_end) spans on the same 0..1 calendar axis as ``series`` -
    see utils.elo_graph.find_rest_gaps() / rest_gap_fractions(). Each
    span is drawn as a translucent blue vertical band spanning the full
    chart height, with a dashed blue border, so a multi-day stretch with
    no match played reads as visually distinct from an ordinary stretch
    of the line instead of blending in with the rest of the chart.
    """
    # Rendered at double the usual SCALE used by other cards (leaderboard,
    # history table, ...). The earlier anti-aliased curve layer fixed the
    # jagged line *edges*, but the card's overall pixel resolution was
    # still the limiting factor once a person pinch-zooms in on Discord:
    # zooming past an image's native resolution always softens it, no
    # matter how clean the edges were drawn. Doubling the resolution here
    # (only for this card) gives real extra pixel detail to zoom into,
    # closer to how crisp ClashPerk's own graph stays under zoom.
    GSCALE = SCALE * 2

    pad_x = 20 * GSCALE
    title_h = 34 * GSCALE
    season_h = 20 * GSCALE if season_month_text else 0
    stat_box_h = 64 * GSCALE
    stat_gap = 10 * GSCALE
    chart_top_pad = 8 * GSCALE
    chart_h = 220 * GSCALE
    x_label_h = 40 * GSCALE
    pad_bottom = 14 * GSCALE
    width_px = width * GSCALE

    height_px = (
        title_h
        + season_h
        + stat_gap
        + stat_box_h
        + chart_top_pad
        + chart_h
        + x_label_h
        + pad_bottom
    )

    img = _get_lb_template(width_px, height_px).copy()
    draw = ImageDraw.Draw(img)

    title_font = _load_lb_font(22 * GSCALE, bold=True, for_text=title)
    stat_label_font = _load_latin_font(12 * GSCALE, bold=True)
    stat_value_font = _load_latin_font(22 * GSCALE, bold=True)
    stat_compare_font = _load_latin_font(13 * GSCALE, bold=True)
    axis_font = _load_latin_font(13 * GSCALE)

    # ── title (avatar + name, season/month top-right) ───────────────────
    title_x = pad_x
    title_y = 8 * GSCALE
    avatar_size = 26 * GSCALE
    if avatar_bytes:
        avatar_img = _circular_avatar(avatar_bytes, avatar_size)
        if avatar_img is not None:
            probe_bbox = draw.textbbox((0, title_y), title, font=title_font)
            text_center_y = (probe_bbox[1] + probe_bbox[3]) / 2
            avatar_y = int(text_center_y - avatar_size / 2)
            img.paste(avatar_img, (pad_x, avatar_y), avatar_img)
            title_x = pad_x + avatar_size + 8 * GSCALE

    draw.text((title_x, title_y), title, fill=_LB_TEXT, font=title_font)
    if year_text:
        year_font = _load_latin_font(22 * GSCALE, bold=True)
        year_w = draw.textlength(year_text, font=year_font)
        draw.text((width_px - pad_x - year_w, title_y), year_text, fill=_LB_TEXT, font=year_font)
    if season_month_text:
        season_font = _load_latin_font(14 * GSCALE, bold=True)
        season_w = draw.textlength(season_month_text, font=season_font)
        draw.text(
            (width_px - pad_x - season_w, title_h + 4 * GSCALE),
            season_month_text,
            fill=_LB_TEXT_MUTED,
            font=season_font,
        )
    # ── stat boxes: AVG. WIN / AVG. LOSS / AVG. GAIN / FINAL ELO ────────
    stat_top = title_h + season_h + stat_gap
    box_gap = 8 * GSCALE
    box_w = (width_px - pad_x * 2 - box_gap * 3) // 4

    def _fmt_signed(value: int) -> str:
        return f"+{value}" if value >= 0 else str(value)

    stats = [
        ("AVG. WIN", _fmt_signed(avg_win), _HIST_WIN, prev_avg_win),
        ("AVG. LOSS", _fmt_signed(avg_loss), _HIST_LOSS, prev_avg_loss),
        (
            "AVG. GAIN",
            _fmt_signed(avg_gain),
            _HIST_WIN if avg_gain >= 0 else _HIST_LOSS,
            prev_avg_gain,
        ),
        (
            "FINAL ELO",
            str(final_elo) if final_elo is not None else "-",
            _LB_TEXT,
            prev_final_elo,
        ),
    ]

    for idx, (label, value_text, value_color, prev_value) in enumerate(stats):
        box_x0 = pad_x + idx * (box_w + box_gap)
        box_x1 = box_x0 + box_w
        draw.rounded_rectangle(
            (box_x0, stat_top, box_x1, stat_top + stat_box_h),
            radius=8 * GSCALE,
            fill=_GRAPH_BOX_BG,
        )

        inner_x = box_x0 + 10 * GSCALE
        draw.text(
            (inner_x, stat_top + 8 * GSCALE), label, fill=_LB_TEXT_MUTED, font=stat_label_font
        )

        value_y = stat_top + 24 * GSCALE
        draw.text((inner_x, value_y), value_text, fill=value_color, font=stat_value_font)

        if prev_value is not None:
            compare_text = _fmt_signed(prev_value) if label != "FINAL ELO" else str(prev_value)
            if prev_month_label:
                compare_text = f"{compare_text} ({prev_month_label})"
            value_w = _lb_text_width(draw, value_text, stat_value_font)
            compare_x = inner_x + value_w + 6 * GSCALE
            compare_max_w = box_x1 - 6 * GSCALE - compare_x
            if compare_max_w > 0:
                display_compare = _lb_truncate_text(
                    draw, compare_text, stat_compare_font, compare_max_w
                )
                draw.text(
                    (compare_x, value_y + 5 * GSCALE),
                    display_compare,
                    fill=_GRAPH_COMPARE,
                    font=stat_compare_font,
                )

    # ── line chart ────────────────────────────────────────────────────
    chart_top = stat_top + stat_box_h + chart_top_pad
    chart_bottom = chart_top + chart_h
    axis_label_w = 42 * GSCALE
    chart_x0 = pad_x + axis_label_w
    chart_x1 = width_px - pad_x - axis_label_w

    ticks = _graph_ticks(axis_min, axis_max, axis_rows)

    def _y_for(elo: int) -> int:
        clamped = min(max(elo, axis_min), axis_max)
        ratio = (clamped - axis_min) / (axis_max - axis_min) if axis_max != axis_min else 0
        return int(chart_bottom - ratio * chart_h)

    def _x_for(fraction: float) -> float:
        return chart_x0 + fraction * (chart_x1 - chart_x0)

    # Figure out which axis_ticks (calendar days) actually get a visible
    # label, ahead of drawing anything - these same x-positions are used
    # for the vertical gridlines below, so the vertical lines always line
    # up with the day labels instead of being independently spaced.
    tick_xs: list[float] = []
    label_indices: set[int] = set()
    if axis_ticks:
        tick_xs = [_x_for(frac) for frac, _ in axis_ticks]
        max_label_w = max(
            (_lb_text_width(draw, lbl, axis_font) for _, lbl in axis_ticks), default=0
        )
        # Slanted labels only take up ``width * cos(angle)`` of
        # horizontal room instead of their full upright width, so
        # thin them out based on that projected footprint - using
        # the un-rotated width here (as the old code did) reserves
        # far more gap than the slant actually needs and, combined
        # with the old center-then-clamp positioning, is what caused
        # edge labels to collide (e.g. "07-31" and "08-03" rendered
        # on top of each other).
        min_gap_px = max_label_w * math.cos(math.radians(_GRAPH_AXIS_LABEL_ANGLE)) + 10 * GSCALE
        label_indices = _graph_pick_label_indices(tick_xs, min_gap_px)

    for tick in ticks:
        ty = _y_for(tick)
        draw.line((chart_x0, ty, chart_x1, ty), fill=_GRAPH_GRID, width=max(1, GSCALE // 2))
        tick_text = str(round(tick))
        tick_w = _lb_text_width(draw, tick_text, axis_font)
        draw.text(
            (chart_x0 - 8 * GSCALE - tick_w, ty - 7 * GSCALE),
            tick_text,
            fill=_GRAPH_AXIS_TEXT,
            font=axis_font,
        )
        draw.text(
            (chart_x1 + 8 * GSCALE, ty - 7 * GSCALE),
            tick_text,
            fill=_GRAPH_AXIS_TEXT,
            font=axis_font,
        )

    # Vertical gridlines, one per labeled day tick - mirrors the
    # horizontal Elo gridlines above so the calendar axis is marked out
    # the same way the Elo axis is, instead of leaving the chart body
    # blank between the date labels at the bottom.
    for idx in label_indices:
        tx = tick_xs[idx]
        draw.line(
            (tx, chart_top, tx, chart_bottom), fill=_GRAPH_GRID, width=max(1, GSCALE // 2)
        )

    # Rest-gap bands (multi-day stretches with no match played) - drawn
    # on top of the grid but underneath the curve, so a genuine break in
    # play reads as a distinct blue-shaded region instead of looking
    # like an ordinary played stretch of the line. Uses its own RGBA
    # overlay layer since the fill needs alpha transparency, which
    # ImageDraw can't blend directly onto the (opaque) base image.
    if rest_gaps:
        gap_layer = Image.new("RGBA", (width_px, height_px), (0, 0, 0, 0))
        gap_draw = ImageDraw.Draw(gap_layer)
        dash_on = 4 * GSCALE
        dash_off = 3 * GSCALE
        border_width = max(1, GSCALE)
        for frac_start, frac_end in rest_gaps:
            gx0 = _x_for(frac_start)
            gx1 = _x_for(frac_end)
            gap_draw.rectangle(
                (gx0, chart_top, gx1, chart_bottom), fill=_GRAPH_REST_GAP_FILL
            )
            for gx in (gx0, gx1):
                _draw_dashed_vline(
                    gap_draw,
                    gx,
                    chart_top,
                    chart_bottom,
                    _GRAPH_REST_GAP_BORDER,
                    border_width,
                    dash_on=dash_on,
                    dash_off=dash_off,
                )
        img.paste(gap_layer, (0, 0), gap_layer)

    def _xs_for(count: int) -> list[float]:
        if count == 1:
            return [chart_x0 + (chart_x1 - chart_x0) / 2]
        step_x = (chart_x1 - chart_x0) / (count - 1)
        return [chart_x0 + i * step_x for i in range(count)]

    # ── anti-aliased curve layer ─────────────────────────────────────
    # ImageDraw.line() has no built-in anti-aliasing, so the smooth
    # monotone Hermite curve comes out with visibly jagged/aliased edges once
    # zoomed in (unlike ClashPerk's smooth vector line). To fix this
    # without touching the rest of the (already crisp) card, the curve(s)
    # are drawn on a separate transparent layer at LINE_SS times this
    # image's resolution, then downsampled back down with LANCZOS -
    # LANCZOS averages multiple supersampled pixels into each final
    # pixel, which is what produces smooth anti-aliased edges - and
    # composited onto the main image using its own alpha as the paste
    # mask.
    # LINE_SS is layered ON TOP of the doubled GSCALE resolution above,
    # so it's halved from before (4 -> 2) to keep total supersampling
    # work about the same as before this resolution bump, while still
    # giving LANCZOS enough samples per final pixel for clean edges.
    LINE_SS = 2
    line_layer = Image.new("RGBA", (width_px * LINE_SS, height_px * LINE_SS), (0, 0, 0, 0))
    line_draw = ImageDraw.Draw(line_layer)

    def _ss_pts(pts: list[tuple[float, float]]) -> list[tuple[float, float]]:
        return [(x * LINE_SS, y * LINE_SS) for x, y in pts]

    # ── "vs last month" dashed comparison line, drawn BEHIND the main
    # line so the current month's trend stays visually on top ─────────
    if compare_series:
        compare_xs = _xs_for(len(compare_series))
        compare_ys = [_y_for(elo) for _, elo in compare_series]
        _draw_smooth_dashed_line(
            line_draw,
            _ss_pts(list(zip(compare_xs, compare_ys))),
            color=_GRAPH_COMPARE,
            width=max(2, 2 * GSCALE) * LINE_SS,
        )

    if series:
        xs = [_x_for(frac) for frac, _ in series]
        ys = [_y_for(elo) for _, elo in series]

        # ClashPerk-style smooth curve instead of sharp straight
        # segments - no per-day dots, since a dot every single calendar
        # day (including flat-filled non-played days) would clutter the
        # chart rather than help read it. Color is decided per tiny
        # interpolated sub-segment inside _draw_smooth_line itself (see
        # its docstring), not per original data segment, so the
        # rise/fall transition always lands exactly on the curve's own
        # peak/valley instead of on an unrelated data vertex.
        _draw_smooth_line(
            line_draw,
            _ss_pts(list(zip(xs, ys))),
            color_rising=_HIST_WIN,
            color_falling=_HIST_LOSS,
            width=max(3, 3 * GSCALE) * LINE_SS,
        )

    # Downsample the supersampled curve layer (main line and/or "vs last
    # month" dashed line) and composite it onto the main image now,
    # before the x-axis day labels are drawn below, so the labels stay
    # crisp on top rather than being dragged through the resize. Safe to
    # do unconditionally even with nothing drawn on line_layer - pasting
    # a fully transparent layer is a no-op.
    line_layer = line_layer.resize((width_px, height_px), Image.LANCZOS)
    img.paste(line_layer, (0, 0), line_layer)

    if series:
        # Draw Peak and Bottom markers (green for peak, red for bottom)
        peak_val = max(elo for _, elo in series)
        bottom_val = min(elo for _, elo in series)

        if peak_val > bottom_val:
            peak_frac = [f for f, elo in series if elo == peak_val][-1]
            bottom_frac = [f for f, elo in series if elo == bottom_val][-1]

            peak_x = _x_for(peak_frac)
            peak_y = _y_for(peak_val)
            bottom_x = _x_for(bottom_frac)
            bottom_y = _y_for(bottom_val)

            r = 4 * GSCALE
            marker_font = _load_latin_font(12 * GSCALE, bold=True)
            
            # Draw peak (green)
            draw.ellipse((peak_x - r, peak_y - r, peak_x + r, peak_y + r), fill=_HIST_WIN)
            peak_text = str(peak_val)
            peak_w = _lb_text_width(draw, peak_text, marker_font)
            # Offset the text slightly above the marker
            draw.text((peak_x - peak_w // 2, peak_y - 20 * GSCALE), peak_text, fill=_HIST_WIN, font=marker_font)

            # Draw bottom (red)
            draw.ellipse((bottom_x - r, bottom_y - r, bottom_x + r, bottom_y + r), fill=_HIST_LOSS)
            bottom_text = str(bottom_val)
            bottom_w = _lb_text_width(draw, bottom_text, marker_font)
            # Offset the text slightly below the marker
            draw.text((bottom_x - bottom_w // 2, bottom_y + 8 * GSCALE), bottom_text, fill=_HIST_LOSS, font=marker_font)

        # x-axis day labels come from axis_ticks (pre-generated for every
        # calendar day of the month, whether or not it has a match yet -
        # see utils.elo_graph.build_calendar_series()), not from the data
        # points themselves, so unplayed days still get their own tick.
        if axis_ticks:
            label_top_y = chart_bottom + 6 * GSCALE
            for idx in sorted(label_indices):
                label_text = axis_ticks[idx][1]
                _draw_slanted_axis_label(
                    img,
                    draw,
                    tick_xs[idx],
                    label_top_y,
                    label_text,
                    axis_font,
                    _GRAPH_AXIS_TEXT,
                )
    else:
        no_data_text = "No matches recorded this month"
        no_data_w = _lb_text_width(draw, no_data_text, axis_font)
        draw.text(
            ((width_px - no_data_w) // 2, (chart_top + chart_bottom) // 2),
            no_data_text,
            fill=_GRAPH_AXIS_TEXT,
            font=axis_font,
        )

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


async def render_elo_graph_image_async(
    series: list[GraphCalendarPoint],
    *,
    title: str,
    width: int = 660,
    avatar_bytes: bytes | None = None,
    season_month_text: str | None = None,
    year_text: str | None = None,
    avg_win: int = 0,
    avg_loss: int = 0,
    avg_gain: int = 0,
    final_elo: int | None = None,
    prev_avg_win: int | None = None,
    prev_avg_loss: int | None = None,
    prev_avg_gain: int | None = None,
    prev_final_elo: int | None = None,
    prev_month_label: str | None = None,
    no_prev_data: bool = False,
    compare_series: list[GraphSeriesPoint] | None = None,
    axis_ticks: list[GraphAxisTick] | None = None,
    rest_gaps: list[GraphRestGap] | None = None,
    axis_min: int = 300,
    axis_max: int = 1000,
    axis_rows: int = 7,
) -> io.BytesIO:
    """Async wrapper around render_elo_graph_image (see render_leaderboard_image_async)."""
    return await asyncio.to_thread(
        render_elo_graph_image,
        series,
        title=title,
        width=width,
        avatar_bytes=avatar_bytes,
        season_month_text=season_month_text,
        year_text=year_text,
        avg_win=avg_win,
        avg_loss=avg_loss,
        avg_gain=avg_gain,
        final_elo=final_elo,
        prev_avg_win=prev_avg_win,
        prev_avg_loss=prev_avg_loss,
        prev_avg_gain=prev_avg_gain,
        prev_final_elo=prev_final_elo,
        prev_month_label=prev_month_label,
        no_prev_data=no_prev_data,
        compare_series=compare_series,
        axis_ticks=axis_ticks,
        rest_gaps=rest_gaps,
        axis_min=axis_min,
        axis_max=axis_max,
        axis_rows=axis_rows,
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