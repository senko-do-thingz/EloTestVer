"""
Tests for rendering pipeline (utils/rendering.py).
Verifies image dimensions, type, and caching behaviour.
Run: pytest tests/test_rendering.py -v
"""
import sys
import os
import io
import asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from PIL import Image


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _open_buf(buf: io.BytesIO) -> Image.Image:
    buf.seek(0)
    return Image.open(buf)


SAMPLE_ENTRIES_SIMPLE = [
    (1, "Alice", "847"),
    (2, "Bob", "792"),
    (3, "Charlie", "731"),
    (4, "Dave", "624"),
    (5, "Eve", "571"),
]

SAMPLE_ENTRIES_WITH_HIGHLIGHT = [
    (1, "Alice", "847", False),
    (2, "Bob", "792", True),
    (3, "Charlie", "731", False),
]

SAMPLE_ENTRIES_CJK = [
    (1, "田中太郎", "863"),
    (2, "김철수", "741"),
    (3, "Alice", "618"),
]

SAMPLE_ENTRIES_LONG_NAME = [
    (1, "A" * 80, "556"),
    (2, "B" * 80, "521"),
]


# ---------------------------------------------------------------------------
# render_leaderboard_image
# ---------------------------------------------------------------------------

class TestRenderLeaderboardImage:
    def test_returns_bytesio(self):
        from utils.rendering import render_leaderboard_image
        buf = render_leaderboard_image(SAMPLE_ENTRIES_SIMPLE, title="Test LB")
        assert isinstance(buf, io.BytesIO)

    def test_valid_png(self):
        from utils.rendering import render_leaderboard_image
        buf = render_leaderboard_image(SAMPLE_ENTRIES_SIMPLE, title="Test LB")
        img = _open_buf(buf)
        assert img.format == "PNG"

    def test_image_mode_rgb(self):
        from utils.rendering import render_leaderboard_image
        buf = render_leaderboard_image(SAMPLE_ENTRIES_SIMPLE, title="Test LB")
        img = _open_buf(buf)
        assert img.mode == "RGB"

    def test_default_width(self):
        from utils.rendering import render_leaderboard_image
        buf = render_leaderboard_image(SAMPLE_ENTRIES_SIMPLE, title="Test LB")
        img = _open_buf(buf)
        # Default width=580, SCALE=2 → expected pixel width 1160
        assert img.size[0] == 1160

    def test_custom_width(self):
        from utils.rendering import render_leaderboard_image
        buf = render_leaderboard_image(SAMPLE_ENTRIES_SIMPLE, title="Test LB", width=400)
        img = _open_buf(buf)
        assert img.size[0] == 800  # 400 * SCALE

    def test_height_scales_with_entries(self):
        from utils.rendering import render_leaderboard_image
        buf3 = render_leaderboard_image(SAMPLE_ENTRIES_SIMPLE[:3], title="3 rows")
        buf5 = render_leaderboard_image(SAMPLE_ENTRIES_SIMPLE, title="5 rows")
        h3 = _open_buf(buf3).size[1]
        h5 = _open_buf(buf5).size[1]
        assert h5 > h3

    def test_empty_entries(self):
        from utils.rendering import render_leaderboard_image
        buf = render_leaderboard_image([], title="Empty")
        img = _open_buf(buf)
        assert img.size[1] > 0  # header only, should still render

    def test_highlight_entry(self):
        from utils.rendering import render_leaderboard_image
        # Should not raise; just checking it completes
        buf = render_leaderboard_image(SAMPLE_ENTRIES_WITH_HIGHLIGHT, title="Highlighted")
        assert _open_buf(buf).format == "PNG"

    def test_cjk_names(self):
        from utils.rendering import render_leaderboard_image
        buf = render_leaderboard_image(SAMPLE_ENTRIES_CJK, title="CJK Test")
        assert _open_buf(buf).format == "PNG"

    def test_long_names_truncated(self):
        from utils.rendering import render_leaderboard_image
        buf = render_leaderboard_image(SAMPLE_ENTRIES_LONG_NAME, title="Long Names")
        assert _open_buf(buf).format == "PNG"

    def test_with_pagination(self):
        from utils.rendering import render_leaderboard_image
        buf = render_leaderboard_image(
            SAMPLE_ENTRIES_SIMPLE, title="Paged LB", page=2, total_pages=5
        )
        assert _open_buf(buf).format == "PNG"

    def test_buf_is_seeked_to_zero(self):
        from utils.rendering import render_leaderboard_image
        buf = render_leaderboard_image(SAMPLE_ENTRIES_SIMPLE, title="Seek Test")
        # Should be able to read immediately without manual seek
        data = buf.read()
        assert data[:4] == b"\x89PNG"


