"""Feasible requested counts must not lose slots to a too-small ideal pitch."""
from io import BytesIO

from PIL import Image

from core.chapter_watermarks import chapter_watermark_settings


def test_three_marks_fit_at_minimum_gap_without_discarding_middle_slot():
    png = BytesIO()
    Image.new("RGBA", (100, 50), (255, 0, 0, 255)).save(png, "PNG")
    pages = [dict(name="page.png", width=800, height=1000)]
    value = dict(png_bytes=png.getvalue(), repeat=True, auto_count=False,
                 repeat_count=3, minimum_gap=400, size_mode="pixels", width_px=100,
                 enabled_pages=["page.png"], margin_y=24, avoid_text=False)

    points = chapter_watermark_settings(pages, value)["page.png"]["positions"]

    assert len(points) == 3, "All three logos fit: 3×50 + 2×400 + 2×24 = 998"
    assert [y for _, y in points] == [25, 475, 925]


def test_large_end_margins_reduce_count_and_spread_remaining_marks_evenly():
    png = BytesIO()
    Image.new("RGBA", (100, 50), (255, 0, 0, 255)).save(png, "PNG")
    pages = [dict(name="page.png", width=800, height=1000)]
    value = dict(png_bytes=png.getvalue(), repeat=True, auto_count=False,
                 repeat_count=8, minimum_gap=250, size_mode="pixels", width_px=100,
                 enabled_pages=["page.png"], margin_y=200, avoid_text=False)

    points = chapter_watermark_settings(pages, value)["page.png"]["positions"]

    assert len(points) == 2
    assert all(200 <= y <= 750 for _, y in points)
    assert points[1][1] - points[0][1] >= 300
