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
    Font files must be bundled manually — no automatic download is performed.
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

    # Ưu tiên file đã tải về / có sẵn trong assets/
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
# Font caching – lru_cache giữ font objects trong memory, tránh đọc disk
# mỗi lần render.  maxsize=128 đủ cho tất cả size/bold combos thực tế.
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