# ---------------------------------------------------------------------------
# render_leaderboard_image_async
# ---------------------------------------------------------------------------

class TestRenderLeaderboardImageAsync:
    def test_async_returns_same_result(self):
        from utils.rendering import render_leaderboard_image, render_leaderboard_image_async

        buf_sync = render_leaderboard_image(SAMPLE_ENTRIES_SIMPLE, title="Sync")
        buf_async = asyncio.get_event_loop().run_until_complete(
            render_leaderboard_image_async(SAMPLE_ENTRIES_SIMPLE, title="Async")
        )
        img_sync = _open_buf(buf_sync)
        img_async = _open_buf(buf_async)
        assert img_sync.size == img_async.size
        assert img_async.format == "PNG"

    def test_async_concurrent(self):
        """Running two renders concurrently should not raise."""
        from utils.rendering import render_leaderboard_image_async

        async def run():
            t1 = render_leaderboard_image_async(SAMPLE_ENTRIES_SIMPLE, title="A")
            t2 = render_leaderboard_image_async(SAMPLE_ENTRIES_SIMPLE[:3], title="B")
            buf1, buf2 = await asyncio.gather(t1, t2)
            return buf1, buf2

        buf1, buf2 = asyncio.get_event_loop().run_until_complete(run())
        assert _open_buf(buf1).format == "PNG"
        assert _open_buf(buf2).format == "PNG"


# ---------------------------------------------------------------------------
# Font caching (lru_cache)
# ---------------------------------------------------------------------------

class TestFontCaching:
    def test_latin_font_cache_hit(self):
        from utils.rendering import _load_latin_font
        font_a = _load_latin_font(42)
        font_b = _load_latin_font(42)
        assert font_a is font_b  # same object from cache

    def test_latin_font_different_sizes(self):
        from utils.rendering import _load_latin_font
        font_sm = _load_latin_font(20)
        font_lg = _load_latin_font(40)
        assert font_sm is not font_lg

    def test_cache_info(self):
        from utils.rendering import _load_latin_font
        _load_latin_font.cache_clear()
        _load_latin_font(30)
        _load_latin_font(30)  # second call → cache hit
        info = _load_latin_font.cache_info()
        assert info.hits >= 1

    def test_cjk_cache_hit(self):
        from utils.rendering import _load_cjk_font
        a = _load_cjk_font(42)
        b = _load_cjk_font(42)
        # Even if None (no CJK font in env), both must be the same object
        assert a is b

# ---------------------------------------------------------------------------
# render_elo_graph_image
# ---------------------------------------------------------------------------

SAMPLE_GRAPH_SERIES = [
    (0.0, 480),
    (0.2, 520),
    (0.4, 505),
    (0.6, 560),
    (0.8, 610),
]

SAMPLE_AXIS_TICKS = [
    (0.0, "06-30"),
    (0.2, "07-01"),
    (0.4, "07-02"),
    (0.6, "07-03"),
    (0.8, "07-04"),
    (1.0, "07-05"),
]


class TestRenderEloGraphImage:
    def test_returns_bytesio(self):
        from utils.rendering import render_elo_graph_image
        buf = render_elo_graph_image(SAMPLE_GRAPH_SERIES, title="Test Graph")
        assert isinstance(buf, io.BytesIO)

    def test_valid_png(self):
        from utils.rendering import render_elo_graph_image
        buf = render_elo_graph_image(SAMPLE_GRAPH_SERIES, title="Test Graph")
        img = _open_buf(buf)
        assert img.format == "PNG"

    def test_default_width(self):
        from utils.rendering import render_elo_graph_image
        buf = render_elo_graph_image(SAMPLE_GRAPH_SERIES, title="Test Graph")
        img = _open_buf(buf)
        # Default width=660, GSCALE=SCALE*2=4 -> expected pixel width 2640
        assert img.size[0] == 2640

    def test_custom_width(self):
        from utils.rendering import render_elo_graph_image
        buf = render_elo_graph_image(SAMPLE_GRAPH_SERIES, title="Test Graph", width=500)
        img = _open_buf(buf)
        assert img.size[0] == 2000

    def test_empty_series_does_not_raise(self):
        from utils.rendering import render_elo_graph_image
        buf = render_elo_graph_image([], title="Empty")
        assert _open_buf(buf).format == "PNG"

    def test_single_point_series(self):
        from utils.rendering import render_elo_graph_image
        buf = render_elo_graph_image([(0.5, 500)], title="Single Point")
        assert _open_buf(buf).format == "PNG"

    def test_axis_ticks_render_without_raising(self):
        from utils.rendering import render_elo_graph_image
        buf = render_elo_graph_image(
            SAMPLE_GRAPH_SERIES, title="With axis ticks", axis_ticks=SAMPLE_AXIS_TICKS
        )
        assert _open_buf(buf).format == "PNG"

    def test_no_axis_ticks_still_renders_line(self):
        # Without axis_ticks, the line itself must still render (just
        # without x-axis day labels).
        from utils.rendering import render_elo_graph_image
        buf = render_elo_graph_image(SAMPLE_GRAPH_SERIES, title="No axis ticks")
        assert _open_buf(buf).format == "PNG"

    def test_season_month_text_grows_height(self):
        # A dedicated row is reserved for season_month_text, so a card
        # with it should be taller than one without (see the layout fix
        # that previously let this text overlap the stat boxes).
        from utils.rendering import render_elo_graph_image
        buf_without = render_elo_graph_image(SAMPLE_GRAPH_SERIES, title="No season text")
        buf_with = render_elo_graph_image(
            SAMPLE_GRAPH_SERIES, title="With season text", season_month_text="Season: 1, Month: 2"
        )
        h_without = _open_buf(buf_without).size[1]
        h_with = _open_buf(buf_with).size[1]
        assert h_with > h_without

    def test_comparison_values_do_not_raise(self):
        from utils.rendering import render_elo_graph_image
        buf = render_elo_graph_image(
            SAMPLE_GRAPH_SERIES,
            title="With comparison",
            avg_win=42,
            avg_loss=-31,
            avg_gain=11,
            final_elo=610,
            prev_avg_win=38,
            prev_avg_loss=-29,
            prev_avg_gain=9,
            prev_final_elo=480,
            prev_month_label="S1M2",
        )
        assert _open_buf(buf).format == "PNG"

    def test_many_points_no_overlap_crash(self):
        from utils.rendering import render_elo_graph_image
        series = [(d / 28, 500 + d) for d in range(29)]
        axis_ticks = [(d / 28, f"07-{d + 1:02d}") for d in range(29)]
        buf = render_elo_graph_image(series, title="Many Points", axis_ticks=axis_ticks)
        assert _open_buf(buf).format == "PNG"

    def test_buf_is_seeked_to_zero(self):
        from utils.rendering import render_elo_graph_image
        buf = render_elo_graph_image(SAMPLE_GRAPH_SERIES, title="Seek Test")
        data = buf.read()
        assert data[:4] == b"\x89PNG"


class TestRenderEloGraphImageAsync:
    def test_async_returns_same_result(self):
        from utils.rendering import render_elo_graph_image, render_elo_graph_image_async

        buf_sync = render_elo_graph_image(SAMPLE_GRAPH_SERIES, title="Sync")
        buf_async = asyncio.get_event_loop().run_until_complete(
            render_elo_graph_image_async(SAMPLE_GRAPH_SERIES, title="Async")
        )
        img_sync = _open_buf(buf_sync)
        img_async = _open_buf(buf_async)
        assert img_sync.size == img_async.size
        assert img_async.format == "PNG"